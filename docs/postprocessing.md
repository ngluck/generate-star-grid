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
