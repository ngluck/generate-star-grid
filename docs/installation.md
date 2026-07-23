# Installation

## Requirements

### MESA
- MESA r24.08.1 (or compatible) compiled and available in your run directory
- Each grid run directory must contain the compiled MESA executables: `rn`, `star`, `mk`
- Standard MESA support files: `inlist`, `inlist_pgstar`, `history_columns.list`, `profile_columns.list`
- If you do not have MESA installed on your machine, see their [documentation](https://docs.mesastar.org/en/26.4.1/installation.html).

### Python
- Python ≥ 3.9
- Dependencies (installed automatically): `numpy`, `pandas`, `scipy`, `tables`

## From PyPI

```bash
pip install generate-star-grid
```

## From Source (Development)

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

````{note}
If you skip `pip install -e .` and run directly from the cloned directory, Python
won't find the package unless you add it to your path manually:

```bash
export PYTHONPATH=/path/to/generate-star-grid:$PYTHONPATH
```

Add this line to your `~/.bashrc` (or `~/.zshrc`) to make it permanent. The
`pip install -e .` approach is recommended instead, as it avoids having to set
this in every new shell or SLURM job.
````

## What Installation Creates

Installing puts the `generate_star_grid` **Python package** into your
environment's `site-packages` (or, with `-e`, links it back to your clone). That
is the only thing it creates:

- **No console scripts.** There is no `generate-star-grid` executable on your
  `PATH`. Every tool is invoked as a module, e.g.
  `python -m generate_star_grid.grid_utils`.
- **No files in your working directory.** Installation never creates a grid run
  directory, an `inlist_template`, or any MESA files — you set those up yourself
  (see [below](#setting-up-directories-installation-does-not-create)).

The modules installed, each runnable with `python -m generate_star_grid.<module>`:

| Module | Purpose |
|---|---|
| `grid_utils` | Build and run a grid of MESA models (the main entry point) |
| `submit_grid` | Build a multi-batch queue and submit/chain SLURM jobs |
| `chunk_grid` | Run a flat (e.g. Sobol) grid in disk-bounded task-id chunks |
| `make_grid` | Combine finished tracks into `combined_history.hdf5` |
| `merge_grids` | Merge or expand per-batch HDF5s into one grid |
| `grid_inventory` | Report outer-parameter coverage of a merged grid |
| `failure_report` | Collect every failed track and its reason into one report |
| `grid_utils_cont` | Continuation runs past TAMS |
| `make_yrec_grid`, `make_starpasta_grid` | HDF5 builders for YREC / StarPASTA output |

Verify the install:

```bash
python -c "import generate_star_grid; print(generate_star_grid.__file__)"
python -m generate_star_grid.grid_utils --help
```

````{warning}
The repository's `examples/` and `slurm/` directories are **not** part of the
installed package — `pip install generate-star-grid` does not give you
`examples/inlist_template` or the SLURM templates. Either clone the repo, or
download the files you need directly:

```bash
curl -O https://raw.githubusercontent.com/ngluck/generate-star-grid/main/examples/inlist_template
curl -O https://raw.githubusercontent.com/ngluck/generate-star-grid/main/slurm/generate_grid_week_array.sh
```
````

## Setting Up Directories Installation Does Not Create

The pipeline runs inside a **grid run directory** that you create. `grid_utils`
resolves every path relative to the directory it is launched from (see
[Choosing the Grid Directory](usage.md#choosing-the-grid-directory)), so the
files below must sit together in that one directory.

```text
my_grid_run/                    # you create this — any name, any location
├── inlist_template             # REQUIRED — you write this (see examples/)
├── rn                          # REQUIRED — from the MESA work directory
├── star                        # REQUIRED — produced by ./mk
├── mk                          # REQUIRED for local runs (grid_utils runs ./mk)
├── re                          # optional — needed for photo restarts
├── inlist                      # top-level inlist; must read inlist_project
├── inlist_pgstar               # pgstar settings (pgstar_flag = .false.)
├── history_columns.list        # optional — MESA defaults used if absent
└── profile_columns.list        # optional — MESA defaults used if absent
```

### Where Each File Comes From

| File | Source |
|---|---|
| `rn`, `re`, `mk`, `inlist`, `inlist_pgstar` | Copied from a MESA work directory: `cp -r $MESA_DIR/star/work my_grid_run` |
| `star` | Built by running `./mk` inside `my_grid_run` |
| `history_columns.list`, `profile_columns.list` | Copied from `$MESA_DIR/star/defaults/` and edited to select the columns you want |
| `inlist_template` | Written by you — start from [`examples/inlist_template`](https://github.com/ngluck/generate-star-grid/blob/main/examples/inlist_template) |

A working setup from scratch:

```bash
cp -r $MESA_DIR/star/work my_grid_run
cd my_grid_run
cp $MESA_DIR/star/defaults/history_columns.list .
cp $MESA_DIR/star/defaults/profile_columns.list .
curl -O https://raw.githubusercontent.com/ngluck/generate-star-grid/main/examples/inlist_template
./mk                      # compiles ./star
```

````{important}
`./mk` must be run at least once before submitting a **SLURM array job**. Array
tasks (`--task_id`) skip the build step and copy the existing `star` binary into
each model directory; a local run (no `--task_id`) runs `./mk` for you at
startup. If `star` or `rn` is missing when a model starts, the run aborts with:

```text
FileNotFoundError: Required MESA file 'star' not found in /path/to/my_grid_run
```
````

````{note}
`inlist` must read `inlist_project` — that is the per-model file `grid_utils`
writes with the substituted parameter values. The stock MESA work-directory
`inlist` already does this. `history_columns.list` and `profile_columns.list`
are copied into each model directory only if present; without them MESA falls
back to its own defaults, which changes the columns available in
`combined_history.hdf5`.
````

### What the Pipeline Creates at Run Time

You do **not** need to create these — `grid_utils` makes them on the first run:
`notes.txt`, `stages.json`, `LOGS/`, one `M_..._Y_..._Z_..._alpha_.../` directory
per model, one `grid_<STEM>/` per save stage (`grid_TAMS/` for a main-sequence
run), `grid_inlists/`, and `grid_profiles/`. See
[Output Structure](output.md#directory-layout) for the full layout.

Post-processing outputs (`combined_history.hdf5`, batch directories, queue JSON
files) are written by `make_grid`, `merge_grids`, and `submit_grid` into the
directories you pass on the command line.
