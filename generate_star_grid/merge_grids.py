"""
Merge per-batch combined_history.hdf5 files from a completed submit_grid run
into a single combined_history.hdf5 spanning the full parameter space (e.g.
the full M×Z grid).

Each batch's Track values are offset so that all tracks are globally unique
across the merged file: batch N's tracks start at max(batch N-1 tracks) + 1.

The merged directory is created in parent_dir (sibling to the batch dirs),
named by replacing per-batch outer-parameter values with 'var<Label>' tokens
in canonical parameter order — e.g. mytemplate_varM_varZ for a grid that
swept mass (inner) and metallicity (outer). All batch directories are then
moved inside the merged directory.

Usage (standalone):
    # Auto-discover batch dirs and config from queue.json:
    python -m generate_star_grid.merge_grids --queue_file /path/to/queue.json

    # Explicit batch dirs (output_dir required):
    python -m generate_star_grid.merge_grids \\
        --batch_dirs /path/to/batch1 /path/to/batch2 \\
        --output_dir /path/to/merged_dir

    # Preview without writing:
    python -m generate_star_grid.merge_grids --queue_file /path/to/queue.json --dry_run
"""
import argparse
import json
import re
import shutil
from pathlib import Path

import pandas as pd

from .grid_utils import PARAM_FORMAT


def _merged_dir_name(config: dict) -> str:
    """
    Derive the merged directory name from queue config.

    Strips the _var<Param> suffix (if any) from source_dir's base name, then
    appends var<Label> for every varying parameter (both inner and outer) in
    canonical PARAM_FORMAT order, followed by any non-built-in extra keys.

    Example: source_dir='mytemplate_varM', outer=Z, inner=M
             -> 'mytemplate_varM_varZ'
    """
    source_dir = Path(config["source_dir"])
    registry = config["registry"]

    base = re.sub(r"_var[A-Za-z]*$", "", source_dir.name, flags=re.IGNORECASE)

    all_varying_keys = (
        set(config.get("inner_keys", [])) | set(config.get("outer_formats", {}).keys())
    )

    canonical_order = list(PARAM_FORMAT.keys())
    var_parts = []
    for key in canonical_order:
        if key in all_varying_keys and key in registry:
            var_parts.append(f"var{registry[key]['label']}")
    for key in sorted(all_varying_keys):
        if key not in canonical_order and key in registry:
            var_parts.append(f"var{registry[key]['label']}")

    return f"{base}_{'_'.join(var_parts)}"


def _merged_dir_name_v2(config: dict) -> str:
    """
    Like _merged_dir_name but strips ALL trailing _var<Param> suffixes from
    source_dir's name (not just the last one), so source dirs that already
    carry one or more _var* tokens are handled correctly.
    """
    source_dir = Path(config["source_dir"])
    registry = config["registry"]

    base = re.sub(r"(_var[A-Za-z]+)+$", "", source_dir.name, flags=re.IGNORECASE)

    all_varying_keys = (
        set(config.get("inner_keys", [])) | set(config.get("outer_formats", {}).keys())
    )

    canonical_order = list(PARAM_FORMAT.keys())
    var_parts = []
    for key in canonical_order:
        if key in all_varying_keys and key in registry:
            var_parts.append(f"var{registry[key]['label']}")
    for key in sorted(all_varying_keys):
        if key not in canonical_order and key in registry:
            var_parts.append(f"var{registry[key]['label']}")

    return f"{base}_{'_'.join(var_parts)}"


def _find_batch_dirs_v2(config: dict) -> list:
    """
    Like _find_batch_dirs but uses _merged_dir_name_v2 (multi-suffix regex)
    to correctly exclude the merged dir when the source dir name already
    contains _var* tokens.
    """
    parent_dir = Path(config["parent_dir"])
    source_dir = Path(config["source_dir"])
    base = re.sub(r"(_var[A-Za-z]+)+$", "", source_dir.name, flags=re.IGNORECASE)
    merged_name = _merged_dir_name_v2(config)

    return sorted(
        d for d in parent_dir.iterdir()
        if d.is_dir()
        and d.name.startswith(base)
        and d.name != merged_name
        and (d / "combined_history.hdf5").exists()
    )


