import argparse
from pathlib import Path

from .grid_utils import load_history_with_constants_from_profile, cleanup_grid_data
from .failure_report import DEFAULT_REPORT_NAME, write_failure_report


def main(parent_dir: Path, save_as_hdf5: bool, hdf5_filename: str, constant_columns: list,
         cleanup: str = "none", exclude_dirs: list = None,
         failure_report: bool = True, report_name: str = DEFAULT_REPORT_NAME):
    """
    Load MESA history files from a grid run directory and optionally save to HDF5.

    Args:
        parent_dir: Directory containing one subdirectory per stellar model.
        save_as_hdf5: Write combined output to an HDF5 file.
        hdf5_filename: Output HDF5 filename (written into parent_dir).
        constant_columns: Parameter names to extract from subdirectory names
            and add as constant columns (e.g. ['M', 'Y', 'Z', 'alpha']).
        cleanup: 'none', 'zip', or 'delete'. After a successful HDF5 save,
            archive ('zip') or remove ('delete') each model's DATA/ folder
            (see cleanup_grid_data). Only applies when save_as_hdf5 is True.
        exclude_dirs: Subdirectory names to skip (e.g. still-failed tasks).
        failure_report: Write a failure report collecting every track that
            never produced its save file, and why (see failure_report). On by
            default -- it is written before any cleanup, while the logs and
            failed run directories are still in place.
        report_name: Filename for that report, written into parent_dir.
    """
    df = load_history_with_constants_from_profile(
        parent_dir=parent_dir,
        constant_columns=constant_columns,
        save_as_hdf5=save_as_hdf5,
        hdf5_filename=hdf5_filename,
        extract_constants_from_dirname=True,
        exclude_dirs=set(exclude_dirs) if exclude_dirs else None,
    )
    print(f"Loaded {len(df)} preview rows.")
    print("Preview:\n", df.head())

    # Before cleanup, always: the report is built from LOGS/ and grid_TAMS/,
    # which cleanup (here and in the submit_grid combine job) removes for every
    # task that succeeded. Afterwards it cannot be regenerated.
    if failure_report:
        write_failure_report(parent_dir, keys=constant_columns, report_name=report_name)

    if save_as_hdf5 and not df.empty:
        cleanup_grid_data(parent_dir, cleanup)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Combine MESA history files from a grid run directory into a single HDF5."
    )
    parser.add_argument("--parent_dir", type=str, required=True,
                        help="Path to the grid run directory containing model subdirectories.")
    parser.add_argument("--hdf5_filename", type=str, default="combined_history.hdf5",
                        help="Output HDF5 filename (default: combined_history.hdf5).")
    parser.add_argument("--save", action="store_true",
                        help="Save the combined output as an HDF5 file.")
    parser.add_argument("--constants", nargs="*", default=["M", "Y", "Z", "alpha"],
                        help="Parameter keys to extract from subdirectory names "
                             "(default: M Y Z alpha).")
    parser.add_argument("--cleanup", choices=["none", "zip", "delete"], default="none",
                        help="After a successful --save, archive ('zip') or remove "
                             "('delete') each model's DATA/ folder to save disk space. "
                             "Refuses to run unless every model has a TAMS save file in "
                             "grid_TAMS/ (i.e. all array jobs have finished). "
                             "Default: 'none'.")
    parser.add_argument("--exclude_dirs", nargs="*", default=None,
                        help="Subdirectory names to exclude from the HDF5 "
                             "(e.g. still-failed task directories).")
    parser.add_argument("--no_failure_report", dest="failure_report", action="store_false",
                        help="Skip writing the failure report. By default one is written "
                             "into the grid directory, collecting every track that never "
                             "produced its save file and the reason for each.")
    parser.add_argument("--report_name", default=DEFAULT_REPORT_NAME,
                        help=f"Filename for the failure report (default: {DEFAULT_REPORT_NAME}).")
    args = parser.parse_args()

    main(Path(args.parent_dir).expanduser(), args.save, args.hdf5_filename, args.constants,
         args.cleanup, args.exclude_dirs, args.failure_report, args.report_name)
