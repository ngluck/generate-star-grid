# generate-star-grid

[![PyPI version](https://img.shields.io/pypi/v/generate-star-grid.svg)](https://pypi.org/project/generate-star-grid/)
[![Documentation Status](https://readthedocs.org/projects/generate-star-grid/badge/?version=latest)](https://generate-star-grid.readthedocs.io/en/latest/?badge=latest)

Python tools for generating grids of MESA stellar evolutionary tracks and
post-processing their output into HDF5 files for downstream ML pipelines.

Supports linear and Sobol-sampled grids over any combination of MESA parameters
(initial mass, metallicity Z, helium abundance Y, mixing-length α, etc.) with
SLURM job-array submission for HPC clusters.

```{toctree}
:maxdepth: 2
:caption: Getting Started
:hidden:

installation
usage
```

```{toctree}
:maxdepth: 2
:caption: Working with Output
:hidden:

output
postprocessing
```

```{toctree}
:maxdepth: 2
:caption: Reference
:hidden:

contributing
api
citing
```
