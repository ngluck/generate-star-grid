"""
Generalized SLURM orchestration for MESA grids that are too large to keep on
disk all at once.

Splits a grid into an "outer" sweep (any parameter(s), e.g. initial_z) and an
"inner" sweep (any other parameter(s), e.g. initial_mass). Each outer
combination becomes one sequential batch: a directory is created, a SLURM
array job sweeps the inner parameters within it, a combine job builds that
batch's combined_history.hdf5, retries any failed tasks once, logs any still-
failing stars (initial conditions + array index) to notes.txt, deletes the
batch's run artifacts, and only then triggers the next outer batch -- so peak
disk usage is bounded by a single batch's footprint, not the whole grid's.

Both --outer and --inner accept KEY=SPEC the same way grid_utils.py's --param
does: KEY is either a built-in alias (mass/initial_mass, y/initial_y,
z/initial_z, alpha/mixing_length_alpha) or any other parameter settable in
inlist_template; SPEC is VALUE, V1,V2,..., MIN:MAX, or MIN:MAX:STEP (see
VALUE_SPEC_HELP in grid_utils.py).

Usage:
    python -m generate_star_grid.submit_grid start \\
        --source_dir /path/to/clean/template_dir \\
        --queue_file /path/to/queue.json \\
        --outer initial_z=0.001,0.0015,...,0.04 \\
        --inner initial_mass=0.7:1.2 --grid_type linear --num_points 500

    # Called automatically by the generated combine/cleanup script once a
    # batch's real work (HDF5 + cleanup) is done; not normally run by hand.
    python -m generate_star_grid.submit_grid next --queue_file /path/to/queue.json

    # Standalone utility (also used internally by the generated combine script):
    python -m generate_star_grid.submit_grid check-failed --dest /path/to/batch_dir --keys M,Y,Z,alpha
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
    compute_param_formats,
    find_failed_tasks,
    generate_grid,
    list_inlist_params,
    make_run_dir_name,
    parse_param_value,
    resolve_param_key,
)

_BUILTIN_ALIASES = {
    "mass": "initial_mass", "m": "initial_mass", "initial_mass": "initial_mass",
    "y": "initial_y", "initial_y": "initial_y",
    "z": "initial_z", "initial_z": "initial_z",
    "alpha": "mixing_length_alpha", "alpha_mlt": "mixing_length_alpha",
    "mixing_length_alpha": "mixing_length_alpha",
}
_BUILTIN_CLI_FLAG = {
    "initial_mass": "--mass",
    "initial_y": "--initial_Y",
    "initial_z": "--initial_Z",
    "mixing_length_alpha": "--alpha_MLT",
}


def _resolve_key(key_str: str, inlist_text: str) -> tuple:
    """Returns (internal_key, registry_entry_or_None) for a KEY=SPEC's KEY."""
    alias = _BUILTIN_ALIASES.get(key_str.strip().lower())
    if alias:
        return alias, None
    inlist_key = resolve_param_key(key_str.strip(), inlist_text)
    registry_entry = {"label": re.sub(r"[()]", "", inlist_key), "fmt": ".6f", "inlist_key": inlist_key}
    return inlist_key, registry_entry


def _parse_named_params(specs: list, inlist_text: str) -> tuple:
    """
    Parses a list of 'KEY=SPEC' strings into (param_specs, registry_extra).

    param_specs maps internal key -> fixed float, (min, max) tuple, or list
    of values (see parse_param_value). registry_extra maps internal key ->
    a PARAM_FORMAT-style entry, for any key that wasn't one of the 4 built-ins.
    """
    param_specs = {}
    registry_extra = {}
    for item in specs:
        if "=" not in item:
            raise ValueError(f"Expected KEY=SPEC, got '{item}'")
        key_str, spec_str = item.split("=", 1)
        internal_key, registry_entry = _resolve_key(key_str, inlist_text)
        param_specs[internal_key] = parse_param_value(spec_str.strip())
        if registry_entry:
            registry_extra[internal_key] = registry_entry
    return param_specs, registry_extra


