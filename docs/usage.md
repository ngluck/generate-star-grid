# Basic Usage

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
See [`examples/inlist_template`](https://github.com/ngluck/generate-star-grid/blob/main/examples/inlist_template) for a reference inlist. For details on the required format and disk space expectations, see [Output Structure](output.md#inlist_template-format).
````

For where each of these files comes from and how to assemble the directory, see
[Setting Up Directories](installation.md#setting-up-directories-installation-does-not-create).

The template uses standard Fortran namelist syntax; `grid_utils` substitutes values for:

| Template line | Controlled by |
|---|---|
| `initial_mass = ...` | `--mass` (or `--min_mass` / `--max_mass` / `--num_points`) |
| `initial_z = ...` | `--initial_Z` |
| `initial_y = ...` | `--initial_Y` |
| `mixing_length_alpha = ...` | `--alpha_MLT` |
| any other settable parameter | `--param KEY=SPEC` (repeatable) |
| `log_directory = ...` | always set to `'DATA'` |
| `save_model_filename = ...` | set to `<STEM>_<run_dir_name>.mod`, where `STEM` comes from the name you declared (`TAMS_0.70.mod` → `TAMS`). Declare several to run [multiple stages](advanced_usage.md#multi-stage-runs) |

## Choosing the Grid Directory

`grid_utils` has **no `--grid_dir` flag**. It always uses the current working
directory as the grid directory: that is where it looks for `inlist_template`,
`mk`, `rn`, and `star`, and where it writes `notes.txt`, `LOGS/`, the per-model
directories, one `grid_<STEM>/` per save stage (`grid_TAMS/` for a
main-sequence run), `grid_inlists/`, and `grid_profiles/`.

Inside a SLURM script this is handled by the `cd` line at the top of the job
script. When running by hand, you point at the directory by launching from it:

````{tab-set}
```{tab-item} cd first
cd /path/to/my_grid_run
python -m generate_star_grid.grid_utils \
    --mass 0.7:1.2 --num_points 8 --max_workers 4
```
```{tab-item} Subshell (keeps your shell's cwd)
(cd /path/to/my_grid_run && python -m generate_star_grid.grid_utils \
    --mass 0.7:1.2 --num_points 8 --max_workers 4)
```
```{tab-item} env -C (no cd at all)
env -C /path/to/my_grid_run python -m generate_star_grid.grid_utils \
    --mass 0.7:1.2 --num_points 8 --max_workers 4
```
````

The run prints the directory it resolved before starting any models, so you can
confirm you are in the right place:

```text
Building MESA...
Grid directory: /path/to/my_grid_run
Running 8 models.
```

````{warning}
Launching from the wrong directory fails immediately rather than silently
writing to the wrong place — but the error depends on what is missing:

| Missing | Error |
|---|---|
| `inlist_template` | `FileNotFoundError: ... 'inlist_template'` |
| `mk` (local run) | `FileNotFoundError: ... './mk'` from the build step |
| `rn` or `star` | `FileNotFoundError: Required MESA file 'rn' not found in <cwd>` |

A `--dry_run` does **not** catch this: it only reads `inlist_template`, and only
when `--param` is used, so it can succeed from a directory that a real run would
fail in.
````

````{note}
Only `grid_utils` is cwd-based. The post-processing and submission tools take
the directory explicitly, so they can be run from anywhere:

```bash
python -m generate_star_grid.make_grid --parent_dir /path/to/my_grid_run --save
python -m generate_star_grid.merge_grids merge --batch_dirs /path/to/batch_* --output_dir /path/to/merged
python -m generate_star_grid.submit_grid start --source_dir /path/to/my_grid_run ...
python -m generate_star_grid.grid_inventory --parent_dir /path/to/merged
```
````

## Specifying Parameter Values

`--mass`, `--initial_Z`, `--initial_Y`, `--alpha_MLT`, and `--param KEY=SPEC`
all accept the same grammar for describing one or more values for a parameter:

| Spec | Meaning |
|---|---|
| `VALUE` | held constant |
| `V1,V2,V3,...` | explicit list of specific values (discrete sweep) |
| `MIN:MAX` | continuous range, sampled at `--num_points` values via `--grid_type` |
| `MIN:MAX:STEP` | explicit values from `MIN` to `MAX`, spaced by `STEP`, inclusive of both endpoints |
| `MIN:MAX:log` | continuous range sampled evenly in log₁₀ space (`MIN`, `MAX` must be positive); use for parameters spanning several OOM, e.g. metallicity |

Multiple swept parameters are combined via Cartesian product — e.g. 200 mass points × 2 Z values = 400 models.

`--mass`, `--initial_Z`, `--initial_Y`, and `--alpha_MLT` are `nargs="+"`, so
an explicit list can be written as space-separated or comma-separated values — both are equivalent.
`MIN:MAX` and `MIN:MAX:STEP` specs must be given as a single token (no spaces).

````{dropdown} Examples
```bash
--initial_Z 0.02                        # constant
--initial_Z 0.014 0.02                  # 2 specific values
--initial_Z 0.01:0.03                   # continuous range, sampled via --num_points/--grid_type
--initial_Z 1e-4:0.04:log               # continuous range sampled evenly in log10 space
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

### Disk Cleanup During a Run

Each model directory gets a copy of the `star` binary (~54 MB) so MESA can run
inside it. By default the copy is deleted as soon as that model completes
successfully, keeping a large grid from filling the disk with identical
binaries; copies from failed models are kept so they can be debugged. Pass
`--no_cleanup_star` to keep all of them. See
[Output Structure](output.md#the-star-binary-in-model-directories).

### Sobol Sampling

Grids are evenly spaced (`--grid_type linear`) by default. As an alternative,
`--grid_type sobol` draws a quasi-random [Sobol sequence](https://en.wikipedia.org/wiki/Sobol_sequence)
that fills the parameter space more uniformly than a random draw. This is
especially useful when sweeping several parameters at once (e.g. mass,
metallicity, and mixing-length alpha): every parameter given as a `MIN:MAX`
range is sampled *jointly* in one `--num_points`-point cloud, rather than as a
full Cartesian product that grows exponentially with each added dimension.

````bash
python -m generate_star_grid.grid_utils \
    --mass 0.7:1.8 \
    --initial_Z 1e-4:0.04:log \
    --alpha_MLT 1:3 \
    --grid_type sobol --num_points 8192 --sobol_seed 0 \
    --task_id $SLURM_ARRAY_TASK_ID
````

For Sobol grids, `--num_points` is the *total* number of samples (not points per
dimension) and must be a power of 2.

````{tip}
For a parameter that spans several OOM (e.g. metallicity over
`1e-4:0.04`, ~2.6 OOM), use the `MIN:MAX:log` spec so it is sampled evenly in
log₁₀ space. A plain `MIN:MAX` range is sampled linearly, which for metallicity
would place ~90% of models in the top half-OOM and almost none metal-poor;
`1e-4:0.04:log` instead spreads models evenly across every OOM (~38% land
below `Z=1e-3`). Narrow-range parameters like mass and mixing-length alpha can
stay linear.
````

````{warning}
If `--num_points` is not a power of 2, the dry run will warn you before any models are built.
````

````{note}
`--sobol_seed` (default `0`) seeds the Sobol scramble. Because every SLURM array
task rebuilds the grid independently and selects its own `--task_id`, a **fixed
seed is required** for all tasks to agree on the same sampled points — the
default of `0` ensures this. Pass a different integer to draw a different cloud.
The seed is recorded in `notes.txt` for provenance. Only `MIN:MAX` ranges are
Sobol-sampled; parameters given as explicit lists are still combined via
Cartesian product, and `--grid_type linear` ignores the seed entirely.
````

### SLURM Job Array

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

````{tip}
If a task hits the SLURM `--time` limit before finishing, jobs submitted via
`submit_grid start`/`expand` automatically resume it from its last MESA
checkpoint on retry rather than starting over — see
[Resuming Timed-Out Runs](troubleshooting.md#resuming-timed-out-runs-photo-restart).
````

Below is a working template for the array job script, reflecting the resource
allocations used for a 500-track `day`-partition grid:

````bash
#!/bin/bash
#SBATCH --job-name=mesa_grid
#SBATCH --array=0-499
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition=day
#SBATCH --nodes=1
#SBATCH --time=12:00:00
#SBATCH --mem=8G
#SBATCH --mail-type=ALL
#SBATCH --mail-user=your@email.edu
#SBATCH --output=/path/to/batch_dir/slurm_%A_%a.out

cd "/path/to/batch_dir" || { echo "FATAL: cannot cd to /path/to/batch_dir" >&2; exit 1; }

module purge
module load miniconda
conda activate my_env

export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

python -m generate_star_grid.grid_utils \
    --initial_Z 0.014 \
    --mass 0.7:1.2 \
    --grid_type linear \
    --num_points 500 \
    --task_id=$SLURM_ARRAY_TASK_ID
````

