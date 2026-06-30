import shutil
import sys
import numpy as np
from pathlib import Path
import re
import argparse
import difflib
import itertools
import subprocess
import pandas as pd
from scipy.stats.qmc import Sobol
from concurrent.futures import ProcessPoolExecutor
from typing import Union, Optional
import datetime


# Maps Python parameter keys to their MESA inlist name, directory label, and
# format spec. This is the single place to register a new swept parameter —
# update_inlist and make_run_dir_name both derive their behaviour from it.
PARAM_FORMAT = {
    "initial_mass":        {"label": "M",     "fmt": ".6f", "inlist_key": "initial_mass"},
    "initial_y":           {"label": "Y",     "fmt": ".3f", "inlist_key": "initial_y"},
    "initial_z":           {"label": "Z",     "fmt": ".4f", "inlist_key": "initial_z"},
    "mixing_length_alpha": {"label": "alpha", "fmt": ".2f", "inlist_key": "mixing_length_alpha"},
}

# Shared description of the value-spec grammar accepted by --mass,
# --initial_Z/--initial_Y/--alpha_MLT, and --param KEY=SPEC. See
# parse_param_value/coerce_cli_values for the implementation.
VALUE_SPEC_HELP = (
    "VALUE (constant), V1,V2,... (explicit values), MIN:MAX (continuous "
    "range, uses --num_points/--grid_type), or MIN:MAX:STEP (explicit "
    "values spaced by STEP, inclusive of both ends)."
)


def _min_decimals_for_value(value: float, min_decimals: int = 1, max_decimals: int = 10) -> int:
    """
    Smallest decimal count in [min_decimals, max_decimals] that represents
    `value` exactly (i.e. rounding to that many decimals doesn't change it).

    Falls back to max_decimals if no count in range represents it exactly.
    """
    for d in range(min_decimals, max_decimals + 1):
        if round(value, d) == value:
            return d
    return max_decimals


def _exact_fmt(val: float, default_fmt: str, max_decimals: int = 10) -> str:
    """
    Format spec at least as precise as `default_fmt` that represents `val` exactly.

    Prevents silently truncating values that need more decimals than a
    registry's default format provides -- e.g. an initial_z of 0.000379 would
    round to 0.0004 under '.4f', which is wrong for the actual MESA run, not
    just a cosmetic label issue.
    """
    default_decimals = int(default_fmt.rstrip("f").lstrip("."))
    decimals = max(default_decimals, _min_decimals_for_value(val, default_decimals, max_decimals))
    return f".{decimals}f"


def _min_decimals_for_unique(values, min_decimals: int = 1, max_decimals: int = 10) -> int:
    """
    Smallest decimal count in [min_decimals, max_decimals] that gives every
    value in `values` a distinct formatted string.

    Falls back to max_decimals if no count in range disambiguates all values.
    """
    for d in range(min_decimals, max_decimals + 1):
        if len({f"{v:.{d}f}" for v in values}) == len(values):
            return d
    return max_decimals


def _expand_range(lo: float, hi: float, step: float) -> list:
    """
    Generate explicit values from lo to hi, inclusive of both endpoints, spaced by step.

    If (hi - lo) isn't an exact multiple of step, the final interval is
    shorter than step so that hi is always included exactly. A single value
    [lo] is returned if lo == hi.
    """
    if step <= 0:
        raise ValueError(f"Step must be positive, got {step}.")
    if hi < lo:
        raise ValueError(f"max ({hi}) must be >= min ({lo}).")
    values = []
    i = 0
    while lo + i * step < hi - step * 1e-9:
        values.append(round(lo + i * step, 10))
        i += 1
    values.append(round(hi, 10))
    return values


def _format_value_list(values: list, fmt: Optional[str] = None, max_inline: int = 6) -> str:
    """
    Format a list of swept values for display.

    Short lists are shown in full, e.g. '3 value(s) = [0.014, 0.02]'. Longer
    lists are condensed to their endpoints, e.g. '11 value(s) = 0.7 to 1.2
    (spacing 0.05)' if evenly spaced, or '11 value(s) = 0.7 to 1.2' otherwise.
    """
    def fmt_val(v):
        return f"{v:{fmt}}" if fmt else f"{v:g}"

    if len(values) <= max_inline:
        return f"{len(values)} value(s) = [{', '.join(fmt_val(v) for v in values)}]"

    line = f"{len(values)} value(s) = {fmt_val(values[0])} to {fmt_val(values[-1])}"
    diffs = np.diff(values)
    if np.allclose(diffs, diffs[0]):
        line += f" (spacing {fmt_val(diffs[0])})"
    return line


def compute_param_formats(
    param_specs: dict,
    grid_type: str = "linear",
    num_points: int = 8,
    min_decimals: int = 1,
    max_decimals: int = 10,
    param_registry: Optional[dict] = None,
) -> dict:
    """
    Choose the minimum decimal precision needed for each parameter's directory label.

    For a continuous swept parameter (a `(min, max)` tuple in param_specs), this
    is the fewest decimals such that every grid value in that range produces a
    unique formatted string, given the spacing implied by `num_points`. For a
    discrete swept parameter (a list of values), it's the fewest decimals that
    give every value in the list a unique formatted string. For a fixed
    (scalar) parameter, it's the fewest decimals that represent its value exactly.

    For 'sobol' grids the actual sampled values aren't reproducible across
    processes (Sobol scrambling isn't seeded), so the same evenly-spaced
    `num_points` values used for 'linear' grids are used here for format
    selection only — this doesn't affect the actual sampled points, just how
    many decimals are used to label them.

    Args:
        param_specs: Dict passed to generate_grid (scalars, (min, max) tuples,
            or lists of values).
        grid_type: 'linear' or 'sobol'.
        num_points: Points per swept dimension (linear) or total samples (sobol).
        min_decimals: Smallest number of decimals to use for any parameter.
        max_decimals: Largest number of decimals to consider before giving up.
        param_registry: Dict like PARAM_FORMAT (label/fmt/inlist_key per key).
            Defaults to PARAM_FORMAT; pass an extended copy to include extra
            (non-built-in) parameters.

    Returns:
        Dict mapping each param_registry key present in param_specs to a format
        spec string (e.g. '.3f'), for use with make_run_dir_name.
    """
    registry = param_registry or PARAM_FORMAT
    formats = {}
    for key, spec in param_specs.items():
        if key not in registry:
            continue
        if isinstance(spec, tuple):
            lo, hi = spec
            if num_points <= 1 or lo == hi:
                d = _min_decimals_for_value(lo, min_decimals, max_decimals)
            else:
                values = np.linspace(lo, hi, num_points)
                d = _min_decimals_for_unique(values, min_decimals, max_decimals)
        elif isinstance(spec, list):
            # Use enough decimals to represent every listed value exactly (not
            # just uniquely) -- e.g. [0.014, 0.02] -> '.3f' ('0.014', '0.020'),
            # not '.2f' ('0.01', '0.02'), which would round 0.014 down silently.
            d = max(_min_decimals_for_value(v, min_decimals, max_decimals) for v in spec)
        else:
            d = _min_decimals_for_value(spec, min_decimals, max_decimals)
        formats[key] = f".{d}f"
    return formats


