"""Stage discovery: every mechanism a user might reach for, and the guards."""
import pytest

from generate_star_grid.stages import (
    STAGES_FILENAME,
    discover_stages,
    is_complete,
    legacy_stages,
    load_stages,
    parse_stage_spec,
    reached_stage_index,
    reached_stem,
    rename_save_declarations,
    resolve_stages,
    save_stages,
    unreachable_stage_inlists,
    stage_save_path,
    stem_for_save_name,
)

from conftest import BASE_INLIST, write_grid


def stems(stages):
    return [s.stem for s in stages]


# --- stem derivation -------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("TAMS_0.70.mod", "TAMS"),
    ("TAMS_1.80.mod", "TAMS"),
    ("final.mod", "final"),
    ("RGB_tip.mod", "RGB_tip"),
    ("15M_at_TAMS.mod", "15M_at_TAMS"),
    ("ZAMS.mod", "ZAMS"),
    ("some/dir/TAMS_0.70.mod", "TAMS"),
])
def test_stem_for_save_name(name, expected):
    assert stem_for_save_name(name) == expected


# --- discovery -------------------------------------------------------------

def test_single_stage_template_is_the_legacy_layout(single_stage_grid):
    stages = resolve_stages(single_stage_grid)
    assert stems(stages) == ["TAMS"]
    assert stages[0].save_dir == "grid_TAMS"
    assert stages[0].filename("M_1.000") == "TAMS_M_1.000.mod"


def test_single_non_tams_save_uses_its_own_name(tmp_path):
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\n./star\n",
        "inlist": BASE_INLIST,
        "inlist_template": "&star_job\n save_model_filename = 'final.mod'\n/\n",
    })
    stages = resolve_stages(grid)
    assert stems(stages) == ["final"]
    assert stages[0].save_dir == "grid_final"


def test_rn_do_one_sequence_gives_stage_order(tmp_path):
    grid = write_grid(tmp_path / "g", {
        "rn": ("#!/bin/bash\n"
               "do_one inlist_pre_ms_header ZAMS.mod\n"
               "do_one inlist_to_tams_header TAMS.mod\n"
               "do_one inlist_to_rgb_header RGB.mod\n"),
        "inlist_pre_ms_header": "&star_job\n save_model_filename = 'ZAMS_0.70.mod'\n/\n",
        "inlist_to_tams_header": "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n",
        "inlist_to_rgb_header": "&star_job\n save_model_filename = 'RGB_0.70.mod'\n/\n",
    })
    assert stems(discover_stages(grid)) == ["ZAMS", "TAMS", "RGB"]


def test_rn_cp_then_star_sequence(tmp_path):
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\ncp inlist_a inlist\n./star\ncp inlist_b inlist\n./star\n",
        "inlist_a": "&star_job\n save_model_filename = 'ZAMS_1.0.mod'\n/\n",
        "inlist_b": "&star_job\n save_model_filename = 'TAMS_1.0.mod'\n/\n",
    })
    assert stems(discover_stages(grid)) == ["ZAMS", "TAMS"]


def test_several_saves_in_one_inlist_are_several_stages(tmp_path):
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\n./star\n",
        "inlist": BASE_INLIST,
        "inlist_template": ("&star_job\n save_model_filename = 'ZAMS_0.70.mod'\n/\n"
                            "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n"),
    })
    assert stems(discover_stages(grid)) == ["ZAMS", "TAMS"]


def test_extra_inlist_overrides_the_base_it_was_pulled_into(tmp_path):
    """MESA reads the extra inlist last, so its save wins -- not both."""
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\n./star\n",
        "inlist": BASE_INLIST + "\n&star_job\n save_model_filename = 'IGNORED.mod'\n/\n",
        "inlist_template": "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n",
    })
    assert stems(discover_stages(grid)) == ["TAMS"]


def test_commented_out_save_is_not_a_stage(tmp_path):
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\n./star\n",
        "inlist": BASE_INLIST,
        "inlist_template": ("&star_job\n"
                            " ! save_model_filename = 'OLD.mod'\n"
                            " save_model_filename = 'TAMS_0.70.mod'\n/\n"),
    })
    assert stems(discover_stages(grid)) == ["TAMS"]


