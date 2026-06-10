import shutil
import numpy as np
from pathlib import Path
import re
import argparse
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


def compute_param_formats(
    param_specs: dict,
    grid_type: str = "linear",
    num_points: int = 8,
    min_decimals: int = 1,
    max_decimals: int = 10,
) -> dict:
    """
    Choose the minimum decimal precision needed for each parameter's directory label.

    For a swept parameter (a `(min, max)` tuple in param_specs), this is the
    fewest decimals such that every grid value in that range produces a unique
    formatted string, given the spacing implied by `num_points`. For a fixed
    (scalar) parameter, it's the fewest decimals that represent its value exactly.

    For 'sobol' grids the actual sampled values aren't reproducible across
    processes (Sobol scrambling isn't seeded), so the same evenly-spaced
    `num_points` values used for 'linear' grids are used here for format
    selection only — this doesn't affect the actual sampled points, just how
    many decimals are used to label them.

    Args:
        param_specs: Dict passed to generate_grid (scalars or (min, max) tuples).
        grid_type: 'linear' or 'sobol'.
        num_points: Points per swept dimension (linear) or total samples (sobol).
        min_decimals: Smallest number of decimals to use for any parameter.
        max_decimals: Largest number of decimals to consider before giving up.

    Returns:
        Dict mapping each PARAM_FORMAT key present in param_specs to a format
        spec string (e.g. '.3f'), for use with make_run_dir_name.
    """
    formats = {}
    for key, spec in param_specs.items():
        if key not in PARAM_FORMAT:
            continue
        if isinstance(spec, tuple):
            lo, hi = spec
            if num_points <= 1 or lo == hi:
                d = _min_decimals_for_value(lo, min_decimals, max_decimals)
            else:
                values = np.linspace(lo, hi, num_points)
                d = _min_decimals_for_unique(values, min_decimals, max_decimals)
        else:
            d = _min_decimals_for_value(spec, min_decimals, max_decimals)
        formats[key] = f".{d}f"
    return formats


def make_run_dir_name(params: dict, param_formats: Optional[dict] = None) -> str:
    """
    Build the run directory name from a parameter dict.

    Uses the canonical order and labels in PARAM_FORMAT. Only parameters
    present in both params and PARAM_FORMAT are included, so adding a new
    swept parameter to PARAM_FORMAT automatically includes it in all
    directory and log file names without any other code changes.

    Args:
        params: Parameter dict (keys matching PARAM_FORMAT entries).
        param_formats: Optional per-key format overrides (e.g. from
            compute_param_formats), falling back to PARAM_FORMAT's defaults
            for any key not present.

    Returns:
        Directory name string, e.g. 'M_0.700000_Y_0.270_Z_0.0200_alpha_2.00'.
    """
    parts = []
    for key, spec in PARAM_FORMAT.items():
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
) -> None:
    """
    Write a notes.txt summarizing a grid's constant and swept parameters.

    Records, for each PARAM_FORMAT parameter: whether it's held constant or
    swept over a range, its value(s), the spacing between grid points (for
    swept parameters), and the directory-naming format chosen for it.

    Args:
        param_specs: Dict passed to generate_grid (scalars or (min, max) tuples).
        param_formats: Dict from compute_param_formats mapping each key to a
            format spec (e.g. '.3f').
        grid_type: 'linear' or 'sobol'.
        num_points: Points per swept dimension (linear) or total samples (sobol).
        out_path: File path to write the notes to.
    """
    fixed = {k: v for k, v in param_specs.items() if k in PARAM_FORMAT and not isinstance(v, tuple)}
    swept = {k: v for k, v in param_specs.items() if k in PARAM_FORMAT and isinstance(v, tuple)}

    lines = [
        f"Grid generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Grid type: {grid_type}, num_points: {num_points}",
        "",
        "Constant parameters:",
    ]
    if not fixed:
        lines.append("  (none)")
    for key, val in fixed.items():
        spec = PARAM_FORMAT[key]
        fmt = param_formats.get(key, spec["fmt"])
        lines.append(f"  {key} ({spec['label']}) = {val:{fmt}}")

    lines.append("")
    lines.append("Swept parameter(s):")
    if not swept:
        lines.append("  (none)")
    for key, (lo, hi) in swept.items():
        spec = PARAM_FORMAT[key]
        fmt = param_formats.get(key, spec["fmt"])
        line = f"  {key} ({spec['label']}): {lo} to {hi}, {num_points} points"
        if num_points > 1 and lo != hi:
            spacing = (hi - lo) / (num_points - 1)
            line += f", spacing = {spacing:{fmt}}"
        line += f", directory format = '{fmt}'"
        lines.append(line)

    lines.append("")
    lines.append(
        "Note: 'M' (initial_mass) in directory/file names is always the "
        "*initial* mass at the start of the run, even though mass may "
        "decrease over the evolution due to mass loss."
    )

    Path(out_path).write_text("\n".join(lines) + "\n")


