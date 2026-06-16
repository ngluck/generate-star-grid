# Usage

## Setting Up a Grid Run Directory

Each grid run lives in its own directory. The minimum required contents are:

````
my_grid_run/
├── inlist_template       # MESA inlist with placeholder parameter values
├── inlist                # top-level MESA inlist (calls inlist_project)
├── inlist_pgstar         # pgstar settings (pgstar_flag = .false. recommended)
├── history_columns.list
├── profile_columns.list
├── rn                    # compiled MESA run script
├── star                  # compiled MESA binary
└── mk                    # MESA build script
````

````{tip}
See ['examples/inlist_template'](https://github.com/ngluck/generate-star-grid/blob/main/examples/inlist_template) for a reference inlist.
````

The template uses standard Fortran namelist syntax; `grid_utils` substitutes values for:

| Template line | Controlled by |
|---|---|
| `initial_mass = ...` | `--mass` (or `--min_mass` / `--max_mass` / `--num_points`) |
| `initial_z = ...` | `--initial_Z` |
| `initial_y = ...` | `--initial_Y` |
| `mixing_length_alpha = ...` | `--alpha_MLT` |
| any other settable parameter | `--param KEY=SPEC` (repeatable) |
| `log_directory = ...` | always set to `'DATA'` |
| `save_model_filename = ...` | always set to `TAMS_<run_dir_name>.mod` |

## Specifying Parameter Values

`--mass`, `--initial_Z`, `--initial_Y`, `--alpha_MLT`, and `--param KEY=SPEC`
all accept the same grammar for describing one or more values for a parameter:

| Spec | Meaning |
|---|---|
| `VALUE` | held constant |
| `V1,V2,V3,...` | explicit list of specific values (discrete sweep) |
| `MIN:MAX` | continuous range, sampled at `--num_points` values via `--grid_type` |
| `MIN:MAX:STEP` | explicit values from `MIN` to `MAX`, spaced by `STEP`, inclusive of both endpoints |

Multiple swept parameters are combined via Cartesian product — e.g. 200 mass points × 2 Z values = 400 models.

`--mass`, `--initial_Z`, `--initial_Y`, and `--alpha_MLT` are `nargs="+"`, so
an explicit list can be written as space-separated or comma-separated values — both are equivalent.
`MIN:MAX` and `MIN:MAX:STEP` specs must be given as a single token (no spaces).

````{dropdown} Examples
```bash
--initial_Z 0.02                        # constant
--initial_Z 0.014 0.02                  # 2 specific values
--initial_Z 0.01:0.03                   # continuous range, sampled via --num_points/--grid_type
--initial_Z 0.01:0.03:0.005             # 5 specific values: 0.01, 0.015, 0.02, 0.025, 0.03
--mass 0.7:1.2:0.1                      # 6 specific masses: 0.7, 0.8, ..., 1.2
--param 'overshoot_f(1)=0.0:0.04:0.01'  # 5 specific values for an extra inlist param
```
````

### Mass: `--mass` vs `--min_mass`/`--max_mass`

`--min_mass`/`--max_mass`/`--num_points`/`--grid_type` are the default way to specify
a continuous mass sweep. `--mass SPEC`, if given, overrides them and accepts the full
grammar above — e.g. `--mass 0.7:1.2:0.05` for an explicit list of masses spaced by
0.05, or `--mass 0.8,1.0,1.5,2.0` for a non-uniform list of specific masses.

### Extra Inlist Parameters (`--param`)

To set or sweep any parameter from `inlist_template` that doesn't have its own flag,
use `--param KEY=SPEC` (repeatable):

````bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 --num_points 4 \
    --initial_Z 0.014 0.02 \
    --param 'overshoot_f(1)=0.01,0.02'
````

`KEY` is matched case-insensitively against the parameters actually settable in
`inlist_template` (including array indices like `overshoot_f(1)`).

````{warning}
If `KEY` doesn't match anything in `inlist_template`, an error is raised before any
models are built, listing close matches and the full list of available parameters:

````
ValueError: Parameter 'overshoot_fbase' not found in inlist_template. Did you
mean: overshoot_f(2), overshoot_f(1), overshoot_f0(2), overshoot_f0(1),
overshoot_scheme(2)?
Available parameters in inlist_template:
  ...
````
````

Extra parameters set via `--param` are appended to directory, log, and inlist-archive
names (with `()` stripped from the label, e.g. `..._overshoot_f1_0.010`), and get
their own entry in `notes.txt`.

## Running a Grid

### Dry Run: Preview a Grid Before Running

````{tip}
Always do a dry run before committing to a full grid submission — it's instant and
shows you exactly what will be built.
````

Add `--dry_run` to any command to print a plan summary and exit without running any models:

````bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 --num_points 4 \
    --initial_Z 0.014 0.02 \
    --param 'overshoot_f(1)=0.01,0.02' \
    --dry_run
````

```{dropdown} Example dry run output
~~~
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
  grid_TAMS/TAMS_M_0.7_Y_0.27_Z_0.014_alpha_2.0_overshoot_f1_0.01.mod
  grid_inlists/inlist_M_0.7_Y_0.27_Z_0.014_alpha_2.0_overshoot_f1_0.01
  grid_profiles/M_0.7_Y_0.27_Z_0.014_alpha_2.0_overshoot_f1_0.01/
  LOGS/log_M_0.7_Y_0.27_Z_0.014_alpha_2.0_overshoot_f1_0.01_TASK_0.txt
  notes.txt

SLURM array:
  --array=0-15
============================================================
~~~
```

The disk estimate uses `--avg_data_mb` (default 20 MB/model — override it for grids
that run much longer or shorter than usual). For Sobol grids, this also warns if
`--num_points` isn't a power of 2. For grids with a long list of values, the swept
parameters line is condensed to endpoints and spacing instead of listing every value.
````

### Local Parallel Run (Small Grids / Testing)

For small grids or testing your setup before scaling up, run locally:

````bash
cd my_grid_run/
python -m generate_star_grid.grid_utils \
    --min_mass 0.9 --max_mass 1.1 \
    --grid_type linear --num_points 8 \
    --max_workers 4
````

````{tip}
Use `--max_workers 1` for serial/debug mode.
````

### Sobol Sampling

For Sobol grids, `--num_points` must be a power of 2:

````bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type sobol --num_points 128 \
    --task_id $SLURM_ARRAY_TASK_ID
````

````{warning}
If `--num_points` is not a power of 2, the dry run will warn you before any models are built.
````

### SLURM Job Array (Recommended for Large Grids)

Copy [`slurm/generate_grid_week_array.sh`](https://github.com/ngluck/generate-star-grid/blob/main/slurm/generate_grid_week_array.sh) 
into the parent directory of your run,
edit the configuration variables at the top, and submit:

````{tab-set}
```{tab-item} Submit job
sbatch generate_grid_week_array.sh
```
```{tab-item} Single array task
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type linear --num_points 200 \
    --task_id $SLURM_ARRAY_TASK_ID
```
```{tab-item} With fixed parameters
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 \
    --initial_Z 0.014 --initial_Y 0.27 --alpha_MLT 1.8 \
    --grid_type linear --num_points 200 \
    --task_id $SLURM_ARRAY_TASK_ID
```
````

````{note}
The `--array` index must match `--num_points` (array `0-N` for `N+1` points).
````

## Continuation Runs (Post-MS Evolution)

To resume from TAMS save files and continue evolution:

````bash
cd my_grid_run/
python -m generate_star_grid.grid_utils_cont \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type linear --num_points 200 \
    --max_workers 8 \
    --resume \
    --resume_edit_path /path/to/update_inlist.py
````

The `--resume_edit_path` script must define:
- `resume_tag` (str): appended to archived inlist filenames
- `modifications` (list of callables): each takes `(inlist_text, params)` and returns modified text

````{note}
`grid_utils_cont` accepts the same `--mass`, `--initial_Z`/`--initial_Y`/`--alpha_MLT`,
`--param`, `--dry_run`, and `--avg_data_mb` flags as `grid_utils`.
````

## Diagnosing Failed Array Tasks

From inside the grid run directory, run:

````{tab-set}
```{tab-item} Check failed tasks
bash /path/to/slurm/find_failed.sh
```
```{tab-item} Check and clean corrupted DATA/
bash /path/to/slurm/find_failed.sh clean
```
````

````{warning}
Cleaning corrupted `DATA/` folders with `clean` is irreversible. Always review the
list of failed tasks before resubmitting.
````
