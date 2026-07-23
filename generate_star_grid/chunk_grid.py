"""
Disk-bounded chunked execution of a flat parameter grid (e.g. a joint Sobol cloud).

A flat grid runs as one SLURM array over task ids 0..N-1, and every run directory
lands side by side in the grid directory. For a large grid that footprint can
exhaust the filesystem long before the array finishes -- each run directory holds
its own DATA/ and photos/, and the grid's real output (grid_TAMS, grid_profiles,
combined_history.hdf5) is a small fraction of it.

This module carves the task-id range into contiguous chunks and, once a chunk's
models have finished, folds that chunk down to a single combined_history.hdf5:

    chunk_00000_00511/combined_history.hdf5   <- intermediate
    chunk_00512_01023/combined_history.hdf5   <- intermediate
    combined_history.hdf5                     <- master, merged from the above

Everything lives in the grid's own (project) directory -- there is no scratch
staging or relocation. Once the master is built and its row count verified, the
intermediate chunk directories are deleted to reclaim space (pass --keep_chunks
to keep them).

Chunking is safe for Sobol grids because every task rebuilds the identical cloud
from --sobol_seed and selects its own index, so task id -> parameters is fixed no
matter how the range is split.

Unlike cleanup_grid_data's all-or-nothing check, the guard here is per model
directory: a directory is compressible if and only if its own TAMS save file
exists in grid_TAMS/. A partially failed grid therefore still compresses its
completed models, rather than refusing to free anything.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .grid_utils import (
    PARAM_FORMAT,
    coerce_cli_values,
    compute_param_formats,
    generate_grid,
    load_history_with_constants_from_profile,
    make_run_dir_name,
    parse_extra_params,
)
from .merge_grids import merge_batch_hdf5, _hdf5_nrows

CHUNK_DIR_GLOB = "chunk_*"
COMBINED_NAME = "combined_history.hdf5"

# Directories in a grid dir that are outputs/bookkeeping, never model run dirs.
NON_MODEL_DIRS = {"grid_TAMS", "grid_profiles", "grid_inlists", "LOGS", "slurm_logs", "DATA"}


def free_gb(path: Path) -> float:
    """
    Return the free space in GiB available at path.

    On the /nfs/roberts project mount this is quota-aware: statvfs reports the
    group quota's headroom (quota minus usage), not the underlying filesystem's
    hundreds of free TB that `df -h` shows. It is also live, whereas `getquota`
    serves cached figures that can lag a large delete by hours -- so this is the
    signal to drive the compression threshold from.
    """
    st = shutil.disk_usage(str(path))
    return st.free / 1024 ** 3


def chunk_bounds(num_points: int, chunk_size: int) -> list:
    """
    Split range(num_points) into contiguous [start, end] task-id pairs (inclusive).

    Args:
        num_points: Total models in the grid (the array is 0..num_points-1).
        chunk_size: Maximum tasks per chunk.

    Returns:
        List of (start, end) inclusive bounds; the final chunk may be short.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
    if num_points < 1:
        raise ValueError(f"num_points must be >= 1, got {num_points}")
    return [(s, min(s + chunk_size, num_points) - 1) for s in range(0, num_points, chunk_size)]


def chunk_dir_for(parent_dir: Path, start: int, end: int) -> Path:
    """Return the chunk directory path for an inclusive task-id range."""
    return Path(parent_dir) / f"chunk_{start:05d}_{end:05d}"