def _find_batch_dirs(config: dict) -> list:
    """
    Discover batch directories in parent_dir that have a combined_history.hdf5.

    Excludes the merged directory itself (identified by _merged_dir_name) so
    that re-running merge after a partial run doesn't pick up the merged dir.
    """
    parent_dir = Path(config["parent_dir"])
    source_dir = Path(config["source_dir"])
    base = re.sub(r"_var[A-Za-z]*$", "", source_dir.name, flags=re.IGNORECASE)
    merged_name = _merged_dir_name(config)

    return sorted(
        d for d in parent_dir.iterdir()
        if d.is_dir()
        and d.name.startswith(base)
        and d.name != merged_name
        and (d / "combined_history.hdf5").exists()
    )


def merge_batch_hdf5(
    batch_dirs: list,
    output_path: Path,
    hdf5_key: str = "history",
    chunksize: int = 100_000,
) -> int:
    """
    Read each batch's combined_history.hdf5, offset Track values for global
    uniqueness, and write a single merged HDF5 to output_path.

    Track offset for each batch = max(Track in previous batch) + 1, so tracks
    are contiguous across the full merged file.

    Args:
        batch_dirs: Ordered list of batch directories, each containing combined_history.hdf5.
        output_path: Destination path for the merged HDF5.
        hdf5_key: HDF5 store key (must match what make_grid.py used; default 'history').
        chunksize: Rows read per chunk to bound memory use.

    Returns:
        Total number of rows written.
    """
    output_path = Path(output_path)
    if output_path.exists():
        output_path.unlink()

    total_rows = 0
    track_offset = 0
    n_batches = len(batch_dirs)
    canonical_cols = None

    with pd.HDFStore(str(output_path), mode="w", complevel=5, complib="blosc") as out_store:
        for i, batch_dir in enumerate(batch_dirs):
            hdf5_path = batch_dir / "combined_history.hdf5"
            print(f"[{i + 1}/{n_batches}] Reading {hdf5_path} (track offset={track_offset}) ...")
            with pd.HDFStore(str(hdf5_path), mode="r") as in_store:
                nrows = in_store.get_storer(hdf5_key).nrows
                batch_min_track = None
                batch_max_track = None

                for start in range(0, nrows, chunksize):
                    chunk = in_store.select(hdf5_key, start=start, stop=start + chunksize)
                    chunk = chunk.copy()
                    # Record original min/max before offsetting to compute track count below.
                    chunk_min = int(chunk["Track"].min())
                    chunk_max = int(chunk["Track"].max())
                    if batch_min_track is None or chunk_min < batch_min_track:
                        batch_min_track = chunk_min
                    if batch_max_track is None or chunk_max > batch_max_track:
                        batch_max_track = chunk_max
                    chunk["Track"] = chunk["Track"] + track_offset

                    # Batches can have identical column sets but different physical
                    # column order (e.g. 'Track' before 'Z' in some, after in others),
                    # which breaks PyTables' block-matching on append. Pin every
                    # batch to the first batch's column order rather than relying
                    # on source files already agreeing.
                    if canonical_cols is None:
                        canonical_cols = list(chunk.columns)
                    elif set(chunk.columns) != set(canonical_cols):
                        raise ValueError(
                            f"{hdf5_path} has columns {sorted(chunk.columns)} "
                            f"which do not match the first batch's columns "
                            f"{sorted(canonical_cols)}. Reconcile schemas before merging."
                        )
                    else:
                        chunk = chunk[canonical_cols]

                    out_store.append(hdf5_key, chunk, format="table")
                    total_rows += len(chunk)

            if batch_max_track is not None and batch_min_track is not None:
                # Advance offset by the number of unique tracks in this batch so that
                # the next batch's tracks follow on sequentially from this one's.
                # e.g. if this batch had tracks [2,3,4], the next batch's tracks
                # [2,3,4] become [5,6,7] — not [7,8,9] as max+1 would give.
                track_offset += batch_max_track - batch_min_track + 1
            print(f"  Appended {nrows} rows. Next track offset: {track_offset}")

    return total_rows


