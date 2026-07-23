# Post-Processing

## Combining Histories into HDF5

After all runs finish, combine the per-track `history.data` files into a single
HDF5 file for downstream analysis:

```bash
python -m generate_star_grid.make_grid \
    --parent_dir /path/to/my_grid_run \
    --save \
    --hdf5_filename combined_history.hdf5 \
    --constants M Y Z alpha
```

`--constants` is parsed from each model's directory name. Extra `--param`
parameters can be included too:

```bash
python -m generate_star_grid.make_grid \
    --parent_dir /path/to/my_grid_run \
    --save \
    --constants M Y Z alpha overshoot_f1
```

This writes `combined_history.hdf5` into the grid run directory, with one row
per timestep and columns for all history quantities plus the requested constants.

## Loading `combined_history.hdf5` in Python

The HDF5 file is a pandas DataFrame stored under the key `"history"`:

```python
import pandas as pd

df = pd.read_hdf("/path/to/my_grid/combined_history.hdf5", key="history")
```

Each row is one timestep. The `Track` column (integer) identifies which stellar
evolution track a row belongs to, and the constant parameters (`M`, `Y`, `Z`,
`alpha`, and any extras passed to `--constants`) are repeated on every row of
that track.

```python
# How many tracks are in the grid
print(df["Track"].nunique())

# All column names
print(df.columns.tolist())

# Select all timesteps for one track
track0 = df[df["Track"] == 0]

# Select all tracks at a given metallicity
solar_z = df[df["Z"] == 0.014]

# Iterate over tracks
for tid, grp in df.groupby("Track"):
    print(tid, grp["star_age"].iloc[-1])
```

Key columns available in every grid:

| Column | Description |
|---|---|
| `Track` | Integer track ID, unique per stellar evolution track |
| `M` | Initial mass (M☉) |
| `Z` | Initial metallicity |
| `Y` | Initial helium abundance |
| `alpha` | Mixing-length parameter |
| `star_age` | Stellar age (years) |
| `log_Teff` | Log effective temperature |
| `log_L` | Log luminosity (L☉) |
| `log_R` | Log radius (R☉) |
| `log_g` | Log surface gravity |
| `center_h1` | Central hydrogen mass fraction |
| `center_he4` | Central helium mass fraction |
| `delta_nu` | Large frequency separation (μHz) |
| `nu_max` | Frequency of maximum oscillation power (μHz) |
| `delta_Pg` | Period spacing of g-modes (seconds) |

All other columns in the file come directly from your `history_columns.list`.

## Cleaning Up `DATA/` After Combining

Once `combined_history.hdf5` has been written, the per-model `DATA/` folders
can be archived or removed to save space. Pass `--cleanup zip` or `--cleanup delete`:

```bash
python -m generate_star_grid.make_grid \
    --parent_dir /path/to/my_grid_run \
    --save --cleanup zip \
    --constants M Y Z alpha
```

| Option | Behavior |
|---|---|
| `zip` | Archives each `DATA/` to `DATA.zip`, then removes `DATA/` |
| `delete` | Removes `DATA/` without archiving |
| `none` | Default — leaves `DATA/` untouched |

Cleanup only runs after a successful `--save`, and only if every model directory
has a corresponding save file in `grid_TAMS/`. If some jobs are still running
or failed, cleanup is skipped with an explanatory message.

## Merging Multi-Batch Grid Histories

For grids run via `submit_grid` (see [Multi-Batch Grids](advanced_usage.md#multi-batch-grids)),
each outer batch produces its own `combined_history.hdf5`. Once all batches are
complete, `merge_grids.py` combines them into a single file spanning the full
parameter space (e.g. the complete M×Z grid).

### Automatic merge (default)

Unless `--no_merge_after` is passed to `submit_grid start`, a final merge job
is submitted automatically when the last batch's combine/cleanup job finishes.
No manual action is needed.

### What the merge does

- Reads each batch's `combined_history.hdf5` in sorted directory order.
- Re-assigns `Track` values so they are globally unique across the merged file.
  Each batch's tracks are offset by the cumulative count of unique tracks in all
  prior batches — so if batch 1 has tracks `[2, 3, 4]`, batch 2's tracks
  `[2, 3, 4]` become `[5, 6, 7]`, giving contiguous ranges with no gaps.
- Creates a merged directory in `parent_dir` named after the varying parameters
  in canonical order, e.g. `mytemplate_varM_varZ` for a mass × metallicity grid.
- Writes the merged `combined_history.hdf5` at the top level of that directory.
- Moves all per-batch directories inside the merged directory, so each original
  per-batch HDF5 is preserved alongside the new merged one.

### Resulting directory structure

```text
parent_dir/
├── mytemplate_varM_varZ/               ← new merged directory
│   ├── combined_history.hdf5           ← full grid, Track values unique across all batches
│   ├── mytemplate_Z_0p001/             ← original batch directory, moved here
│   │   ├── combined_history.hdf5       ← per-batch HDF5 (preserved)
│   │   ├── notes.txt
│   │   └── ...
│   ├── mytemplate_Z_0p002/
│   │   ├── combined_history.hdf5
│   │   └── ...
│   └── ...
└── queue.json
```

### Manual invocation

If the automatic merge was skipped (e.g. for a run started before this feature
existed, or with `--no_merge_after`), run it manually:

````{tab-set}
```{tab-item} From queue.json (recommended)
python -m generate_star_grid.merge_grids \
    --queue_file /path/to/queue.json
```
```{tab-item} Explicit batch dirs
python -m generate_star_grid.merge_grids \
    --batch_dirs /path/to/batch1 /path/to/batch2 \
    --output_dir /path/to/merged_dir
```
```{tab-item} Preview (dry run)
python -m generate_star_grid.merge_grids \
    --queue_file /path/to/queue.json \
    --dry_run
```
````

`--queue_file` is the recommended form: it auto-discovers all batch directories
(any sibling of the merged dir that contains a `combined_history.hdf5`) and
derives the merged directory name from the queue config. The `--batch_dirs` form
is a fallback when no queue file is available, and requires `--output_dir` to
specify where the merged directory should be created.

### SLURM resource flags

The merge job reads every per-batch HDF5 in 100 k-row chunks so memory usage is
bounded regardless of grid size. The defaults are generous enough for most grids,
but all are overridable when calling `submit_grid start`:

| Flag | Default | Notes |
|---|---|---|
| `--merge_time` | `4:00:00` | Wall time for the merge SLURM job |
| `--merge_mem` | `32G` | Memory for the merge SLURM job |
| `--merge_partition` | same as `--combine_partition` | SLURM partition |
| `--merge_mail_type` | same as `--combine_mail_type` | Email notification trigger |
| `--no_merge_after` | *(flag, off by default)* | Skip the automatic merge entirely |
