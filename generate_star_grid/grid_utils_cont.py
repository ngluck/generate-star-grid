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
import importlib.util

from resume_utils import get_next_resume_index, modify_inlist_for_resume


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
    extract_constants_from_dirname: bool = False
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

def update_inlist(template_text, params, log_dir, resume=False, tams_dir=None):
    """
    Update the MESA inlist template with parameter values and paths.
    """
    for key, val in params.items():
        if key == "initial_mass":
            template_text = re.sub(r"initial_mass\s*=\s*[\d.eE+-]+", f"initial_mass = {val:.4f}", template_text)
        elif key == "initial_y":
            template_text = re.sub(r"initial_y\s*=\s*[\d.eE+-]+", f"initial_y = {val:.4f}", template_text)
        elif key == "initial_z":
            template_text = re.sub(r"initial_z\s*=\s*[\d.eE+-]+", f"initial_z = {val:.4f}", template_text)
        elif key == "mixing_length_alpha":
            template_text = re.sub(r"mixing_length_alpha\s*=\s*[\d.eE+-]+", f"mixing_length_alpha = {val:.4f}", template_text)

    # Always reset log_directory
    template_text = re.sub(r"log_directory\s*=\s*'.*?'", f"log_directory = 'DATA'", template_text)

    tams_fname = f"TAMS_{params['initial_mass']:.3f}.mod"

    if resume:
        if tams_dir is None:
            raise ValueError("Must provide tams_dir when resume=True")

        tams_path = Path(tams_dir) / tams_fname
        print("TAMS path:", tams_path)
        if not tams_path.exists():
            raise FileNotFoundError(f"Setting resume=True requires TAMS file at {tams_path}")

        # Enable load_saved_model and specify load_model_filename
        template_text = re.sub(r'^\s*!\s*(load_saved_model\s*=\s*\.(true|false)\.)', r'\1', template_text, flags=re.MULTILINE)
        template_text = re.sub(r"load_saved_model\s*=\s*\.false\.", "load_saved_model = .true.", template_text)
        template_text = re.sub(r'^\s*!\s*(load_model_filename\s*=\s*[\'\"].*?[\'\"])', r'\1', template_text, flags=re.MULTILINE)
        template_text = re.sub(r"load_model_filename\s*=\s*['\"].*?\.mod['\"]", f"load_model_filename = '{tams_fname}'", template_text)

        # Save output to cont_xxx.mod
        cont_fname = f"cont_{params['initial_mass']:.3f}.mod"
        template_text = re.sub(r"save_model_filename\s*=\s*['\"].*?\.mod['\"]", f"save_model_filename = '{cont_fname}'", template_text)
        template_text = re.sub(r"save_model_when_terminate\s*=\s*\.false\.", "save_model_when_terminate = .true.", template_text)

    else:
        # Save output to TAMS_xxx.mod
        template_text = re.sub(r"save_model_filename\s*=\s*['\"].*?\.mod['\"]", f"save_model_filename = '{tams_fname}'", template_text)

    return template_text


