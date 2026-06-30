# Output Structure

## Directory Layout

::::{tab-set}
:::{tab-item} Before Running
```{code-block} text
my_grid_run/
├── inlist_template
├── inlist
├── inlist_pgstar
├── history_columns.list
├── profile_columns.list
├── rn
├── star
└── mk
```
:::
:::{tab-item} After Running
```{code-block} text
:emphasize-lines: 9,10,11,12,13,14,15,16,17,18,19,20
my_grid_run/
├── inlist_template
├── inlist
├── inlist_pgstar
├── history_columns.list
├── profile_columns.list
├── rn
├── mk
├── notes.txt                                  # constant/swept params, spacing, formats used
├── M_0.70_Y_0.27_Z_0.02_alpha_2.0/           # one per model
│   ├── DATA/
│   │   ├── history.data
│   │   ├── profile1.data
│   │   ├── profile1.data.GYRE
│   │   └── profiles.index
│   └── inlist_project
├── grid_TAMS/                                 # saved model at TAMS, one per model
├── grid_inlists/                              # archived inlist, one per model
├── grid_profiles/                             # copied profile files, one subdir per model
└── LOGS/                                      # one log per array task
```
:::
::::

```{note}
Items highlighted above are added by the pipeline after all array tasks complete.
```

## Directory Naming and `notes.txt`

Directory names always include all four `PARAM_FORMAT` parameters:
`M_<...>_Y_<...>_Z_<...>_alpha_<...>`. Any extra parameters added via `--param`
are appended after these four in the order they were given.

The number of decimal places for each value is chosen automatically so every
grid point gets a unique label. A `notes.txt` file records which parameters
were held constant, which were swept, and the format used for each.

## `inlist_template` Format

`grid_utils` works by **substituting** values into lines that already exist in
`inlist_template` — it does not insert new lines. Every parameter you want to
sweep or fix must already appear in the file with a placeholder value. The key
lines are:

```fortran
&controls
    initial_mass = 0.7000      ! swept via --mass
    initial_z = 0.0200         ! swept via --initial_Z
    initial_y = 0.2700         ! swept via --initial_Y
    mixing_length_alpha = 2.0  ! swept via --alpha_MLT
    log_directory = 'DATA'     ! always overridden to 'DATA'
    save_model_filename = 'TAMS_0.70.mod'  ! always overridden per run
```

Any other parameter you want to sweep via `--param KEY=SPEC` must also be
present as a line in the file. Array-indexed parameters use standard Fortran
notation, e.g. `overshoot_f(1) = 0.01`.

````{note}
`log_directory` and `save_model_filename` are always overridden automatically —
you do not need to set them correctly in the template.
````

## Disk Space Guidance

The dry run prints an estimated disk usage based on `--avg_data_mb` (default:
20 MB per model). Real-world usage from typical main-sequence grids:

| Grid size | Approx. disk usage |
|---|---|
| 16 tracks (local test) | ~320 MB |
| 500 tracks (one batch) | ~10 GB |
| 500 tracks × 10 Z values (multi-batch) | ~10 GB peak (one batch at a time) |

Peak usage for multi-batch grids is bounded by a single batch — completed
batches are cleaned up before the next one starts. After `combined_history.hdf5`
is written, the per-model `DATA/` folders are deleted, leaving only the HDF5
(typically 1–3 GB per 500-track batch).

If your models run significantly longer or shorter than a typical main-sequence
track, override the estimate with `--avg_data_mb`:

```bash
python -m generate_star_grid.grid_utils ... --avg_data_mb 50 --dry_run
```

## Saved Profile Files (`grid_profiles/`)

If a model's `DATA/` contains any `profile*.data` files, they are copied —
along with matching `profile*.data.GYRE` pulse files and `profiles.index` —
into `grid_profiles/<run_dir_name>/` after the run finishes.

- With `profile_interval = -1` (the default), MESA writes one profile at
  termination, giving a single `profile1.data` per model.
- Set `profile_interval = N` (`N > 0`) to save a profile every `N` steps.
- These are copies — the originals stay in `DATA/` and are handled by
  `--cleanup` separately.
- If a model never wrote any profile files, no subdirectory is created for it.
