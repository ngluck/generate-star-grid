# Troubleshooting

## Diagnosing Failed Array Tasks

### Quick check with `find_failed.sh`

From inside the grid run directory:

````{tab-set}
```{tab-item} Check failed tasks
bash /path/to/slurm/find_failed.sh
```
```{tab-item} Check and clean corrupted DATA/
bash /path/to/slurm/find_failed.sh clean
```
````

````{warning}
Cleaning corrupted `DATA/` folders with `clean` is irreversible. Always review the
list of failed tasks before resubmitting.
````

`find_failed.sh` hardcodes a single Y/Z/alpha combination in its model-directory
naming guess, so it only works for single-batch grids swept over mass alone. For
grids with other or multiple swept parameters, use `submit_grid check-failed` instead.

### `submit_grid check-failed`

`check-failed` is the general-purpose failure detector. It scans the `LOGS/` directory
for per-task log files, reconstructs each task's model directory name from the log
filename, and checks two things for each one: whether it has a `grid_TAMS/TAMS_*.mod`
save file, and whether `DATA/history.data` exists and meets a minimum size threshold.
A task is considered failed if either check fails — its TAMS file is missing, or
`history.data` is missing or smaller than `--threshold_mb` (default: 5 MB).

The TAMS check matters because `history.data` size alone isn't a reliable signal
of completion. A task can accumulate well over `threshold_mb` of `history.data`
and still never finish — either because it hit the SLURM `--time` limit mid-run,
or because MESA itself gave up (e.g. `termination code: min_timestep_limit` after
exhausting solver retries) without reaching a real stop condition. `grid_TAMS/TAMS_*.mod`
is only ever written by `save_model_when_terminate` on a genuine termination, so its
absence is what actually distinguishes a finished track from one cut short — `history.data`
size on its own would silently pass both of those cases as successes.

````bash
python -m generate_star_grid.submit_grid check-failed \
    --dest /path/to/batch_dir \
    --keys M,Y,Z,alpha
````

Each failed task is printed as one line:

```
<task_id>|<folder_name>|<key>=<value>,...
```

For example:

```
283|M_0.984_Y_0.27_Z_0.00143_alpha_2.0|M=0.984,Y=0.27,Z=0.00143,alpha=2.0
430|M_1.131_Y_0.27_Z_0.00143_alpha_2.0|M=1.131,Y=0.27,Z=0.00143,alpha=2.0
```

**Arguments:**

| Argument | Required | Description |
|---|---|---|
| `--dest` | yes | Path to the batch directory (contains `LOGS/` and model subdirectories) |
| `--keys` | yes | Comma-separated parameter labels to extract from the folder name, e.g. `M,Y,Z,alpha` |
| `--threshold_mb` | no | Minimum acceptable `history.data` size in MB (default: `5.0`) |

#### How it works internally

`check-failed` calls `find_failed_tasks()` from `grid_utils.py`, which:

1. Globs `LOGS/log_*_TASK_*.txt` to find every task that ran
2. Parses the task ID from the `_TASK_<id>` suffix of each filename
3. Reconstructs the model subdirectory name by stripping the `log_` prefix and
   `_TASK_<id>` suffix — this works for any parameter combination without any
   hardcoded assumptions
4. Checks whether `grid_TAMS/TAMS_<folder>.mod` exists **and** whether
   `<folder>/DATA/history.data` exists and is at least `threshold_mb` in size —
   a task must pass both checks to count as succeeded
5. Returns a list of dicts with `task_id`, `folder`, and `params` for each failure

This is also the function the combine/cleanup job calls internally to detect
failures before retrying and again after the retry. After the retry, the
still-failed folder names are passed to `make_grid --exclude_dirs`, which
filters them out before writing `combined_history.hdf5` — so the exclusion
is real, not just a warning in `notes.txt`.

#### Tuning the failure threshold

Since the TAMS check is the real completion signal, `--threshold_mb` only exists
to catch a `history.data` that's missing, empty, or truncated to a near-useless
stub — it's a corruption floor, not a completeness check. The default (5 MB) is
already permissive enough for short legitimate tracks (e.g. high-mass stars that
terminate quickly), so you generally shouldn't need to touch it. Note that a task
missing its `grid_TAMS/TAMS_*.mod` save file is always reported as failed
regardless of this setting, since that means the track never reached a real
termination, no matter how much `history.data` it accumulated.

Raise it if you want a stricter sanity floor for a particular grid:

```bash
python -m generate_star_grid.submit_grid check-failed \
    --dest /path/to/batch_dir --keys M,Y,Z,alpha \
    --threshold_mb 10.0
```

For grids run via `submit_grid start`, pass `--fail_threshold_mb` to bake the
threshold into the generated combine/cleanup script:

```bash
python -m generate_star_grid.submit_grid start \
    ... \
    --fail_threshold_mb 10.0
```

#### Resubmitting failed tasks manually

To resubmit only the failed task IDs as a new array job:

````bash
FAILED=$(python -m generate_star_grid.submit_grid check-failed \
    --dest /path/to/batch_dir --keys M,Y,Z,alpha)

FAILED_IDS=$(echo "$FAILED" | cut -d'|' -f1 | paste -sd, -)

sbatch --array=$FAILED_IDS /path/to/batch_dir/run_array.sh
````

````{tip}
Don't wipe `DATA/` or `photos/` before resubmitting. `run_array.sh` generated by
`submit_grid start`/`expand` always passes `--restart_photos`, so a retried task
resumes from its latest MESA checkpoint instead of starting over — see
[Resuming Timed-Out Runs](#resuming-timed-out-runs-photo-restart) below. Only
delete `DATA/` yourself if you want a guaranteed clean restart from scratch
(e.g. the failure was a bad inlist value rather than a timeout).
````

## Resuming Timed-Out Runs (Photo Restart)

A SLURM array task can hit its `--time` limit before a track finishes — common
for slow-converging high-mass or low-Z models on a busy `day` partition. Rather
than losing that progress, the pipeline resumes timed-out tasks from MESA's own
checkpoint files ("photos") instead of restarting from scratch.

This is **always on** for grids submitted via `submit_grid start`/`expand`; there
is no flag to set.

#### How it works

- MESA periodically writes checkpoint files to each run directory's `photos/`
  folder while it runs.
- When `run_combine_cleanup.sh` detects a failed task, it no longer clears that
  task's `DATA/` and `photos/` before retrying — both are preserved.
- The retry array job (`run_array.sh`) always invokes `grid_utils.py` with
  `--restart_photos`. At runtime, `find_latest_photo()` picks the newest file
  (by modification time) in `run_dir/photos/`; if one is found, the model is
  resumed with `./re <photo>` instead of run from scratch with `./rn`, and the
  task's log file is opened in append mode so the pre-timeout output is kept.
- If no photo exists yet (e.g. the task failed before MESA wrote its first
  checkpoint) or `./re` isn't available in the run directory, it falls back to
  a normal `./rn` run — restart is opportunistic, not required.

#### `history.data` deduplication

Resuming from a photo makes MESA re-append rows starting at the photo's
`model_number`, which leaves a short non-monotonic, duplicated stretch where
the original run and the restart overlap. The HDF5 loader
(`load_history_with_constants_from_profile`, used by `make_grid`) detects
non-monotonic `model_number` sequences, keeps only the last (i.e. restarted)
occurrence of each one, and re-sorts — so `combined_history.hdf5` doesn't
double-count steps. This is a no-op for tracks that completed without ever
timing out.

#### Using `grid_utils.py` directly (outside `submit_grid`)

If you invoke `grid_utils.py` yourself rather than going through `submit_grid`,
photo-restart is opt-in — pass `--restart_photos` to resume from any existing
checkpoints instead of starting over:

````bash
python -m generate_star_grid.grid_utils \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type linear --num_points 200 \
    --task_id $SLURM_ARRAY_TASK_ID \
    --restart_photos
````

## Common MESA Failure Modes

When a task fails, the most useful first step is to look at the SLURM output
file for that task (`slurm_<jobid>_<taskid>.out` in the batch directory, if it
hasn't been cleaned up) and the MESA terminal output it contains.

Common causes of convergence failure include:

- **High mass at low or high metallicity** — MESA's solver can struggle near the
  edges of parameter space. Failures tend to cluster at the upper end of a mass
  sweep (e.g. M > 1.15 M☉) when Z is very low or very high.
- **Timestep or mesh convergence** — MESA reports these as repeated retries
  before terminating. Tightening `varcontrol_target` or `mesh_delta_coeff` in
  `inlist_template` can help, at the cost of longer run times.
- **Pre-main-sequence relaxation** — failures early in the run (before ZAMS)
  are often caused by `pre_ms_T_c` being too low or too high for the chosen mass.

For detailed guidance on MESA error messages and convergence controls, see the
[MESA documentation](https://docs.mesastar.org) and the
[MESA FAQs on the MESA forums](https://lists.mesastar.org).
