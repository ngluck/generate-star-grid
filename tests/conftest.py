"""Shared fixtures for the grid pipeline tests."""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOLDEN = Path(__file__).resolve().parent / "golden"

# The base inlist a MESA work directory ships: it declares nothing itself and
# points at inlist_project, which the pipeline generates from inlist_template.
BASE_INLIST = """&star_job
    read_extra_star_job_inlist(1) = .true.
    extra_star_job_inlist_name(1) = 'inlist_project'
/ ! end of star_job namelist

&controls
    read_extra_controls_inlist(1) = .true.
    extra_controls_inlist_name(1) = 'inlist_project'
/ ! end of controls namelist
"""

SINGLE_STAGE_TEMPLATE = """&star_job
    save_model_when_terminate = .true.
    save_model_filename = 'TAMS_0.70.mod'
/

&controls
    initial_mass = 0.7000
    initial_z = 0.0200
    initial_y = 0.2700
    mixing_length_alpha = 2.0
    log_directory = 'M_0.700_Y_0.270_Z_0.020_alpha_2.00'
/
"""


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def golden_dir():
    return GOLDEN


def write_grid(root, files):
    """Create a grid directory containing the given {name: text} files."""
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
    return root


@pytest.fixture
def single_stage_grid(tmp_path):
    """A grid directory laid out exactly as every grid built before stages.py."""
    return write_grid(tmp_path / "single", {
        "rn": "#!/bin/bash\n./star\n",
        "inlist": BASE_INLIST,
        "inlist_template": SINGLE_STAGE_TEMPLATE,
    })
