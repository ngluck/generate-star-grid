# Usage

## Setting Up a Grid Run Directory

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

`--min_mass`/`--max_mass`/`--num_points`/`--grid_type` are the default way to specify
a continuous mass sweep. `--mass SPEC`, if given, overrides them and accepts the full
grammar above.

### Extra Inlist Parameters (`--param`)

To set or sweep any parameter from `inlist_template` that doesn't have its own flag,
use `--param KEY=SPEC` (repeatable):

```bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 --num_points 4 \
    --initial_Z 0.014 0.02 \
    --param 'overshoot_f(1)=0.01,0.02'
```

If `KEY` doesn't match anything in `inlist_template`, an error is raised before any
models are built, listing close matches and all available parameters.

## Running a Grid

### Dry Run: Preview a Grid Before Running

Add `--dry_run` to any command to print a plan summary and exit without running any models.
This is a good first step before committing to a full run:

```bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 --num_points 4 \
    --initial_Z 0.014 0.02 \
    --param 'overshoot_f(1)=0.01,0.02' \
    --dry_run
```

### Local Parallel Run (Small Grids / Testing)

For small grids or testing your setup before scaling up, run locally:

```bash
cd my_grid_run/
python -m generate_star_grid.grid_utils \
    --min_mass 0.9 --max_mass 1.1 \
    --grid_type linear --num_points 8 \
    --max_workers 4
```

Use `--max_workers 1` for serial/debug mode.

### Sobol Sampling

For Sobol grids, `--num_points` must be a power of 2:

```bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type sobol --num_points 128 \
    --task_id $SLURM_ARRAY_TASK_ID
```

### SLURM Job Array (Recommended for Large Grids)

Copy `slurm/generate_grid_week_array.sh` into the parent directory of your run,
edit the configuration variables at the top, and submit:

```bash
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

## Continuation Runs (Post-MS Evolution)

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

`grid_utils_cont` accepts the same flags as `grid_utils`.

## Diagnosing Failed Array Tasks

From inside the grid run directory, run:

```bash
bash /path/to/slurm/find_failed.sh
```

This prints task IDs of failed/incomplete runs and a ready-to-use `sbatch --array=...`
resubmit command. To also clear corrupted `DATA/` folders before resubmitting:

```bash
bash /path/to/slurm/find_failed.sh clean
```
