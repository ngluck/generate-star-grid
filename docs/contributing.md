# Contributing

## Repository Structure

```
generate-star-grid/
├── generate_star_grid/
│   ├── grid_utils.py           # core grid generation, inlist update, MESA execution
│   ├── grid_utils_cont.py      # continuation variant (resume from TAMS)
│   ├── resume_utils.py         # helpers for resume indexing and inlist modification
│   ├── make_grid.py            # post-processing: combine history files into HDF5
│   ├── make_starpasta_grid.py  # assign Track IDs to starpasta HDF5 files
│   └── make_yrec_grid.py       # assign Track IDs to YREC HDF5 files
├── slurm/
│   ├── generate_grid_week_array.sh  # template SLURM job array script
│   └── find_failed.sh               # detect and resubmit failed array tasks
├── examples/
│   └── inlist_template          # reference MESA inlist template
├── docs/                        # Sphinx sources for the ReadTheDocs site
├── .readthedocs.yaml
├── .github/workflows/publish.yml
├── LICENSE
└── pyproject.toml
```

## Releasing a New Version

Releases are published to PyPI automatically by `.github/workflows/publish.yml`
whenever a `v*` tag is pushed:

1. Bump `version` in `pyproject.toml`
2. Commit the change
3. Tag and push:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

This requires a one-time PyPI trusted publisher setup for this repository
(see [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/)).