def _cli_args_for_fixed_value(internal_key: str, value: float, fmt: str) -> list:
    """CLI args to fix one outer parameter to a single value for grid_utils.py."""
    value_str = f"{value:{fmt}}"
    if internal_key in _BUILTIN_CLI_FLAG:
        return [_BUILTIN_CLI_FLAG[internal_key], value_str]
    return ["--param", f"{internal_key}={value_str}"]


def _label_for_key(internal_key: str, registry: dict) -> str:
    return registry[internal_key]["label"]


def _trim_trailing_zeros(value: float, fmt: str) -> str:
    """
    Format value with fmt, then strip cosmetic trailing zeros (and a bare
    trailing '.'). Batch directory names replace '.' with 'p' right after
    this, so 'p' should read as a clean decimal point -- not be followed by
    zero-padding left over from a fixed-width format chosen to fit other
    values in the same outer sweep.
    """
    s = f"{value:{fmt}}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
        if not s or s == "-":
            s = "0"
    return s


def _dest_name_for_batch(source_dir: Path, batch: dict, formats: dict, registry: dict) -> str:
    base = re.sub(r"_var[A-Za-z]*$", "", source_dir.name, flags=re.IGNORECASE)
    parts = []
    for key, value in batch.items():
        fmt = formats.get(key, registry[key]["fmt"])
        parts.append(f"{registry[key]['label']}_{_trim_trailing_zeros(value, fmt)}")
    label = "_".join(parts)
    return f"{base}_{label.replace('.', 'p')}"


def cmd_start(args):
    source_dir = Path(args.source_dir).resolve()
    inlist_text = (source_dir / "inlist_template").read_text()

    outer_specs, outer_extra = _parse_named_params(args.outer, inlist_text)
    inner_specs, inner_extra = _parse_named_params(args.inner, inlist_text)
    registry = dict(PARAM_FORMAT)
    registry.update(outer_extra)
    registry.update(inner_extra)

    outer_formats = compute_param_formats(
        outer_specs, grid_type=args.outer_grid_type, num_points=args.outer_num_points, param_registry=registry
    )
    outer_batches = generate_grid(outer_specs, grid_type=args.outer_grid_type, num_points=args.outer_num_points)

    # Precompute the inner CLI args once -- identical for every outer batch.
    inner_cli_args = []
    for item in args.inner:
        key_str, spec_str = item.split("=", 1)
        internal_key, _ = _resolve_key(key_str, inlist_text)
        if internal_key in _BUILTIN_CLI_FLAG:
            inner_cli_args += [_BUILTIN_CLI_FLAG[internal_key], spec_str.strip()]
        else:
            inner_cli_args += ["--param", f"{internal_key}={spec_str.strip()}"]
    inner_cli_args += ["--grid_type", args.grid_type, "--num_points", str(args.num_points)]

    inner_keys = [_resolve_key(item.split("=", 1)[0], inlist_text)[0] for item in args.inner]
    inner_count = len(generate_grid(inner_specs, grid_type=args.grid_type, num_points=args.num_points))

    print(f"Outer batches: {len(outer_batches)}")
    print(f"Inner models per batch: {inner_count}")
    print(f"Total models: {len(outer_batches) * inner_count}")
    for b in outer_batches[:3]:
        print(f"  batch dir: {_dest_name_for_batch(source_dir, b, outer_formats, registry)}/  (array 0-{inner_count - 1})")
    if len(outer_batches) > 3:
        print(f"  ... ({len(outer_batches) - 3} more)")

    if args.dry_run:
        print("\n--dry_run: queue file not written, no jobs submitted.")
        return

    config = {
        "source_dir": str(source_dir),
        "parent_dir": str(Path(args.parent_dir).resolve()) if args.parent_dir else str(source_dir.parent),
        "registry": registry,
        "outer_formats": outer_formats,
        "inner_cli_args": inner_cli_args,
        "inner_keys": inner_keys,
        "python": args.python,
        "conda_env": args.conda_env,
        "array_time": args.array_time,
        "array_mem": args.array_mem,
        "array_partition": args.array_partition,
        "array_mail_type": args.array_mail_type,
        "combine_time": args.combine_time,
        "combine_mem": args.combine_mem,
        "combine_partition": args.combine_partition,
        "combine_mail_type": args.combine_mail_type,
        "retry_once": not args.no_retry,
        "fail_threshold_mb": args.fail_threshold_mb,
    }
    queue_file = Path(args.queue_file).resolve()
    queue_file.write_text(json.dumps({"config": config, "remaining_batches": outer_batches}, indent=2))
    print(f"\nQueue written to {queue_file}.")

    cmd_next(argparse.Namespace(queue_file=str(queue_file)))


