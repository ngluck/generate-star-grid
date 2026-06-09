#!/usr/bin/env python3
import argparse
import pandas as pd

def get_prefix(name):
    """
    Extract prefix such as '0.725_solar' from strings like '0.725_solar_0001'.
    Modify this if your filename pattern is different.
    """
    return "_".join(name.split("_")[:2])  # keeps '0.725_solar'

def main():
    parser = argparse.ArgumentParser(description="Add a Track column and overwrite the HDF5 file.")
    parser.add_argument("file", help="Path to the HDF5 file to update")
    parser.add_argument("--column", default="filename",
                        help="Column containing filenames (default: 'filename')")
    args = parser.parse_args()

    # ---- READ FILE ----
    df = pd.read_hdf(args.file)

    if args.column not in df.columns:
        raise ValueError(f"Column '{args.column}' not found. Columns = {list(df.columns)}")

    print(f"Loaded {len(df)} rows from {args.file}")
    print(f"Assigning Track IDs based on column '{args.column}'...")

    # ---- EXTRACT PREFIXES ----
    prefixes = df[args.column].apply(get_prefix)

    # ---- ASSIGN TRACK NUMBERS ----
    unique_prefixes = {p: i+1 for i, p in enumerate(prefixes.unique())}
    df["Track"] = prefixes.map(unique_prefixes)

    n_tracks = len(unique_prefixes)

    print(f"Found {n_tracks} unique tracks.")
    print("\nPreview of updated DataFrame:")
    print(df.head())

    # ---- OVERWRITE ORIGINAL FILE ----
    df.to_hdf(args.file, key="data", mode="w")
    print(f"\nUpdated file written in-place → {args.file}")

    print(f"\nDone. Total unique Track values: {n_tracks}")

if __name__ == "__main__":
    main()

