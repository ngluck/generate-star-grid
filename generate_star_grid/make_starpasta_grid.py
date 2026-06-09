import pandas as pd
import glob
import os
import argparse
from pathlib import Path

if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="Combine many h5 tracks into one grid with Track IDs.")
    parser.add_argument("--h5_dir", type=str, required=True,
                        help="Directory containing HDF5 files")
    parser.add_argument("--out", type=str, required=False, default="grid.h5",
                        help="Output filename (HDF5)")
    args = parser.parse_args()

    h5_dir = Path(args.h5_dir)
    out_file = h5_dir/args.out

    h5_files = sorted(glob.glob(os.path.join(h5_dir, "*.h5")))
    if not h5_files:
        h5_files = sorted(glob.glob(os.path.join(h5_dir, "*.hdf5")))

    if not h5_files:
        raise ValueError(f"No .h5 or .hdf5 files found in {h5_dir}")

    grid_parts = []

    for track_num, h5_file in enumerate(h5_files, start=1):
        df = pd.read_hdf(h5_file, key="data")
        df["Track"] = track_num
        grid_parts.append(df)
        #print(f"✔ Loaded {h5_file} → Track {track_num}")

    grid = pd.concat(grid_parts, ignore_index=True)

    grid.to_hdf(out_file, key="grid", mode="w")
    print(f"\nGrid saved to {out_file}")
    print(f"Tracks: {grid.Track.nunique()}  Rows: {len(grid)}")
    print(grid.head())

