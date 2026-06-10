"""
Continuation variant of grid_utils for resumed stellar evolution runs.

Use this module when you want to restart models from an existing TAMS save file
(e.g. to continue evolution past the main sequence). The run logic is identical
to grid_utils except that update_inlist and run_mesa_model handle the extra
bookkeeping for loading/saving continuation models.
"""
import shutil
import subprocess
import importlib.util
import argparse
import datetime
import re
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

from .grid_utils import (
    PARAM_FORMAT,
    make_run_dir_name,
    generate_grid,
    compute_param_formats,
    write_grid_notes,
    extract_constants_from_subdir_name,
    extract_constant_from_profile,
    extract_mass,
    load_history_with_constants_from_profile,
    load_mesa_histories_from_subdirs,
)
from .resume_utils import modify_inlist_for_resume


def update_inlist(
    template_text: str,
    params: dict,
    log_dir: str,
    resume: bool = False,
    tams_dir=None,
) -> str:
    """
    Substitute parameter values into a MESA inlist template, with optional resume support.

    Handles keys: initial_mass, initial_y, initial_z, mixing_length_alpha.
    When resume=True, also enables load_saved_model and sets load_model_filename to the
    corresponding TAMS file; the output save file is named cont_<mass>.mod.

    Args:
        template_text: Raw text of the inlist_template file.
        params: Parameter dict (keys as above).
        log_dir: Unused; kept for API compatibility. log_directory is always set to 'DATA'.
        resume: If True, configure the inlist to load an existing TAMS model.
        tams_dir: Directory containing TAMS_<mass>.mod files (required when resume=True).

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
        if key == "initial_z":
            template_text = re.sub(
                r"Zbase\s*=\s*[\d.eE+-]+", f"Zbase = {val:{fmt}}", template_text
            )

    template_text = re.sub(r"log_directory\s*=\s*'.*?'", "log_directory = 'DATA'", template_text)

    tams_fname = f"TAMS_{params['initial_mass']:.3f}.mod"

    if resume:
        if tams_dir is None:
            raise ValueError("tams_dir is required when resume=True")
        tams_path = Path(tams_dir) / tams_fname
        if not tams_path.exists():
            raise FileNotFoundError(f"TAMS file not found: {tams_path}")

        template_text = re.sub(
            r'^\s*!\s*(load_saved_model\s*=\s*\.(true|false)\.)', r'\1',
            template_text, flags=re.MULTILINE,
        )
        template_text = re.sub(
            r"load_saved_model\s*=\s*\.false\.", "load_saved_model = .true.", template_text
        )
        template_text = re.sub(
            r'^\s*!\s*(load_model_filename\s*=\s*[\'\"].*?[\'\"])', r'\1',
            template_text, flags=re.MULTILINE,
        )
        template_text = re.sub(
            r"load_model_filename\s*=\s*['\"].*?\.mod['\"]",
            f"load_model_filename = '{tams_fname}'",
            template_text,
        )
        cont_fname = f"cont_{params['initial_mass']:.3f}.mod"
        template_text = re.sub(
            r"save_model_filename\s*=\s*['\"].*?\.mod['\"]",
            f"save_model_filename = '{cont_fname}'",
            template_text,
        )
        template_text = re.sub(
            r"save_model_when_terminate\s*=\s*\.false\.", "save_model_when_terminate = .true.", template_text
        )
    else:
        template_text = re.sub(
            r"save_model_filename\s*=\s*['\"].*?\.mod['\"]",
            f"save_model_filename = '{tams_fname}'",
            template_text,
        )

    return template_text


def run_mesa_model(
    run_dir: Path,
    log_path: Path,
    mesa_dir: Path,
    params: dict,
    resume: bool = False,
    tams_dir=None,
) -> None:
    """
    Copy MESA runtime files into run_dir and execute MESA.

    Assumes inlist_project is already written to run_dir. On completion, moves
    the output model file to grid_TAMS/ (new run) or grid_CONT/ (continuation run).

    Args:
        run_dir: Pre-created directory for this model run.
        log_path: File path where MESA stdout/stderr will be written.
        mesa_dir: Root grid directory (source of rn, star, inlist, etc.).
        params: Parameter dict (must include initial_mass).
        resume: If True, also copies the TAMS file into run_dir before running.
        tams_dir: Directory containing TAMS_<mass>.mod files (required when resume=True).
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "DATA").mkdir(exist_ok=True)

    for fname in ["rn", "star", "inlist", "inlist_pgstar", "profile_columns.list", "history_columns.list"]:
        src = mesa_dir / fname
        if src.exists():
            shutil.copy(src, run_dir / fname)
        elif fname in ("rn", "star"):
            raise FileNotFoundError(f"Required MESA file '{fname}' not found in {mesa_dir}")

    if resume:
        if tams_dir is None:
            raise ValueError("tams_dir is required when resume=True")
        tams_fname = f"TAMS_{params['initial_mass']:.3f}.mod"
        tams_src = Path(tams_dir) / tams_fname
        if not tams_src.exists():
            raise FileNotFoundError(f"TAMS file not found: {tams_src}")
        shutil.copy(tams_src, run_dir / tams_fname)

    print(f"Running MESA ({'resumed' if resume else 'new'}) for {log_path}...", flush=True)
    with open(log_path, "a" if resume else "w") as log_file:
        try:
            subprocess.run(
                ["./rn"], cwd=run_dir, stdout=log_file, stderr=subprocess.STDOUT, text=True, check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] MESA run failed for {run_dir} (exit code {e.returncode})")
        except Exception as e:
            print(f"[ERROR] Unexpected error running MESA in {run_dir}: {e}")

    out_fname = f"cont_{params['initial_mass']:.3f}.mod" if resume else f"TAMS_{params['initial_mass']:.3f}.mod"
    src = run_dir / out_fname
    dest_dir = mesa_dir / ("grid_CONT" if resume else "grid_TAMS")
    dest_dir.mkdir(exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(dest_dir / out_fname))
    else:
        print(f"[WARNING] Expected output file not found: {src}")