def extract_constants_from_subdir_name(name: str, keys: list) -> dict:
    """
    Extract parameter values encoded in a subdirectory name.

    Expects alternating key/value tokens separated by underscores,
    e.g. 'M_1.114_Y_0.270_Z_0.020_alpha_2.00'.

    Args:
        name: Subdirectory name string.
        keys: Parameter keys to extract (e.g. ['M', 'Y', 'Z', 'alpha']).

    Returns:
        Dict mapping each found key to its float value.
    """
    constants = {}
    parts = name.split('_')
    for i in range(0, len(parts) - 1, 2):
        key = parts[i]
        if key in keys:
            try:
                constants[key] = float(parts[i + 1])
            except ValueError:
                print(f"Warning: could not convert value '{parts[i + 1]}' for key '{key}' in '{name}'")
    for key in keys:
        if key not in constants:
            print(f"Warning: key '{key}' not found in directory name: '{name}'")
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


def extract_mass(subdir_name: str) -> float:
    """Return the mass value encoded in a subdirectory name (e.g. 'M_1.114_...' → 1.114)."""
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


def generate_grid(param_specs: dict, grid_type: str = "linear", num_points: int = 8) -> list:
    """
    Generate a list of parameter dictionaries for a MESA grid.

    Parameters are either fixed (scalar) or swept (tuple of (min, max)).
    Sweep combinations are formed as a Cartesian product (linear) or Sobol sequence.

    Args:
        param_specs: Dict mapping parameter names to a fixed float value or a
            (min, max) tuple to be swept. Example::

                {
                    "initial_mass": (0.7, 1.2),
                    "initial_y": 0.27,
                    "initial_z": 0.02,
                    "mixing_length_alpha": 2.0,
                }

        grid_type: 'linear' (Cartesian product of linspace grids) or 'sobol'
            (quasi-random Sobol sequence; num_points must be a power of 2).
        num_points: Points per swept dimension (linear) or total samples (sobol).

    Returns:
        List of parameter dicts, one per grid point.
    """
    sweep_keys = [k for k, v in param_specs.items() if isinstance(v, tuple)]
    fixed_params = {k: v for k, v in param_specs.items() if not isinstance(v, tuple)}

    if not sweep_keys:
        return [fixed_params]

    if grid_type == "linear":
        sweep_values = [np.linspace(*param_specs[k], num_points) for k in sweep_keys]
        combos = list(itertools.product(*sweep_values))
    elif grid_type == "sobol":
        m = np.log2(num_points)
        if not m.is_integer():
            raise ValueError("For Sobol sampling, num_points must be a power of 2.")
        sobol_vals = Sobol(d=len(sweep_keys), scramble=True).random_base2(m=int(m))
        sweep_values = [
            param_specs[k][0] + sobol_vals[:, i] * (param_specs[k][1] - param_specs[k][0])
            for i, k in enumerate(sweep_keys)
        ]
        combos = list(zip(*sweep_values))
    else:
        raise ValueError(f"Unsupported grid_type '{grid_type}'. Choose 'linear' or 'sobol'.")

    return [{**dict(zip(sweep_keys, combo)), **fixed_params} for combo in combos]


