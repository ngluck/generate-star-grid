# Advanced Usage

## Multi-Batch Grids

For grids too large to keep on disk all at once (e.g. 500 masses × 10 metallicities),
`submit_grid.py` splits the sweep into an **outer** parameter (processed sequentially,
one disk-bounded batch at a time) and an **inner** parameter (swept within each
batch's SLURM array). Each batch: copies the template directory, submits the array job,
then submits a combine/cleanup job that builds that batch's `combined_history.hdf5`,
retries any failed tasks once, deletes the batch's run artifacts, and only then
triggers the next outer batch — so peak disk usage is bounded by a single batch's
footprint, not the whole grid's.

````{note}
Each batch's `combined_history.hdf5` gets a constant column for both its inner
key (e.g. `M`) and every outer key fixed for that batch (e.g. `Z`) — both are
present in each per-star subdirectory's name (e.g.
`M_0.700_Y_0.27_Z_0.000379_alpha_2.0`), so both are extracted.
````

````{tip}
If the environment variable `SEISTRON_BASE_DIR` is set, each combine job also
plots a quick HR diagram (evenly spaced tracks colored by mass) into the batch
directory next to `combined_history.hdf5`, via a sibling project's
`my_library.grid_builders.plot_grid_hr_diagram` module, as a visual sanity
check that the grid looks as expected. This step is entirely optional: it's
skipped with a one-line message if the variable isn't set, and a plotting
failure only logs a warning rather than failing the combine/cleanup job.
````

`--outer` and `--inner` both accept repeatable `KEY=SPEC` arguments, using the same
grammar as `--param` (built-in aliases `mass`/`y`/`z`/`alpha`, or any other
`inlist_template` parameter):

````bash
python -m generate_star_grid.submit_grid start \
    --source_dir /path/to/clean/template_dir \
    --queue_file /path/to/queue.json \
    --outer 'initial_z=0.001,0.0015,0.0023,...,0.04' \
    --inner mass=0.7:1.2 --grid_type linear --num_points 500
````

````{tip}
Add `--dry_run` to preview the batch count, models-per-batch, and example batch
directory names without writing the queue file or submitting anything.
````

This submits the first batch and writes `queue.json`, which tracks the remaining
outer batches and all per-batch configuration. Each batch's combine/cleanup job
calls `submit_grid next --queue_file ...` itself once it actually finishes, rather
than via a pre-declared SLURM dependency — this is what lets a failed-task retry
happen first without losing track of when the batch is really done.

Key SLURM flags for the array jobs (all overridable via `submit_grid start`):

| Flag | Default | Notes |
|---|---|---|
| `--array_partition` | `day` | Partition for each batch's SLURM array; `day` allows up to 1000 simultaneous CPUs |
| `--array_time` | `12:00:00` | Per-task time limit; individual MESA tasks typically finish in under 10h |
| `--array_mem` | `8G` | Per-task memory; actual MESA usage is typically 4–5 GB |
| `--combine_partition` | `day` | Partition for the combine/cleanup job after each batch |
| `--combine_time` | `2:00:00` | Wall time for the combine/cleanup job |
| `--combine_mem` | `16G` | Memory for the combine/cleanup job |

See `submit_grid start --help` for the full list of overridable flags.

## Running Large Grids with Parallel Queues

For grids with many outer batches (e.g. 500 masses × 10 Z × 12 Y × 15 α = 1,800 outer
batches), advancing one batch at a time can take a long time. Use `--parallel N` to
advance N queues simultaneously:

````bash
python -m generate_star_grid.submit_grid start \
    --source_dir /path/to/template_dir \
    --queue_file /path/to/queue.json \
    --outer 'initial_z=0.001,0.002,...,0.04' \
    --outer 'initial_y=0.24,0.26,0.28,0.30' \
    --outer 'mixing_length_alpha=1.5,1.8,2.0,2.2,2.5' \
    --inner 'mass=0.7:1.2' --num_points 500 \
    --parallel 5
````

This splits the full outer batch list into 5 roughly equal contiguous chunks, writes 5
queue files (`queue_par0.json` … `queue_par4.json` in the same directory as
`--queue_file`), and calls `submit_grid next` for each — so up to 5 batches advance
simultaneously from the start. Each queue progresses through its slice of the outer
parameter space independently, and the final `merge_grids` step fires automatically
once the last queue finishes, using an atomic sentinel file to ensure the merge runs
exactly once even if multiple queues happen to complete at the same time.

````{tip}
`--parallel 1` (the default) is identical to the original serial behavior —
no new files are written and no queue file structure changes. Existing queue
files from prior runs are unaffected.
````

Add `--dry_run` to preview the split before committing:

````
Outer batches: 1800
Inner models per batch: 500
Total models: 900000
  batch dir: template_Z_0p001_Y_0p24_alpha_1p50/  (array 0-499)
  batch dir: template_Z_0p001_Y_0p24_alpha_1p80/  (array 0-499)
  batch dir: template_Z_0p001_Y_0p24_alpha_2p00/  (array 0-499)
  ... (1797 more)
--parallel 5: would create 5 queue files of ≤360 batches each.
--dry_run: queue file not written, no jobs submitted.
````

## Limiting CPU Usage (`--max_cpus`)

On partitions with a per-user CPU cap, use `--max_cpus` to limit how many array tasks
run at once across all parallel queues. This works via SLURM's `--array=0-N%T` throttle
syntax, where `T = max_cpus ÷ parallel`.

````bash
    --parallel 5 --max_cpus 990
````

With 500 inner tasks per batch, this gives a per-batch throttle of 990 ÷ 5 = 198 — so
at most 990 CPUs are used at once, leaving 10 free for other work (e.g. an interactive
Jupyter session on the same partition).

| Setup | Peak CPUs |
|---|---|
| `--parallel 1`, no `--max_cpus` (default) | up to 500 for a 500-mass inner sweep |
| `--parallel 5 --max_cpus 990` | up to 990 |
| `--parallel 3 --max_cpus 600` | up to 600 |

````{note}
`--max_cpus` throttles the grid's own array jobs — it does not reserve CPUs or block
other users from filling those slots. If your Jupyter session runs on a separate
partition (e.g. `gpu_devel`), there is no CPU competition with the `day` partition and
`--max_cpus` is not needed.
````

## Retry Job Naming

When a combine/cleanup job detects failed MESA tasks and retries them, the retry array
job is submitted with the prefix `retry_` in its SLURM job name — e.g.
`retry_Z0p001_alpha2p00` instead of `mesa_Z0p001_alpha2p00`. This makes retries
immediately distinguishable in `squeue`, `sacct`, and SLURM notification email subject
lines. The full list of retried task IDs, folders, and initial conditions is always
written to the batch's `combine_<jobid>.out` stdout file.