def model_dirs_for_tasks(
    parent_dir: Path,
    param_ranges: dict,
    task_ids: range,
    grid_type: str = "sobol",
    num_points: int = 8,
    sobol_seed: Optional[int] = None,
    param_registry: Optional[dict] = None,
) -> list:
    """
    Map a range of array task ids to their model directory paths.

    Rebuilds the same cloud the array tasks build (same generate_grid call, same
    seed) and names each directory exactly as run_mesa_model does, so the mapping
    matches what is actually on disk.

    Args:
        parent_dir: Grid run directory holding the model subdirectories.
        param_ranges: Parameter spec dict, as passed to generate_grid.
        task_ids: Array task ids to resolve.
        grid_type: 'linear' or 'sobol'; must match the submitted array.
        num_points: Total models in the grid; must match the submitted array.
        sobol_seed: Sobol seed; must match the submitted array.
        param_registry: Defaults to PARAM_FORMAT.

    Returns:
        List of model directory paths, in task-id order. Directories that do not
        exist on disk are included -- callers decide what to do about them.
    """
    registry = param_registry or dict(PARAM_FORMAT)
    param_dicts = generate_grid(param_ranges, grid_type=grid_type, num_points=num_points,
                               sobol_seed=sobol_seed)
    formats = compute_param_formats(param_ranges, grid_type=grid_type, num_points=num_points,
                                    param_registry=registry)
    dirs = []
    for idx in task_ids:
        if idx < 0 or idx >= len(param_dicts):
            raise IndexError(f"task_id {idx} out of range for {len(param_dicts)} parameter sets.")
        dirs.append(Path(parent_dir) / make_run_dir_name(param_dicts[idx], formats, registry))
    return dirs


def has_tams(parent_dir: Path, model_dir: Path) -> bool:
    """
    Return True if model_dir's TAMS save file is present in grid_TAMS/.

    This is the per-directory completion signal used across the codebase (see
    cleanup_grid_data and submit_grid check-failed): run_mesa_model moves
    TAMS_<dirname>.mod into grid_TAMS/ only after MESA produced it.
    """
    return (Path(parent_dir) / "grid_TAMS" / f"TAMS_{Path(model_dir).name}.mod").exists()


def partition_by_completion(parent_dir: Path, model_dirs: list) -> tuple:
    """
    Split model dirs into (completed, incomplete, missing) by the per-dir TAMS guard.

    Args:
        parent_dir: Grid run directory (must contain grid_TAMS/).
        model_dirs: Candidate model directories.

    Returns:
        (completed, incomplete, missing) lists of paths. 'completed' have a TAMS
        file, 'incomplete' exist on disk without one (still running, or failed),
        'missing' are not on disk at all (never started).
    """
    completed, incomplete, missing = [], [], []
    for d in model_dirs:
        d = Path(d)
        if not d.is_dir():
            missing.append(d)
        elif has_tams(parent_dir, d):
            completed.append(d)
        else:
            incomplete.append(d)
    return completed, incomplete, missing


def compress_chunk(
    parent_dir: Path,
    chunk_dir: Path,
    model_dirs: list,
    constant_columns: Optional[list] = None,
    delete_run_dirs: bool = True,
) -> Optional[Path]:
    """
    Fold a chunk's completed model directories down to one combined_history.hdf5.

    Moves each completed model directory into chunk_dir (a same-filesystem
    rename), builds chunk_dir/combined_history.hdf5 from them, verifies the file
    is non-empty, and only then deletes the moved run directories. Incomplete
    models are left untouched in parent_dir so they can be retried.

    The run directories are redundant once the HDF5 exists: TAMS models are
    already in grid_TAMS/, profiles in grid_profiles/, inlists in grid_inlists/,
    and MESA stdout in LOGS/.

    Args:
        parent_dir: Grid run directory.
        chunk_dir: Destination directory for this chunk (created if needed).
        model_dirs: This chunk's model directories (completed or not).
        constant_columns: Parameter keys to extract from directory names.
            Defaults to ['M', 'Y', 'Z', 'alpha'].
        delete_run_dirs: Delete the moved run dirs after a verified write.
            Set False to keep them (combines without reclaiming space).

    Returns:
        Path to the chunk's combined_history.hdf5, or None if the chunk had no
        completed models (nothing is moved or deleted in that case).
    """
    parent_dir = Path(parent_dir)
    chunk_dir = Path(chunk_dir)
    constants = constant_columns if constant_columns is not None else ["M", "Y", "Z", "alpha"]

    completed, incomplete, missing = partition_by_completion(parent_dir, model_dirs)
    print(f"{chunk_dir.name}: {len(completed)} completed, {len(incomplete)} incomplete, "
          f"{len(missing)} never started.")

    if not completed:
        print(f"{chunk_dir.name}: no completed models; nothing to compress.")
        return None

    chunk_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for d in completed:
        dest = chunk_dir / d.name
        if dest.exists():
            # A previous interrupted run already staged this model; keep the
            # staged copy and drop the duplicate rather than failing the chunk.
            shutil.rmtree(d, ignore_errors=True)
        else:
            shutil.move(str(d), str(dest))
        moved.append(dest)

    load_history_with_constants_from_profile(
        parent_dir=chunk_dir,
        constant_columns=constants,
        save_as_hdf5=True,
        hdf5_filename=COMBINED_NAME,
        extract_constants_from_dirname=True,
    )

    out = chunk_dir / COMBINED_NAME
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(
            f"{out} was not written (or is empty) after combining {len(moved)} models. "
            f"Run directories have been left in {chunk_dir} for inspection."
        )

    if delete_run_dirs:
        freed = 0
        for d in moved:
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
        print(f"{chunk_dir.name}: wrote {out.name} "
              f"({out.stat().st_size / 1024 ** 2:.1f} MiB), freed {freed / 1024 ** 3:.1f} GiB "
              f"from {len(moved)} run directories.")
    else:
        print(f"{chunk_dir.name}: wrote {out.name}; run directories kept.")

    return out


