"""
A run that stops short is a failure, and keeps everything it produced.

The scenario throughout: a three-stage grid (ZAMS -> TAMS -> RGB) where one
track finished, one reached TAMS and died before RGB, and one never got started.
Only the first is a success; the second must survive every cleanup path with its
run directory, log, SLURM output, inlist and both earlier stage models intact.
"""
import pytest

from generate_star_grid.chunk_grid import is_complete_dir, partition_by_completion
from generate_star_grid.failure_report import collect_failures, format_report
from generate_star_grid.grid_utils import cleanup_grid_data, find_failed_tasks
from generate_star_grid.grid_utils import make_run_dir_name
from generate_star_grid.stages import parse_stage_spec, save_stages, stage_save_path

STAGES = parse_stage_spec("ZAMS,TAMS,RGB")

DONE = "M_1.000_Y_0.270_Z_0.0200_alpha_2.00"
PARTIAL = "M_1.200_Y_0.270_Z_0.0200_alpha_2.00"
NOTHING = "M_1.400_Y_0.270_Z_0.0200_alpha_2.00"

MESA_LOG_OK = " step\n 1200 7.3\n termination code: xa_central_lower_limit\n"
MESA_LOG_CUT = " step\n 5360 7.2\n 5361 7.2\n"


@pytest.fixture
def grid(tmp_path):
    """A three-stage grid with one finished, one part-finished and one dead track."""
    root = tmp_path / "grid"
    for sub in ("LOGS", "grid_inlists", "slurm_logs", "grid_ZAMS", "grid_TAMS", "grid_RGB"):
        (root / sub).mkdir(parents=True)
    save_stages(root, STAGES)

    for task_id, folder in enumerate((DONE, PARTIAL, NOTHING)):
        (root / folder / "DATA").mkdir(parents=True)
        (root / folder / "DATA" / "history.data").write_text("model_number\n1\n")
        (root / folder / "photos").mkdir()
        (root / "grid_inlists" / f"inlist_{folder}").write_text("&star_job\n/\n")
        log = MESA_LOG_OK if folder is DONE else MESA_LOG_CUT
        (root / "LOGS" / f"log_{folder}_TASK_{task_id}.txt").write_text(log)
        (root / "slurm_logs" / f"slurm_7000_{task_id}.out").write_text("output\n")

    # DONE reached every stage; PARTIAL stopped one short; NOTHING produced none.
    for stage in STAGES:
        stage_save_path(root, stage, DONE).write_text("model")
    for stage in STAGES[:2]:
        stage_save_path(root, stage, PARTIAL).write_text("model")
    return root


def test_only_the_last_stage_means_finished(grid):
    assert is_complete_dir(grid, grid / DONE, STAGES)
    assert not is_complete_dir(grid, grid / PARTIAL, STAGES)
    assert not is_complete_dir(grid, grid / NOTHING, STAGES)


def test_partition_puts_a_part_finished_run_with_the_incomplete(grid):
    completed, incomplete, missing = partition_by_completion(
        grid, [grid / DONE, grid / PARTIAL, grid / NOTHING], STAGES)
    assert [d.name for d in completed] == [DONE]
    assert sorted(d.name for d in incomplete) == sorted([PARTIAL, NOTHING])
    assert missing == []


def test_find_failed_tasks_reports_how_far_each_got(grid):
    failed = {f["folder"]: f for f in find_failed_tasks(grid, ["M", "Y", "Z", "alpha"],
                                                        stages=STAGES)}
    assert set(failed) == {PARTIAL, NOTHING}
    assert failed[PARTIAL]["reached"] == "TAMS"
    assert failed[NOTHING]["reached"] is None


def test_failure_report_counts_progress_by_stage(grid):
    failures, n_total, n_complete = collect_failures(grid, keys=["M", "Y", "Z", "alpha"],
                                                     stages=STAGES)
    assert (n_total, n_complete, len(failures)) == (3, 1, 2)

    text = format_report(grid, failures, n_total, n_complete,
                         keys=["M", "Y", "Z", "alpha"], stages=STAGES)
    assert "PROGRESS BY STAGE" in text
    assert "     1  reached no stage at all" in text
    assert "     1  reached TAMS, stopped before RGB" in text
    assert "reached stage: TAMS" in text