def _write_and_submit_batch(queue_file: Path, config: dict, batch: dict) -> None:
    source_dir = Path(config["source_dir"])
    parent_dir = Path(config["parent_dir"])
    registry = config["registry"]
    python = config["python"]

    dest = parent_dir / _dest_name_for_batch(source_dir, batch, config["outer_formats"], registry)
    print(f"=== Preparing {dest} ===")

    subprocess.run(
        ["rsync", "-a",
         "--exclude=M_*", "--exclude=*.hdf5", "--exclude=notes.txt", "--exclude=slurm_*.out",
         f"{source_dir}/", f"{dest}/"],
        check=True,
    )
    dest.mkdir(parents=True, exist_ok=True)
    for pattern in ("M_*",):
        for p in dest.glob(pattern):
            shutil.rmtree(p, ignore_errors=True)
    for p in (dest / "grid_TAMS").glob("*.mod"):
        p.unlink(missing_ok=True)
    for p in (dest / "grid_inlists").glob("inlist_*"):
        p.unlink(missing_ok=True)
    logs_dir = dest / "LOGS"
    if logs_dir.is_dir():
        for p in logs_dir.iterdir():
            if p.is_file():
                p.unlink()
    for p in dest.glob("*.hdf5"):
        p.unlink(missing_ok=True)
    for p in dest.glob("slurm_*.out"):
        p.unlink(missing_ok=True)

    fixed_args = []
    for key, value in batch.items():
        fmt = config["outer_formats"].get(key, registry[key]["fmt"])
        fixed_args += _cli_args_for_fixed_value(key, value, fmt)

    batch_label = make_run_dir_name(batch, config["outer_formats"], registry)
    job_label = re.sub(r"[^A-Za-z0-9]", "", batch_label)[:30]

    # Recompute inner model count from the persisted CLI args by re-running
    # the same parse grid_utils.py would do at array-job runtime.
    inner_count = _inner_model_count(config)

    python_inv = " \\\n    ".join([f'"{python}" -m generate_star_grid.grid_utils'] + fixed_args + config["inner_cli_args"])

    run_array = dest / "run_array.sh"
    run_array.write_text(f"""#!/bin/bash
#SBATCH --job-name=mesa_{job_label}
#SBATCH --array=0-{inner_count - 1}
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition={config['array_partition']}
#SBATCH --nodes=1
#SBATCH --time={config['array_time']}
#SBATCH --mem={config['array_mem']}
#SBATCH --mail-type={config['array_mail_type']}
#SBATCH --output={dest}/slurm_%A_%a.out

cd "{dest}" || {{ echo "FATAL: cannot cd to {dest}" >&2; exit 1; }}

module purge
module load miniconda
conda activate {config['conda_env']}

export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

{python_inv} \\
    --task_id=$SLURM_ARRAY_TASK_ID
""")
    run_array.chmod(0o755)

    inner_keys_csv = ",".join(_label_for_key(k, registry) for k in config["inner_keys"])
    # --constants must also include the outer keys fixed for this batch (e.g. Z) --
    # they're embedded in every per-star subdir name alongside the inner keys, but
    # aren't varying *within* this array, so they're absent from inner_keys.
    constants_keys_csv = ",".join(
        _label_for_key(k, registry) for k in list(config["inner_keys"]) + list(batch.keys())
    )
    run_combine = dest / "run_combine_cleanup.sh"
    run_combine.write_text(f"""#!/bin/bash
#SBATCH --job-name=combine_{job_label}
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition={config['combine_partition']}
#SBATCH --nodes=1
#SBATCH --time={config['combine_time']}
#SBATCH --mem={config['combine_mem']}
#SBATCH --mail-type={config['combine_mail_type']}
#SBATCH --output={dest}/combine_%j.out

DEST="{dest}"
RETRY_DONE="${{RETRY_DONE:-0}}"

cd "$DEST" || {{ echo "FATAL: cannot cd to $DEST" >&2; exit 1; }}

module purge
module load miniconda
conda activate {config['conda_env']}

echo "Checking for failed tasks (retry_done=$RETRY_DONE)..."
FAILED=$("{python}" -m generate_star_grid.submit_grid check-failed --dest "$DEST" --keys {inner_keys_csv} --threshold_mb {config['fail_threshold_mb']})

if [ -n "$FAILED" ] && [ "$RETRY_DONE" -eq 0 ] && [ "{1 if config['retry_once'] else 0}" -eq 1 ]; then
    echo "Failed tasks detected:"
    echo "$FAILED"

    echo "Clearing DATA/ for failed runs before retry..."
    echo "$FAILED" | while IFS='|' read -r tid folder params; do
        rm -rf "$DEST/$folder/DATA"
        mkdir -p "$DEST/$folder/DATA"
    done

    FAILED_IDS=$(echo "$FAILED" | cut -d'|' -f1 | paste -sd, -)
    echo "Retrying failed tasks once: $FAILED_IDS"
    RETRY_JOB=$(sbatch --parsable --array=$FAILED_IDS "$DEST/run_array.sh")
    sbatch --dependency=afterany:$RETRY_JOB --export=ALL,RETRY_DONE=1 "$DEST/run_combine_cleanup.sh"
    echo "Handed off to retry chain (array job $RETRY_JOB); exiting without finalizing."
    exit 0
fi

if [ -n "$FAILED" ]; then
    N=$(echo "$FAILED" | wc -l)
    echo "WARNING: $N task(s) still failed. Excluding from HDF5; logging to notes.txt."
fi

echo "Building combined_history.hdf5..."
"{python}" -m generate_star_grid.make_grid \\
    --parent_dir "$DEST" \\
    --constants {constants_keys_csv} \\
    --save

if [ -n "$FAILED" ]; then
    {{
        echo ""
        echo "Failed stars (excluded from combined_history.hdf5):"
        echo "$FAILED" | while IFS='|' read -r tid folder params; do
            echo "  TASK_$tid ($folder): $params"
        done
    }} >> "$DEST/notes.txt"
fi

if [ -n "$SEISTRON_BASE_DIR" ]; then
    echo "Plotting HR diagram..."
    PYTHONPATH="$SEISTRON_BASE_DIR:$PYTHONPATH" "{python}" -m my_library.grid_builders.plot_grid_hr_diagram \\
        --combined_history "$DEST/combined_history.hdf5" || echo "WARNING: HR diagram plotting failed; continuing."
else
    echo "SEISTRON_BASE_DIR not set; skipping optional HR diagram plot."
fi

echo "Deleting run directories and artifacts..."
rm -rf "$DEST"/M_*/
rm -f  "$DEST"/grid_TAMS/TAMS_*.mod
rm -f  "$DEST"/grid_inlists/inlist_*
find   "$DEST/LOGS" -type f -delete
rm -f  "$DEST"/slurm_*.out

echo "Done. HDF5 saved at $DEST/combined_history.hdf5"

echo "Triggering next batch in queue..."
"{python}" -m generate_star_grid.submit_grid next --queue_file "{queue_file}"
""")
    run_combine.chmod(0o755)

    array_job = subprocess.run(
        ["sbatch", "--parsable", str(run_array)], check=True, capture_output=True, text=True
    ).stdout.strip()
    print(f"  Submitted array job {array_job}")

    combine_job = subprocess.run(
        ["sbatch", "--parsable", "--dependency", f"afterany:{array_job}", "--export", "ALL,RETRY_DONE=0", str(run_combine)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    print(f"  Submitted combine/cleanup job {combine_job} (afterany:{array_job})")


def _inner_model_count(config: dict) -> int:
    """Re-derives the inner model count from the persisted inner CLI args."""
    args = config["inner_cli_args"]
    specs = {}
    grid_type = "linear"
    num_points = 8
    i = 0
    flag_to_key = {v: k for k, v in _BUILTIN_CLI_FLAG.items()}
    while i < len(args):
        flag, val = args[i], args[i + 1]
        if flag == "--grid_type":
            grid_type = val
        elif flag == "--num_points":
            num_points = int(val)
        elif flag in flag_to_key:
            specs[flag_to_key[flag]] = parse_param_value(val)
        elif flag == "--param":
            key, spec_str = val.split("=", 1)
            specs[key] = parse_param_value(spec_str)
        i += 2
    return len(generate_grid(specs, grid_type=grid_type, num_points=num_points))


def cmd_next(args):
    queue_file = Path(args.queue_file).resolve()
    state = json.loads(queue_file.read_text())
    if not state["remaining_batches"]:
        print("Queue empty. All outer batches have been processed.")
        return
    batch = state["remaining_batches"].pop(0)
    queue_file.write_text(json.dumps(state, indent=2))
    _write_and_submit_batch(queue_file, state["config"], batch)


def cmd_check_failed(args):
    keys = args.keys.split(",")
    failed = find_failed_tasks(args.dest, keys, threshold_mb=args.threshold_mb)
    for f in failed:
        param_str = ",".join(f"{k}={v}" for k, v in f["params"].items())
        print(f"{f['task_id']}|{f['folder']}|{param_str}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Build the outer-batch queue and submit the first batch.")
    p_start.add_argument("--source_dir", required=True, help="Clean template grid directory to copy per batch.")
    p_start.add_argument("--parent_dir", default=None, help="Where batch directories are created (default: source_dir's parent).")
    p_start.add_argument("--queue_file", required=True)
    p_start.add_argument("--outer", action="append", required=True, metavar="KEY=SPEC",
                          help="Repeatable. Parameter(s) processed sequentially, one disk-bounded batch per value/combination.")
    p_start.add_argument("--inner", action="append", required=True, metavar="KEY=SPEC",
                          help="Repeatable. Parameter(s) swept within each batch's SLURM array.")
    p_start.add_argument("--grid_type", choices=["linear", "sobol"], default="linear", help="For inner continuous ranges.")
    p_start.add_argument("--num_points", type=int, default=8, help="For inner continuous ranges.")
    p_start.add_argument("--outer_grid_type", choices=["linear", "sobol"], default="linear")
    p_start.add_argument("--outer_num_points", type=int, default=8)
    p_start.add_argument("--python", default=sys.executable)
    p_start.add_argument("--conda_env", default="py311")
    p_start.add_argument("--array_time", default="6-00:00:00")
    p_start.add_argument("--array_mem", default="8G")
    p_start.add_argument("--array_partition", default="week")
    p_start.add_argument("--array_mail_type", default="ALL")
    p_start.add_argument("--combine_time", default="2:00:00")
    p_start.add_argument("--combine_mem", default="16G")
    p_start.add_argument("--combine_partition", default="day")
    p_start.add_argument("--combine_mail_type", default="ALL")
    p_start.add_argument("--no_retry", action="store_true", help="Disable the retry-once-on-failure behavior.")
    p_start.add_argument("--fail_threshold_mb", type=float, default=13.0)
    p_start.add_argument("--dry_run", action="store_true", help="Preview batch count/names; write nothing, submit nothing.")
    p_start.set_defaults(func=cmd_start)

    p_next = sub.add_parser("next", help="Pop and submit the next batch in the queue (called automatically).")
    p_next.add_argument("--queue_file", required=True)
    p_next.set_defaults(func=cmd_next)

    p_check = sub.add_parser("check-failed", help="Print failed array tasks for a batch directory.")
    p_check.add_argument("--dest", required=True)
    p_check.add_argument("--keys", required=True, help="Comma-separated parameter labels to report, e.g. M,Y,Z,alpha")
    p_check.add_argument("--threshold_mb", type=float, default=13.0)
    p_check.set_defaults(func=cmd_check_failed)

    parsed = parser.parse_args()
    parsed.func(parsed)