def find_chunk_dirs(parent_dir: Path) -> list:
    """Return existing chunk dirs holding a combined_history.hdf5, in task-id order."""
    return sorted(
        d for d in Path(parent_dir).glob(CHUNK_DIR_GLOB)
        if d.is_dir() and (d / COMBINED_NAME).exists()
    )


def merge_master(parent_dir: Path, output_path: Optional[Path] = None,
                 hdf5_key: str = "history") -> Optional[Path]:
    """
    Merge every chunk's combined_history.hdf5 into one master file.

    Delegates to merge_batch_hdf5, which offsets each chunk's Track values so
    every stellar track is globally unique in the master, and pins all chunks to
    a single canonical column order.

    Args:
        parent_dir: Grid run directory containing the chunk_* directories.
        output_path: Master file path. Defaults to parent_dir/combined_history.hdf5.
        hdf5_key: HDF5 store key (must match what the chunks were written with).

    Returns:
        Path to the master file, or None if no chunks were found.
    """
    parent_dir = Path(parent_dir)
    chunk_dirs = find_chunk_dirs(parent_dir)
    if not chunk_dirs:
        print(f"No chunk directories with {COMBINED_NAME} found in {parent_dir}.")
        return None

    out = Path(output_path) if output_path else parent_dir / COMBINED_NAME
    print(f"Merging {len(chunk_dirs)} chunks into {out} ...")
    rows = merge_batch_hdf5(chunk_dirs, out, hdf5_key=hdf5_key)
    print(f"Master written: {out} ({rows} rows from {len(chunk_dirs)} chunks).")
    return out


# ---------------------------------------------------------------------------
# CLI: disk-bounded chunked orchestration of a flat (e.g. Sobol) grid.
#
# Subcommands:
#   plan      - preview the chunk task-id bounds for a grid.
#   compress  - fold one finished chunk down to its combined_history.hdf5.
#   merge     - merge all chunk HDF5s into the master combined_history.hdf5.
#   finalize  - merge into the master, verify its row count, then delete the
#               intermediate chunk directories (unless --keep_chunks).
#   submit    - generate + submit the chained SLURM jobs: one chunk's array at
#               a time, each compressed before the next starts (so peak disk is
#               bounded by a single chunk), then a finalize job at the end.
#   advance   - internal; run by the generated step job to compress the chunk
#               that just finished and submit the next (or finalize).
# ---------------------------------------------------------------------------

import shlex  # noqa: E402  (kept beside the CLI it serves)

DEFAULT_CONSTANTS = ["M", "Y", "Z", "alpha"]
CHUNK_QUEUE_NAME = "chunk_queue.json"
DEFAULT_CHUNK_SIZE = 512


