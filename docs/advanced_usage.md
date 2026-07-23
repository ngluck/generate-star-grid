# Advanced Usage

## Multi-Batch Grids

For grids too large to keep on disk all at once (e.g. 500 masses × 10 metallicities),
`submit_grid.py` splits the sweep into an **outer** parameter (processed sequentially,
one disk-bounded batch at a time) and an **inner** parameter (swept within each
batch's SLURM array). Each batch: copies the template directory, submits the array job,
then submits a combine/cleanup job that builds that batch's `combined_history.hdf5`,
writes its [failure report](troubleshooting.md#the-failure-report), deletes the batch's
run artifacts, and only then triggers the next outer batch — so peak disk usage is
bounded by a single batch's footprint, not the whole grid's.

````{tip}
This requires a parameter to batch *along*. For a flat grid with no outer
dimension — a joint Sobol cloud run as one array over task ids `0..N-1` — use
[`chunk_grid`](#chunked-runs-for-flat-grids-chunk_grid) instead, which bounds
disk by splitting the task-id range itself.
````

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

````{note}
Both the inner sweep and the outer batch dimension support Sobol sampling: pass
`--grid_type sobol` (with `--sobol_seed`) for the inner ranges and/or
`--outer_grid_type sobol` (with `--outer_sobol_seed`) for the outer ranges. See
[Sobol Sampling](usage.md#sobol-sampling) for details. When using a Sobol outer
dimension, keep `--outer_sobol_seed` **identical** between `start` and any later
`expand` — the two must draw the same outer cloud for `expand` to recognize which
batches already exist.
````

This submits the first batch and writes `queue.json`, which tracks the remaining
outer batches and all per-batch configuration. Each batch's combine/cleanup job
calls `submit_grid next --queue_file ...` itself once it actually finishes, rather
than via a pre-declared SLURM dependency — this is what lets an optional
failed-task retry happen first without losing track of when the batch is really done.

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

## Retrying Failed Tasks (`--retry`)

Retrying is **off by default**. Re-running a track with the same settings usually
reproduces the same failure, so the compute is better spent after reading the
[failure report](troubleshooting.md#the-failure-report) and changing something.

Pass `--retry` to `submit_grid start`/`expand` to retry each failed task once
before the batch is finalized. It is worth enabling when you expect failures to
be **timeouts** rather than numerical give-ups: a retried task resumes from its
MESA photos (see [Resuming Timed-Out Runs](troubleshooting.md#resuming-timed-out-runs-photo-restart)),
so it continues from where it stopped instead of recomputing from scratch. The
failure report's reason breakdown is what tells you which case you are in.

````{note}
`--no_retry` is still accepted and is now a no-op, since off is the default.
Existing queue files keep working unchanged.
````

### Retry Job Naming

When a combine/cleanup job retries failed MESA tasks, the retry array
job is submitted with the prefix `retry_` in its SLURM job name — e.g.
`retry_Z0p001_alpha2p00` instead of `mesa_Z0p001_alpha2p00`. This makes retries
immediately distinguishable in `squeue`, `sacct`, and SLURM notification email subject
lines. The full list of retried task IDs, folders, and initial conditions is always
written to the batch's `combine_<jobid>.out` stdout file.

## Preserved Directories for Persistent Failures

When a task is left failed — immediately, or after the one retry if `--retry`
was passed — two things happen:

- Its `M_*` run directory is **not** deleted during cleanup, so you can inspect
  the history files, MESA output, and any individual log files to diagnose what
  went wrong.
- It is **excluded from `combined_history.hdf5`** — the combine job passes the
  still-failed folder names to `make_grid --exclude_dirs`, so the exclusion is
  enforced in the HDF5, not just noted in `notes.txt`.

The corresponding entry in `notes.txt` records the task ID, folder name, and
initial conditions.

Each batch's combine job also writes a
[failure report](troubleshooting.md#the-failure-report) into the batch directory,
collecting every failed track and the reason for each — so what failed is
inspectable in a single document rather than by opening logs one at a time.

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
a time, then automatically triggering `merge_grids expand`
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

## Chunked Runs for Flat Grids (`chunk_grid`)

`submit_grid` bounds disk usage by splitting a grid along an **outer** parameter.
A flat grid has no outer parameter to split on — a joint Sobol cloud over mass,
Z, and α (see [Sobol Sampling](usage.md#sobol-sampling)) is one array over task
ids `0..N-1`, and every run directory lands side by side in the grid directory.
At 8192 models × ~20 MB of `DATA/` and `photos/` each, that footprint can exhaust
the filesystem long before the array finishes, even though the output you
actually keep (`grid_TAMS/`, `grid_profiles/`, `combined_history.hdf5`) is a
small fraction of it.

`chunk_grid` carves the task-id range into contiguous chunks and runs **one chunk
at a time**, folding each finished chunk down to a single HDF5 before the next
chunk starts:

```text
my_grid/
├── chunk_00000_00511/combined_history.hdf5     # intermediate
├── chunk_00512_01023/combined_history.hdf5     # intermediate
├── ...
└── combined_history.hdf5                       # master, merged from the above
```

Peak disk usage is bounded by one chunk's run directories rather than the whole
grid's. Everything stays in the grid's own directory — there is no scratch
staging or relocation step.

````{note}
Chunking is safe for Sobol grids because every array task rebuilds the identical
cloud from `--sobol_seed` and selects its own index, so the mapping from task id
to parameters is fixed no matter how the range is split. The flags you pass to
`chunk_grid` must therefore match the cloud exactly — same `--mass`/`--initial_Z`/
`--initial_Y`/`--alpha_MLT`/`--param` specs, same `--grid_type`, `--num_points`,
and `--sobol_seed`. `chunk_grid submit` writes them into the generated array
script itself, so a submitted run stays consistent by construction.
````

### Submitting a Chunked Grid

`chunk_grid submit` generates the SLURM scripts, writes `chunk_queue.json`, and
submits the first chunk:

````bash
python -m generate_star_grid.chunk_grid submit \
    --parent_dir /path/to/my_grid \
    --mass 0.7:1.8 \
    --initial_Z 1e-4:0.04:log \
    --alpha_MLT 1:3 \
    --grid_type sobol --num_points 8192 --sobol_seed 0 \
    --chunk_size 512 \
    --max_cpus 990
````

````{tip}
Add `--dry_run` to preview the chunk split without writing or submitting anything:

~~~
8192 models / chunk_size 512 -> 16 chunk(s).
  chunk 0: tasks 0-511
  chunk 1: tasks 512-1023
  chunk 2: tasks 1024-1535
  ... (13 more)
On completion: merge the master, verify it, and delete the intermediate chunks.
--dry_run: nothing written or submitted.
~~~

`chunk_grid plan --num_points 8192 --chunk_size 512` prints the same split
(including each chunk's directory name) without needing the grid flags.
````

Three scripts are written into the grid directory, plus a `slurm_logs/` directory
for their output:

| File | Role |
|---|---|
| `run_chunk_array.sh` | The MESA array job — one chunk's task-id range at a time |
| `run_chunk_step.sh` | Runs after each array (`afterany`): compresses the chunk, submits the next |
| `run_finalize.sh` | Merges the master and deletes the intermediates |
| `chunk_queue.json` | Queue state: full config, `current` chunk, and `remaining` chunk indices |

The chain is: chunk *i*'s array → step job (`chunk_grid advance`) → chunk *i+1*'s
array → … → finalize. The step job is submitted with `afterany`, so it runs even
if some tasks in the array fail — a partly failed chunk still compresses its
completed models instead of stalling the queue.

Key flags (see `chunk_grid submit --help` for the rest):

| Flag | Default | Notes |
|---|---|---|
| `--chunk_size` | `512` | Tasks per chunk; sets the peak disk footprint |
| `--max_cpus` | none | Throttles each chunk's array via `--array=S-E%T` |
| `--array_time` / `--array_mem` / `--array_partition` | `23:59:59` / `8G` / `day` | Per-task MESA resources |
| `--step_time` / `--step_mem` | `2:00:00` / `16G` | The compress-and-submit-next job |
| `--finalize_time` / `--finalize_mem` | `8:00:00` / `32G` | The final merge job |
| `--constants` | `M Y Z alpha` | Parameter labels extracted from directory names into the HDF5 |
| `--parent_dir` | current directory | Grid directory |

### What Compression Does

For each chunk, `chunk_grid advance` (or `chunk_grid compress`, run by hand):

1. Rebuilds the chunk's task ids → model directory names, exactly as the array
   tasks named them.
2. Partitions them into **completed** / **incomplete** / **never started**. The
   guard is per directory: a model is complete if and only if its own
   `grid_TAMS/TAMS_<dirname>.mod` exists. Unlike the all-or-nothing check in
   `make_grid --cleanup`, a partially failed chunk still compresses everything
   that did finish.
3. Moves the completed directories into `chunk_<start>_<end>/` (a same-filesystem
   rename) and builds `combined_history.hdf5` from them.
4. Deletes the moved run directories — but only after verifying the HDF5 exists
   and is non-empty. If the write failed, the run directories are left in the
   chunk directory for inspection and the job errors out.

Incomplete models are left untouched in the grid directory, so they can be
inspected or re-run. The run directories are redundant once the HDF5 exists:
TAMS models are already in `grid_TAMS/`, profiles in `grid_profiles/`, inlists in
`grid_inlists/`, and MESA stdout in `LOGS/`.

````{note}
`chunk_grid` does not retry failed tasks the way `submit_grid` does — re-running
a track unchanged usually reproduces the same failure. Tasks that never produced
a TAMS file are skipped by compression, their directories are left in place, and
the queue moves on. `finalize` then writes a
[failure report](troubleshooting.md#the-failure-report) collecting every one of
them and why, so the whole grid's failures can be read at once and acted on
deliberately.
````

### Finalizing

Once every chunk is compressed, the finalize job merges the chunk HDF5s into the
master. This delegates to the same `merge_batch_hdf5` used for multi-batch
grids, so each chunk's `Track` values are offset to stay globally unique and all
chunks are pinned to one canonical column order.

The intermediates are only deleted once the master **provably** contains every
row they held:

```text
Merging 16 chunks into /path/to/my_grid/combined_history.hdf5 ...
Master written: /path/to/my_grid/combined_history.hdf5 (4821330 rows from 16 chunks).
Master verified (4821330 rows). Deleted 16 intermediate chunk dir(s).
```

If the row counts disagree, nothing is deleted:

```text
Refusing to delete intermediates: master has 4700112 rows but the 16 chunk
file(s) total 4821330. Master left in place; chunks kept for inspection.
```

### Verifying Nothing Was Stranded

`chunk_grid verify` checks, per chunk, that every model which produced a save
file actually reached that chunk's HDF5. It compares two independently derived
numbers — how many of the chunk's task ids have a save file, and how many
distinct tracks its `combined_history.hdf5` holds — and flags any completed
model still sitting in the grid directory as a run directory.

````bash
python -m generate_star_grid.chunk_grid verify --parent_dir /path/to/my_grid
````

```text
chunk                           range  completed  in HDF5   status
chunk_00000_00511               0-511        507      507   OK
chunk_00512_01023            512-1023        505      505   OK
...
chunk_03584_04095           3584-4095          1        -   not compressed yet (1 completed on disk)

3535 completed model(s); 3534 track(s) in compressed chunks.

1 chunk(s) not compressed yet (normal while the grid is running).
```

It is safe to run at any time, including mid-run, and reads the chunk bounds
from `chunk_queue.json` (override with `--num_points` / `--chunk_size`). A
`STRANDED` row means a chunk's step job never ran or its compression died
partway; re-run `chunk_grid compress` for that chunk index to fold the models
in. The command exits non-zero when anything is stranded, so it can gate a
follow-on job.

### Running the Steps Manually

Each stage is a standalone subcommand, useful for recovering a run whose chain
was interrupted. `--parent_dir` defaults to the current directory.

````{tab-set}
```{tab-item} Compress one chunk
python -m generate_star_grid.chunk_grid compress \
    --parent_dir /path/to/my_grid \
    --mass 0.7:1.8 --initial_Z 1e-4:0.04:log --alpha_MLT 1:3 \
    --grid_type sobol --num_points 8192 --sobol_seed 0 \
    --chunk_size 512 --chunk_index 3
```
```{tab-item} Merge without deleting
python -m generate_star_grid.chunk_grid merge \
    --parent_dir /path/to/my_grid \
    --output /path/to/my_grid/combined_history.hdf5
```
```{tab-item} Finalize, keeping chunks
python -m generate_star_grid.chunk_grid finalize \
    --parent_dir /path/to/my_grid \
    --keep_chunks
```
````

````{tip}
`compress --no_delete` combines a chunk without deleting the moved run
directories — it reclaims no space, but lets you confirm the HDF5 looks right
before committing to the delete. `finalize --keep_chunks` is the same idea for
the intermediates.
````

### Choosing Between `submit_grid` and `chunk_grid`

| | `submit_grid start` | `chunk_grid submit` |
|---|---|---|
| Grid shape | Outer × inner Cartesian product | Flat task-id range (e.g. a joint Sobol cloud) |
| Split along | An outer parameter's values | Contiguous task-id chunks |
| Output | One `combined_history.hdf5` per batch, merged at the end | One per chunk, merged into a master at the end |
| Failed tasks | Excluded; retried once only with `--retry` | Skipped; directories left for manual handling |
| Failure report | Written by each batch's combine job | Written by `finalize` |
| Parallel queues | Yes (`--parallel N`) | No — one chunk at a time by design |

## Continuation Runs (Post-MS Evolution)

To resume from TAMS save files and continue evolution:

````bash
cd my_grid_run/
python -m generate_star_grid.grid_utils_cont \
    --min_mass 0.7 --max_mass 1.2 \
    --grid_type linear --num_points 200 \
    --max_workers 8 \
    --resume \
    --resume_edit_path /path/to/update_inlist.py
````

The `--resume_edit_path` script must define:
- `resume_tag` (str): appended to archived inlist filenames
- `modifications` (list of callables): each takes `(inlist_text, params)` and returns modified text

````{note}
`grid_utils_cont` accepts the same `--mass`, `--initial_Z`/`--initial_Y`/`--alpha_MLT`,
`--param`, `--dry_run`, and `--avg_data_mb` flags as `grid_utils`.
````