def test_duplicate_stems_raise(tmp_path):
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\ndo_one a x\ndo_one b y\n",
        "a": "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n",
        "b": "&star_job\n save_model_filename = 'TAMS_1.80.mod'\n/\n",
    })
    with pytest.raises(ValueError, match="same name 'TAMS'"):
        discover_stages(grid)


def test_no_inlists_at_all_falls_back_to_legacy(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert stems(resolve_stages(empty)) == ["TAMS"]


def test_legacy_guard_keeps_an_existing_grid_tams_layout(tmp_path):
    """Renaming a running grid's stage would strand every save it has written."""
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\n./star\n",
        "inlist": BASE_INLIST,
        "inlist_template": "&star_job\n save_model_filename = 'final.mod'\n/\n",
    })
    assert stems(resolve_stages(grid)) == ["final"]
    (grid / "grid_TAMS").mkdir()
    assert stems(resolve_stages(grid)) == ["TAMS"]


# --- explicit --stages -----------------------------------------------------

def test_parse_stage_spec():
    stages = parse_stage_spec("ZAMS,TAMS,RGB")
    assert stems(stages) == ["ZAMS", "TAMS", "RGB"]
    assert [s.save_dir for s in stages] == ["grid_ZAMS", "grid_TAMS", "grid_RGB"]


def test_parse_stage_spec_custom_dir():
    stages = parse_stage_spec("TAMS,RGB:grid_giants")
    assert [s.save_dir for s in stages] == ["grid_TAMS", "grid_giants"]


def test_parse_stage_spec_rejects_duplicates():
    with pytest.raises(ValueError):
        parse_stage_spec("TAMS,TAMS")


def test_explicit_stages_beat_discovery(single_stage_grid):
    assert stems(resolve_stages(single_stage_grid, explicit="ZAMS,TAMS")) == ["ZAMS", "TAMS"]


# --- persistence -----------------------------------------------------------

def test_stages_json_round_trip(tmp_path):
    stages = parse_stage_spec("ZAMS,TAMS,RGB")
    path = save_stages(tmp_path, stages)
    assert path.name == STAGES_FILENAME
    assert stems(load_stages(tmp_path)) == ["ZAMS", "TAMS", "RGB"]


def test_stages_json_beats_discovery(single_stage_grid):
    """It outlives the inlists, which cleanup deletes."""
    save_stages(single_stage_grid, parse_stage_spec("ZAMS,TAMS"))
    assert stems(resolve_stages(single_stage_grid)) == ["ZAMS", "TAMS"]


def test_corrupt_stages_json_falls_back_to_discovery(single_stage_grid):
    (single_stage_grid / STAGES_FILENAME).write_text("{not json")
    assert stems(resolve_stages(single_stage_grid)) == ["TAMS"]


# --- completion predicates -------------------------------------------------

def test_completion_is_the_last_stage(tmp_path):
    stages = parse_stage_spec("ZAMS,TAMS,RGB")
    folder = "M_1.000_Y_0.270"
    for stage in stages[:2]:
        (tmp_path / stage.save_dir).mkdir()
        stage_save_path(tmp_path, stage, folder).write_text("model")

    assert not is_complete(tmp_path, folder, stages)
    assert reached_stage_index(tmp_path, folder, stages) == 1
    assert reached_stem(tmp_path, folder, stages) == "TAMS"

    (tmp_path / "grid_RGB").mkdir()
    stage_save_path(tmp_path, stages[2], folder).write_text("model")
    assert is_complete(tmp_path, folder, stages)
    assert reached_stem(tmp_path, folder, stages) == "RGB"


def test_reached_nothing(tmp_path):
    stages = parse_stage_spec("ZAMS,TAMS")
    assert reached_stage_index(tmp_path, "M_1.000", stages) == -1
    assert reached_stem(tmp_path, "M_1.000", stages) is None


# --- inlist rewriting ------------------------------------------------------

