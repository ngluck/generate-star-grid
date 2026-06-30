# generate-star-grid

[![PyPI version](https://img.shields.io/pypi/v/generate-star-grid.svg)](https://pypi.org/project/generate-star-grid/)
[![Documentation Status](https://readthedocs.org/projects/generate-star-grid/badge/?version=latest)](https://generate-star-grid.readthedocs.io/en/latest/?badge=latest)

Python tools for generating grids of MESA stellar evolutionary tracks and
post-processing their output into HDF5 files for downstream ML pipelines.

Supports linear and Sobol-sampled grids over any combination of MESA parameters
(initial mass, metallicity Z, helium abundance Y, mixing-length α, etc.) with
SLURM job-array submission for HPC clusters.

::::{grid} 1 2 2 3
:gutter: 2

:::{grid-item-card} 🚀 Getting Started
:link: installation
:link-type: doc

Install generate-star-grid and run your first grid.
:::

:::{grid-item-card} 📁 Output Structure
:link: output
:link-type: doc

Understand the directory layout and output files.
:::

:::{grid-item-card} 🔬 Post-processing
:link: postprocessing
:link-type: doc

Convert MESA output into HDF5 files for ML pipelines.
:::

:::{grid-item-card} 📖 API Reference
:link: api
:link-type: doc

Full documentation of all modules and functions.
:::

:::{grid-item-card} 🤝 Contributing
:link: contributing
:link-type: doc

Guidelines for contributing to the project.
:::

:::{grid-item-card} 📝 Citing
:link: citing
:link-type: doc

How to cite generate-star-grid in your research.
:::

::::

```{toctree}
:maxdepth: 2
:caption: Getting Started
:hidden:

installation
usage
advanced_usage
troubleshooting
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