def _hdf5_nrows(hdf5_path: Path, hdf5_key: str) -> int:
    """Return the row count of hdf5_key in an HDF5 store (0 if key absent)."""
    with pd.HDFStore(str(hdf5_path), mode="r") as store:
        storer = store.get_storer(hdf5_key)
        return int(storer.nrows) if storer is not None else 0


def _extract_var_labels(dir_name: str) -> list:
    """Return the _var<Label> labels present in a directory name, in order."""
    return re.findall(r"_var([A-Za-z]+)", dir_name, flags=re.IGNORECASE)


def _expanded_dir_name(base_dir: Path, new_config: dict) -> str:
    """
    Derive the name for the new expanded merged directory.

    Combines the _var<Label> labels already in base_dir's name with those
    from the new expand queue config, deduplicates, and sorts in canonical
    PARAM_FORMAT order.
    """
    registry = new_config["registry"]
    base = re.sub(r"(_var[A-Za-z]+)+$", "", base_dir.name, flags=re.IGNORECASE)

    existing_labels = set(_extract_var_labels(base_dir.name))

    new_varying_keys = (
        set(new_config.get("inner_keys", [])) | set(new_config.get("outer_formats", {}).keys())
    )
    new_labels = {registry[k]["label"] for k in new_varying_keys if k in registry}

    all_labels = existing_labels | new_labels

    # Sort by canonical PARAM_FORMAT order; unknowns go last alphabetically
    canonical_order = [PARAM_FORMAT[k]["label"] for k in PARAM_FORMAT]
    def label_order(lbl):
        try:
            return canonical_order.index(lbl)
        except ValueError:
            return len(canonical_order)

    sorted_labels = sorted(all_labels, key=label_order)
    var_parts = "_".join(f"var{lbl}" for lbl in sorted_labels)
    return f"{base}_{var_parts}"


def cmd_expand(args) -> None:
    """
    Incrementally expand an existing merged grid with new batch HDF5s.

    Reads the existing merged HDF5 from base_dir as the first input, then
    appends new batch HDF5s (discovered via the expand queue file) with Track
    values offset after the base. Creates a new expanded merged directory,
    moves base_dir and new batch dirs inside it.
    """
    queue_file = Path(args.queue_file).resolve()
    state = json.loads(queue_file.read_text())
    config = state["config"]

    base_dir = Path(args.base_dir).resolve()
    if not base_dir.is_dir():
        raise ValueError(f"--base_dir {base_dir} does not exist.")
    if not (base_dir / "combined_history.hdf5").exists():
        raise ValueError(f"{base_dir} has no combined_history.hdf5.")

    parent_dir = Path(config["parent_dir"])

    # Discover new batch dirs: siblings in parent_dir with combined_history.hdf5,
    # excluding base_dir itself and any existing merged dirs (_var* in name).
    new_batch_dirs = sorted(
        d for d in parent_dir.iterdir()
        if d.is_dir()
        and d != base_dir
        and not re.search(r"_var[A-Za-z]+", d.name, re.IGNORECASE)
        and (d / "combined_history.hdf5").exists()
    )

    if not new_batch_dirs:
        print("No new batch directories with combined_history.hdf5 found. Nothing to expand.")
        return

    expanded_name = _expanded_dir_name(base_dir, config)
    expanded_dir = parent_dir / expanded_name

    print(f"Base merged dir:  {base_dir}")
    print(f"New batch dirs ({len(new_batch_dirs)}):")
    for d in new_batch_dirs:
        print(f"  {d.name}")
    print(f"Expanded merged dir: {expanded_dir}")

    if args.dry_run:
        print("\n--dry_run: no files written or moved.")
        return

    expanded_dir.mkdir(parents=True, exist_ok=True)

    # Merge: base_dir first (tracks unchanged), then new batches (tracks offset)
    all_inputs = [base_dir] + list(new_batch_dirs)
    output_path = expanded_dir / args.hdf5_filename
    total_rows = merge_batch_hdf5(all_inputs, output_path, hdf5_key=args.hdf5_key)
    print(f"Expanded HDF5 written to {output_path} ({total_rows} rows total).")

    # Move base_dir and new batch dirs inside expanded_dir
    print("Moving directories into expanded merged directory...")
    for d in all_inputs:
        dest = expanded_dir / d.name
        shutil.move(str(d), str(dest))
        print(f"  Moved {d.name} -> {expanded_name}/{d.name}")

    print(f"\nDone. Expanded grid at {expanded_dir}")


