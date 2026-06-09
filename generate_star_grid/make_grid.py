import numpy as np
import pandas as pd
import argparse
from pathlib import Path
from grid_utils import load_mesa_histories_from_subdirs, load_history_with_constants_from_profile


def main(parent_dir, save_as_hdf5, hdf5_filename, constant_columns):
    df = load_history_with_constants_from_profile(
        parent_dir=parent_dir,
        constant_columns=constant_columns,
        save_as_hdf5=save_as_hdf5,
        hdf5_filename=hdf5_filename,
        extract_constants_from_dirname=True
    )
    print(f"Loaded {len(df)} rows from history files.")
    #print(df.head())
    print("Preview:\n", df.head())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load MESA history files and enrich them with profile constants.")
    parser.add_argument("--parent_dir", type=str, required=True,
                        help="Path to the parent directory containing MESA subdirectories.")
    parser.add_argument("--hdf5_filename", type=str, default="combined_history.hdf5",
                        help="Filename to save the combined HDF5 (default: combined_history.hdf5).")
    parser.add_argument("--save", action="store_true",
                        help="If set, save the output as an HDF5 file in the parent directory.")
    parser.add_argument("--constants", nargs="*", default=["Y", "Z", "alpha"],
                        help="List of constant column names to extract from profile.data (default: initial_y, initial_z, alpha_mlt)" \
                        "OR subdir name (default: Y, Z, alpha).")

    args = parser.parse_args()

    main(Path(args.parent_dir).expanduser(), args.save, args.hdf5_filename, args.constants)


# def main(parent_dir, save_as_hdf5, hdf5_filename):
#     df = load_mesa_histories_from_subdirs(
#         parent_dir=parent_dir,
#         save_as_hdf5=save_as_hdf5,
#         hdf5_filename=hdf5_filename
#     )
#     print(f"Loaded {len(df)} rows from history files.")
#     print(df.head())

# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="Load MESA history files from a grid of star models.")
#     parser.add_argument("--parent_dir", type=str, required=True,
#                         help="Path to the parent directory containing MESA subdirectories.")
#     parser.add_argument("--hdf5_filename", type=str, default="combined_history.hdf5",
#                         help="Filename to save the combined HDF5 (default: combined_history.hdf5).")
#     parser.add_argument("--save", action="store_true",
#                         help="If set, save the output as an HDF5 file in the parent directory.")

#     args = parser.parse_args()

#     main(Path(args.parent_dir).expanduser(), args.save, args.hdf5_filename)

