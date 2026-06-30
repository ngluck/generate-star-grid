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

## Preserved Directories for Persistent Failures

If a task still fails after the one retry, its `M_*` run directory is **not** deleted
during cleanup. All other successfully-completed run directories are removed as usual.
This lets you inspect the history files, MESA output, and any individual log files
inside the kept directory to diagnose what went wrong. The corresponding entry in
`notes.txt` records the task ID, folder name, and initial conditions.

## Expanding an Existing Grid

If you have a finished merged grid and want to add new outer-parameter combinations
without re-running what is already computed, three tools work together:

1. **`grid_inventory`** — inspect what is already covered
2. **`submit_grid expand`** — submit only the missing batches
3. **`merge_grids expand`** — stitch the new batches into the existing merged grid (runs automatically)

### Checking Coverage with `grid_inventory`

`grid_inventory` scans a parent directory for merged grid directories (any directory
containing `combined_history.hdf5` and a `_var<Label>` token in its name) and reports
which outer-parameter combinations are covered, by reading each batch's `notes.txt`.

````bash
python -m generate_star_grid.grid_inventory --parent_dir /path/to/parent_dir
````

Example output for a finished M × Z grid:

````
my_grid_varM_varZ/
  Varies: M (inner), Z (outer)
  Fixed:  Y=0.28, alpha=2.0
  Z values covered: 0.001, 0.002, 0.004, 0.007, 0.01, 0.014, 0.02, 0.028, 0.035, 0.04
````

For large grids with more than 8 outer values, the list is condensed to the first four
with a count: `0.001, 0.002, 0.004, 0.007, ... (20 batches total)`.

### Submitting Missing Batches with `submit_grid expand`

`submit_grid expand` takes a finished merged grid directory (`--base_dir`) and the full
*desired* outer spec. It reads each batch's `notes.txt` inside `--base_dir` to find what
is already covered, then submits only the missing batches:

````bash
python -m generate_star_grid.submit_grid expand \
    --base_dir /path/to/my_grid_varM_varZ \
    --source_dir /path/to/template_dir \
    --queue_file /path/to/expand_queue.json \
    --outer 'initial_z=0.001,0.002,0.004,0.007,0.01,0.014,0.02,0.028,0.035,0.04' \
    --outer 'initial_y=0.24,0.26,0.28,0.30' \
    --inner 'mass=0.7:1.2' --num_points 500
````

````{tip}
Add `--dry_run` to see coverage stats and the list of missing batches without
writing a queue file or submitting anything:

~~~
Desired outer batches: 40
Already covered:       10
Missing (to submit):   30

Missing batches:
  my_grid_Y_0p24_Z_0p001/
  my_grid_Y_0p24_Z_0p002/
  ...
--dry_run: queue file not written, no jobs submitted.
~~~
````

The expand run behaves like a normal `submit_grid start` queue from there: one batch at
a time, retrying failed tasks once, then automatically triggering `merge_grids expand`
once all missing batches are done.

````{note}
Coverage matching uses float tolerance so that values like `0.0200` and `0.02` are
treated as the same — minor formatting differences in `notes.txt` do not cause a batch
to be incorrectly flagged as missing.
````

### How `merge_grids expand` Assembles the Result

`merge_grids expand` is called automatically at the end of `submit_grid expand` but can
also be run manually:

````bash
python -m generate_star_grid.merge_grids expand \
    --base_dir /path/to/my_grid_varM_varZ \
    --queue_file /path/to/expand_queue.json
````

It:
1. Reads the existing `combined_history.hdf5` from `--base_dir` as the base
2. Discovers new batch directories (siblings in the parent directory that have a
   `combined_history.hdf5` and are not themselves a merged directory)
3. Concatenates base + new batches, offsetting Track IDs in the new batches so they do
   not collide with tracks already in the base
4. Derives a new expanded directory name from the union of all parameter labels — e.g.
   `my_grid_varM_varZ` becomes `my_grid_varM_varY_varZ`
5. Moves `--base_dir` and all new batch directories inside the expanded directory

The result is a single merged directory containing all original and new per-batch
subdirectories alongside the combined `combined_history.hdf5` covering the full
expanded parameter space.
