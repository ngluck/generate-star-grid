# generate-star-grid

[![PyPI version](https://img.shields.io/pypi/v/generate-star-grid.svg)](https://pypi.org/project/generate-star-grid/)
[![Documentation Status](https://readthedocs.org/projects/generate-star-grid/badge/?version=latest)](https://generate-star-grid.readthedocs.io/en/latest/?badge=latest)

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

### From PyPI

```bash
pip install generate-star-grid
```

### From source (development)

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