def test_single_stage_report_omits_the_progress_section(grid):
    """It would say nothing, so it is not printed for grids that have one stage."""
    one = parse_stage_spec("RGB")
    failures, n_total, n_complete = collect_failures(grid, stages=one)
    text = format_report(grid, failures, n_total, n_complete, stages=one)
    assert "PROGRESS BY STAGE" not in text


def test_cleanup_keeps_data_for_a_run_that_stopped_short(grid):
    cleanup_grid_data(grid, "delete", stages=STAGES)

    assert not (grid / DONE / "DATA").exists(), "finished run's DATA/ should be reclaimed"
    assert (grid / PARTIAL / "DATA" / "history.data").exists()
    assert (grid / NOTHING / "DATA" / "history.data").exists()


def test_a_part_finished_run_keeps_every_artifact(grid):
    """Its earlier stage models are the only record of how far it got."""
    cleanup_grid_data(grid, "delete", stages=STAGES)

    assert stage_save_path(grid, STAGES[0], PARTIAL).exists()
    assert stage_save_path(grid, STAGES[1], PARTIAL).exists()
    assert not stage_save_path(grid, STAGES[2], PARTIAL).exists()
    assert (grid / "LOGS" / f"log_{PARTIAL}_TASK_1.txt").exists()
    assert (grid / "slurm_logs" / "slurm_7000_1.out").exists()
    assert (grid / "grid_inlists" / f"inlist_{PARTIAL}").exists()
    assert (grid / PARTIAL / "photos").exists()


def test_cleanup_is_safe_while_the_grid_is_still_running(grid, capsys):
    """Nothing outstanding is touched, and the skips are named."""
    cleanup_grid_data(grid, "delete", stages=STAGES)
    out = capsys.readouterr().out
    assert "Keeping DATA/ for 2/3" in out
    assert PARTIAL in out


# --- run_mesa_model with a fake MESA ---------------------------------------

def _fake_mesa_grid(tmp_path, produce):
    """
    A runnable grid dir whose ./rn writes whichever save files 'produce' names.

    Stands in for MESA: it reads each stage inlist's save_model_filename exactly
    as MESA would, so the test exercises the real substitution rather than
    assuming what it wrote.
    """
    root = tmp_path / "fake"
    root.mkdir()
    (root / "star").write_text("#!/bin/bash\n")
    (root / "rn").write_text(f"""#!/bin/bash
for f in inlist_project {' '.join(produce['inlists'])}; do
    [ -f "$f" ] || continue
    grep -o "save_model_filename = '[^']*'" "$f" | sed "s/.*'\\(.*\\)'/\\1/" >> /tmp/_names_$$
done
n=0
while read -r name; do
    n=$((n+1))
    [ "$n" -le {produce['n_stages']} ] && : > "$name"
done < /tmp/_names_$$
rm -f /tmp/_names_$$
""")
    (root / "rn").chmod(0o755)
    (root / "star").chmod(0o755)
    return root


def test_run_mesa_model_archives_every_stage_it_reached(tmp_path, monkeypatch):
    from generate_star_grid.grid_utils import run_mesa_model

    stages = parse_stage_spec("ZAMS,TAMS,RGB")
    root = _fake_mesa_grid(tmp_path, {"inlists": [], "n_stages": 3})
    (root / "inlist_template").write_text(
        "&star_job\n"
        " save_model_filename = 'ZAMS_0.70.mod'\n"
        " save_model_filename = 'TAMS_0.70.mod'\n"
        " save_model_filename = 'RGB_0.70.mod'\n"
        "/\n&controls\n initial_mass = 0.7\n initial_y = 0.27\n"
        " initial_z = 0.02\n mixing_length_alpha = 2.0\n/\n")

    params = {"initial_mass": 1.0, "initial_y": 0.27,
              "initial_z": 0.02, "mixing_length_alpha": 2.0}
    (root / "LOGS").mkdir()
    run_mesa_model(root / "inlist_template", root, params,
                   root / "LOGS" / "log.txt", stages=stages, cleanup_star=False)

    folder = make_run_dir_name(params)
    for stage in stages:
        assert stage_save_path(root, stage, folder).exists(), f"{stage.stem} not archived"
    assert is_complete_dir(root, root / folder, stages)


