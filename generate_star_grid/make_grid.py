import argparse
from pathlib import Path

from .grid_utils import load_history_with_constants_from_profile, cleanup_grid_data


def main(parent_dir: Path, save_as_hdf5: bool, hdf5_filename: str, constant_columns: list,
         cleanup: str = "none"):
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
    """
    df = load_history_with_constants_from_profile(
        parent_dir=parent_dir,
        constant_columns=constant_columns,
        save_as_hdf5=save_as_hdf5,
        hdf5_filename=hdf5_filename,
        extract_constants_from_dirname=True,
    )
    print(f"Loaded {len(df)} preview rows.")
    print("Preview:\n", df.head())

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
    args = parser.parse_args()

    main(Path(args.parent_dir).expanduser(), args.save, args.hdf5_filename, args.constants, args.cleanup)
