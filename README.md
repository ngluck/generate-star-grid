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
| `initial_mass = ...` | `--min_mass` / `--max_mass` |
| `initial_z = ...` | `--initial_Z` |
| `initial_y = ...` | `--initial_Y` |
| `mixing_length_alpha = ...` | `--alpha_MLT` |
| `log_directory = ...` | always set to `'DATA'` |
| `save_model_filename = ...` | always set to `TAMS_<mass>.mod` |

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

---

## Output structure

After all array tasks complete, each run directory will contain:

```
my_grid_run/
├── M_0.700000_Y_0.270_Z_0.0200_alpha_2.00/
│   ├── DATA/
│   │   └── history.data
│   └── inlist_project
├── grid_TAMS/
│   └── TAMS_0.700000.mod      # saved model at TAMS
├── grid_inlists/
│   └── inlist_M_0.700000_...  # archived inlist for each run
└── LOGS/
    └── log_M_0.700000_..._TASK_0.txt
```

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

This writes `combined_history.hdf5` into the grid run directory, with one row
per timestep and columns for all history quantities plus the requested constants.

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

---

## Diagnosing failed array tasks

From inside the grid run directory, run:

```bash
bash /path/to/slurm/find_failed.sh
```

Edit the `FIXED_Y`, `FIXED_Z`, `FIXED_ALPHA` variables at the top to match
your grid's fixed parameters. Prints task IDs of failed/incomplete runs and
a ready-to-use `sbatch --array=...` resubmit command.

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