def update_inlist(template_text: str, params: dict, log_dir: str) -> str:
    """
    Substitute parameter values into a MESA inlist template.

    Handles keys: initial_mass, initial_y, initial_z, mixing_length_alpha.
    Also sets log_directory to 'DATA' and save_model_filename to TAMS_<mass>.mod.

    Args:
        template_text: Raw text of the inlist_template file.
        params: Parameter dict (keys as above).
        log_dir: Unused; kept for API compatibility. log_directory is always set to 'DATA'.

    Returns:
        Modified inlist text.
    """
    for key, val in params.items():
        if key not in PARAM_FORMAT:
            continue
        inlist_key = PARAM_FORMAT[key]["inlist_key"]
        fmt = PARAM_FORMAT[key]["fmt"]
        template_text = re.sub(
            rf"{inlist_key}\s*=\s*[\d.eE+-]+",
            f"{inlist_key} = {val:{fmt}}",
            template_text,
        )
        # Zbase in &kap must match initial_z when using Type2 opacities.
        if key == "initial_z":
            template_text = re.sub(
                r"Zbase\s*=\s*[\d.eE+-]+", f"Zbase = {val:{fmt}}", template_text
            )

    template_text = re.sub(r"log_directory\s*=\s*'.*?'", "log_directory = 'DATA'", template_text)

    save_fname = f"TAMS_{params['initial_mass']:.6f}.mod"
    template_text = re.sub(
        r"save_model_filename\s*=\s*['\"].*?\.mod['\"]",
        f"save_model_filename = '{save_fname}'",
        template_text,
    )
    return template_text


def run_mesa_model(template_file: Path, mesa_dir: Path, params: dict, log_path: Path,
                    param_formats: Optional[dict] = None) -> None:
    """
    Set up a run directory for a single MESA model and execute it.

    Creates a subdirectory named by the parameter values under mesa_dir, writes
    the updated inlist, copies MESA runtime files, runs MESA, then archives the
    output TAMS model and inlist.

    Args:
        template_file: Path to the inlist_template file.
        mesa_dir: Root directory of the grid run (must contain rn, star, inlist, etc.).
        params: Parameter dict with keys initial_mass, initial_y, initial_z, mixing_length_alpha.
        log_path: File path where MESA stdout/stderr will be written.
        param_formats: Optional per-key directory-naming format overrides (see
            compute_param_formats). Does not affect the values written into the
            inlist itself.
    """
    log_dir_name = make_run_dir_name(params, param_formats)
    run_dir = mesa_dir / log_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "DATA").mkdir(exist_ok=True)

    with open(template_file, "r") as f:
        updated_text = update_inlist(f.read(), params, log_dir_name)

    inlist_path = run_dir / "inlist_project"
    with open(inlist_path, "w") as f:
        f.write(updated_text)

    for fname in ["rn", "star", "inlist", "inlist_pgstar", "profile_columns.list", "history_columns.list"]:
        src = mesa_dir / fname
        if src.exists():
            shutil.copy(src, run_dir / fname)
        elif fname in ("rn", "star"):
            raise FileNotFoundError(f"Required MESA file '{fname}' not found in {mesa_dir}")

    with open(log_path, "w") as log_file:
        try:
            subprocess.run(
                ["./rn"], cwd=run_dir, stdout=log_file, stderr=subprocess.STDOUT, text=True, check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"MESA run failed for {run_dir} (exit code {e.returncode})")
        except Exception as e:
            print(f"Unexpected error running MESA in {run_dir}: {e}")

    save_fname = f"TAMS_{params['initial_mass']:.6f}.mod"
    src = run_dir / save_fname
    grid_tams = mesa_dir / "grid_TAMS"
    grid_tams.mkdir(exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(grid_tams / save_fname))

    inlist_out_dir = mesa_dir / "grid_inlists"
    inlist_out_dir.mkdir(exist_ok=True)
    shutil.copy(inlist_path, inlist_out_dir / f"inlist_{log_dir_name}")


