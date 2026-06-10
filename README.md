# generate-star-grid

Python tools for generating grids of MESA stellar evolutionary tracks and
post-processing their output into HDF5 files for downstream ML pipelines.

Supports linear and Sobol-sampled grids over any combination of MESA parameters
(initial mass, metallicity Z, helium abundance Y, mixing-length α, etc.) with
SLURM job-array submission for HPC clusters.

---

## Requirements

### MESA
- MESA r24.08.1 (or compatible) compiled and available in your run directory
- Each grid run directory must contain the compiled MESA executables: `rn`, `star`, `mk`
- Standard MESA support files: `inlist`, `inlist_pgstar`, `history_columns.list`, `profile_columns.list`

### Python
- Python ≥ 3.9
- Dependencies (installed automatically): `numpy`, `pandas`, `scipy`, `tables`

---

## Installation

Clone the repo and install in editable mode into your Python environment:

```bash
git clone git@github.com:ngluck/generate-star-grid.git
cd generate-star-grid
pip install -e .
```

On a cluster, activate your environment first:

```bash
module load miniconda
conda activate your_venv
pip install -e /path/to/generate-star-grid
```

You only need to do this once per environment. After that, `python -m generate_star_grid.grid_utils` works from any directory.

---

## Setting up a grid run directory

Each grid run lives in its own directory. The minimum required contents are:

```
my_grid_run/
├── inlist_template       # MESA inlist with placeholder parameter values
├── inlist                # top-level MESA inlist (calls inlist_project)
├── inlist_pgstar         # pgstar settings (pgstar_flag = .false. recommended)
├── history_columns.list
├── profile_columns.list
├── rn                    # compiled MESA run script
├── star                  # compiled MESA binary
└── mk                    # MESA build script
```

See `examples/inlist_template` for a reference inlist. The template uses standard
Fortran namelist syntax; `grid_utils` substitutes values for:

| Template line | Controlled by |
|---|---|
| `initial_mass = ...` | `--mass` (or `--min_mass` / `--max_mass` / `--num_points`) |
| `initial_z = ...` | `--initial_Z` |
| `initial_y = ...` | `--initial_Y` |
| `mixing_length_alpha = ...` | `--alpha_MLT` |
| any other settable parameter | `--param KEY=SPEC` (repeatable) |
| `log_directory = ...` | always set to `'DATA'` |
| `save_model_filename = ...` | always set to `TAMS_<mass>.mod` |