def test_rename_single_declaration_matches_legacy_naming():
    text = "&star_job\n    save_model_filename = 'TAMS_0.70.mod'\n/\n"
    out, n = rename_save_declarations(text, "M_1.000_Y_0.270", legacy_stages())
    assert n == 1
    assert "save_model_filename = 'TAMS_M_1.000_Y_0.270.mod'" in out


def test_rename_numbers_declarations_in_order():
    text = ("save_model_filename = 'a.mod'\n"
            "save_model_filename = 'b.mod'\n"
            "save_model_filename = 'c.mod'\n")
    out, n = rename_save_declarations(text, "RUN", parse_stage_spec("ZAMS,TAMS,RGB"))
    assert n == 3
    assert out.splitlines() == [
        "save_model_filename = 'ZAMS_RUN.mod'",
        "save_model_filename = 'TAMS_RUN.mod'",
        "save_model_filename = 'RGB_RUN.mod'",
    ]


def test_rename_honours_the_offset():
    text = "save_model_filename = 'x.mod'\n"
    out, _ = rename_save_declarations(text, "RUN", parse_stage_spec("ZAMS,TAMS"), offset=1)
    assert "TAMS_RUN.mod" in out


def test_rename_leaves_commented_declarations_alone():
    text = ("  ! save_model_filename = 'OLD.mod'\n"
            "  save_model_filename = 'TAMS_0.70.mod'\n")
    out, n = rename_save_declarations(text, "RUN", legacy_stages())
    assert n == 1
    assert "! save_model_filename = 'OLD.mod'" in out
    assert "save_model_filename = 'TAMS_RUN.mod'" in out


def test_rename_leaves_surplus_declarations_alone():
    """Better an untouched name than several stages collapsed onto one."""
    text = "save_model_filename = 'a.mod'\nsave_model_filename = 'b.mod'\n"
    out, n = rename_save_declarations(text, "RUN", legacy_stages())
    assert n == 2
    assert "TAMS_RUN.mod" in out
    assert "'b.mod'" in out


# --- unreachable stage inlists ---------------------------------------------

def test_stage_inlist_rn_never_runs_is_flagged(tmp_path):
    """The quiet failure: the grid runs one stage and calls tracks finished early."""
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\n./star\n",
        "inlist": BASE_INLIST,
        "inlist_template": "&star_job\n save_model_filename = 'ZAMS_0.70.mod'\n/\n",
        "inlist_tams": "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n",
    })
    stages = resolve_stages(grid)
    assert stems(stages) == ["ZAMS"]
    assert unreachable_stage_inlists(grid, stages) == [("inlist_tams", "TAMS_0.70.mod")]


def test_nothing_flagged_when_rn_wires_every_stage(tmp_path):
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\ndo_one inlist_project x\ndo_one inlist_tams y\n",
        "inlist": BASE_INLIST,
        "inlist_template": "&star_job\n save_model_filename = 'ZAMS_0.70.mod'\n/\n",
        "inlist_tams": "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n",
    })
    stages = resolve_stages(grid)
    assert stems(stages) == ["ZAMS", "TAMS"]
    assert unreachable_stage_inlists(grid, stages) == []


def test_nothing_flagged_for_a_plain_single_stage_grid(single_stage_grid):
    """inlist and inlist_project are not forgotten stage inlists."""
    stages = resolve_stages(single_stage_grid)
    (single_stage_grid / "inlist_project").write_text(
        "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n")
    assert unreachable_stage_inlists(single_stage_grid, stages) == []


def test_explicit_stages_silence_the_flag(tmp_path):
    grid = write_grid(tmp_path / "g", {
        "rn": "#!/bin/bash\n./star\n",
        "inlist": BASE_INLIST,
        "inlist_template": "&star_job\n save_model_filename = 'ZAMS_0.70.mod'\n/\n",
        "inlist_tams": "&star_job\n save_model_filename = 'TAMS_0.70.mod'\n/\n",
    })
    stages = resolve_stages(grid, explicit="ZAMS,TAMS")
    assert unreachable_stage_inlists(grid, stages) == []
