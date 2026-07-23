# Troubleshooting

## The Failure Report

Every grid writes a `failure_report.txt` into its grid directory collecting
**all** failed tracks and the reason for each, so the whole grid's failures can
be inspected together instead of one log at a time. It is written by default —
by `make_grid` (and therefore by every `submit_grid` combine job) and by
`chunk_grid finalize`.

A track counts as failed if and only if it never produced its **last save file**
(`grid_TAMS/TAMS_<dir>.mod` for a main-sequence run). That file is written by
`save_model_when_terminate` on a genuine stop condition, so its absence is the
one signal that separates a finished track from one cut short.

For a run with several stages, the *last* one decides: a track that produced
ZAMS and TAMS but never RGB has failed. See
[Multi-Stage Runs](advanced_usage.md#multi-stage-runs) — the report then also
breaks the failures down by how far each track got.

Nothing is retried automatically on the strength of this report: re-running the
same track with the same settings usually reproduces the same failure, so the
compute is better spent after you have read the report and changed something.

### Reading the Report

```text
Tasks examined: 4096
Completed:      3534 (86.3%)
Failed:         562

SUMMARY BY REASON
------------------------------------------------------------------------------
   511  log ends mid-run with no termination code (cut off)
    37  MESA terminated: min_timestep_limit
    14  cancelled at the SLURM time limit

PARAMETER RANGE PER REASON
------------------------------------------------------------------------------
  log ends mid-run with no termination code (cut off)
      M 0.7014-1.7984 (med 1.2561), Z 0.000102-0.0396 (med 0.00204), alpha 1.0-3.0 (med 2.0)
  MESA terminated: min_timestep_limit
      M 0.7024-1.675 (med 0.7957), Z 0.0001-0.0387 (med 0.017), alpha 1.11-2.90 (med 1.81)
```

The median matters more than the range on a Sobol grid, where almost every
reason spans the whole space. Above, the `min_timestep_limit` failures sit at
median M≈0.80 and Z≈0.017 against the cut-off group's M≈1.26 and Z≈0.002 —
MESA is giving up on the *low-mass, metal-rich* corner, which is a timestep-control
problem to fix in the inlist. The cut-off group skews to high mass, which is a
wall-clock problem to fix with `--array_time`.

Each failed track is then listed under its reason with its parameters, the last
MESA model number reached, and paths to both its MESA log and its SLURM output.

### Where the Reasons Come From

No single source is sufficient, so three are merged:

| Source | What it establishes |
|---|---|
| The last stage's save file being absent | That the track failed at all |
| Earlier stages' save files | How far it got before it stopped |
| `LOGS/log_<dir>_TASK_<id>.txt` | MESA's own verdict (`termination code: ...`), or where it was cut off |
| `slurm_logs/*_<id>.out` | The SLURM-level cause — a time limit or an OOM kill never appears in the MESA log |

Categories, in the order they are decided:

| Category | Meaning |
|---|---|
| `mesa_terminated` | MESA printed a termination code but saved nothing — it gave up for a numerical or physical reason |
| `slurm_timeout` | SLURM cancelled the task at its time limit |
| `slurm_oom` | The task was killed for exceeding its memory allocation |
| `truncated` | The log stops mid-step with no verdict from either side — cut off by a time limit, node failure, or kill |
| `no_mesa_output` | The log is missing or empty: the task died before MESA started, typically an environment problem |
| `never_started` | No log and no run directory — the array task never ran |

### Running It Yourself

The report can be regenerated at any time without re-running anything:

````bash
python -m generate_star_grid.failure_report --parent_dir /path/to/my_grid
````

````{tab-set}
```{tab-item} Print instead of write
python -m generate_star_grid.failure_report --parent_dir /path/to/my_grid --stdout
```
```{tab-item} Cap the per-reason listing
python -m generate_star_grid.failure_report --parent_dir /path/to/my_grid \
    --max_detail_per_reason 20
```
```{tab-item} Name the stages explicitly
python -m generate_star_grid.failure_report --parent_dir /path/to/my_grid \
    --stages ZAMS,TAMS,RGB
```
```{tab-item} A continuation run's save files
python -m generate_star_grid.failure_report --parent_dir /path/to/my_grid \
    --save_dir grid_CONT --save_prefix cont_
```
````

Stages are normally read from the grid's `stages.json` or its inlists; `--stages`
overrides that. The older `--save_dir` / `--save_prefix` / `--save_suffix` trio
still works as a single-stage shorthand. Pass `--no_failure_report` to
`make_grid` or `chunk_grid finalize` to skip writing the report.

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

`check-failed` is the general-purpose failure detector, and the machine-readable
counterpart to [the failure report](#the-failure-report). It scans the `LOGS/`
directory for per-task log files, reconstructs each task's model directory name
from the log filename, and reports the task as failed if its **last stage's**
save file (`grid_TAMS/TAMS_*.mod` for a main-sequence run) is absent.

That save file is the whole test. It is written by `save_model_when_terminate`
on a genuine stop condition, so its absence is what distinguishes a finished
track from one cut short — whether the task hit the SLURM `--time` limit mid-run
or MESA itself gave up (e.g. `termination code: min_timestep_limit` after
exhausting solver retries).

````{note}
Earlier versions also required `DATA/history.data` to exceed `--threshold_mb`.
That check is gone: file size measures how long a track ran, not whether it
finished. A track killed at the time limit routinely exceeds any threshold
without ever completing, while a legitimately short track can finish with a
small history file. It was also actively wrong after any pipeline stage that
reclaims disk — on a chunked grid whose completed models have been folded into
their HDF5 and deleted, the size check reports every task in the grid as failed.

`--threshold_mb` and `--fail_threshold_mb` are still accepted and ignored, so
existing queue files and generated SLURM scripts keep working unchanged.
````

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
| `--stages` | no | Ordered save-file names, e.g. `ZAMS,TAMS,RGB`; the last decides completion. Read from `stages.json` or the inlists when omitted |
| `--threshold_mb` | no | Deprecated and ignored; accepted for backward compatibility |

#### How it works internally

`check-failed` calls `find_failed_tasks()` from `grid_utils.py`, which:

1. Globs `LOGS/log_*_TASK_*.txt` to find every task that ran
2. Parses the task ID from the `_TASK_<id>` suffix of each filename
3. Reconstructs the model subdirectory name by stripping the `log_` prefix and
   `_TASK_<id>` suffix — this works for any parameter combination without any
   hardcoded assumptions
4. Checks whether the last stage's save file `grid_TAMS/TAMS_<folder>.mod` exists —
   the sole test for whether the task succeeded
5. Returns a list of dicts with `task_id`, `folder`, `params`, and `reached` (the
   furthest stage the run did produce) for each failure

The stage list comes from the grid's `stages.json` or its inlists, so the same
check applies unchanged to runs that continue past the main sequence.

This is also the function the combine/cleanup job calls internally to detect
failures (and again after the retry, if `--retry` was passed). The still-failed
folder names are passed to `make_grid --exclude_dirs`, which
filters them out before writing `combined_history.hdf5` — so the exclusion
is real, not just a warning in `notes.txt`.

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

### How it works

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

### `history.data` deduplication

Resuming from a photo makes MESA re-append rows starting at the photo's
`model_number`, which leaves a short non-monotonic, duplicated stretch where
the original run and the restart overlap. The HDF5 loader
(`load_history_with_constants_from_profile`, used by `make_grid`) detects
non-monotonic `model_number` sequences, keeps only the last (i.e. restarted)
occurrence of each one, and re-sorts — so `combined_history.hdf5` doesn't
double-count steps. This is a no-op for tracks that completed without ever
timing out.

### Using `grid_utils.py` directly (outside `submit_grid`)

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
