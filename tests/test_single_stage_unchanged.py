"""
The back-compat gate: a single-stage grid must behave exactly as it did before
stages.py existed.

Every grid built so far declares one ``save_model_filename = 'TAMS_0.70.mod'``
and archives to ``grid_TAMS/TAMS_<run_dir>.mod``. Multi-stage support must not
move, rename or add a single file for those grids -- one of them is usually in
flight, running against an editable install, when this code changes.

The golden files under tests/golden/ were captured from the implementation that
predates stages.py.
"""
import re

import pytest

from generate_star_grid.grid_utils import update_inlist
from generate_star_grid.stages import resolve_stages

PARAMS = {
    "initial_mass": 1.234,
    "initial_y": 0.271,
    "initial_z": 0.00143,
    "mixing_length_alpha": 1.87,
}
RUN_DIR = "M_1.234_Y_0.271_Z_0.00143_alpha_1.87"


def test_update_inlist_output_is_byte_identical(repo_root, golden_dir):
    """The real examples/inlist_template, substituted, against the pre-stages output."""
    template = (repo_root / "examples" / "inlist_template").read_text()
    assert update_inlist(template, PARAMS, RUN_DIR) == \
        (golden_dir / "update_inlist_single_stage.txt").read_text()


def test_repo_template_resolves_to_exactly_the_legacy_stage(repo_root):
    stages = resolve_stages(repo_root / "examples")
    assert len(stages) == 1
    assert stages[0].save_dir == "grid_TAMS"
    assert stages[0].filename(RUN_DIR) == f"TAMS_{RUN_DIR}.mod"


def test_generated_array_script_is_unchanged(generated_scripts, golden_dir):
    assert generated_scripts["run_array.sh"] == \
        (golden_dir / "run_array_pre_stages.sh").read_text()


def test_generated_combine_script_only_gains_stage_handling(generated_scripts, golden_dir):
    """
    The combine script does change -- deliberately, so a failed task keeps the
    stage models it reached. Nothing else about it may.
    """
    new = generated_scripts["run_combine_cleanup.sh"]
    old = (golden_dir / "run_combine_cleanup_pre_stages.sh").read_text()

    # A single-stage grid resolves to precisely the old hardcoded location.
    assert 'STAGE_SPECS="grid_TAMS|TAMS_|.mod"' in new
    assert "--stages TAMS" in new

    def commands(text):
        return [re.sub(r"\s+", " ", line.strip()) for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#")]

    removed = [line for line in commands(old) if line not in commands(new)]

    # The only commands that may disappear are the unconditional deletions of
    # every TAMS model (now a per-stage loop that spares failed tasks), the
    # check-failed call (now passed --stages), and the summary echo that names
    # what was kept.
    assert set(removed) == {
        'rm -f "$DEST"/grid_TAMS/TAMS_*.mod',
        'FAILED=$("/usr/bin/python3" -m generate_star_grid.submit_grid check-failed '
        '--dest "$DEST" --keys M,alpha)',
        'echo "Kept run dir, MESA log, SLURM output and archived inlist for each '
        'still-failed task."',
    }


@pytest.fixture(scope="module")
def generated_scripts(tmp_path_factory):
    """Generate run_array.sh / run_combine_cleanup.sh with SLURM and rsync stubbed."""
    import json
    import subprocess

    from generate_star_grid import submit_grid
    from generate_star_grid.grid_utils import PARAM_FORMAT

    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        prog = cmd[0] if isinstance(cmd, (list, tuple)) else cmd
        if prog == "rsync":
            from pathlib import Path
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if prog == "sbatch":
            return subprocess.CompletedProcess(cmd, 0, "99999", "")
        return real_run(cmd, *a, **kw)

    tmp = tmp_path_factory.mktemp("scripts")
    src = tmp / "src"
    src.mkdir()
    repo_root = __import__("conftest").REPO_ROOT
    (src / "inlist_template").write_text((repo_root / "examples" / "inlist_template").read_text())
    parent = tmp / "grid"
    parent.mkdir()

    config = {
        "source_dir": str(src), "parent_dir": str(parent), "registry": PARAM_FORMAT,
        "python": "/usr/bin/python3",
        "outer_formats": {"initial_z": ".5f", "initial_y": ".3f"},
        "inner_keys": ["initial_mass", "mixing_length_alpha"],
        "inner_cli_args": ["--mass", "0.7:1.8", "--mixing_length_alpha", "1.0:3.0",
                           "--grid_type", "sobol", "--num_points", "8"],
        "constants": ["M", "Y", "Z", "alpha"],
        "array_partition": "day", "array_time": "1-00:00:00", "array_mem": "8G",
        "array_mail_type": "NONE", "conda_env": "mesa",
        "combine_partition": "day", "combine_time": "2:00:00", "combine_mem": "16G",
        "combine_mail_type": "NONE", "retry_once": False,
        "max_cpus": None, "parallel_total": 1,
    }
    batch = {"initial_y": 0.27, "initial_z": 0.02}
    queue = tmp / "queue.json"
    queue.write_text(json.dumps({"config": {}, "batches": []}))

    subprocess.run = fake_run
    try:
        submit_grid._write_and_submit_batch(queue, config, batch)
    finally:
        subprocess.run = real_run

    dest = next(d for d in parent.iterdir() if d.is_dir())
    return {name: (dest / name).read_text().replace(str(tmp), "TMP")
            for name in ("run_array.sh", "run_combine_cleanup.sh")}
