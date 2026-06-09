import re
from pathlib import Path


def get_next_resume_index(base_path: Path, prefix: str) -> int:
    """
    Return the next available resume index for a given inlist prefix.

    Scans base_path for files matching ``<prefix>_resume<N>`` and returns
    the next integer after the highest found (or 1 if only the base file exists).

    Args:
        base_path: Directory containing inlist files.
        prefix: Base inlist name (e.g. 'inlist_M_1.000_Y_0.270_Z_0.020_alpha_2.00').

    Returns:
        Next available resume index.
    """
    existing = [f.name for f in base_path.glob(f"{prefix}*")]
    indices = []
    for fname in existing:
        match = re.search(rf"{re.escape(prefix)}_resume(\d+)$", fname)
        if match:
            indices.append(int(match.group(1)))
        elif fname == prefix:
            indices.append(0)
    return max(indices, default=0) + 1


def modify_inlist_for_resume(
    inlist_path: Path,
    modifications: dict,
    output_path: Path = None,
    tag: str = None,
) -> Path:
    """
    Apply parameter modifications to an existing inlist for a resumed run.

    Replaces matching ``key = value`` lines in the inlist. If a key is not found,
    it is inserted after the ``&controls`` block header, or appended at the end.

    Args:
        inlist_path: Path to the original inlist file (e.g. from grid_inlists/).
        modifications: Dict of parameter names to new values, e.g. ``{'xa_central_lower_limit': '1d-3'}``.
        output_path: Directory to write the modified inlist (default: same as inlist_path's parent).
        tag: Optional string appended to the output filename (e.g. '_rgb').

    Returns:
        Path to the written modified inlist file.
    """
    print(f"Modifying inlist: {inlist_path}")
    with open(inlist_path, "r") as f:
        text = f.read()

    for key, val in modifications.items():
        pattern = rf"^\s*{re.escape(key)}\s*=.*?$"
        replacement = f"{key} = {val}"
        text, n_subs = re.subn(pattern, replacement, text, flags=re.MULTILINE)

        if n_subs == 0:
            inserted = False
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("&controls"):
                    lines.insert(i + 1, f"    {replacement}")
                    inserted = True
                    print(f"Inserted '{key} = {val}' into &controls block.")
                    break
            text = "\n".join(lines)
            if not inserted:
                text += f"\n{replacement}"
                print(f"Appended '{key} = {val}' to end of inlist.")

    if output_path is None:
        output_path = inlist_path.parent

    suffix = f"_{tag}" if tag else "_mod"
    new_inlist_path = Path(output_path) / f"{inlist_path.stem}{suffix}"

    with open(new_inlist_path, "w") as f:
        f.write(text)

    print(f"Modified inlist written to: {new_inlist_path}")
    return new_inlist_path
