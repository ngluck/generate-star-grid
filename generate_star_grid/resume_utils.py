import shutil
import subprocess
import os
from pathlib import Path
import re


def get_next_resume_index(base_path: Path, prefix: str) -> int:
    """
    Return the next available resume index based on existing inlist files without extensions.

    Args:
        base_path (Path): Directory where inlist files are located.
        prefix (str): Base inlist name (e.g. 'inlist_M_1.000_Y_0.270_Z_0.02_alpha_2.00').

    Returns:
        int: Next available resume index. Returns 1 if only the base file exists.
    """
    existing = [f.name for f in base_path.glob(f"{prefix}*")]
    indices = []

    for fname in existing:
        match = re.search(rf"{re.escape(prefix)}_resume(\d+)$", fname)
        if match:
            indices.append(int(match.group(1)))
        elif fname == prefix:
            indices.append(0)  # base file with no resume suffix

    return max(indices, default=0) + 1

def modify_inlist_for_resume(inlist_path, modifications, output_path=None, tag=None):
    """
    Modify an existing inlist file with new parameter settings for resumed evolution.

    Args:
        inlist_path (Path): Path to original inlist (from grid_inlists).
        modifications (dict): Dictionary of parameter-value pairs to update.
        output_path (Path): Where to write the new inlist (default: same directory).
        tag (str): Optional tag to append to output filename (e.g., "_mod1").

    Returns:
        Path to modified inlist file.
    """
    print(f"Modifying inlist: {inlist_path}")
    with open(inlist_path, "r") as f:
        text = f.read()

    for key, val in modifications.items():
        # Try to replace the key if it exists
        pattern = rf"^\s*{re.escape(key)}\s*=.*?$"
        replacement = f"{key} = {val}"
        text, n_subs = re.subn(pattern, replacement, text, flags=re.MULTILINE)

        if n_subs == 0:
            # Try to insert into the appropriate block
            inserted = False
            block_match = re.search(r"^&controls.*?$", text, flags=re.MULTILINE)
            if block_match:
                # Insert after &controls
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if line.strip().startswith("&controls"):
                        lines.insert(i + 1, f"    {replacement}")
                        inserted = True
                        print(f"Inserted '{key} = {val}' into &controls block.")
                        break
                text = "\n".join(lines)
            if not inserted:
                # If no &controls block found, append at the end
                text += f"\n{replacement}"
                print(f"Appended '{key} = {val}' to end of inlist.")

    # Determine output path
    if output_path is None:
        output_path = inlist_path.parent

    base_name = inlist_path.stem
    suffix = f"_{tag}" if tag else "_mod"
    new_inlist_name = f"{base_name}{suffix}"
    new_inlist_path = output_path / new_inlist_name

    with open(new_inlist_path, "w") as f:
        f.write(text)

    print(f"Modified inlist written to: {new_inlist_path}")
    return new_inlist_path


def old_modify_inlist_for_resume(inlist_path, modifications, output_path=None, tag=None):
    """
    Modify an existing inlist file with new parameter settings for resumed evolution.

    Args:
        inlist_path (Path): Path to original inlist (from grid_inlists).
        modifications (dict): Dictionary of parameter-value pairs to update.
        output_path (Path): Where to write the new inlist (default: same directory).
        tag (str): Optional tag to append to output filename (e.g., "_mod1").

    Returns:
        Path to modified inlist file.
    """
    with open(inlist_path, "r") as f:
        text = f.read()

    for key, val in modifications.items():
        # Replace lines like `key = value`
        text, n_subs = re.subn(
            rf"^\s*{re.escape(key)}\s*=.*?$",
            f"{key} = {val}",
            text,
            flags=re.MULTILINE
        )
        if n_subs == 0:
            print(f"Warning: '{key}' not found in inlist — consider inserting manually.")

    # Determine output path
    if output_path is None:
        output_path = inlist_path.parent

    base_name = inlist_path.stem
    suffix = f"_{tag}" if tag else "_mod"
    new_inlist_name = f"{base_name}{suffix}"
    new_inlist_path = output_path / new_inlist_name

    with open(new_inlist_path, "w") as f:
        f.write(text)

    print(f"Modified inlist written to: {new_inlist_path}")
    return new_inlist_path