def task_wrapper(args: tuple) -> None:
    """
    Unpack args, write the inlist, and run a single MESA model (with optional resume).

    Args:
        args: Tuple of (params, template_file, mesa_dir, resume, modifications, tag, param_formats).
            modifications: list of callables ``f(inlist_text, params) -> inlist_text``
                applied after the standard substitutions (used for resume edits).
            tag: optional string appended to the archived inlist filename.
            param_formats: per-key directory-naming format overrides (see
                compute_param_formats).
    """
    params, template_file, mesa_dir, resume, modifications, tag, param_formats = args

    log_dir_name = make_run_dir_name(params, param_formats)
    run_dir = mesa_dir / log_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)

    logs_dir = mesa_dir / "LOGS"
    logs_dir.mkdir(exist_ok=True)
    log_path = logs_dir / f"log_{log_dir_name}.txt"

    inlist_out_dir = mesa_dir / "grid_inlists"
    inlist_out_dir.mkdir(exist_ok=True)

    tams_dir = mesa_dir / "grid_TAMS"

    with open(template_file, "r") as f:
        updated_text = update_inlist(f.read(), params, log_dir_name, resume=resume, tams_dir=tams_dir)

    if resume and modifications:
        for modification in modifications:
            updated_text = modification(updated_text, params)

    inlist_path = run_dir / "inlist_project"
    with open(inlist_path, "w") as f:
        f.write(updated_text)

    archive_name = f"inlist_{log_dir_name}" + ("_resume" if resume else "")
    if tag:
        archive_name += f"_{tag}"
    with open(inlist_out_dir / archive_name, "w") as f:
        f.write(updated_text)

    run_mesa_model(run_dir, log_path, mesa_dir, params, resume=resume, tams_dir=tams_dir)


def run_grid(
    param_ranges: dict,
    grid_type: str = "linear",
    num_points: int = 8,
    max_workers: int = 2,
    resume: bool = False,
    resume_edit_path: str = None,
) -> None:
    """
    Build MESA, generate the parameter grid, and run all models in parallel.

    Must be called from the grid run directory. When resume=True, a Python script
    at resume_edit_path is imported; it must define:
      - ``resume_tag`` (str): appended to archived inlist filenames
      - ``modifications`` (list of callables): each takes (inlist_text, params) and
        returns modified inlist_text

    Args:
        param_ranges: Parameter spec dict passed to generate_grid.
        grid_type: 'linear' or 'sobol'.
        num_points: Grid points per swept dimension (linear) or total samples (sobol).
        max_workers: Parallel MESA processes. Use 1 for serial/debug mode.
        resume: Continue from existing TAMS models.
        resume_edit_path: Path to the resume edits script (required when resume=True).
    """
    this_grid_dir = Path.cwd()
    print("Building MESA...", flush=True)
    subprocess.run(["./mk"], cwd=this_grid_dir, check=True)
    print(f"Grid directory: {this_grid_dir}")

    (this_grid_dir / "LOGS").mkdir(exist_ok=True)

    tag = modifications = None
    if resume:
        if resume_edit_path is None:
            raise ValueError("--resume_edit_path is required when --resume is set.")
        edit_path = Path(resume_edit_path).resolve()
        if not edit_path.exists():
            raise FileNotFoundError(f"Resume edit script not found: {edit_path}")
        spec = importlib.util.spec_from_file_location("resume_edits", str(edit_path))
        resume_edits = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resume_edits)
        tag = resume_edits.resume_tag
        modifications = resume_edits.modifications

    param_dicts = generate_grid(param_ranges, grid_type=grid_type, num_points=num_points)
    print(f"Running {len(param_dicts)} models.", flush=True)

    param_formats = compute_param_formats(param_ranges, grid_type=grid_type, num_points=num_points)
    write_grid_notes(param_ranges, param_formats, grid_type, num_points, this_grid_dir / "notes.txt")

    args_list = [
        (p, this_grid_dir / "inlist_template", this_grid_dir, resume, modifications, tag, param_formats)
        for p in param_dicts
    ]

    if max_workers == 1:
        for a in args_list:
            task_wrapper(a)
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            executor.map(task_wrapper, args_list)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a MESA continuation grid.")
    parser.add_argument("--min_mass", type=float, default=0.7)
    parser.add_argument("--max_mass", type=float, default=None,
                        help="If omitted, runs a single model at min_mass.")
    parser.add_argument("--initial_Z", type=float, default=0.02)
    parser.add_argument("--initial_Y", type=float, default=0.27)
    parser.add_argument("--alpha_MLT", type=float, default=2.0)
    parser.add_argument("--grid_type", choices=["linear", "sobol"], default="linear")
    parser.add_argument("--num_points", type=int, default=8)
    parser.add_argument("--max_workers", type=int, default=1)
    parser.add_argument("--resume", action="store_true",
                        help="Continue evolution from existing TAMS models.")
    parser.add_argument("--resume_edit_path", type=str, default=None,
                        help="Path to a Python script defining resume_tag and modifications.")
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

    run_grid(
        param_ranges=param_ranges,
        grid_type=args.grid_type,
        num_points=args.num_points,
        max_workers=args.max_workers,
        resume=args.resume,
        resume_edit_path=args.resume_edit_path,
    )
    print(f"Done at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.")