def make_run_dir_name(
    params: dict, param_formats: Optional[dict] = None, param_registry: Optional[dict] = None
) -> str:
    """
    Build the run directory name from a parameter dict.

    Uses the canonical order and labels in param_registry (PARAM_FORMAT by
    default). Only parameters present in both params and param_registry are
    included, so adding a new swept parameter to the registry automatically
    includes it in all directory and log file names without any other code
    changes.

    Args:
        params: Parameter dict (keys matching param_registry entries).
        param_formats: Optional per-key format overrides (e.g. from
            compute_param_formats), falling back to param_registry's defaults
            for any key not present.
        param_registry: Dict like PARAM_FORMAT (label/fmt/inlist_key per key).
            Defaults to PARAM_FORMAT; pass an extended copy to include extra
            (non-built-in) parameters.

    Returns:
        Directory name string, e.g. 'M_0.700000_Y_0.270_Z_0.0200_alpha_2.00'.
    """
    registry = param_registry or PARAM_FORMAT
    parts = []
    for key, spec in registry.items():
        if key in params:
            fmt = (param_formats or {}).get(key, spec["fmt"])
            parts.append(f"{spec['label']}_{params[key]:{fmt}}")
    return "_".join(parts)


def write_grid_notes(
    param_specs: dict,
    param_formats: dict,
    grid_type: str,
    num_points: int,
    out_path: Union[str, Path],
    param_registry: Optional[dict] = None,
) -> None:
    """
    Write a notes.txt summarizing a grid's constant and swept parameters.

    Records, for each param_registry parameter: whether it's held constant,
    swept over a continuous range, or swept over a discrete list of values;
    its value(s); the spacing between grid points (for continuous sweeps);
    and the directory-naming format chosen for it.

    Args:
        param_specs: Dict passed to generate_grid (scalars, (min, max)
            tuples, or lists of values).
        param_formats: Dict from compute_param_formats mapping each key to a
            format spec (e.g. '.3f').
        grid_type: 'linear' or 'sobol'.
        num_points: Points per swept dimension (linear) or total samples (sobol).
        out_path: File path to write the notes to.
        param_registry: Dict like PARAM_FORMAT (label/fmt/inlist_key per key).
            Defaults to PARAM_FORMAT; pass an extended copy to include extra
            (non-built-in) parameters.
    """
    registry = param_registry or PARAM_FORMAT
    fixed = {k: v for k, v in param_specs.items() if k in registry and not isinstance(v, (tuple, list))}
    swept_continuous = {k: v for k, v in param_specs.items() if k in registry and isinstance(v, tuple)}
    swept_discrete = {k: v for k, v in param_specs.items() if k in registry and isinstance(v, list)}

    lines = [
        f"Grid generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Grid type: {grid_type}, num_points: {num_points}",
        "",
        "Constant parameters:",
    ]
    if not fixed:
        lines.append("  (none)")
    for key, val in fixed.items():
        spec = registry[key]
        fmt = param_formats.get(key, spec["fmt"])
        lines.append(f"  {key} ({spec['label']}) = {val:{fmt}}")

    lines.append("")
    lines.append("Swept parameter(s):")
    if not swept_continuous and not swept_discrete:
        lines.append("  (none)")
    for key, (lo, hi) in swept_continuous.items():
        spec = registry[key]
        fmt = param_formats.get(key, spec["fmt"])
        line = f"  {key} ({spec['label']}): {lo} to {hi}, {num_points} points"
        if num_points > 1 and lo != hi:
            spacing = (hi - lo) / (num_points - 1)
            line += f", spacing = {spacing:{fmt}}"
        line += f", directory format = '{fmt}'"
        lines.append(line)
    for key, values in swept_discrete.items():
        spec = registry[key]
        fmt = param_formats.get(key, spec["fmt"])
        lines.append(
            f"  {key} ({spec['label']}): {_format_value_list(values, fmt)}, "
            f"directory format = '{fmt}'"
        )

    lines.append("")
    lines.append(
        "Note: 'M' (initial_mass) in directory/file names is always the "
        "*initial* mass at the start of the run, even though mass may "
        "decrease over the evolution due to mass loss."
    )

    Path(out_path).write_text("\n".join(lines) + "\n")