def cmd_merge(args) -> None:
    config = None
    if args.queue_file:
        queue_file = Path(args.queue_file).resolve()
        state = json.loads(queue_file.read_text())
        config = state["config"]
        batch_dirs = _find_batch_dirs(config)
        merged_dir = (
            Path(args.output_dir).resolve()
            if args.output_dir
            else Path(config["parent_dir"]) / _merged_dir_name(config)
        )
    else:
        if not args.output_dir:
            raise ValueError("--output_dir is required when using --batch_dirs (no queue_file to derive name from).")
        batch_dirs = [Path(d).resolve() for d in args.batch_dirs]
        merged_dir = Path(args.output_dir).resolve()

    if not batch_dirs:
        print("No batch directories with combined_history.hdf5 found. Nothing to merge.")
        return

    print(f"Merging {len(batch_dirs)} batch(es) into: {merged_dir}")
    for d in batch_dirs:
        print(f"  {d}")

    if args.dry_run:
        print("\n--dry_run: no files written or moved.")
        return

    merged_dir.mkdir(parents=True, exist_ok=True)

    output_path = merged_dir / args.hdf5_filename
    total_rows = merge_batch_hdf5(batch_dirs, output_path, hdf5_key=args.hdf5_key)
    print(f"Merged HDF5 written to {output_path} ({total_rows} rows total).")

    print("Moving batch directories into merged directory...")
    for d in batch_dirs:
        dest = merged_dir / d.name
        shutil.move(str(d), str(dest))
        print(f"  Moved {d.name} -> {merged_dir.name}/{d.name}")

    print(f"\nDone. Merged grid at {merged_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ----- merge -----
    p_merge = sub.add_parser("merge", help="Merge per-batch HDF5s into one merged grid.")
    source = p_merge.add_mutually_exclusive_group(required=True)
    source.add_argument("--queue_file", help="Path to submit_grid queue.json.")
    source.add_argument("--batch_dirs", nargs="+", metavar="DIR")
    p_merge.add_argument("--output_dir")
    p_merge.add_argument("--hdf5_filename", default="combined_history.hdf5")
    p_merge.add_argument("--hdf5_key", default="history")
    p_merge.add_argument("--dry_run", action="store_true")
    p_merge.set_defaults(func=cmd_merge)

    # ----- expand -----
    p_expand = sub.add_parser("expand", help="Expand an existing merged grid with new batches.")
    p_expand.add_argument("--base_dir", required=True, help="Existing merged grid directory.")
    p_expand.add_argument("--queue_file", required=True, help="Expand queue file from submit_grid expand.")
    p_expand.add_argument("--hdf5_filename", default="combined_history.hdf5")
    p_expand.add_argument("--hdf5_key", default="history")
    p_expand.add_argument("--dry_run", action="store_true")
    p_expand.set_defaults(func=cmd_expand)

    parsed = parser.parse_args()
    parsed.func(parsed)
