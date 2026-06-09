import os
import shutil
import numpy as np
from pathlib import Path
import re
import argparse
import itertools
import subprocess
import pandas as pd 
from scipy.stats.qmc import Sobol
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Union, List, Optional
import datetime

def extract_constants_from_subdir_name(name: str, keys: list) -> dict:
    """
    Extract constant values from a subdirectory name using the format: key_val (e.g., M_1.114).

    Args:
        name: Subdirectory name (e.g., 'M_1.114_Y_0.270_Z_0.020').
        keys: List of constant keys to extract (e.g., ['M', 'Y', 'Z', 'alpha']).

    Returns:
        dict: Mapping of key to float value.
    """
    constants = {}
    parts = name.split('_')
    for i in range(0, len(parts) - 1, 2):
        key = parts[i]
        val_str = parts[i + 1]
        if key in keys:
            try:
                constants[key] = float(val_str)
            except ValueError:
                print(f"Warning: could not convert value '{val_str}' for key '{key}' in '{name}'")
    for key in keys:
        if key not in constants:
            print(f"Warning: key '{key}' not found in directory name: '{name}'")
    return constants


def extract_constant_from_profile(profile_path: Union[str, Path], key: str) -> float:
    """
    Extract a scalar constant (e.g. initial_z) from the metadata block of a MESA profile.data file.

    Args:
        profile_path: Path to profile.data file.
        key: The name of the constant to extract (e.g. 'initial_z').

    Returns:
        float: The value associated with the requested key.

    Raises:
        ValueError: If the key is not found or value is missing.
    """
    df = pd.read_csv(profile_path, header=None, sep='\s+', comment="#", nrows=10)

    for i in range(len(df) - 1):
        if key in df.iloc[i].values:
            col_idx = list(df.iloc[i]).index(key)
            value = df.iloc[i + 1, col_idx]
            try:
                return float(value)
            except Exception as e:
                raise ValueError(f"Failed to parse value for '{key}' from {profile_path}: {e}")

    raise ValueError(f"Key '{key}' not found in {profile_path}")