def task_wrapper(args: tuple) -> None:
    """
    Unpack args and run a single MESA model; for use with ProcessPoolExecutor.

    Args:
        args: Tuple of (params, template_file, mesa_dir, param_formats).
    """
    params, template_file, mesa_dir, param_formats = args
    log_dir_name = make_run_dir_name(params, param_formats)
    logs_dir = mesa_dir / "LOGS"
    logs_dir.mkdir(exist_ok=True)
    run_mesa_model(template_file, mesa_dir, params, logs_dir / f"log_{log_dir_name}.txt", param_formats)


def run_grid(
    param_ranges: dict,
    grid_type: str = "linear",
    num_points: int = 8,
    max_workers: int = 2,
) -> None:
    """
    Build MESA, generate the parameter grid, and run all models in parallel.

    Must be called from the grid run directory (the one containing inlist_template, rn, etc.).

    Args:
        param_ranges: Parameter spec dict passed to generate_grid.
        grid_type: 'linear' or 'sobol'.
        num_points: Grid points per swept dimension (linear) or total samples (sobol).
        max_workers: Parallel MESA processes. Use 1 for serial/debug mode.
    """
    this_grid_dir = Path.cwd()
    print("Building MESA...", flush=True)
    subprocess.run(["./mk"], cwd=this_grid_dir, check=True)
    print(f"Grid directory: {this_grid_dir}")

    (this_grid_dir / "LOGS").mkdir(exist_ok=True)

    param_dicts = generate_grid(param_ranges, grid_type=grid_type, num_points=num_points)
    print(f"Running {len(param_dicts)} models.", flush=True)

    param_formats = compute_param_formats(param_ranges, grid_type=grid_type, num_points=num_points)
    write_grid_notes(param_ranges, param_formats, grid_type, num_points, this_grid_dir / "notes.txt")

    args_list = [(p, this_grid_dir / "inlist_template", this_grid_dir, param_formats) for p in param_dicts]

    if max_workers == 1:
        for a in args_list:
            task_wrapper(a)
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            executor.map(task_wrapper, args_list)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a MESA stellar evolution parameter grid.")
    parser.add_argument("--min_mass", type=float, default=0.7)
    parser.add_argument("--max_mass", type=float, default=None,
                        help="If omitted, runs a single model at min_mass.")
    parser.add_argument("--initial_Z", type=float, default=0.02)
    parser.add_argument("--initial_Y", type=float, default=0.27)
    parser.add_argument("--alpha_MLT", type=float, default=2.0)
    parser.add_argument("--grid_type", choices=["linear", "sobol"], default="linear")
    parser.add_argument("--num_points", type=int, default=8)
    parser.add_argument("--max_workers", type=int, default=1)
    parser.add_argument("--task_id", type=int, default=None,
                        help="SLURM array task index: runs only this one parameter set.")
    args = parser.parse_args()

    if args.max_mass is None:
        args.max_mass = args.min_mass
        args.num_points = 1

    param_ranges = {
        "initial_mass": (args.min_mass, args.max_mass),
        "initial_y": args.initial_Y,
        "initial_z": args.initial_Z,
        "mixing_length_alpha": args.alpha_MLT,
    }

    param_dicts = generate_grid(param_ranges, grid_type=args.grid_type, num_points=args.num_points)
    param_formats = compute_param_formats(param_ranges, grid_type=args.grid_type, num_points=args.num_points)

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
                              this_grid_dir / "notes.txt")

        log_dir_name = make_run_dir_name(params, param_formats) + f"_TASK_{idx}"
        run_mesa_model(
            this_grid_dir / "inlist_template",
            this_grid_dir,
            params,
            logs_dir / f"log_{log_dir_name}.txt",
            param_formats,
        )

    else:
        run_grid(
            param_ranges=param_ranges,
            grid_type=args.grid_type,
            num_points=args.num_points,
            max_workers=args.max_workers,
        )

    print(f"Done at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.")
