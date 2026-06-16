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
