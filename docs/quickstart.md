# Quickstart

This page walks through the full workflow from a MESA template directory to a
loaded `combined_history.hdf5` ready for analysis.

## 1. Prepare a Template Directory

Create a directory containing your compiled MESA executables and inlists:

```text
my_grid/
├── inlist_template       # MESA inlist with placeholder parameter values
├── inlist                # top-level MESA inlist (calls inlist_project)
├── inlist_pgstar         # pgstar settings (pgstar_flag = .false. recommended)
├── history_columns.list
├── profile_columns.list
├── rn                    # compiled MESA run script
├── star                  # compiled MESA binary
└── mk                    # MESA build script
```

See [`examples/inlist_template`](https://github.com/ngluck/generate-star-grid/blob/main/examples/inlist_template)
for a reference inlist showing the expected placeholder format. For details on
the required format and disk space expectations, see
[Output Structure](output.md#inlist-template-format).

## 2. Preview the Grid (Dry Run)

Always do a dry run first — it's instant and shows exactly what will be built:

```bash
python -m generate_star_grid.grid_utils \
    --mass 0.7:1.2 \
    --initial_Z 0.014 \
    --grid_type linear --num_points 16 \
    --dry_run
```

Check the model count, estimated disk usage, and example directory names before
committing to a full run.

## 3. Run the Grid

````{tab-set}
```{tab-item} Local (small grids / testing)
python -m generate_star_grid.grid_utils \
    --mass 0.7:1.2 \
    --initial_Z 0.014 \
    --grid_type linear --num_points 16 \
    --max_workers 4
```
```{tab-item} SLURM array job
# Submit as a SLURM array — --array must match --num_points (0-N for N+1 points)
sbatch --array=0-499 run_array.sh
```
```{tab-item} Multi-batch (large grids)
# Sweep mass as inner parameter over 10 metallicities as outer batches
python -m generate_star_grid.submit_grid start \
    --source_dir /path/to/my_grid \
    --queue_file /path/to/queue.json \
    --outer 'initial_z=0.001,0.002,0.005,0.01,0.014,0.02,0.025,0.03,0.035,0.04' \
    --inner 'mass=0.7:1.2' \
    --grid_type linear --num_points 500
```
````

For SLURM runs, see [Basic Usage](usage.md) for the full array script template.
For grids too large to keep on disk at once, see [Advanced Usage](advanced_usage.md).

## 4. Combine into HDF5

Once all MESA runs finish, combine the per-track `history.data` files:

```bash
python -m generate_star_grid.make_grid \
    --parent_dir /path/to/my_grid \
    --constants M Y Z alpha \
    --save
```

This writes `combined_history.hdf5` into the grid directory. For multi-batch grids
run via `submit_grid`, this step runs automatically at the end of each batch.

## 5. Load and Analyse in Python

```python
import pandas as pd

df = pd.read_hdf("/path/to/my_grid/combined_history.hdf5", key="history")

# Each unique integer in the Track column identifies one stellar evolution track
print(df["Track"].nunique(), "tracks")
print(df.columns.tolist())

# Select a single track
track = df[df["Track"] == 0]

# Plot an HR diagram
import matplotlib.pyplot as plt
for tid, grp in df.groupby("Track"):
    plt.plot(grp["log_Teff"], grp["log_L"], lw=0.5, alpha=0.5)
plt.gca().invert_xaxis()
plt.xlabel("log Teff"); plt.ylabel("log L")
plt.tight_layout(); plt.show()
```

See [Post-Processing](postprocessing.md) for more on the HDF5 structure and
how to work with the data.