def extract_mass(subdir_name):
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
    Load history.data files and enrich them with constant values from one profile.data file per model.

    Args:
        parent_dir: Directory containing star subdirectories.
        history_filename: Name of the MESA history file in each subdir.
        profile_filename_glob: Glob pattern to find a single profile file (e.g., 'profile*.data').
        use_subdir_as_track: Use subdir name as track ID if True.
        skiprows_history: Header rows to skip in history file.
        constant_columns: List of constant names to extract from profile.data (e.g. ['initial_y','initial_z']).
        save_as_hdf5: Save output as HDF5 file.
        hdf5_filename: Name of HDF5 file to save in parent_dir.

    Returns:
        Combined DataFrame.
    """

    parent_dir = Path(parent_dir)
    #dfs = []
    subdirs = sorted([d for d in parent_dir.iterdir() if d.is_dir()])
    total = len(subdirs)

    if hdf5_filename is None:
        raise ValueError("hdf5_filename must be set when save_as_hdf5=True")

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
                        profile_path = profile_files[0]
                        for key in constant_columns:
                            try:
                                constants[key] = extract_constant_from_profile(profile_path, key)
                            except Exception as e:
                                print(f"Warning: could not extract '{key}' from {profile_path}: {e}")

                for k, v in constants.items():
                    hist_df[k] = v

                track_label = subdir.name if use_subdir_as_track else i
                hist_df["Track"] = track_label

                if preview_df is None and return_preview_rows > 0:
                    preview_df = hist_df.head(return_preview_rows).copy()

                # IMPORTANT: appendable table format
                store.append(hdf5_key, hist_df, format="table") #, data_columns=True)
                appended += 1
                if appended % 200 == 0 or appended == total:
                    print(f"Appended {appended}/{total} files.")

                wrote_any = True
                n_total += len(hist_df)

            except Exception as e:
                print(f"Error in {subdir}: {e}")

    if wrote_any:
        print(f"Saved merged data to {hdf5_path} (key='{hdf5_key}'), rows={n_total}")
    else:
        print("No history files found; nothing written.")

    return preview_df if preview_df is not None else pd.DataFrame()

    """

    parent_dir = Path(parent_dir)
    dfs = []
    subdirs = sorted([d for d in parent_dir.iterdir() if d.is_dir()])

    for i, subdir in enumerate(subdirs):
        hist_path = subdir / "DATA" / history_filename
        print("hist_path:", hist_path)
        profile_files = list(subdir.glob(profile_filename_glob))
        if not hist_path.exists():
            continue

        try:
            hist_df = pd.read_csv(hist_path, sep='\s+', comment="#", skiprows=skiprows_history)

            constants = {}
            if constant_columns:
                if extract_constants_from_dirname:
                    constants = extract_constants_from_subdir_name(subdir.name, constant_columns)
                elif profile_files:
                    profile_path = profile_files[0]
                    for key in constant_columns:
                        try:
                            constants[key] = extract_constant_from_profile(profile_path, key)
                        except Exception as e:
                            print(f"Warning: could not extract '{key}' from {profile_path}: {e}")

            for k, v in constants.items():
                hist_df[k] = v

            track_label = subdir.name if use_subdir_as_track else i
            hist_df["Track"] = track_label

            dfs.append(hist_df)

        except Exception as e:
            print(f"Error in {subdir}: {e}")

    full_df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    if save_as_hdf5 and not full_df.empty:
        hdf5_path = parent_dir / hdf5_filename
        full_df.to_hdf(hdf5_path, key="history", mode="w")
        print(f"Saved merged data to {hdf5_path}")

    return full_df
    """

def load_mesa_histories_from_subdirs(
    parent_dir: Union[str, Path],
    history_filename: str = "history.data",
    use_subdir_as_track: bool = False,
    skiprows: int = 5,
    save_as_hdf5: bool = False,
    hdf5_filename: Optional[str] = "grid_history.hdf5"
) -> pd.DataFrame:
    """
    Load all MESA history files from subdirectories and combine into a single DataFrame,
    adding a 'track' column per star model. 

    Args:
        parent_dir: Path to the top-level directory containing subdirectories for each star.
        history_filename: Name of the history file in each subdir (default: 'history.data').
        use_subdir_as_track: If True, uses the subdirectory name as the track label.
                             If False, assigns track as integer index.
        skiprows: Number of header lines to skip (default = 5 for MESA).
        save_as_hdf5: If True, saves combined dataframe as HDF5 file.
        hdf5_filename: Name of HDF5 file to save (saved in parent_dir).

    Returns:
        pd.DataFrame: Combined DataFrame with a 'track' column identifying each star.
    """
    parent_dir = Path(parent_dir)
    dfs = []

    subdirs = sorted([d for d in parent_dir.iterdir() if d.is_dir()])
    for i, subdir in enumerate(subdirs):
        hist_file = subdir / history_filename
        if not hist_file.exists():
            continue

        try:
            df = pd.read_csv(hist_file, skiprows=skiprows, sep='\s+', comment="#")
            track_label = subdir.name if use_subdir_as_track else i
            df["track"] = track_label
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

def generate_grid(param_specs, grid_type="linear", num_points=8):
    """
    Detect fixed vs. swept parameters and generate a list of parameter dictionaries.

    Args:
        param_specs (dict): Parameters, where values are either:
                            - floats (fixed), or
                            - (min, max) tuples (sweep)
        grid_type (str): 'linear' or 'sobol'
        num_points (int): Number of samples to generate for swept parameters

    Returns:
        param_list (list of dict): Each dict is a unique combination of params
    """
    sweep_keys = [k for k, v in param_specs.items() if isinstance(v, tuple)]
    fixed_params = {k: v for k, v in param_specs.items() if not isinstance(v, tuple)}

    if not sweep_keys:
        return [fixed_params]

    if grid_type == "linear":
        sweep_values = [np.linspace(*param_specs[k], num_points) for k in sweep_keys]
        combos = list(itertools.product(*sweep_values))
    elif grid_type == "sobol":
        sampler = Sobol(d=len(sweep_keys), scramble=True)
        m = np.log2(num_points)
        if not m.is_integer():
            raise ValueError("For Sobol, num_points must be a power of 2.")
        sobol_vals = sampler.random_base2(m=int(m))  # ← you need this!

        sweep_values = []
        for i, key in enumerate(sweep_keys):
            low, high = param_specs[key]
            sweep_values.append(low + sobol_vals[:, i] * (high - low))
        
        combos = list(zip(*sweep_values)) 
    else:
        raise ValueError("Unsupported grid_type.")

    param_list = []
    for combo in combos:
        p = {k: v for k, v in zip(sweep_keys, combo)}
        p.update(fixed_params)
        param_list.append(p)

    return param_list

def update_inlist(template_text, params, log_dir):
    """
    Update the MESA inlist template with parameter values and paths.

    Args:
        template_text (str): Contents of the inlist template.
        params (dict): Dictionary of parameter values.
        log_dir (str): Directory for MESA output logs (not the log.txt files).

    Returns:
        str: Modified inlist text.
    """
    for key, val in params.items():
        if key == "initial_mass":
            template_text = re.sub(r"initial_mass\s*=\s*[\d.eE+-]+", f"initial_mass = {val:.6f}", template_text)
        elif key == "initial_y":
            template_text = re.sub(r"initial_y\s*=\s*[\d.eE+-]+", f"initial_y = {val:.4f}", template_text)
        elif key == "initial_z":
            template_text = re.sub(r"initial_z\s*=\s*[\d.eE+-]+", f"initial_z = {val:.4f}", template_text)
        elif key == "alpha":
            template_text = re.sub(r"mixing_length_alpha\s*=\s*[\d.eE+-]+", f"alpha_mlt = {val:.4f}", template_text)

    # Update log directory
    template_text = re.sub(r"log_directory\s*=\s*'.*?'", f"log_directory = 'DATA'", template_text)

    # Update save_model_filename based on initial_mass
    save_fname = f"TAMS_{params['initial_mass']:.6f}.mod"
    template_text = re.sub(
        r"save_model_filename\s*=\s*['\"].*?\.mod['\"]",
        f"save_model_filename = '{save_fname}'",
        template_text
    )

    return template_text

def run_mesa_model(template_file, mesa_dir, params, log_path):
    log_dir_name = f"M_{params['initial_mass']:.6f}_Y_{params['initial_y']:.3f}_Z_{params['initial_z']:.4f}_alpha_{params['mixing_length_alpha']:.2f}"
    run_dir = mesa_dir / log_dir_name
    run_dir.mkdir(parents=True,exist_ok=True)
    (run_dir / "DATA").mkdir(exist_ok=True)

    # Read and update inlist
    with open(template_file, "r") as f:
        template_text = f.read()
    updated_text = update_inlist(template_text, params, log_dir_name)

    # Write updated inlist to run_dir
    inlist_path = run_dir / "inlist_project"
    with open(inlist_path, "w") as f:
        f.write(updated_text)

    # Copy other necessary MESA files into run_dir (e.g., 'rn', makefile, etc.)
    for fname in ["rn","star", "inlist", "inlist_pgstar", 
                  "profile_columns.list", "history_columns.list"]:
        src = mesa_dir / fname
        if src.exists():
            shutil.copy(src, run_dir / fname)


    for fname in ["rn", "star"]:
        src = mesa_dir / fname
        if not src.exists():
            raise FileNotFoundError(f"{fname} not found in {mesa_dir}")
        shutil.copy(src, run_dir / fname)


    # Run MESA
    with open(log_path, "w+") as log_file:
        try:
            print(f"Running MESA for {log_path}...", flush=True)
            result = subprocess.run(
                ["./rn"],
                cwd=run_dir,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                check=True
            )
            # rewind and print log tail
            #log_file.seek(0)
            #lines = log_file.readlines()
            #print("Run completed. Last lines of log output:")
            #print("".join(lines[-20:]))  # tail of log
        except subprocess.CalledProcessError as e:
            print(f"MESA run failed for {run_dir} with exit code {e.returncode}")
        except Exception as e:
            print(f"Unexpected error running MESA: {e}")


    # Collect outputs
    save_fname = f"TAMS_{params['initial_mass']:.6f}.mod"
    src = run_dir / save_fname
    grid_tams = mesa_dir / "grid_TAMS"
    grid_tams.mkdir(exist_ok=True)
    if src.exists():
        shutil.move(src, grid_tams / save_fname)

    # Save inlist
    inlist_out_dir = mesa_dir / "grid_inlists"
    inlist_out_dir.mkdir(exist_ok=True)
    shutil.copy(inlist_path, inlist_out_dir / f"inlist_{log_dir_name}")


def task_wrapper(args):
    params, template_file, mesa_dir = args
    log_dir_name = f"M_{params['initial_mass']:.6f}_Y_{params['initial_y']:.3f}_Z_{params['initial_z']:.3f}_alpha_{params['mixing_length_alpha']:.2f}"
    logs_dir = mesa_dir / "LOGS"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / f"log_{log_dir_name}.txt"
    run_mesa_model(template_file, mesa_dir, params, log_file)



def debug_task(args):
    print("Running task with args:", args)
    task_wrapper(args)

def main(param_ranges, grid_type="linear", num_points=8, max_workers=2):
    """
    Main execution function for MESA parameter grid runs.

    Args:
        param_ranges (dict): ranges or single values for parameters to include in grid.
            e.g. param_ranges = {"initial_mass": (1.4, 1.6),
                                "initial_y": 0.27,             # fixed value
                                "initial_z": 0.02,             # fixed value
                                "mixing_length_alpha": (1.8, 2.2)} 
        grid_type (str): 'linear' or 'sobol'.
        num_points (int): Number of grid points.
        max_workers (int): Number of parallel MESA runs.
    """
    this_grid_dir = Path.cwd()
    print("Building MESA once before running the grid...", flush=True)
    subprocess.run(["./mk"], cwd=this_grid_dir, check=True)
    print(f"Running script and saving files in: {this_grid_dir}.")

    template_file = this_grid_dir / "inlist_template"
    #inlist_path = this_grid_dir / "inlist_project"
    log_file_dir = Path(f"{this_grid_dir}/LOGS" )

    param_dicts = generate_grid(param_ranges, grid_type=grid_type, num_points=num_points)
    print(f"Prepared {len(param_dicts)} parameter sets to run.")

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
    #with ThreadPoolExecutor(max_workers=max_workers) as executor:
        args_list = [(p, template_file, this_grid_dir) for p in param_dicts]
        executor.map(task_wrapper, args_list)
        #executor.map(debug_task, args_list)


def run_grid(param_ranges, grid_type="linear", num_points=8, max_workers=2):
    """
    Runs the full MESA parameter grid in parallel using ProcessPoolExecutor.
    An alternative to main() to test if ThreadPoolExecutor is the issue.
    """
    this_grid_dir = Path.cwd()
    print("Building MESA once before running the grid...", flush=True)

    # Build MESA once at the top-level grid directory
    subprocess.run(["./mk"], cwd=this_grid_dir, check=True)
    print(f"Running script and saving files in: {this_grid_dir}.")

    template_file = this_grid_dir / "inlist_template"
    log_file_dir = this_grid_dir / "LOGS"
    log_file_dir.mkdir(exist_ok=True)

    # Generate parameter combinations
    param_dicts = generate_grid(param_ranges, grid_type=grid_type, num_points=num_points)
    print(f"Prepared {len(param_dicts)} parameter sets to run.", flush=True)

    args_list = [(p, template_file, this_grid_dir) for p in param_dicts]

    if max_workers == 1:
        print("Running in serial mode for debugging...", flush=True)
        for args in args_list:
            task_wrapper(args)
    else:
        print(f"Running with ProcessPoolExecutor using {max_workers} workers...", flush=True)
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            executor.map(task_wrapper, args_list)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MESA parameter grid")
    parser.add_argument("--min_mass", type=float, default=0.7)
    parser.add_argument("--max_mass", type=float, default=None)
    parser.add_argument("--initial_Z", type=float, default=0.02)
    parser.add_argument("--grid_type", choices=["linear", "sobol"], default="linear")
    parser.add_argument("--num_points", type=int, default=8)
    parser.add_argument("--max_workers", type=int, default=1)
    parser.add_argument("--task_id", type=int, default=None, help="Index of the parameter set to run (used for SLURM job arrays)")
    args = parser.parse_args()

    if args.max_mass is None:
        max_mass_final = args.min_mass
        args.num_points=1
    else:
        max_mass_final=args.max_mass

    param_ranges = {
        "initial_mass": (args.min_mass, max_mass_final),         
        "initial_y": 0.27,                 
        "initial_z": args.initial_Z,                  
        "mixing_length_alpha": 2.0   
    }

    param_dicts = generate_grid(param_ranges, grid_type=args.grid_type, num_points=args.num_points)

    if args.task_id is not None:

        # ----- in job-array mode -----
        idx = args.task_id
        if idx < 0 or idx >= len(param_dicts):
            raise IndexError(f"task_id {idx} is out of range for {len(param_dicts)} parameter sets.")

        print(f"[SLURM ARRAY MODE] Running parameter index {idx} of {len(param_dicts)}")

        # always run a single model in array mode
        this_grid_dir = Path.cwd()
        template_file = this_grid_dir / "inlist_template"
        params = param_dicts[idx]

        # build logs directory
        logs_dir = this_grid_dir / "LOGS"
        logs_dir.mkdir(exist_ok=True)

        log_dir_name = (f"M_{params['initial_mass']:.6f}_Y_{params['initial_y']:.3f}_Z_{params['initial_z']:.4f}_alpha_{params['mixing_length_alpha']:.2f}"
                        f"_TASK_{idx}")
        log_file = logs_dir / f"log_{log_dir_name}.txt"

        run_mesa_model(template_file, this_grid_dir, params, log_file)
    else:

        # ----- normal multiprocessing mode -----

        run_grid(
            param_ranges=param_ranges,
            grid_type=args.grid_type,
            num_points=args.num_points,
            max_workers=args.max_workers)
    print(f"All MESA runs completed successfully at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.")


"""
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MESA parameter grid")
    parser.add_argument("--grid_type", choices=["linear", "sobol"], default="linear")
    parser.add_argument("--num_points", type=int, default=8)
    parser.add_argument("--max_workers", type=int, default=1)
    args = parser.parse_args()
    param_ranges = {
        "initial_mass": (0.7, 1.2),         
        "initial_y": 0.27,                 
        "initial_z": 0.02,                  
        "mixing_length_alpha": 2.0   
    }
    main(param_ranges=param_ranges,
         grid_type=args.grid_type,
         num_points=args.num_points,
         max_workers=args.max_workers)
"""
