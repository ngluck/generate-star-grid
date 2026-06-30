"""
Patch existing run_array.sh and run_combine_cleanup.sh scripts in batch
directories to enable photo-restart for timed-out SLURM jobs.

Intended for grids that were already submitted *before* --restart_photos
support was added.  Run once on your parent directory (or queue file) before
jobs time out -- or any time before the combine/retry job fires:

    # patch every batch dir under a parent directory:
    python -m generate_star_grid.patch_batch_for_restart --parent_dir /path/to/parent

    # patch dirs recorded in a submit_grid queue file:
    python -m generate_star_grid.patch_batch_for_restart --queue_file /path/to/queue.json

    # patch specific dirs explicitly:
    python -m generate_star_grid.patch_batch_for_restart /path/to/batch_dir_Z_0p001 ...

What it changes in each batch directory:
  run_array.sh           -- adds --restart_photos to the grid_utils invocation
  run_combine_cleanup.sh -- replaces the DATA/ wipe before retry with a no-op
                            so photos/ is preserved for the photo restart
"""
import argparse
import json
import re
from pathlib import Path

_CLEAR_BLOCK_RE = re.compile(
    r'    echo "Clearing DATA/ for failed runs before retry\.\.\.".*?'
    r'    done\n',
    re.DOTALL,
)
_CLEAR_BLOCK_REPLACEMENT = '    echo "Preserving DATA/ and photos/ for photo restart."\n'


def patch_run_array(path: Path, dry_run: bool = False) -> bool:
    """
    Add --restart_photos to the grid_utils invocation in run_array.sh.

    Returns True if a change was (or would be) made.
    """
    text = path.read_text()
    if "--restart_photos" in text:
        print(f"  run_array.sh: already patched, skipping.")
        return False
    if "--task_id=$SLURM_ARRAY_TASK_ID" not in text:
        print(f"  run_array.sh: WARNING: --task_id line not found; skipping.")
        return False
    new_text = text.replace(
        "    --task_id=$SLURM_ARRAY_TASK_ID",
        "    --restart_photos \\\n    --task_id=$SLURM_ARRAY_TASK_ID",
    )
    if dry_run:
        print(f"  run_array.sh: would add --restart_photos (dry run).")
    else:
        path.write_text(new_text)
        print(f"  run_array.sh: patched (added --restart_photos).")
    return True


def patch_run_combine(path: Path, dry_run: bool = False) -> bool:
    """
    Replace the DATA/ wipe block in run_combine_cleanup.sh with a no-op so
    that photos/ and partial history.data are preserved before the retry.

    Returns True if a change was (or would be) made.
    """
    text = path.read_text()
    if "Preserving DATA/ and photos/" in text:
        print(f"  run_combine_cleanup.sh: already patched, skipping.")
        return False
    if "Clearing DATA/ for failed runs" not in text:
        print(f"  run_combine_cleanup.sh: WARNING: clear block not found; skipping.")
        return False
    new_text, n = _CLEAR_BLOCK_RE.subn(_CLEAR_BLOCK_REPLACEMENT, text)
    if n == 0:
        print(f"  run_combine_cleanup.sh: WARNING: regex did not match; skipping.")
        return False
    if dry_run:
        print(f"  run_combine_cleanup.sh: would remove DATA/ wipe (dry run).")
    else:
        path.write_text(new_text)
        print(f"  run_combine_cleanup.sh: patched (DATA/ wipe removed for retry).")
    return True


def patch_batch_dir(batch_dir: Path, dry_run: bool = False) -> None:
    """Patch run_array.sh and run_combine_cleanup.sh in a single batch directory."""
    print(f"\n=== {'[dry run] ' if dry_run else ''}Patching {batch_dir} ===")
    if not batch_dir.is_dir():
        print(f"  ERROR: not a directory, skipping.")
        return

    run_array = batch_dir / "run_array.sh"
    run_combine = batch_dir / "run_combine_cleanup.sh"

    if not run_array.exists():
        print(f"  ERROR: run_array.sh not found.")
    else:
        patch_run_array(run_array, dry_run=dry_run)

    if not run_combine.exists():
        print(f"  ERROR: run_combine_cleanup.sh not found.")
    else:
        patch_run_combine(run_combine, dry_run=dry_run)


def find_batch_dirs(parent_dir: Path) -> list:
    """Return all subdirectories of parent_dir that contain a run_array.sh."""
    return sorted(d for d in parent_dir.iterdir() if d.is_dir() and (d / "run_array.sh").exists())


def patch_from_queue_file(queue_file: Path, dry_run: bool = False) -> None:
    """Read parent_dir from a submit_grid queue file and patch all batch dirs inside it."""
    state = json.loads(queue_file.read_text())
    parent_dir = Path(state["config"]["parent_dir"])
    print(f"Queue file points to parent_dir: {parent_dir}")
    batch_dirs = find_batch_dirs(parent_dir)
    if not batch_dirs:
        print("No batch directories (with run_array.sh) found.")
        return
    print(f"Found {len(batch_dirs)} batch director{'y' if len(batch_dirs) == 1 else 'ies'}.")
    for d in batch_dirs:
        patch_batch_dir(d, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--parent_dir", default=None,
                       help="Patch every batch directory (containing run_array.sh) "
                            "found directly inside this directory.")
    group.add_argument("--queue_file", default=None,
                       help="Read parent_dir from a submit_grid queue file and patch "
                            "all batch directories found there.")
    parser.add_argument("batch_dirs", nargs="*", metavar="BATCH_DIR",
                        help="Explicit batch directories to patch (alternative to "
                             "--parent_dir / --queue_file).")
    parser.add_argument("--dry_run", action="store_true",
                        help="Print what would be changed without writing anything.")
    args = parser.parse_args()

    if args.queue_file:
        patch_from_queue_file(Path(args.queue_file), dry_run=args.dry_run)
    elif args.parent_dir:
        parent = Path(args.parent_dir)
        batch_dirs = find_batch_dirs(parent)
        if not batch_dirs:
            print("No batch directories (with run_array.sh) found.")
        else:
            print(f"Found {len(batch_dirs)} batch director{'y' if len(batch_dirs) == 1 else 'ies'}.")
            for d in batch_dirs:
                patch_batch_dir(d, dry_run=args.dry_run)
    elif args.batch_dirs:
        for d in args.batch_dirs:
            patch_batch_dir(Path(d), dry_run=args.dry_run)
    else:
        parser.error("Provide --parent_dir, --queue_file, or explicit BATCH_DIR arguments.")

    print("\nDone.")