def test_run_mesa_model_leaves_a_short_run_intact(tmp_path):
    """MESA stops after ZAMS and TAMS: the run is failed and keeps its star binary."""
    from generate_star_grid.grid_utils import run_mesa_model

    stages = parse_stage_spec("ZAMS,TAMS,RGB")
    root = _fake_mesa_grid(tmp_path, {"inlists": [], "n_stages": 2})
    (root / "inlist_template").write_text(
        "&star_job\n"
        " save_model_filename = 'ZAMS_0.70.mod'\n"
        " save_model_filename = 'TAMS_0.70.mod'\n"
        " save_model_filename = 'RGB_0.70.mod'\n"
        "/\n&controls\n initial_mass = 0.7\n/\n")

    params = {"initial_mass": 1.0, "initial_y": 0.27,
              "initial_z": 0.02, "mixing_length_alpha": 2.0}
    (root / "LOGS").mkdir()
    run_mesa_model(root / "inlist_template", root, params,
                   root / "LOGS" / "log.txt", stages=stages, cleanup_star=True)

    folder = make_run_dir_name(params)
    assert stage_save_path(root, stages[0], folder).exists()
    assert stage_save_path(root, stages[1], folder).exists()
    assert not stage_save_path(root, stages[2], folder).exists()
    assert not is_complete_dir(root, root / folder, stages)
    assert (root / folder / "star").exists(), "a failed run keeps its star binary"


def test_run_mesa_model_writes_every_stage_inlist(tmp_path):
    """A multi-inlist run needs them all present, each with its own save name."""
    from generate_star_grid.grid_utils import run_mesa_model

    root = tmp_path / "multi"
    root.mkdir()
    (root / "star").write_text("#!/bin/bash\n")
    (root / "rn").write_text("#!/bin/bash\ndo_one inlist_template x\ndo_one inlist_rgb y\n")
    (root / "rn").chmod(0o755)
    (root / "star").chmod(0o755)
    (root / "inlist_template").write_text(
        "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n"
        "&controls\n initial_mass = 0.7\n/\n")
    (root / "inlist_rgb").write_text(
        "&star_job\n save_model_filename = 'RGB_0.70.mod'\n/\n"
        "&controls\n initial_mass = 0.7\n/\n")

    params = {"initial_mass": 1.0, "initial_y": 0.27,
              "initial_z": 0.02, "mixing_length_alpha": 2.0}
    (root / "LOGS").mkdir()
    run_mesa_model(root / "inlist_template", root, params,
                   root / "LOGS" / "log.txt", cleanup_star=False)

    folder = make_run_dir_name(params)
    assert f"TAMS_{folder}.mod" in (root / folder / "inlist_project").read_text()
    assert f"RGB_{folder}.mod" in (root / folder / "inlist_rgb").read_text()
    # Both are archived, so a cleaned grid still shows what each stage ran with.
    assert (root / "grid_inlists" / f"inlist_{folder}").exists()
    assert (root / "grid_inlists" / f"inlist_rgb_{folder}").exists()


def test_single_stage_run_writes_no_extra_inlists(tmp_path):
    """The classic layout must produce exactly the files it always has."""
    from generate_star_grid.grid_utils import run_mesa_model

    root = _fake_mesa_grid(tmp_path, {"inlists": [], "n_stages": 1})
    (root / "inlist_template").write_text(
        "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n"
        "&controls\n initial_mass = 0.7\n/\n")

    params = {"initial_mass": 1.0, "initial_y": 0.27,
              "initial_z": 0.02, "mixing_length_alpha": 2.0}
    (root / "LOGS").mkdir()
    run_mesa_model(root / "inlist_template", root, params,
                   root / "LOGS" / "log.txt", cleanup_star=False)

    folder = make_run_dir_name(params)
    written = {p.name for p in (root / folder).iterdir()}
    assert "inlist_project" in written
    assert "inlist_template" not in written
    assert (root / "grid_TAMS" / f"TAMS_{folder}.mod").exists()
    assert {p.name for p in (root / "grid_inlists").iterdir()} == {f"inlist_{folder}"}