def _resolve_parent_dir(value: Optional[str]) -> str:
    """Return an explicit --parent_dir, else the current working directory."""
    return value if value else str(Path.cwd())


def _add_grid_args(p) -> None:
    """Add the parameter-spec flags shared with grid_utils (same grammar)."""
    p.add_argument("--mass", nargs="+", default=None, metavar="SPEC",
                   help="Initial mass spec, e.g. 0.7:1.8 (required).")
    p.add_argument("--initial_Y", nargs="+", default=["0.27"], metavar="SPEC")
    p.add_argument("--initial_Z", nargs="+", default=["0.02"], metavar="SPEC")
    p.add_argument("--alpha_MLT", nargs="+", default=["2.0"], metavar="SPEC")
    p.add_argument("--param", action="append", default=[], metavar="KEY=SPEC")
    p.add_argument("--grid_type", choices=["linear", "sobol"], default="sobol")
    p.add_argument("--num_points", type=int, required=True)
    p.add_argument("--sobol_seed", type=int, default=0)


def build_param_ranges(args) -> tuple:
    """Build (param_ranges, param_registry) from CLI args, mirroring grid_utils."""
    if args.mass is None:
        raise SystemExit("--mass is required (e.g. --mass 0.7:1.8).")
    param_ranges = {
        "initial_mass": coerce_cli_values(args.mass),
        "initial_y": coerce_cli_values(args.initial_Y),
        "initial_z": coerce_cli_values(args.initial_Z),
        "mixing_length_alpha": coerce_cli_values(args.alpha_MLT),
    }
    registry = dict(PARAM_FORMAT)
    if args.param:
        inlist_text = (Path(args.parent_dir) / "inlist_template").read_text()
        extra_specs, extra_registry = parse_extra_params(args.param, inlist_text)
        param_ranges.update(extra_specs)
        registry.update(extra_registry)
    return param_ranges, registry


def _param_ranges_from_config(config: dict) -> tuple:
    """Rebuild (param_ranges, registry) from a persisted chunk-queue config."""
    ns = argparse.Namespace(
        mass=config["mass"], initial_Y=config["initial_Y"], initial_Z=config["initial_Z"],
        alpha_MLT=config["alpha_MLT"], param=config.get("param", []),
        parent_dir=config["parent_dir"],
    )
    return build_param_ranges(ns)


def _grid_flags(config: dict) -> list:
    """The grid_utils flags that reproduce this grid's cloud (no --task_id)."""
    flags = (["--mass"] + list(config["mass"])
             + ["--initial_Y"] + list(config["initial_Y"])
             + ["--initial_Z"] + list(config["initial_Z"])
             + ["--alpha_MLT"] + list(config["alpha_MLT"]))
    for item in config.get("param", []):
        flags += ["--param", item]
    flags += ["--grid_type", config["grid_type"], "--num_points", str(config["num_points"]),
              "--sobol_seed", str(config["sobol_seed"])]
    return flags


def cmd_plan(args) -> None:
    bounds = chunk_bounds(args.num_points, args.chunk_size)
    print(f"{args.num_points} models / chunk_size {args.chunk_size} -> {len(bounds)} chunk(s):")
    for i, (s, e) in enumerate(bounds):
        print(f"  chunk {i}: tasks {s}-{e}  ({e - s + 1} models)  -> {chunk_dir_for('.', s, e).name}")


def cmd_compress(args) -> None:
    args.parent_dir = _resolve_parent_dir(args.parent_dir)
    parent = Path(args.parent_dir)
    param_ranges, registry = build_param_ranges(args)
    bounds = chunk_bounds(args.num_points, args.chunk_size)
    if not 0 <= args.chunk_index < len(bounds):
        raise SystemExit(f"--chunk_index {args.chunk_index} out of range 0..{len(bounds) - 1}")
    start, end = bounds[args.chunk_index]
    model_dirs = model_dirs_for_tasks(
        parent, param_ranges, range(start, end + 1),
        grid_type=args.grid_type, num_points=args.num_points,
        sobol_seed=args.sobol_seed, param_registry=registry,
    )
    compress_chunk(parent, chunk_dir_for(parent, start, end), model_dirs,
                   constant_columns=args.constants, delete_run_dirs=not args.no_delete)