`--mass`, `--initial_Z`, `--initial_Y`, `--alpha_MLT`, and `--param KEY=SPEC`
all accept the same value-spec grammar — see
[Specifying parameter values](#specifying-parameter-values) below.

---

## Specifying parameter values

`--mass`, `--initial_Z`, `--initial_Y`, `--alpha_MLT`, and `--param KEY=SPEC`
all accept the same grammar for describing one or more values for a parameter:

| Spec | Meaning |
|---|---|
| `VALUE` | held constant |
| `V1,V2,V3,...` | explicit list of specific values (discrete sweep) |
| `MIN:MAX` | continuous range, sampled at `--num_points` values via `--grid_type` (`linear` = evenly spaced, `sobol` = quasi-random) |
| `MIN:MAX:STEP` | explicit values from `MIN` to `MAX`, spaced by `STEP`, **inclusive of both endpoints** |

For `MIN:MAX:STEP`, if `(MAX - MIN)` isn't an exact multiple of `STEP`, the
final interval is shorter so that `MAX` is always included exactly, e.g.
`0.7:1.25:0.1` → `[0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.25]`.

Multiple swept parameters (continuous or discrete) are combined via Cartesian
product — e.g. 200 mass points × 2 Z values = 400 models.

`--mass`, `--initial_Z`, `--initial_Y`, and `--alpha_MLT` are `nargs="+"`, so
an explicit list can also be written as separate space-separated values
(`--initial_Z 0.014 0.02`) instead of comma-separated (`--initial_Z
0.014,0.02`) — both are equivalent. `MIN:MAX` and `MIN:MAX:STEP` specs must be
given as a single token (no spaces).

Examples:

```bash
--initial_Z 0.02                        # constant
--initial_Z 0.014 0.02                  # 2 specific values
--initial_Z 0.01:0.03                   # continuous range, sampled via --num_points/--grid_type
--initial_Z 0.01:0.03:0.005             # 5 specific values: 0.01, 0.015, 0.02, 0.025, 0.03
--mass 0.7:1.2:0.1                      # 6 specific masses: 0.7, 0.8, ..., 1.2
--param 'overshoot_f(1)=0.0:0.04:0.01'  # 5 specific values for an extra inlist param
```

### Mass: `--mass` vs `--min_mass`/`--max_mass`

`--min_mass`/`--max_mass`/`--num_points`/`--grid_type` remain the default way
to specify a continuous mass sweep (unchanged from before). `--mass SPEC`, if
given, overrides them and accepts the full grammar above — e.g. `--mass
0.7:1.2:0.05` for an explicit list of masses spaced by 0.05, or `--mass
0.8,1.0,1.5,2.0` for a non-uniform list of specific masses.

### Extra inlist parameters (`--param`)

To set or sweep any parameter from `inlist_template` that doesn't have its
own flag, use `--param KEY=SPEC` (repeatable):

```bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 --num_points 4 \
    --initial_Z 0.014 0.02 \
    --param 'overshoot_f(1)=0.01,0.02'
```

`KEY` is matched case-insensitively against the parameters actually settable
in `inlist_template` (including array indices like `overshoot_f(1)`). If
`KEY` doesn't match anything, `--param` raises an error before any models are
built, listing close matches and the full list of available parameters:

```
ValueError: Parameter 'overshoot_fbase' not found in inlist_template. Did you
mean: overshoot_f(2), overshoot_f(1), overshoot_f0(2), overshoot_f0(1),
overshoot_scheme(2)?
Available parameters in inlist_template:
  ...
```

Extra parameters set via `--param` are appended to directory, log, and
inlist-archive names (with `()` stripped from the label, e.g.
`..._overshoot_f1_0.010`), and get their own entry in `notes.txt`.

---

## Running a grid

### SLURM job array (recommended for large grids)

Copy `slurm/generate_grid_week_array.sh` into the parent directory of your run,
edit the configuration variables at the top, and submit:

```bash
# Edit GRID_DIR, mass range, --num_points, and --array to match
sbatch generate_grid_week_array.sh
```

The `--array` index must match `--num_points` (array `0-N` for `N+1` points).

Each array task runs one MESA model:

```bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type linear --num_points 200 \
    --task_id $SLURM_ARRAY_TASK_ID
```

Additional fixed parameters can be passed:

```bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 \
    --initial_Z 0.014 --initial_Y 0.27 --alpha_MLT 1.8 \
    --grid_type linear --num_points 200 \
    --task_id $SLURM_ARRAY_TASK_ID
```

### Local parallel run (small grids / testing)

```bash
cd my_grid_run/
python -m generate_star_grid.grid_utils \
    --min_mass 0.9 --max_mass 1.1 \
    --grid_type linear --num_points 8 \
    --max_workers 4
```

Use `--max_workers 1` for serial/debug mode.

### Sobol sampling

For Sobol grids, `--num_points` must be a power of 2:

```bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type sobol --num_points 128 \
    --task_id $SLURM_ARRAY_TASK_ID
```

### Dry run: preview a grid before running it

Add `--dry_run` to any of the commands above to print a plan summary and
exit without building MESA or running any models:

```bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 --num_points 4 \
    --initial_Z 0.014 0.02 \
    --param 'overshoot_f(1)=0.01,0.02' \
    --dry_run
```

```
============================================================
DRY RUN: grid plan (no MESA models will be built or run)
============================================================

Constant parameters:
  initial_y (Y) = 0.27
  mixing_length_alpha (alpha) = 2.0

Swept parameters:
  initial_mass (M): 0.7 to 1.2, 4 points (linear), spacing ~ 0.166667
  initial_z (Z): 2 value(s) = [0.014, 0.02]
  overshoot_f(1) (overshoot_f1): 2 value(s) = [0.01, 0.02]

Model count:
  4 stars varying M
  8 total stars varying M, Z
  16 total stars varying M, Z, overshoot_f1

Estimated disk usage:
  ~20 MB/model x 16 model(s) ~ 0.3 GB total (before any --cleanup)
  (default avg_data_mb is a rough estimate from prior grids; override with --avg_data_mb)

Example directory/file names:
  M_0.7_Y_0.27_Z_0.014_alpha_2.0_overshoot_f1_0.01/
  M_1.0_Y_0.27_Z_0.014_alpha_2.0_overshoot_f1_0.01/
  M_1.2_Y_0.27_Z_0.020_alpha_2.0_overshoot_f1_0.02/
  grid_TAMS/TAMS_0.700000.mod
  grid_inlists/inlist_M_0.7_Y_0.27_Z_0.014_alpha_2.0_overshoot_f1_0.01
  LOGS/log_M_0.7_Y_0.27_Z_0.014_alpha_2.0_overshoot_f1_0.01_TASK_0.txt   (for SLURM array runs)
  notes.txt

SLURM array:
  --array=0-15
============================================================
```

The disk estimate uses `--avg_data_mb` (default 20 MB/model, a rough average
from prior grids — override it for grids that run much longer or shorter than
usual). For Sobol grids, this also warns if `--num_points` isn't a power of 2.

For grids with a long list of values (e.g. `--mass 0.7:2.0:0.05`, 27 values),
the "Swept parameters" line is condensed to its endpoints and spacing instead
of listing every value, e.g. `initial_mass (M): 27 value(s) = 0.7 to 2.0
(spacing 0.05)`.

---

## Output structure

After all array tasks complete, each run directory will contain:

```
my_grid_run/
├── notes.txt                  # which params are constant/swept, spacing, formats used
├── M_0.70_Y_0.27_Z_0.02_alpha_2.0/
│   ├── DATA/
│   │   └── history.data
│   └── inlist_project
├── grid_TAMS/
│   └── TAMS_0.700000.mod      # saved model at TAMS
├── grid_inlists/
│   └── inlist_M_0.70_..._     # archived inlist for each run
└── LOGS/
    └── log_M_0.70_..._TASK_0.txt
```

### Directory naming and `notes.txt`

`M_<...>_Y_<...>_Z_<...>_alpha_<...>` directory names always include all four
`PARAM_FORMAT` parameters (`initial_mass`, `initial_y`, `initial_z`,
`mixing_length_alpha`) — `M` is always the model's *initial* mass, even though
mass may decrease over the evolution due to mass loss in continuation runs.
Any extra parameters added via `--param` are appended after these four, in
the order they were given (e.g. `..._alpha_2.0_overshoot_f1_0.010`).

The number of decimal places used for each value is chosen automatically
(`compute_param_formats`): for a continuously swept parameter, the fewest
decimals needed so every grid point gets a unique label given its spacing;
for a discretely swept parameter (e.g. `--initial_Z 0.014 0.02` or any
`MIN:MAX:STEP` spec), the fewest decimals that represent every listed value
exactly; for fixed parameters, the fewest decimals that represent the value
exactly. A `notes.txt` file is written into the grid directory recording which
parameters were held constant (and their values), which parameter(s) were
swept (range/values, spacing, number of points), and the format used for each
— so you don't have to reverse-engineer the precision later. Long discrete
lists are condensed to their endpoints and spacing, same as in `--dry_run`.

---

## Post-processing: combining histories into HDF5

After all runs finish, combine the per-track `history.data` files into a single
HDF5 file for downstream analysis:

```bash
python -m generate_star_grid.make_grid \
    --parent_dir /path/to/my_grid_run \
    --save \
    --hdf5_filename combined_history.hdf5 \
    --constants M Y Z alpha
```

`--constants` is parsed from each model's directory name
(`extract_constants_from_subdir_name`), which looks for each key as a
`<key>_<value>` token bounded by underscores (or the start/end of the name).
This works regardless of how many decimal places `compute_param_formats`
chose, and regardless of whether the label itself contains underscores, so
extra `--param` parameters can be included too:

```bash
python -m generate_star_grid.make_grid \
    --parent_dir /path/to/my_grid_run \
    --save \
    --constants M Y Z alpha overshoot_f1
```

This writes `combined_history.hdf5` into the grid run directory, with one row
per timestep and columns for all history quantities plus the requested constants.

### Cleaning up `DATA/` after combining

Once `combined_history.hdf5` has been written, the per-model `DATA/` folders
(containing `history.data`, profiles, etc.) are no longer needed and can take
up significant space. Pass `--cleanup zip` or `--cleanup delete`:

```bash
python -m generate_star_grid.make_grid \
    --parent_dir /path/to/my_grid_run \
    --save --cleanup zip \
    --constants M Y Z alpha
```

- `zip` archives each model's `DATA/` to `DATA.zip` in the same directory,
  then removes `DATA/`.
- `delete` removes `DATA/` without archiving.
- `none` (default) leaves `DATA/` alone.

Cleanup only runs after a successful `--save`, and only if **every** model
directory has a corresponding save file in `grid_TAMS/` — i.e. all SLURM
array jobs have finished (successfully or not). If some are still running or
failed without producing a TAMS file, cleanup is skipped entirely with a
message like:

```
Skipping cleanup: only 14/16 model directories have a TAMS save file in
grid_TAMS/. Some array jobs may still be running, or may have failed (see
slurm/find_failed.sh). Re-run with --cleanup once all jobs finish.
```

---

## Continuation runs (post-MS evolution)

To resume from TAMS save files and continue evolution:

```bash
cd my_grid_run/
python -m generate_star_grid.grid_utils_cont \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type linear --num_points 200 \
    --max_workers 8 \
    --resume \
    --resume_edit_path /path/to/update_inlist.py
```

The `--resume_edit_path` script must define:
- `resume_tag` (str): appended to archived inlist filenames
- `modifications` (list of callables): each takes `(inlist_text, params)` and returns modified text

`grid_utils_cont` accepts the same `--mass`, `--initial_Z`/`--initial_Y`/`--alpha_MLT`,
`--param`, `--dry_run`, and `--avg_data_mb` flags as `grid_utils` (see
[Specifying parameter values](#specifying-parameter-values)).

---

## Diagnosing failed array tasks

From inside the grid run directory, run:

```bash
bash /path/to/slurm/find_failed.sh
```

Prints task IDs of failed/incomplete runs and a ready-to-use
`sbatch --array=...` resubmit command. Each task's run directory is located
from its `M_<mass>` prefix (matching whatever precision was used by
`compute_param_formats`), so no per-grid configuration is needed.

To also clear corrupted `DATA/` folders before resubmitting:

```bash
bash /path/to/slurm/find_failed.sh clean
```

---

## Repository structure

```
generate-star-grid/
├── generate_star_grid/
│   ├── grid_utils.py        # core grid generation, inlist update, MESA execution
│   ├── grid_utils_cont.py   # continuation variant (resume from TAMS)
│   ├── resume_utils.py      # helpers for resume indexing and inlist modification
│   ├── make_grid.py         # post-processing: combine history files into HDF5
│   ├── make_starpasta_grid.py  # assign Track IDs to starpasta HDF5 files
│   └── make_yrec_grid.py    # assign Track IDs to YREC HDF5 files
├── slurm/
│   ├── generate_grid_week_array.sh  # template SLURM job array script
│   └── find_failed.sh               # detect and resubmit failed array tasks
├── examples/
│   └── inlist_template      # reference MESA inlist template
└── pyproject.toml
```