def print_grid_dry_run(
    param_specs: dict,
    grid_type: str = "linear",
    num_points: int = 8,
    avg_data_mb: float = 20.0,
    param_registry: Optional[dict] = None,
) -> None:
    """
    Print a plan summary for a grid without building or running MESA.

    Reports the constant and swept parameters, the number of MESA models that
    would run (broken down incrementally per swept dimension), an estimated
    total disk footprint, and example directory/file names.

    Args:
        param_specs: Dict passed to generate_grid (scalars, (min, max)
            tuples, or lists of values).
        grid_type: 'linear' or 'sobol'.
        num_points: Points per swept dimension (linear) or total samples (sobol).
        avg_data_mb: Estimated MESA output size (MB) per model, used for the
            disk usage estimate.
        param_registry: Dict like PARAM_FORMAT (label/fmt/inlist_key per key).
            Defaults to PARAM_FORMAT; pass an extended copy to include extra
            (non-built-in) parameters added via --param.
    """
    registry = param_registry or PARAM_FORMAT
    fixed = {k: v for k, v in param_specs.items() if k in registry and not isinstance(v, (tuple, list))}
    continuous = {k: v for k, v in param_specs.items() if k in registry and isinstance(v, tuple)}
    discrete = {k: v for k, v in param_specs.items() if k in registry and isinstance(v, list)}

    print("=" * 60)
    print("DRY RUN: grid plan (no MESA models will be built or run)")
    print("=" * 60)

    print("\nConstant parameters:")
    if not fixed:
        print("  (none)")
    for key, val in fixed.items():
        spec = registry[key]
        print(f"  {key} ({spec['label']}) = {val}")

    print("\nSwept parameters:")
    if not continuous and not discrete:
        print("  (none)")
    for key, (lo, hi) in continuous.items():
        spec = registry[key]
        line = f"  {key} ({spec['label']}): {lo} to {hi}, {num_points} points ({grid_type})"
        if num_points > 1 and lo != hi:
            spacing = (hi - lo) / (num_points - 1)
            line += f", spacing ~ {spacing:.6g}"
        print(line)
    for key, values in discrete.items():
        spec = registry[key]
        print(f"  {key} ({spec['label']}): {_format_value_list(values)}")

    print("\nModel count:")
    if not continuous and not discrete:
        print("  1 star (single fixed model)")
        total_models = 1
    else:
        total_models = 1
        labels_so_far = []
        first_line = True

        if continuous:
            continuous_total = num_points if grid_type == "sobol" else num_points ** len(continuous)
            total_models *= continuous_total
            labels_so_far.extend(registry[k]["label"] for k in continuous)
            n = "star" if total_models == 1 else "stars"
            print(f"  {total_models} {n} varying {', '.join(labels_so_far)}")
            first_line = False

        for key, values in discrete.items():
            total_models *= len(values)
            labels_so_far.append(registry[key]["label"])
            n = "star" if total_models == 1 else "stars"
            prefix = "" if first_line else "total "
            print(f"  {total_models} {prefix}{n} varying {', '.join(labels_so_far)}")
            first_line = False

    print("\nEstimated disk usage:")
    total_gb = total_models * avg_data_mb / 1024
    print(f"  ~{avg_data_mb:g} MB/model x {total_models} model(s) ~ {total_gb:.1f} GB total"
          f" (before any --cleanup)")
    print("  (default avg_data_mb is a rough estimate from prior grids; override with --avg_data_mb)")

    param_formats = compute_param_formats(param_specs, grid_type=grid_type, num_points=num_points,
                                           param_registry=registry)
    param_dicts = generate_grid(param_specs, grid_type=grid_type, num_points=num_points)

    print("\nExample directory/file names:")
    sample_idxs = sorted({0, len(param_dicts) // 2, len(param_dicts) - 1})
    for i in sample_idxs:
        print(f"  {make_run_dir_name(param_dicts[i], param_formats, registry)}/")
    first_name = make_run_dir_name(param_dicts[0], param_formats, registry)
    print(f"  grid_TAMS/TAMS_{first_name}.mod")
    print(f"  grid_inlists/inlist_{first_name}")
    print(f"  grid_profiles/{first_name}/   (profile*.data, profiles.index, etc., if any were saved)")
    print(f"  LOGS/log_{first_name}_TASK_0.txt   (for SLURM array runs)")
    print("  notes.txt")

    if grid_type == "sobol":
        m = np.log2(num_points)
        if not m.is_integer():
            print("\nWARNING: Sobol sampling requires --num_points to be a power of 2.")

    print("\nSLURM array:")
    print(f"  --array=0-{total_models - 1}")
    print("=" * 60)


def extract_constants_from_subdir_name(name: str, keys: list) -> dict:
    """
    Extract parameter values encoded in a subdirectory name.

    Looks for each key as a '<key>_<value>' token bounded by underscores or
    the start/end of the string, e.g. for keys=['M', 'Y'] and
    name='M_1.114_Y_0.270_Z_0.020_alpha_2.00', extracts {'M': 1.114, 'Y': 0.270}.
    Matching per-key (rather than blindly splitting on '_') means labels that
    themselves contain underscores (e.g. extra MESA parameters like
    'overshoot_f_above_nonburn_core') are handled correctly.

    Args:
        name: Subdirectory name string.
        keys: Parameter keys to extract (e.g. ['M', 'Y', 'Z', 'alpha']).

    Returns:
        Dict mapping each found key to its float value.
    """
    constants = {}
    for key in keys:
        match = re.search(rf"(?:^|_){re.escape(key)}_([0-9.eE+-]+)(?:_|$)", name)
        if match is None:
            print(f"Warning: key '{key}' not found in directory name: '{name}'")
            continue
        try:
            constants[key] = float(match.group(1))
        except ValueError:
            print(f"Warning: could not convert value '{match.group(1)}' for key '{key}' in '{name}'")
    return constants


def extract_constant_from_profile(profile_path: Union[str, Path], key: str) -> float:
    """
    Extract a scalar constant from the header block of a MESA profile.data file.

    Args:
        profile_path: Path to the profile.data file.
        key: Name of the constant to extract (e.g. 'initial_z').

    Returns:
        The float value associated with the key.

    Raises:
        ValueError: If the key is not found or its value cannot be parsed.
    """
    df = pd.read_csv(profile_path, header=None, sep=r'\s+', comment="#", nrows=10)
    for i in range(len(df) - 1):
        if key in df.iloc[i].values:
            col_idx = list(df.iloc[i]).index(key)
            try:
                return float(df.iloc[i + 1, col_idx])
            except Exception as e:
                raise ValueError(f"Failed to parse value for '{key}' from {profile_path}: {e}")
    raise ValueError(f"Key '{key}' not found in {profile_path}")


def list_inlist_params(inlist_text: str) -> list:
    """
    List the namelist parameter names assigned in an inlist's text.

    Matches lines of the form 'key = value' (Fortran namelist assignment),
    including array-indexed parameters like 'overshoot_f(1)'. Comment lines
    (starting with '!') and lines with no assignment are ignored.

    Args:
        inlist_text: Raw text of an inlist/inlist_template file.

    Returns:
        Sorted list of unique parameter names, e.g.
        ['initial_mass', 'mixing_length_alpha', 'overshoot_f(1)', ...].
    """
    params = []
    for line in inlist_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        match = re.match(r"([a-zA-Z][\w]*(?:\(\d+\))?)\s*=", stripped)
        if match:
            params.append(match.group(1))
    return sorted(set(params))


def resolve_param_key(key: str, inlist_text: str) -> str:
    """
    Validate that `key` is a parameter settable in an inlist, and return its
    spelling as it appears there.

    Matching is case-insensitive (MESA's Fortran namelists are case-insensitive),
    e.g. resolve_param_key('Overshoot_F(1)', inlist_text) -> 'overshoot_f(1)'.

    Args:
        key: Parameter name as given by the user (e.g. on the command line).
        inlist_text: Raw text of the inlist_template file.

    Returns:
        The parameter's spelling as it appears in inlist_text.

    Raises:
        ValueError: If `key` doesn't match any parameter settable in
            inlist_text. The message lists close matches (via difflib, if
            any) and every parameter available in inlist_text.
    """
    available = list_inlist_params(inlist_text)
    for name in available:
        if name.lower() == key.lower():
            return name

    suggestions = difflib.get_close_matches(key, available, n=5, cutoff=0.5)
    msg = f"Parameter '{key}' not found in inlist_template."
    if suggestions:
        msg += f" Did you mean: {', '.join(suggestions)}?"
    msg += "\nAvailable parameters in inlist_template:\n  " + "\n  ".join(available)
    raise ValueError(msg)


def coerce_cli_values(values: list) -> Union[float, tuple, list]:
    """
    Convert string tokens from an argparse `nargs='+'` argument into a
    constant, range, or list spec.

    A single token containing ':' or ',' is parsed as a value-spec string
    (see parse_param_value): 'MIN:MAX' (continuous sweep, uses
    --num_points/--grid_type), 'MIN:MAX:STEP' (explicit values spaced by
    STEP, inclusive of both ends), or 'V1,V2,...' (explicit values).

    Otherwise, the tokens are one or more plain numbers: a single value is
    held constant; two or more are used as an explicit list of values.
    """
    if len(values) == 1 and (":" in values[0] or "," in values[0]):
        return parse_param_value(values[0])
    try:
        floats = [float(v) for v in values]
    except ValueError:
        raise ValueError(
            f"Invalid value(s) {values}: expected one or more plain numbers, "
            f"or a single 'MIN:MAX', 'MIN:MAX:STEP', or 'V1,V2,...' spec."
        )
    return floats[0] if len(floats) == 1 else floats


def parse_param_value(spec_str: str) -> Union[float, tuple, list]:
    """
    Parse a value-spec string into a fixed float, (min, max) tuple, or list.

    - ``'VALUE'`` -> ``float(VALUE)`` (constant)
    - ``'MIN:MAX'`` -> ``(float(MIN), float(MAX))`` (continuous sweep, uses
      --num_points/--grid_type)
    - ``'MIN:MAX:STEP'`` -> ``[v0, v1, ..., MAX]`` (explicit values spaced by
      STEP, inclusive of both ends; see _expand_range)
    - ``'V1,V2,...'`` -> ``[float(V1), float(V2), ...]`` (explicit values)
    """
    if ":" in spec_str:
        parts = spec_str.split(":")
        if len(parts) == 2:
            lo, hi = parts
            return (float(lo), float(hi))
        if len(parts) == 3:
            lo, hi, step = parts
            return _expand_range(float(lo), float(hi), float(step))
        raise ValueError(f"Invalid range spec '{spec_str}': expected 'MIN:MAX' or 'MIN:MAX:STEP'.")
    if "," in spec_str:
        return [float(v) for v in spec_str.split(",")]
    return float(spec_str)


def parse_extra_params(param_args: list, inlist_text: str) -> tuple:
    """
    Parse `--param KEY=SPEC` arguments into param_specs and param_registry entries.

    Each KEY is validated against `inlist_text` via resolve_param_key (raising
    a ValueError listing close matches and available parameters if it isn't a
    settable inlist parameter). Resolved keys are used directly as both the
    param_specs key and the registry's inlist_key/label.

    Args:
        param_args: List of 'KEY=SPEC' strings from --param (repeatable).
        inlist_text: Raw text of the inlist_template file.

    Returns:
        (extra_specs, extra_registry): dicts to merge into param_ranges and
        param_registry respectively.
    """
    extra_specs = {}
    extra_registry = {}
    for item in param_args:
        if "=" not in item:
            raise ValueError(
                f"--param must be of the form KEY=VALUE, KEY=MIN:MAX, or KEY=V1,V2,...; got '{item}'"
            )
        key_str, spec_str = item.split("=", 1)
        inlist_key = resolve_param_key(key_str.strip(), inlist_text)
        extra_specs[inlist_key] = parse_param_value(spec_str.strip())
        extra_registry[inlist_key] = {
            "label": re.sub(r"[()]", "", inlist_key),
            "fmt": ".6f",
            "inlist_key": inlist_key,
        }
    return extra_specs, extra_registry


def extract_mass(subdir_name: str) -> float:
    """Return the mass value encoded in a subdirectory name (e.g. ``M_1.114_...`` → 1.114)."""
    match = re.search(r"M_(\d+\.\d+)", subdir_name)
    return float(match.group(1)) if match else float("inf")


def load_history_with_constants_from_profile(
    parent_dir: Union[str, Path],
    history_filename: str = "history.data",
    profile_filename_glob: str = "profile*.data",
    use_subdir_as_track: bool = False,
    skiprows_history: int = 5,
    constant_columns: Optional[list] = None,
    save_as_hdf5: bool = False,
    hdf5_filename: Optional[str] = "combined_history.hdf5",
    extract_constants_from_dirname: bool = False,
    hdf5_key: str = "history",
    overwrite: bool = True,
    return_preview_rows: int = 5,
    exclude_dirs: Optional[set] = None,
) -> pd.DataFrame:
    """
    Load MESA history files from model subdirectories and enrich with constant parameters.

    Constants are sourced from either the subdirectory name or a profile.data file.
    Data is written incrementally to HDF5 to avoid loading all tracks into memory.

    Args:
        parent_dir: Directory containing one subdirectory per stellar model.
        history_filename: Name of the MESA history file inside each model's DATA/ folder.
        profile_filename_glob: Glob pattern to locate a profile file per model.
        use_subdir_as_track: Use the subdirectory name as the Track ID; otherwise use integer index.
        skiprows_history: Header lines to skip in history files (default 5 for MESA).
        constant_columns: Parameter names to add as constant columns (e.g. ['Y', 'Z', 'alpha']).
        save_as_hdf5: Write output to an HDF5 file in parent_dir.
        hdf5_filename: Output HDF5 filename.
        extract_constants_from_dirname: Parse constants from the subdirectory name rather than
            a profile.data file.
        hdf5_key: HDF5 store key.
        overwrite: Delete any existing HDF5 file before writing.
        return_preview_rows: Number of rows from the first track to return as a preview.

    Returns:
        Preview DataFrame of the first `return_preview_rows` rows, or an empty DataFrame
        if no history files were found.
    """
    parent_dir = Path(parent_dir)
    subdirs = sorted([d for d in parent_dir.iterdir() if d.is_dir()])
    if exclude_dirs:
        subdirs = [d for d in subdirs if d.name not in exclude_dirs]
    total = len(subdirs)

    if hdf5_filename is None:
        raise ValueError("hdf5_filename must be provided")

    hdf5_path = parent_dir / hdf5_filename
    if overwrite and hdf5_path.exists():
        hdf5_path.unlink()

    preview_df = None
    n_total = 0
    appended = 0
    wrote_any = False

    with pd.HDFStore(hdf5_path, mode="w", complevel=5, complib="blosc") as store:
        for i, subdir in enumerate(subdirs):
            hist_path = subdir / "DATA" / history_filename
            profile_files = list(subdir.glob(profile_filename_glob))
            if not hist_path.exists():
                continue
            try:
                hist_df = pd.read_csv(hist_path, sep=r"\s+", comment="#", skiprows=skiprows_history)

                if "model_number" in hist_df.columns and not hist_df["model_number"].is_monotonic_increasing:
                    hist_df = (
                        hist_df.drop_duplicates(subset=["model_number"], keep="last")
                        .sort_values("model_number")
                        .reset_index(drop=True)
                    )

                constants = {}
                if constant_columns:
                    if extract_constants_from_dirname:
                        constants = extract_constants_from_subdir_name(subdir.name, constant_columns)
                    elif profile_files:
                        for key in constant_columns:
                            try:
                                constants[key] = extract_constant_from_profile(profile_files[0], key)
                            except Exception as e:
                                print(f"Warning: could not extract '{key}' from {profile_files[0]}: {e}")

                for k, v in constants.items():
                    hist_df[k] = v
                hist_df["Track"] = subdir.name if use_subdir_as_track else i

                if preview_df is None and return_preview_rows > 0:
                    preview_df = hist_df.head(return_preview_rows).copy()

                store.append(hdf5_key, hist_df, format="table")
                appended += 1
                if appended % 200 == 0 or appended == total:
                    print(f"Appended {appended}/{total} tracks.")

                wrote_any = True
                n_total += len(hist_df)

            except Exception as e:
                print(f"Error in {subdir}: {e}")

    if wrote_any:
        print(f"Saved merged data to {hdf5_path} (key='{hdf5_key}'), rows={n_total}")
    else:
        print("No history files found; nothing written.")

    return preview_df if preview_df is not None else pd.DataFrame()


def load_mesa_histories_from_subdirs(
    parent_dir: Union[str, Path],
    history_filename: str = "history.data",
    use_subdir_as_track: bool = False,
    skiprows: int = 5,
    save_as_hdf5: bool = False,
    hdf5_filename: Optional[str] = "grid_history.hdf5",
) -> pd.DataFrame:
    """
    Load all MESA history files from subdirectories into a single DataFrame.

    Args:
        parent_dir: Directory containing one subdirectory per stellar model.
        history_filename: Name of the history file in each subdirectory.
        use_subdir_as_track: Use subdirectory name as track label; otherwise use integer index.
        skiprows: Header lines to skip (default 5 for MESA).
        save_as_hdf5: Save the combined DataFrame to an HDF5 file.
        hdf5_filename: Output HDF5 filename (saved in parent_dir).

    Returns:
        Combined DataFrame with a 'track' column identifying each stellar model.
    """
    parent_dir = Path(parent_dir)
    dfs = []

    for i, subdir in enumerate(sorted(d for d in parent_dir.iterdir() if d.is_dir())):
        hist_file = subdir / history_filename
        if not hist_file.exists():
            continue
        try:
            df = pd.read_csv(hist_file, skiprows=skiprows, sep=r'\s+', comment="#")
            df["track"] = subdir.name if use_subdir_as_track else i
            dfs.append(df)
        except Exception as e:
            print(f"Error reading {hist_file}: {e}")

    if not dfs:
        return pd.DataFrame()

    full_df = pd.concat(dfs, ignore_index=True)

    if save_as_hdf5:
        hdf5_path = parent_dir / hdf5_filename
        full_df.to_hdf(hdf5_path, key="history", mode="w")
        print(f"Saved HDF5 to {hdf5_path}")

    return full_df


def find_failed_tasks(
    dest: Union[str, Path], keys: list, threshold_mb: float = 5.0
) -> list:
    """
    Find SLURM array tasks whose MESA run didn't produce a usable history.data.

    Generalizes the old slurm/find_failed.sh (which hardcoded a single Y/Z/alpha
    combination) to any grid: it reconstructs each task's model directory name
    directly from its LOGS/log_..._TASK_<id>.txt filename (stripping the
    'log_' prefix, '.txt' suffix, and '_TASK_<id>' suffix), so it works
    regardless of which parameters were swept or what values they took.

    A task is considered failed if it's missing its grid_TAMS/TAMS_*.mod save
    file, or if DATA/history.data is missing or smaller than threshold_mb.
    The TAMS file is what MESA writes on a genuine stop condition (save_model_
    when_terminate), so its absence is what actually distinguishes a finished
    track from one cut off mid-run -- e.g. a task that hit the SLURM --time
    limit can still have accumulated history.data well past threshold_mb
    without ever reaching TAMS, and would otherwise be missed.

    Args:
        dest: Grid run directory containing LOGS/, grid_TAMS/, and the model
            subdirectories.
        keys: Parameter labels to extract from each failed model's directory
            name (e.g. ['M', 'Y', 'Z', 'alpha'], or labels for any extra
            --param parameters), via extract_constants_from_subdir_name.
        threshold_mb: Minimum acceptable history.data size, in MB.

    Returns:
        List of dicts, one per failed task, each with keys 'task_id' (int),
        'folder' (str, the model subdirectory name), and 'params' (dict of
        the requested keys extracted from the folder name).
    """
    dest = Path(dest)
    failed = []
    for log in sorted(dest.glob("LOGS/log_*_TASK_*.txt")):
        match = re.search(r"_TASK_(\d+)$", log.stem)
        if not match:
            continue
        task_id = int(match.group(1))
        folder_name = re.sub(r"^log_", "", log.stem)
        folder_name = re.sub(r"_TASK_\d+$", "", folder_name)
        history_file = dest / folder_name / "DATA" / "history.data"
        tams_file = dest / "grid_TAMS" / f"TAMS_{folder_name}.mod"

        ok = (
            tams_file.exists()
            and history_file.exists()
            and history_file.stat().st_size >= threshold_mb * 1024 * 1024
        )
        if not ok:
            params = extract_constants_from_subdir_name(folder_name, keys)
            failed.append({"task_id": task_id, "folder": folder_name, "params": params})

    return failed


def cleanup_grid_data(parent_dir: Union[str, Path], mode: str = "none") -> None:
    """
    Remove or archive each model's DATA/ folder after combined_history.hdf5 is built.

    Refuses to do anything unless every model directory (one with a DATA/
    subfolder) has a corresponding TAMS save file in grid_TAMS/ -- this is a
    safety check against running cleanup while SLURM array jobs are still in
    progress, or have failed without producing output (see slurm/find_failed.sh).

    Args:
        parent_dir: Grid run directory containing one subdirectory per model.
        mode: 'none' (no-op, default), 'zip' (archive each DATA/ to DATA.zip
            in the same model directory, then remove DATA/), or 'delete'
            (remove DATA/ without archiving).

    Raises:
        ValueError: If mode is not one of 'none', 'zip', 'delete'.
    """
    if mode == "none":
        return
    if mode not in ("zip", "delete"):
        raise ValueError(f"Unsupported cleanup mode '{mode}'. Choose 'none', 'zip', or 'delete'.")

    parent_dir = Path(parent_dir)
    model_dirs = [d for d in parent_dir.iterdir() if d.is_dir() and (d / "DATA").is_dir()]

    tams_dir = parent_dir / "grid_TAMS"
    n_tams = len(list(tams_dir.glob("TAMS_*.mod"))) if tams_dir.is_dir() else 0

    if n_tams < len(model_dirs):
        print(
            f"Skipping cleanup: only {n_tams}/{len(model_dirs)} model directories have a "
            f"TAMS save file in grid_TAMS/. Some array jobs may still be running, or may "
            f"have failed (see slurm/find_failed.sh). Re-run with --cleanup once all jobs finish."
        )
        return

    for subdir in sorted(model_dirs):
        data_dir = subdir / "DATA"
        if mode == "zip":
            shutil.make_archive(str(data_dir), "zip", root_dir=subdir, base_dir="DATA")
        shutil.rmtree(data_dir)

    verb = "Zipped and removed" if mode == "zip" else "Removed"
    print(f"{verb} DATA/ in {len(model_dirs)} model director{'y' if len(model_dirs) == 1 else 'ies'}.")


def generate_grid(param_specs: dict, grid_type: str = "linear", num_points: int = 8) -> list:
    """
    Generate a list of parameter dictionaries for a MESA grid.

    Each parameter is one of:
      - a fixed scalar: held constant across the whole grid.
      - a `(min, max)` tuple: a continuous sweep, sampled at `num_points`
        values via linspace ('linear') or a Sobol sequence ('sobol').
      - a list of explicit values: a discrete sweep, used as-is. Combined
        with everything else via Cartesian product, e.g. a continuous mass
        sweep at 200 points combined with `initial_z=[0.014, 0.02]` yields
        400 total parameter sets (200 per Z value).

    Args:
        param_specs: Dict mapping parameter names to a fixed float value, a
            (min, max) tuple, or a list of values. Example::

                {
                    "initial_mass": (0.7, 1.2),
                    "initial_y": 0.27,
                    "initial_z": [0.014, 0.02],
                    "mixing_length_alpha": 2.0,
                }

        grid_type: 'linear' (Cartesian product of linspace grids) or 'sobol'
            (quasi-random Sobol sequence; num_points must be a power of 2).
            Only applies to (min, max)-tuple parameters; discrete-list
            parameters are always used as-is.
        num_points: Points per continuous-sweep dimension (linear) or total
            continuous samples (sobol).

    Returns:
        List of parameter dicts, one per grid point.
    """
    continuous_keys = [k for k, v in param_specs.items() if isinstance(v, tuple)]
    discrete_keys = [k for k, v in param_specs.items() if isinstance(v, list)]
    fixed_params = {k: v for k, v in param_specs.items() if not isinstance(v, (tuple, list))}

    if not continuous_keys:
        continuous_dicts = [{}]
    elif grid_type == "linear":
        sweep_values = [np.linspace(*param_specs[k], num_points) for k in continuous_keys]
        continuous_dicts = [dict(zip(continuous_keys, combo)) for combo in itertools.product(*sweep_values)]
    elif grid_type == "sobol":
        m = np.log2(num_points)
        if not m.is_integer():
            raise ValueError("For Sobol sampling, num_points must be a power of 2.")
        sobol_vals = Sobol(d=len(continuous_keys), scramble=True).random_base2(m=int(m))
        sweep_values = [
            param_specs[k][0] + sobol_vals[:, i] * (param_specs[k][1] - param_specs[k][0])
            for i, k in enumerate(continuous_keys)
        ]
        continuous_dicts = [dict(zip(continuous_keys, combo)) for combo in zip(*sweep_values)]
    else:
        raise ValueError(f"Unsupported grid_type '{grid_type}'. Choose 'linear' or 'sobol'.")

    if discrete_keys:
        discrete_combos = itertools.product(*(param_specs[k] for k in discrete_keys))
        discrete_dicts = [dict(zip(discrete_keys, combo)) for combo in discrete_combos]
    else:
        discrete_dicts = [{}]

    return [{**c, **d, **fixed_params} for c in continuous_dicts for d in discrete_dicts]


def update_inlist(
    template_text: str, params: dict, log_dir: str, param_registry: Optional[dict] = None
) -> str:
    """
    Substitute parameter values into a MESA inlist template.

    Handles every key in params that's also in param_registry (PARAM_FORMAT's
    four built-in parameters by default, plus any extra parameters added via
    --param). Also sets log_directory to 'DATA' and save_model_filename to
    TAMS_<log_dir>.mod, matching the run directory name.

    Each value is written with at least as many decimals as param_registry's
    default fmt, and more if needed to represent the value exactly (see
    _exact_fmt) -- e.g. an initial_z of 0.000379 is written in full rather
    than rounded to 0.0004 under the registry's '.4f' default. This is
    independent of any directory-naming param_formats (see compute_param_formats),
    which may use fewer decimals than needed for exactness, e.g. only enough
    to keep sibling directory names distinct.

    Args:
        template_text: Raw text of the inlist_template file.
        params: Parameter dict (keys matching param_registry entries).
        log_dir: Run directory name (from make_run_dir_name), used to name the
            saved TAMS model file.
        param_registry: Dict like PARAM_FORMAT (label/fmt/inlist_key per key).
            Defaults to PARAM_FORMAT; pass an extended copy to substitute extra
            (non-built-in) parameters too.

    Returns:
        Modified inlist text.
    """
    registry = param_registry or PARAM_FORMAT
    for key, val in params.items():
        if key not in registry:
            continue
        inlist_key = registry[key]["inlist_key"]
        fmt = _exact_fmt(val, registry[key]["fmt"])
        template_text = re.sub(
            rf"{re.escape(inlist_key)}\s*=\s*[\d.eEdD+-]+",
            f"{inlist_key} = {val:{fmt}}",
            template_text,
        )
        # Zbase in &kap must match initial_z when using Type2 opacities.
        if key == "initial_z":
            template_text = re.sub(
                r"Zbase\s*=\s*[\d.eEdD+-]+", f"Zbase = {val:{fmt}}", template_text
            )

    template_text = re.sub(r"log_directory\s*=\s*'.*?'", "log_directory = 'DATA'", template_text)

    save_fname = f"TAMS_{log_dir}.mod"
    template_text = re.sub(
        r"save_model_filename\s*=\s*['\"].*?\.mod['\"]",
        f"save_model_filename = '{save_fname}'",
        template_text,
    )
    return template_text


def find_latest_photo(run_dir: Path) -> Optional[str]:
    """
    Return the filename of the most recent MESA photo in run_dir/photos/, or None if none exist.

    Photos are sorted by modification time so this works regardless of MESA's
    naming convention (x00500, x500, etc.).
    """
    photos_dir = run_dir / "photos"
    if not photos_dir.is_dir():
        return None
    photos = [p for p in photos_dir.iterdir() if p.is_file()]
    if not photos:
        return None
    return max(photos, key=lambda p: p.stat().st_mtime).name


def collect_profile_files(run_dir: Path, mesa_dir: Path, log_dir_name: str) -> None:
    """
    Copy MESA profile output from run_dir/DATA into grid_profiles/<log_dir_name>/.

    Picks up every profile*.data file, its matching profile*.data.GYRE pulse
    file (written when write_pulse_data_with_profile = .true.), and
    profiles.index -- so models with multiple saved profiles (profile_interval
    > 0) get all of them, not just the one at TAMS. No-op if no profile files
    were written.

    Args:
        run_dir: The model's run directory (contains DATA/).
        mesa_dir: Root grid directory (destination for grid_profiles/).
        log_dir_name: Run directory name, used as the grid_profiles/ subdirectory.
    """
    data_dir = run_dir / "DATA"
    profile_files = sorted(data_dir.glob("profile*.data*"))
    index_file = data_dir / "profiles.index"
    if index_file.exists():
        profile_files.append(index_file)
    if not profile_files:
        return

    profiles_out_dir = mesa_dir / "grid_profiles" / log_dir_name
    profiles_out_dir.mkdir(parents=True, exist_ok=True)
    for f in profile_files:
        shutil.copy(f, profiles_out_dir / f.name)


def run_mesa_model(template_file: Path, mesa_dir: Path, params: dict, log_path: Path,
                    param_formats: Optional[dict] = None, param_registry: Optional[dict] = None,
                    restart_photos: bool = False) -> None:
    """
    Set up a run directory for a single MESA model and execute it.

    Creates a subdirectory named by the parameter values under mesa_dir, writes
    the updated inlist, copies MESA runtime files, runs MESA, then archives the
    output TAMS model, inlist, and any saved profiles (see collect_profile_files).

    Args:
        template_file: Path to the inlist_template file.
        mesa_dir: Root directory of the grid run (must contain rn, star, inlist, etc.).
        params: Parameter dict with keys matching param_registry entries
            (initial_mass, initial_y, initial_z, mixing_length_alpha, plus
            any extra parameters added via --param).
        log_path: File path where MESA stdout/stderr will be written.
        param_formats: Optional per-key directory-naming format overrides (see
            compute_param_formats). Only affects the run directory name;
            update_inlist always writes each value with enough decimals to
            represent it exactly, regardless of this argument.
        param_registry: Dict like PARAM_FORMAT (label/fmt/inlist_key per key).
            Defaults to PARAM_FORMAT; pass an extended copy to include extra
            (non-built-in) parameters.
        restart_photos: If True, check for an existing MESA photo in run_dir/photos/
            and restart from the most recent one (via ./re) rather than starting
            from scratch. Falls back to ./rn if no photos are found or ./re is
            unavailable. The log file is opened in append mode when restarting.
    """
    log_dir_name = make_run_dir_name(params, param_formats, param_registry)
    run_dir = mesa_dir / log_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "DATA").mkdir(exist_ok=True)

    with open(template_file, "r") as f:
        updated_text = update_inlist(f.read(), params, log_dir_name, param_registry)

    inlist_path = run_dir / "inlist_project"
    with open(inlist_path, "w") as f:
        f.write(updated_text)

    for fname in ["rn", "re", "star", "inlist", "inlist_pgstar", "profile_columns.list", "history_columns.list"]:
        src = mesa_dir / fname
        if src.exists():
            shutil.copy(src, run_dir / fname)
        elif fname in ("rn", "star"):
            raise FileNotFoundError(f"Required MESA file '{fname}' not found in {mesa_dir}")

    photo = find_latest_photo(run_dir) if restart_photos else None
    if restart_photos and photo is None:
        print(f"  No photos found for {run_dir.name}; starting from scratch.", flush=True)
    if photo and not (run_dir / "re").exists():
        print(f"  WARNING: photos found for {run_dir.name} but ./re not found; starting from scratch.", flush=True)
        photo = None

    cmd = ["./re", photo] if photo else ["./rn"]
    log_mode = "a" if photo else "w"
    if photo:
        print(f"  Restarting {run_dir.name} from photo {photo}.", flush=True)

    with open(log_path, log_mode) as log_file:
        try:
            subprocess.run(
                cmd, cwd=run_dir, stdout=log_file, stderr=subprocess.STDOUT, text=True, check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"MESA run failed for {run_dir} (exit code {e.returncode})")
        except Exception as e:
            print(f"Unexpected error running MESA in {run_dir}: {e}")

    save_fname = f"TAMS_{log_dir_name}.mod"
    src = run_dir / save_fname
    grid_tams = mesa_dir / "grid_TAMS"
    grid_tams.mkdir(exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(grid_tams / save_fname))

    inlist_out_dir = mesa_dir / "grid_inlists"
    inlist_out_dir.mkdir(exist_ok=True)
    shutil.copy(inlist_path, inlist_out_dir / f"inlist_{log_dir_name}")

    collect_profile_files(run_dir, mesa_dir, log_dir_name)


def task_wrapper(args: tuple) -> None:
    """
    Unpack args and run a single MESA model; for use with ProcessPoolExecutor.

    Args:
        args: Tuple of (params, template_file, mesa_dir, param_formats, param_registry, restart_photos).
    """
    params, template_file, mesa_dir, param_formats, param_registry, restart_photos = args
    log_dir_name = make_run_dir_name(params, param_formats, param_registry)
    logs_dir = mesa_dir / "LOGS"
    logs_dir.mkdir(exist_ok=True)
    run_mesa_model(template_file, mesa_dir, params, logs_dir / f"log_{log_dir_name}.txt",
                    param_formats, param_registry, restart_photos)


def run_grid(
    param_ranges: dict,
    grid_type: str = "linear",
    num_points: int = 8,
    max_workers: int = 2,
    param_registry: Optional[dict] = None,
    restart_photos: bool = False,
) -> None:
    """
    Build MESA, generate the parameter grid, and run all models in parallel.

    Must be called from the grid run directory (the one containing inlist_template, rn, etc.).

    Args:
        param_ranges: Parameter spec dict passed to generate_grid.
        grid_type: 'linear' or 'sobol'.
        num_points: Grid points per swept dimension (linear) or total samples (sobol).
        max_workers: Parallel MESA processes. Use 1 for serial/debug mode.
        param_registry: Dict like PARAM_FORMAT (label/fmt/inlist_key per key).
            Defaults to PARAM_FORMAT; pass an extended copy to include extra
            (non-built-in) parameters added via --param.
        restart_photos: If True, restart each model from its latest MESA photo
            if one exists (see run_mesa_model). Falls back to a fresh run when
            no photos are found.
    """
    this_grid_dir = Path.cwd()
    print("Building MESA...", flush=True)
    subprocess.run(["./mk"], cwd=this_grid_dir, check=True)
    print(f"Grid directory: {this_grid_dir}")

    (this_grid_dir / "LOGS").mkdir(exist_ok=True)

    param_dicts = generate_grid(param_ranges, grid_type=grid_type, num_points=num_points)
    print(f"Running {len(param_dicts)} models.", flush=True)

    param_formats = compute_param_formats(param_ranges, grid_type=grid_type, num_points=num_points,
                                           param_registry=param_registry)
    write_grid_notes(param_ranges, param_formats, grid_type, num_points, this_grid_dir / "notes.txt",
                      param_registry=param_registry)

    args_list = [
        (p, this_grid_dir / "inlist_template", this_grid_dir, param_formats, param_registry, restart_photos)
        for p in param_dicts
    ]

    if max_workers == 1:
        for a in args_list:
            task_wrapper(a)
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            executor.map(task_wrapper, args_list)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a MESA stellar evolution parameter grid.")
    parser.add_argument("--min_mass", type=float, default=0.7,
                        help="Used only if --mass is not given.")
    parser.add_argument("--max_mass", type=float, default=None,
                        help="If omitted (and --mass is not given), runs a single model "
                             "at min_mass.")
    parser.add_argument("--mass", type=str, nargs="+", default=None, metavar="SPEC",
                        help="Initial mass spec, overriding --min_mass/--max_mass: "
                             + VALUE_SPEC_HELP)
    parser.add_argument("--initial_Z", type=str, nargs="+", default=["0.02"], metavar="SPEC",
                        help="Initial metallicity spec: " + VALUE_SPEC_HELP)
    parser.add_argument("--initial_Y", type=str, nargs="+", default=["0.27"], metavar="SPEC",
                        help="Initial helium abundance spec: " + VALUE_SPEC_HELP)
    parser.add_argument("--alpha_MLT", type=str, nargs="+", default=["2.0"], metavar="SPEC",
                        help="Mixing-length alpha spec: " + VALUE_SPEC_HELP)
    parser.add_argument("--param", action="append", default=[], metavar="KEY=SPEC",
                        help="Extra inlist parameter to set or sweep, as KEY=SPEC where "
                             "SPEC is: " + VALUE_SPEC_HELP + " KEY must match a parameter "
                             "settable in inlist_template (case-insensitive); if it doesn't, "
                             "an error lists close matches and all available parameters. "
                             "Repeatable.")
    parser.add_argument("--dry_run", action="store_true",
                        help="Print the grid plan (parameters, model count, disk estimate, "
                             "example filenames) and exit without building or running MESA.")
    parser.add_argument("--avg_data_mb", type=float, default=20.0,
                        help="Estimated MESA output size (MB) per model, for --dry_run's "
                             "disk usage estimate (default: 20).")
    parser.add_argument("--grid_type", choices=["linear", "sobol"], default="linear")
    parser.add_argument("--num_points", type=int, default=8)
    parser.add_argument("--max_workers", type=int, default=1)
    parser.add_argument("--restart_photos", action="store_true",
                        help="Restart each run from the most recent MESA photo in its "
                             "photos/ directory rather than starting from scratch. Falls "
                             "back to a fresh run when no photos are found or ./re is "
                             "unavailable. Use this to resume timed-out SLURM jobs.")
    parser.add_argument("--task_id", type=int, default=None,
                        help="SLURM array task index: runs only this one parameter set.")
    args = parser.parse_args()

    if args.mass is not None:
        initial_mass_spec = coerce_cli_values(args.mass)
    else:
        if args.max_mass is None:
            args.max_mass = args.min_mass
            args.num_points = 1
        initial_mass_spec = (args.min_mass, args.max_mass)

    param_ranges = {
        "initial_mass": initial_mass_spec,
        "initial_y": coerce_cli_values(args.initial_Y),
        "initial_z": coerce_cli_values(args.initial_Z),
        "mixing_length_alpha": coerce_cli_values(args.alpha_MLT),
    }

    param_registry = dict(PARAM_FORMAT)
    if args.param:
        inlist_text = (Path.cwd() / "inlist_template").read_text()
        extra_specs, extra_registry = parse_extra_params(args.param, inlist_text)
        param_ranges.update(extra_specs)
        param_registry.update(extra_registry)

    if args.dry_run:
        print_grid_dry_run(param_ranges, grid_type=args.grid_type, num_points=args.num_points,
                            avg_data_mb=args.avg_data_mb, param_registry=param_registry)
        sys.exit(0)

    param_dicts = generate_grid(param_ranges, grid_type=args.grid_type, num_points=args.num_points)
    param_formats = compute_param_formats(param_ranges, grid_type=args.grid_type, num_points=args.num_points,
                                           param_registry=param_registry)

    if args.task_id is not None:
        idx = args.task_id
        if idx < 0 or idx >= len(param_dicts):
            raise IndexError(f"task_id {idx} out of range for {len(param_dicts)} parameter sets.")

        print(f"[SLURM ARRAY] Running task {idx} of {len(param_dicts)}")

        this_grid_dir = Path.cwd()
        params = param_dicts[idx]
        logs_dir = this_grid_dir / "LOGS"
        logs_dir.mkdir(exist_ok=True)

        if idx == 0:
            write_grid_notes(param_ranges, param_formats, args.grid_type, args.num_points,
                              this_grid_dir / "notes.txt", param_registry=param_registry)

        log_dir_name = make_run_dir_name(params, param_formats, param_registry) + f"_TASK_{idx}"
        run_mesa_model(
            this_grid_dir / "inlist_template",
            this_grid_dir,
            params,
            logs_dir / f"log_{log_dir_name}.txt",
            param_formats,
            param_registry,
            args.restart_photos,
        )

    else:
        run_grid(
            param_ranges=param_ranges,
            grid_type=args.grid_type,
            num_points=args.num_points,
            max_workers=args.max_workers,
            param_registry=param_registry,
            restart_photos=args.restart_photos,
        )

    print(f"Done at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.")