def cmd_merge(args) -> None:
    parent = Path(_resolve_parent_dir(args.parent_dir))
    out = Path(args.output) if args.output else None
    if merge_master(parent, output_path=out) is None:
        raise SystemExit("No chunk directories to merge.")


def cmd_finalize(args) -> None:
    parent = Path(_resolve_parent_dir(args.parent_dir)).resolve()
    chunk_dirs = find_chunk_dirs(parent)
    master = merge_master(parent, hdf5_key=args.hdf5_key)
    if master is None:
        raise SystemExit("No chunks to merge; nothing finalized.")

    if args.keep_chunks:
        print(f"--keep_chunks: leaving {len(chunk_dirs)} intermediate chunk dir(s) in place.")
        return

    # Only delete the intermediates once the master provably contains every row
    # they held -- the Track offsets in merge_batch_hdf5 keep those rows globally
    # unique, so a matching total is a sound integrity check.
    master_rows = _hdf5_nrows(master, args.hdf5_key)
    chunk_rows = sum(_hdf5_nrows(d / COMBINED_NAME, args.hdf5_key) for d in chunk_dirs)
    if master_rows != chunk_rows:
        raise SystemExit(
            f"Refusing to delete intermediates: master has {master_rows} rows but the "
            f"{len(chunk_dirs)} chunk file(s) total {chunk_rows}. Master left in place; "
            f"chunks kept for inspection."
        )
    for d in chunk_dirs:
        shutil.rmtree(d)
    print(f"Master verified ({master_rows} rows). "
          f"Deleted {len(chunk_dirs)} intermediate chunk dir(s).")


def _write_chunk_scripts(parent: Path, config: dict, queue_file: Path) -> None:
    """Write the generic array / step / finalize SLURM scripts once."""
    python = config["python"]
    conda_env = config["conda_env"]
    grid_flags = " \\\n    ".join(shlex.quote(t) for t in _grid_flags(config))

    (parent / "run_chunk_array.sh").write_text(f"""#!/bin/bash
#SBATCH --job-name=mesa_chunk
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition={config['array_partition']}
#SBATCH --nodes=1
#SBATCH --time={config['array_time']}
#SBATCH --mem={config['array_mem']}
#SBATCH --mail-type={config['array_mail_type']}
#SBATCH --output={parent}/slurm_logs/chunk_%A_%a.out

cd "{parent}" || {{ echo "FATAL: cannot cd to {parent}" >&2; exit 1; }}

module purge
module load miniconda
conda activate {conda_env}

export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

"{python}" -m generate_star_grid.grid_utils \\
    {grid_flags} \\
    --task_id=$SLURM_ARRAY_TASK_ID
""")
    (parent / "run_chunk_array.sh").chmod(0o755)

    (parent / "run_chunk_step.sh").write_text(f"""#!/bin/bash
#SBATCH --job-name=chunk_step
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition={config['step_partition']}
#SBATCH --nodes=1
#SBATCH --time={config['step_time']}
#SBATCH --mem={config['step_mem']}
#SBATCH --mail-type={config['step_mail_type']}
#SBATCH --output={parent}/slurm_logs/chunk_step_%j.out

module purge
module load miniconda
conda activate {conda_env}

"{python}" -m generate_star_grid.chunk_grid advance --queue_file "{queue_file}"
""")
    (parent / "run_chunk_step.sh").chmod(0o755)

    (parent / "run_finalize.sh").write_text(f"""#!/bin/bash
#SBATCH --job-name=chunk_finalize
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition={config['step_partition']}
#SBATCH --nodes=1
#SBATCH --time={config['finalize_time']}
#SBATCH --mem={config['finalize_mem']}
#SBATCH --mail-type={config['step_mail_type']}
#SBATCH --output={parent}/slurm_logs/finalize_%j.out

module purge
module load miniconda
conda activate {conda_env}

"{python}" -m generate_star_grid.chunk_grid finalize \\
    --parent_dir "{parent}"
""")
    (parent / "run_finalize.sh").chmod(0o755)


