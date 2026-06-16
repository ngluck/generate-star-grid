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
:emphasize-lines: 9,10,11,12,13,14,15,16,17,18,19,20,21,22
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
