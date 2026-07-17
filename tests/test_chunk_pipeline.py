"""
Tests for the in-project batched (chunked) Sobol pipeline: chunk_grid CLI
plumbing -- chunk bounds, master merge with globally-unique Track offsets,
finalize (verify + delete intermediates), parent-dir resolution, and the
generated SLURM scripts.

These exercise the pure-Python + filesystem behavior end to end (no SLURM, no
MESA) using synthetic HDF5 grids under tmp_path.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from generate_star_grid import chunk_grid
from generate_star_grid.chunk_grid import chunk_bounds, merge_master


def _write_history_hdf5(path, n_tracks=3, rows_per_track=4, track_start=0):
    """Write a minimal history HDF5 (key 'history', a 'Track' column) like a real chunk."""
    tracks = np.repeat(np.arange(track_start, track_start + n_tracks), rows_per_track)
    df = pd.DataFrame({
        "Track": tracks,
        "star_age": np.linspace(0, 1, len(tracks)),
        "log_L": np.random.rand(len(tracks)),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.HDFStore(str(path), mode="w") as store:
        store.append("history", df, format="table")
    return len(df)


def _finalize_args(parent, keep_chunks=False, hdf5_key="history"):
    return argparse.Namespace(parent_dir=str(parent), hdf5_key=hdf5_key, keep_chunks=keep_chunks)


# --------------------------------------------------------------------------- #
# chunk_bounds
# --------------------------------------------------------------------------- #

def test_chunk_bounds_even():
    assert chunk_bounds(8, 4) == [(0, 3), (4, 7)]


def test_chunk_bounds_ragged_last_chunk_short():
    assert chunk_bounds(10, 4) == [(0, 3), (4, 7), (8, 9)]


def test_chunk_bounds_single_chunk_when_size_exceeds_points():
    assert chunk_bounds(5, 100) == [(0, 4)]


def test_chunk_bounds_default_size_512():
    # 8192 Sobol samples at the default batch size -> 16 batches of 512.
    bounds = chunk_bounds(8192, chunk_grid.DEFAULT_CHUNK_SIZE)
    assert len(bounds) == 16
    assert bounds[0] == (0, 511) and bounds[-1] == (7680, 8191)


@pytest.mark.parametrize("np_, cs", [(0, 4), (8, 0), (8, -1)])
def test_chunk_bounds_rejects_bad_args(np_, cs):
    with pytest.raises(ValueError):
        chunk_bounds(np_, cs)


# --------------------------------------------------------------------------- #
# merge_master
# --------------------------------------------------------------------------- #

def test_merge_master_combines_chunks_and_offsets_tracks(tmp_path):
    parent = tmp_path / "grid"
    parent.mkdir()
    r0 = _write_history_hdf5(parent / "chunk_00000_00000" / "combined_history.hdf5", n_tracks=3)
    r1 = _write_history_hdf5(parent / "chunk_00001_00001" / "combined_history.hdf5", n_tracks=2)

    out = merge_master(parent)
    assert out == parent / "combined_history.hdf5"
    with pd.HDFStore(str(out), mode="r") as store:
        df = store.select("history")
    assert len(df) == r0 + r1
    # Tracks are globally unique after offsetting (3 + 2 distinct values).
    assert df["Track"].nunique() == 5


def test_merge_master_no_chunks_returns_none(tmp_path):
    assert merge_master(tmp_path) is None


# --------------------------------------------------------------------------- #
# finalize: merge master, verify row count, delete intermediates
# --------------------------------------------------------------------------- #

def test_finalize_builds_master_and_deletes_intermediates(tmp_path):
    parent = tmp_path / "grid"
    parent.mkdir()
    r0 = _write_history_hdf5(parent / "chunk_00000_00000" / "combined_history.hdf5", n_tracks=3)
    r1 = _write_history_hdf5(parent / "chunk_00001_00001" / "combined_history.hdf5", n_tracks=2)

    chunk_grid.cmd_finalize(_finalize_args(parent))

    master = parent / "combined_history.hdf5"
    assert master.exists()
    with pd.HDFStore(str(master), mode="r") as store:
        df = store.select("history")
    assert len(df) == r0 + r1
    assert df["Track"].nunique() == 5              # unique tracks preserved in the master
    # Intermediates deleted once the master is verified.
    assert not (parent / "chunk_00000_00000").exists()
    assert not (parent / "chunk_00001_00001").exists()


def test_finalize_keep_chunks_retains_intermediates(tmp_path):
    parent = tmp_path / "grid"
    parent.mkdir()
    _write_history_hdf5(parent / "chunk_00000_00000" / "combined_history.hdf5", n_tracks=2)

    chunk_grid.cmd_finalize(_finalize_args(parent, keep_chunks=True))

    assert (parent / "combined_history.hdf5").exists()
    assert (parent / "chunk_00000_00000").exists()  # kept


def test_finalize_no_chunks_errors(tmp_path):
    with pytest.raises(SystemExit):
        chunk_grid.cmd_finalize(_finalize_args(tmp_path))


def test_finalize_refuses_delete_on_rowcount_mismatch(tmp_path, monkeypatch):
    parent = tmp_path / "grid"
    parent.mkdir()
    _write_history_hdf5(parent / "chunk_00000_00000" / "combined_history.hdf5", n_tracks=2)

    master_path = parent / "combined_history.hdf5"
    real_nrows = chunk_grid._hdf5_nrows

    def fake_nrows(path, key):
        # Report the master as short so verification fails.
        if Path(path) == master_path:
            return 1
        return real_nrows(path, key)

    monkeypatch.setattr(chunk_grid, "_hdf5_nrows", fake_nrows)
    with pytest.raises(SystemExit, match="Refusing to delete"):
        chunk_grid.cmd_finalize(_finalize_args(parent))
    # The chunk is kept for inspection when verification fails.
    assert (parent / "chunk_00000_00000").exists()


# --------------------------------------------------------------------------- #
# parent-dir resolution (no scratch/_env anymore -- explicit or cwd)
# --------------------------------------------------------------------------- #

def test_resolve_parent_dir_prefers_explicit():
    assert chunk_grid._resolve_parent_dir("/explicit") == "/explicit"


def test_resolve_parent_dir_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert chunk_grid._resolve_parent_dir(None) == str(Path.cwd())


# --------------------------------------------------------------------------- #
# generated SLURM scripts
# --------------------------------------------------------------------------- #

def _minimal_config(parent):
    return {
        "parent_dir": str(parent),
        "python": "/usr/bin/python3", "conda_env": "py311",
        "mass": ["0.7:1.8"], "initial_Y": ["0.27"], "initial_Z": ["1e-4:0.04:log"],
        "alpha_MLT": ["1:3"], "param": [],
        "grid_type": "sobol", "num_points": 8192, "sobol_seed": 0, "chunk_size": 512,
        "constants": ["M", "Y", "Z", "alpha"],
        "bounds": [[0, 511], [512, 1023]],
        "array_time": "23:59:59", "array_mem": "8G", "array_partition": "day", "array_mail_type": "ALL",
        "step_time": "2:00:00", "step_mem": "16G", "step_partition": "day", "step_mail_type": "ALL",
        "finalize_time": "8:00:00", "finalize_mem": "32G",
        "max_cpus": 990,
    }


def test_generated_scripts_contain_expected_wiring(tmp_path):
    parent = tmp_path / "grid"
    parent.mkdir()
    queue_file = parent / "chunk_queue.json"
    cfg = _minimal_config(parent)

    chunk_grid._write_chunk_scripts(parent, cfg, queue_file)

    array = (parent / "run_chunk_array.sh").read_text()
    assert "grid_utils" in array and "--task_id=$SLURM_ARRAY_TASK_ID" in array
    assert "1e-4:0.04:log" in array           # log-spec preserved through shlex.quote

    step = (parent / "run_chunk_step.sh").read_text()
    assert "chunk_grid advance" in step
    assert str(queue_file) in step

    finalize = (parent / "run_finalize.sh").read_text()
    assert "chunk_grid finalize" in finalize
    assert "--relocate_dest" not in finalize  # no scratch relocation anymore

    for name in ("run_chunk_array.sh", "run_chunk_step.sh", "run_finalize.sh"):
        assert (parent / name).stat().st_mode & 0o111, f"{name} not executable"