def _submit_chunk(config: dict, idx: int) -> None:
    """Submit chunk idx's SLURM array, then a step job that runs after it."""
    parent = Path(config["parent_dir"])
    start, end = config["bounds"][idx]
    throttle = config.get("max_cpus")
    array_spec = f"{start}-{end}" + (f"%{throttle}" if throttle else "")
    print(f"Submitting chunk {idx}: tasks {start}-{end} (array {array_spec}).")
    array_job = subprocess.run(
        ["sbatch", "--parsable", "--array", array_spec, str(parent / "run_chunk_array.sh")],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    print(f"  array job {array_job}")
    step_job = subprocess.run(
        ["sbatch", "--parsable", "--dependency", f"afterany:{array_job}", str(parent / "run_chunk_step.sh")],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    print(f"  step job {step_job} (afterany:{array_job})")


def _submit_finalize(config: dict) -> None:
    parent = Path(config["parent_dir"])
    job = subprocess.run(
        ["sbatch", "--parsable", str(parent / "run_finalize.sh")],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    print(f"Submitted finalize job {job}.")


def cmd_submit(args) -> None:
    args.parent_dir = _resolve_parent_dir(args.parent_dir)
    parent = Path(args.parent_dir).resolve()
    build_param_ranges(args)  # validate params (and --param inlist keys) early
    bounds = chunk_bounds(args.num_points, args.chunk_size)
    print(f"{args.num_points} models / chunk_size {args.chunk_size} -> {len(bounds)} chunk(s).")
    for i, (s, e) in enumerate(bounds[:3]):
        print(f"  chunk {i}: tasks {s}-{e}")
    if len(bounds) > 3:
        print(f"  ... ({len(bounds) - 3} more)")
    print("On completion: merge the master, verify it, and delete the intermediate chunks.")

    if args.dry_run:
        print("--dry_run: nothing written or submitted.")
        return

    (parent / "slurm_logs").mkdir(exist_ok=True)
    config = {
        "parent_dir": str(parent),
        "python": args.python, "conda_env": args.conda_env,
        "mass": args.mass, "initial_Y": args.initial_Y, "initial_Z": args.initial_Z,
        "alpha_MLT": args.alpha_MLT, "param": args.param,
        "grid_type": args.grid_type, "num_points": args.num_points,
        "sobol_seed": args.sobol_seed, "chunk_size": args.chunk_size,
        "constants": args.constants,
        "bounds": [list(b) for b in bounds],
        "array_time": args.array_time, "array_mem": args.array_mem,
        "array_partition": args.array_partition, "array_mail_type": args.array_mail_type,
        "step_time": args.step_time, "step_mem": args.step_mem,
        "step_partition": args.step_partition, "step_mail_type": args.step_mail_type,
        "finalize_time": args.finalize_time, "finalize_mem": args.finalize_mem,
        "max_cpus": args.max_cpus,
    }
    queue_file = parent / CHUNK_QUEUE_NAME
    _write_chunk_scripts(parent, config, queue_file)
    state = {"config": config, "remaining": list(range(1, len(bounds))), "current": 0}
    queue_file.write_text(json.dumps(state, indent=2))
    print(f"Chunk queue written to {queue_file}.")
    _submit_chunk(config, 0)


def cmd_advance(args) -> None:
    queue_file = Path(args.queue_file).resolve()
    state = json.loads(queue_file.read_text())
    config = state["config"]
    parent = Path(config["parent_dir"])
    cur = state["current"]
    start, end = config["bounds"][cur]

    print(f"Compressing finished chunk {cur} (tasks {start}-{end}).")
    param_ranges, registry = _param_ranges_from_config(config)
    model_dirs = model_dirs_for_tasks(
        parent, param_ranges, range(start, end + 1),
        grid_type=config["grid_type"], num_points=config["num_points"],
        sobol_seed=config["sobol_seed"], param_registry=registry,
    )
    compress_chunk(parent, chunk_dir_for(parent, start, end), model_dirs,
                   constant_columns=config["constants"], delete_run_dirs=True)

    if state["remaining"]:
        nxt = state["remaining"].pop(0)
        state["current"] = nxt
        queue_file.write_text(json.dumps(state, indent=2))
        _submit_chunk(config, nxt)
    else:
        print("All chunks compressed. Submitting finalize job...")
        _submit_finalize(config)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Disk-bounded chunked execution of a flat (e.g. Sobol) MESA grid.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Preview chunk task-id bounds.")
    p_plan.add_argument("--num_points", type=int, required=True)
    p_plan.add_argument("--chunk_size", type=int, required=True)
    p_plan.set_defaults(func=cmd_plan)

    p_comp = sub.add_parser("compress", help="Fold one finished chunk to its HDF5.")
    p_comp.add_argument("--parent_dir", default=None, help="Grid dir (default: current dir).")
    _add_grid_args(p_comp)
    p_comp.add_argument("--chunk_size", type=int, required=True)
    p_comp.add_argument("--chunk_index", type=int, required=True)
    p_comp.add_argument("--constants", nargs="*", default=DEFAULT_CONSTANTS)
    p_comp.add_argument("--no_delete", action="store_true",
                        help="Combine without deleting the moved run dirs.")
    p_comp.set_defaults(func=cmd_compress)

    p_merge = sub.add_parser("merge", help="Merge all chunk HDF5s into the master.")
    p_merge.add_argument("--parent_dir", default=None, help="Grid dir (default: current dir).")
    p_merge.add_argument("--output", default=None)
    p_merge.set_defaults(func=cmd_merge)

    p_fin = sub.add_parser("finalize", help="Merge the master, verify it, delete the chunk dirs.")
    p_fin.add_argument("--parent_dir", default=None, help="Grid dir (default: current dir).")
    p_fin.add_argument("--hdf5_key", default="history")
    p_fin.add_argument("--keep_chunks", action="store_true",
                       help="Keep the intermediate chunk dirs instead of deleting them.")
    p_fin.set_defaults(func=cmd_finalize)

    p_sub = sub.add_parser("submit", help="Generate + submit the chained chunked SLURM jobs.")
    p_sub.add_argument("--parent_dir", default=None, help="Grid dir (default: current dir).")
    _add_grid_args(p_sub)
    p_sub.add_argument("--chunk_size", type=int, default=DEFAULT_CHUNK_SIZE,
                       help=f"Tasks per batch (default: {DEFAULT_CHUNK_SIZE}).")
    p_sub.add_argument("--constants", nargs="*", default=DEFAULT_CONSTANTS)
    p_sub.add_argument("--python", default=sys.executable)
    p_sub.add_argument("--conda_env", default="py311")
    p_sub.add_argument("--array_time", default="23:59:59")
    p_sub.add_argument("--array_mem", default="8G")
    p_sub.add_argument("--array_partition", default="day")
    p_sub.add_argument("--array_mail_type", default="ALL")
    p_sub.add_argument("--step_time", default="2:00:00")
    p_sub.add_argument("--step_mem", default="16G")
    p_sub.add_argument("--step_partition", default="day")
    p_sub.add_argument("--step_mail_type", default="ALL")
    p_sub.add_argument("--finalize_time", default="8:00:00")
    p_sub.add_argument("--finalize_mem", default="32G")
    p_sub.add_argument("--max_cpus", type=int, default=None,
                       help="Throttle each chunk's array to at most this many concurrent tasks.")
    p_sub.add_argument("--dry_run", action="store_true")
    p_sub.set_defaults(func=cmd_submit)

    p_adv = sub.add_parser("advance", help="Internal: compress the finished chunk, submit the next.")
    p_adv.add_argument("--queue_file", required=True)
    p_adv.set_defaults(func=cmd_advance)

    return parser


if __name__ == "__main__":
    _args = _build_parser().parse_args()
    _args.func(_args)
