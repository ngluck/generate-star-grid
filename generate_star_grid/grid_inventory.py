"""
Grid inventory: scan a parent directory for merged grid directories and report
which outer-parameter combinations are already covered.

A merged grid directory is one whose name contains at least one _var<Label>
token (e.g. varM_varZ) and which contains a combined_history.hdf5. Per-batch
subdirectories (moved inside the merged dir by merge_grids) each carry a
notes.txt from which exact constant and swept parameter values are read.
"""

import argparse
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# notes.txt parsing
# ---------------------------------------------------------------------------

def parse_notes_txt(notes_path: Path) -> dict:
    """
    Parse a per-batch notes.txt and return structured parameter info.

    Returns a dict with keys::

      'constants': {label: float, ...}   -- parameters fixed for this batch
      'swept':     {label: {'internal_key': str, 'min': float, 'max': float,
                             'num_points': int}, ...}

    Returns empty dicts for both if the file cannot be parsed.
    """
    constants = {}
    swept = {}

    try:
        text = notes_path.read_text()
    except OSError:
        return {"constants": constants, "swept": swept}

    section = None
    for line in text.splitlines():
        if line.startswith("Constant parameters:"):
            section = "constants"
            continue
        if line.startswith("Swept parameter(s):"):
            section = "swept"
            continue
        if line.startswith("Note:") or line.startswith("Failed") or line == "":
            section = None
            continue

        if section == "constants":
            # "  initial_z (Z) = 0.000379"
            m = re.match(r"\s+(\S+)\s+\((\w+)\)\s*=\s*([0-9eE.+-]+)", line)
            if m:
                constants[m.group(2)] = float(m.group(3))

        elif section == "swept":
            # "  initial_mass (M): 0.7 to 1.2, 500 points, ..."
            m = re.match(
                r"\s+(\S+)\s+\((\w+)\):\s*([0-9eE.+-]+)\s+to\s+([0-9eE.+-]+),\s*(\d+)\s+points",
                line,
            )
            if m:
                swept[m.group(2)] = {
                    "internal_key": m.group(1),
                    "min": float(m.group(3)),
                    "max": float(m.group(4)),
                    "num_points": int(m.group(5)),
                }

    return {"constants": constants, "swept": swept}


# ---------------------------------------------------------------------------
# Merged dir scanning
# ---------------------------------------------------------------------------

_VAR_PATTERN = re.compile(r"_var[A-Za-z]+", re.IGNORECASE)


def _is_merged_dir(d: Path) -> bool:
    return (
        d.is_dir()
        and bool(_VAR_PATTERN.search(d.name))
        and (d / "combined_history.hdf5").exists()
    )


def scan_merged_dirs(parent_dir: Path) -> list:
    """
    Scan parent_dir for merged grid directories and return their coverage.

    Each entry in the returned list is a dict::

      'dir':                  Path  -- the merged directory
      'varying_labels':       list[str]  -- labels that differ across batches (outer)
      'fixed_labels':         list[str]  -- labels constant across all batches
      'swept_labels':         list[str]  -- labels swept within each batch (inner)
      'covered_combinations': list[dict] -- one dict per batch, label -> value
                                           for the outer (varying) constants
      'batch_dirs':           list[Path] -- per-batch subdirs found with notes.txt
    """
    results = []

    for d in sorted(parent_dir.iterdir()):
        if not _is_merged_dir(d):
            continue

        batch_dirs = sorted(
            sub for sub in d.iterdir()
            if sub.is_dir() and (sub / "notes.txt").exists()
        )

        if not batch_dirs:
            results.append({
                "dir": d,
                "varying_labels": [],
                "fixed_labels": [],
                "swept_labels": [],
                "covered_combinations": [],
                "batch_dirs": [],
            })
            continue

        parsed = [parse_notes_txt(bd / "notes.txt") for bd in batch_dirs]

        # Collect all constant labels seen across any batch
        all_constant_labels = set()
        for p in parsed:
            all_constant_labels.update(p["constants"].keys())

        # Labels that differ between batches are "outer" (varying)
        varying_labels = []
        fixed_labels = []
        for label in sorted(all_constant_labels):
            values = set()
            for p in parsed:
                if label in p["constants"]:
                    values.add(p["constants"][label])
            if len(values) > 1:
                varying_labels.append(label)
            else:
                fixed_labels.append(label)

        # Swept labels are consistent across batches (inner sweep)
        swept_labels = sorted(
            set(label for p in parsed for label in p["swept"].keys())
        )

        covered_combinations = [
            {label: p["constants"][label] for label in varying_labels if label in p["constants"]}
            for p in parsed
        ]

        results.append({
            "dir": d,
            "varying_labels": varying_labels,
            "fixed_labels": fixed_labels,
            "swept_labels": swept_labels,
            "covered_combinations": covered_combinations,
            "batch_dirs": batch_dirs,
        })

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt_val(v: float) -> str:
    """Format a float without trailing zeros."""
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    return s


def main(parent_dir: Path) -> None:
    merged = scan_merged_dirs(parent_dir)

    if not merged:
        print(f"No merged grid directories found in {parent_dir}")
        return

    for entry in merged:
        print(f"{entry['dir'].name}/")

        if entry["swept_labels"]:
            inner_str = ", ".join(f"{l} (inner)" for l in entry["swept_labels"])
        else:
            inner_str = "(none)"

        if entry["varying_labels"]:
            outer_str = ", ".join(f"{l} (outer)" for l in entry["varying_labels"])
        else:
            outer_str = "(none)"

        print(f"  Varies: {inner_str}, {outer_str}")

        if entry["fixed_labels"]:
            fixed_parts = []
            for label in entry["fixed_labels"]:
                val = next(
                    (p["constants"][label] for p in
                     [parse_notes_txt(bd / "notes.txt") for bd in entry["batch_dirs"]]
                     if label in p["constants"]),
                    None,
                )
                if val is not None:
                    fixed_parts.append(f"{label}={_fmt_val(val)}")
            if fixed_parts:
                print(f"  Fixed:  {', '.join(fixed_parts)}")

        for label in entry["varying_labels"]:
            vals = sorted(set(
                c[label] for c in entry["covered_combinations"] if label in c
            ))
            val_strs = [_fmt_val(v) for v in vals]
            n = len(vals)
            if n <= 8:
                display = ", ".join(val_strs)
            else:
                display = ", ".join(val_strs[:4]) + f", ... ({n} batches total)"
            print(f"  {label} values covered: {display}")

        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="List merged grid directories and their outer-parameter coverage."
    )
    parser.add_argument("--parent_dir", required=True, help="Directory to scan.")
    args = parser.parse_args()
    main(Path(args.parent_dir).expanduser().resolve())
