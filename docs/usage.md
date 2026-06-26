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
See [`examples/inlist_template`](https://github.com/ngluck/generate-star-grid/blob/main/examples/inlist_template) for a reference inlist.
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

### Multi-Batch Grids (Recommended When the Full Grid Won't Fit on Disk)

For grids too large to keep on disk all at once (e.g. 500 masses × 10 metallicities),
`submit_grid.py` splits the sweep into an **outer** parameter (processed sequentially,
one disk-bounded batch at a time) and an **inner** parameter (swept within each
batch's SLURM array). Each batch: copies the template directory, submits the array job,
then submits a combine/cleanup job that builds that batch's `combined_history.hdf5`,
retries any failed tasks once (see below), deletes the batch's run artifacts, and only
then triggers the next outer batch — so peak disk usage is bounded by a single batch's
footprint, not the whole grid's.

````{note}
Each batch's `combined_history.hdf5` gets a constant column for both its inner
key (e.g. `M`) and every outer key fixed for that batch (e.g. `Z`) — both are
present in each per-star subdirectory's name (e.g.
`M_0.700_Y_0.27_Z_0.000379_alpha_2.0`), so both are extracted.
````

````{tip}
If the environment variable `SEISTRON_BASE_DIR` is set, each combine job also
plots a quick HR diagram (evenly spaced tracks colored by mass) into the batch
directory next to `combined_history.hdf5`, via a sibling project's
`my_library.grid_builders.plot_grid_hr_diagram` module, as a visual sanity
check that the grid looks as expected. This step is entirely optional: it's
skipped with a one-line message if the variable isn't set, and a plotting
failure only logs a warning rather than failing the combine/cleanup job.
````

`--outer` and `--inner` both accept repeatable `KEY=SPEC` arguments, using the same
grammar as `--param` (built-in aliases `mass`/`y`/`z`/`alpha`, or any other
`inlist_template` parameter):

````bash
python -m generate_star_grid.submit_grid start \
    --source_dir /path/to/clean/template_dir \
    --queue_file /path/to/queue.json \
    --outer 'initial_z=0.001,0.0015,0.0023,...,0.04' \
    --inner mass=0.7:1.2 --grid_type linear --num_points 500
````

````{tip}
Add `--dry_run` to preview the batch count, models-per-batch, and example batch
directory names without writing the queue file or submitting anything.
````

This submits the first batch and writes `queue.json`, which tracks the remaining
outer batches and all per-batch configuration. Each batch's combine/cleanup job
calls `submit_grid next --queue_file ...` itself once it actually finishes, rather
than via a pre-declared SLURM dependency — this is what lets a failed-task retry
happen first without losing track of when the batch is really done.

Key SLURM flags for the array jobs (all overridable via `submit_grid start`):

| Flag | Default | Notes |
|---|---|---|
| `--array_partition` | `day` | Partition for each batch's SLURM array; `day` allows up to 1000 simultaneous CPUs |
| `--array_time` | `12:00:00` | Per-task time limit; individual MESA tasks typically finish in under 10h |
| `--array_mem` | `8G` | Per-task memory; actual MESA usage is typically 4–5 GB |
| `--combine_partition` | `day` | Partition for the combine/cleanup job after each batch |
| `--combine_time` | `2:00:00` | Wall time for the combine/cleanup job |
| `--combine_mem` | `16G` | Memory for the combine/cleanup job |

See `submit_grid start --help` for the full list of overridable flags.

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
```{tab-item} Any swept parameters
python -m generate_star_grid.submit_grid check-failed \
    --dest /path/to/grid_run --keys M,Y,Z,alpha
```
````

````{warning}
Cleaning corrupted `DATA/` folders with `clean` is irreversible. Always review the
list of failed tasks before resubmitting.
````

`find_failed.sh` hardcodes a single Y/Z/alpha combination in its model-directory
naming guess, so it only works for single-batch grids swept over mass alone. For
grids with other or multiple swept parameters, use `submit_grid check-failed`
(backed by `find_failed_tasks()` in `grid_utils.py`), which reconstructs each
task's model directory name directly from its `LOGS/log_..._TASK_<id>.txt`
filename instead of assuming a fixed naming pattern. This is also what each
`submit_grid`-managed batch's combine/cleanup job uses internally to retry failed
tasks once before finalizing.