def old_update_inlist(template_text, params, log_dir, resume=False, tams_dir=None):
    """
    Update the MESA inlist template with parameter values and paths.
    """
    for key, val in params.items():
        if key == "initial_mass":
            template_text = re.sub(r"initial_mass\s*=\s*[\d.eE+-]+", f"initial_mass = {val:.4f}", template_text)
        elif key == "initial_y":
            template_text = re.sub(r"initial_y\s*=\s*[\d.eE+-]+", f"initial_y = {val:.4f}", template_text)
        elif key == "initial_z":
            template_text = re.sub(r"initial_z\s*=\s*[\d.eE+-]+", f"initial_z = {val:.4f}", template_text)
        elif key == "alpha":
            template_text = re.sub(r"mixing_length_alpha\s*=\s*[\d.eE+-]+", f"alpha_mlt = {val:.4f}", template_text)

    # Always reset log_directory
    template_text = re.sub(r"log_directory\s*=\s*'.*?'", f"log_directory = 'DATA'", template_text)

    tams_fname = f"TAMS_{params['initial_mass']:.3f}.mod"

    if resume:
        if tams_dir is None:
            raise ValueError("Must provide tams_dir when resume=True")

        tams_path = Path(tams_dir) / tams_fname
        print("TAMS path:", tams_path)
        if not tams_path.exists():
            raise FileNotFoundError(f"Setting resume=True requires TAMS file at {tams_path}")

        # Enable load_saved_model and specify load_model_filename
        template_text = re.sub(r'^\s*!\s*(load_saved_model\s*=\s*\.(true|false)\.)', r'\1', template_text, flags=re.MULTILINE)
        template_text = re.sub(r"load_saved_model\s*=\s*\.false\.", "load_saved_model = .true.", template_text)
        template_text = re.sub(r'^\s*!\s*(load_model_filename\s*=\s*[\'\"].*?[\'\"])', r'\1', template_text, flags=re.MULTILINE)
        template_text = re.sub(r"load_model_filename\s*=\s*['\"].*?\.mod['\"]", f"load_model_filename = '{tams_fname}'", template_text)

        # Save output to cont_xxx.mod
        cont_fname = f"cont_{params['initial_mass']:.3f}.mod"
        template_text = re.sub(r"save_model_filename\s*=\s*['\"].*?\.mod['\"]", f"save_model_filename = '{cont_fname}'", template_text)
        template_text = re.sub(r"save_model_when_terminate\s*=\s*\.false\.", "save_model_when_terminate = .true.", template_text)

    else:
        # Save output to TAMS_xxx.mod
        template_text = re.sub(r"save_model_filename\s*=\s*['\"].*?\.mod['\"]", f"save_model_filename = '{tams_fname}'", template_text)

    return template_text



def run_mesa_model(run_dir, log_path, mesa_dir, params, resume=False, tams_dir=None):
    """
    Assumes that inlist_project is already in run_dir.
    Copies necessary MESA files and runs MESA in the given run_dir.
    If resume=True, also copies the TAMS file to the run directory.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "DATA").mkdir(exist_ok=True)

    for fname in ["rn", "star", "inlist", "inlist_pgstar",
                  "profile_columns.list", "history_columns.list"]:
        src = mesa_dir / fname
        if src.exists():
            shutil.copy(src, run_dir / fname)
        else:
            if fname in ["rn", "star"]:
                raise FileNotFoundError(f"{fname} not found in {mesa_dir}")

    # Copy TAMS file if resuming
    if resume:
        if tams_dir is None:
            raise ValueError("Must provide tams_dir when resume=True")
        
        tams_fname = f"TAMS_{params['initial_mass']:.3f}.mod"
        tams_src = tams_dir / tams_fname
        if tams_src.exists():
            shutil.copy(tams_src, run_dir / tams_fname)
        else:
            raise FileNotFoundError(f"TAMS file not found: {tams_src}")

    run_status = "resumed" if resume else "new"
    print(f"Running MESA for {log_path} ({run_status})...", flush=True)

    with open(log_path, "a" if resume else "w") as log_file:
        try:
            result = subprocess.run(
                ["./rn"],
                cwd=run_dir,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] MESA run failed for {run_dir} with exit code {e.returncode}")
        except Exception as e:
            print(f"[ERROR] Unexpected error running MESA: {e}")

    out_fname = f"cont_{params['initial_mass']:.3f}.mod" if resume else f"TAMS_{params['initial_mass']:.3f}.mod"
    src = run_dir / out_fname
    
    # Choose destination based on resume status
    if resume:
        grid_cont = mesa_dir / "grid_CONT"
        grid_cont.mkdir(exist_ok=True)
        dest_dir = grid_cont
    else:
        grid_tams = mesa_dir / "grid_TAMS"
        grid_tams.mkdir(exist_ok=True)
        dest_dir = grid_tams

    if src.exists():
        shutil.move(src, dest_dir / out_fname)
    else:
        print(f"[WARNING] Output model file not found: {src}")

def old_run_mesa_model(run_dir, log_path, mesa_dir, params, resume=False):
    """
    Assumes that inlist_project is already in run_dir.
    Copies necessary MESA files and runs MESA in the given run_dir.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "DATA").mkdir(exist_ok=True)

    for fname in ["rn", "star", "inlist", "inlist_pgstar",
                  "profile_columns.list", "history_columns.list"]:
        src = mesa_dir / fname
        if src.exists():
            shutil.copy(src, run_dir / fname)
        else:
            if fname in ["rn", "star"]:
                raise FileNotFoundError(f"{fname} not found in {mesa_dir}")

    run_status = "resumed" if resume else "new"
    print(f"Running MESA for {log_path} ({run_status})...", flush=True)

    with open(log_path, "a" if resume else "w") as log_file:
        try:
            result = subprocess.run(
                ["./rn"],
                cwd=run_dir,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] MESA run failed for {run_dir} with exit code {e.returncode}")
        except Exception as e:
            print(f"[ERROR] Unexpected error running MESA: {e}")

    out_fname = f"cont_{params['initial_mass']:.3f}.mod" if resume else f"TAMS_{params['initial_mass']:.3f}.mod"
    src = run_dir / out_fname
    grid_tams = mesa_dir / "grid_TAMS"
    grid_tams.mkdir(exist_ok=True)

    if src.exists():
        shutil.move(src, grid_tams / out_fname)
    else:
        print(f"[WARNING] Output model file not found: {src}")


def task_wrapper(args):
    # Fixed to handle correct number of arguments
    if len(args) == 6:
        params, template_file, mesa_dir, resume, modifications, tag = args
    else:
        params, template_file, mesa_dir, resume = args
        modifications = None
        tag = None

    log_dir_name = f"M_{params['initial_mass']:.3f}_Y_{params['initial_y']:.3f}_Z_{params['initial_z']:.3f}_alpha_{params['mixing_length_alpha']:.2f}"
    run_dir = mesa_dir / log_dir_name
    log_path = mesa_dir / "LOGS" / f"log_{log_dir_name}.txt"
    inlist_out_dir = mesa_dir / "grid_inlists"
    inlist_out_dir.mkdir(exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    tams_dir = mesa_dir / "grid_TAMS"

    with open(template_file, "r") as f:
        template_text = f.read()

    updated_text = update_inlist(template_text, params, log_dir_name, resume=resume, tams_dir=tams_dir)
    
    # Apply modifications if resuming and modifications are provided
    if resume and modifications:
        for modification in modifications:
            updated_text = modification(updated_text, params)

    # Write to inlist_project in run_dir
    inlist_path = run_dir / "inlist_project"
    with open(inlist_path, "w") as f:
        f.write(updated_text)

    # Archive inlist version
    archive_name = f"inlist_{log_dir_name}" + ("_resume" if resume else "")
    if tag:
        archive_name += f"_{tag}"
    with open(inlist_out_dir / archive_name, "w") as f:
        f.write(updated_text)

def old_task_wrapper(args):
    params, template_file, mesa_dir, resume = args

    log_dir_name = f"M_{params['initial_mass']:.3f}_Y_{params['initial_y']:.3f}_Z_{params['initial_z']:.3f}_alpha_{params['mixing_length_alpha']:.2f}"
    run_dir = mesa_dir / log_dir_name
    log_path = mesa_dir / "LOGS" / f"log_{log_dir_name}.txt"
    inlist_out_dir = mesa_dir / "grid_inlists"
    inlist_out_dir.mkdir(exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    tams_dir = mesa_dir / "grid_TAMS"

    with open(template_file, "r") as f:
        template_text = f.read()

    updated_text = update_inlist(template_text, params, log_dir_name, resume=resume, tams_dir=tams_dir)

    # Write to inlist_project in run_dir
    inlist_path = run_dir / "inlist_project"
    with open(inlist_path, "w") as f:
        f.write(updated_text)

    # Archive inlist version
    archive_name = f"inlist_{log_dir_name}" + ("_resume" if resume else "")
    with open(inlist_out_dir / archive_name, "w") as f:
        f.write(updated_text)

    run_mesa_model(run_dir, log_path, mesa_dir, params, resume=resume)



def debug_task(args):
    print("Running task with args:", args)
    task_wrapper(args)

def run_grid(param_ranges, grid_type="linear", num_points=8, max_workers=2,
             resume=False, resume_edit_path="/gpfs/gibbs/pi/nagai/mesa_ml/scripts/update_inlist.py"):
    """
    Runs the full MESA parameter grid in parallel using ProcessPoolExecutor.
    If resume=True, it imports and applies modifications from resume_edit_path.
    """
    this_grid_dir = Path.cwd()
    print("Building MESA once before running the grid...", flush=True)

    subprocess.run(["./mk"], cwd=this_grid_dir, check=True)
    print(f"Running script and saving files in: {this_grid_dir}")

    template_file = this_grid_dir / "inlist_template"
    (this_grid_dir / "LOGS").mkdir(exist_ok=True)

    tag = modifications = None
    if resume:
        resume_edit_path = Path(resume_edit_path).resolve()
        if not resume_edit_path.exists():
            raise FileNotFoundError(f"Resume edit script not found: {resume_edit_path}")
        spec = importlib.util.spec_from_file_location("resume_edits", str(resume_edit_path))
        resume_edits = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resume_edits)
        tag = resume_edits.resume_tag
        modifications = resume_edits.modifications

    param_dicts = generate_grid(param_ranges, grid_type=grid_type, num_points=num_points)
    print(f"Prepared {len(param_dicts)} parameter sets to run.", flush=True)

    # Fixed to pass correct number of arguments
    args_list = [(p, template_file, this_grid_dir, resume, modifications, tag) for p in param_dicts]

    if max_workers == 1:
        print("Running in serial mode for debugging...", flush=True)
        for args in args_list:
            task_wrapper(args)
    else:
        print(f"Running with ProcessPoolExecutor using {max_workers} workers...", flush=True)
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            executor.map(task_wrapper, args_list)


def old_run_grid(param_ranges, grid_type="linear", num_points=8, max_workers=2,
             resume=False, resume_edit_path="/gpfs/gibbs/pi/nagai/mesa_ml/scripts/update_inlist.py"):
    """
    Runs the full MESA parameter grid in parallel using ProcessPoolExecutor.
    If resume=True, it imports and applies modifications from resume_edit_path.
    """
    this_grid_dir = Path.cwd()
    print("Building MESA once before running the grid...", flush=True)

    subprocess.run(["./mk"], cwd=this_grid_dir, check=True)
    print(f"Running script and saving files in: {this_grid_dir}")

    template_file = this_grid_dir / "inlist_template"
    (this_grid_dir / "LOGS").mkdir(exist_ok=True)

    tag = modifications = None
    if resume:
        resume_edit_path = Path(resume_edit_path).resolve()
        if not resume_edit_path.exists():
            raise FileNotFoundError(f"Resume edit script not found: {resume_edit_path}")
        spec = importlib.util.spec_from_file_location("resume_edits", str(resume_edit_path))
        resume_edits = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resume_edits)
        tag = resume_edits.resume_tag
        modifications = resume_edits.modifications

    param_dicts = generate_grid(param_ranges, grid_type=grid_type, num_points=num_points)
    print(f"Prepared {len(param_dicts)} parameter sets to run.", flush=True)

    args_list = [(p, template_file, this_grid_dir, resume, modifications, tag) for p in param_dicts]

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
    parser.add_argument("--grid_type", choices=["linear", "sobol"], default="linear")
    parser.add_argument("--num_points", type=int, default=8)
    parser.add_argument("--max_workers", type=int, default=1)
    parser.add_argument("--resume", action="store_true", help="Resume from existing TAMS models")
    parser.add_argument("--resume_edit_path", type=str, default="update_inlist.py",
                        help="Path to Python script defining resume_tag and modifications")
    args = parser.parse_args()

    param_ranges = {
        "initial_mass": (0.7, 1.2),         
        "initial_y": 0.27,                 
        "initial_z": 0.02,                  
        "mixing_length_alpha": 2.0   
    }

    run_grid(
        param_ranges=param_ranges,
        grid_type=args.grid_type,
        num_points=args.num_points,
        max_workers=args.max_workers,
        resume=args.resume,
        resume_edit_path=args.resume_edit_path
    )
    print("All MESA runs completed successfully at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.")


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
