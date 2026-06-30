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
filename, and checks whether `DATA/history.data` exists and meets a minimum size
threshold. A task is considered failed if the file is missing or smaller than
`--threshold_mb` (default: 13 MB).

````bash
python -m generate_star_grid.submit_grid check-failed \
    --dest /path/to/batch_dir \
    --keys M,Y,Z,alpha \
    --threshold_mb 13.0
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
| `--threshold_mb` | no | Minimum acceptable `history.data` size in MB (default: `13.0`) |

#### How it works internally

`check-failed` calls `find_failed_tasks()` from `grid_utils.py`, which:

1. Globs `LOGS/log_*_TASK_*.txt` to find every task that ran
2. Parses the task ID from the `_TASK_<id>` suffix of each filename
3. Reconstructs the model subdirectory name by stripping the `log_` prefix and
   `_TASK_<id>` suffix — this works for any parameter combination without any
   hardcoded assumptions
4. Checks whether `<folder>/DATA/history.data` exists and is at least `threshold_mb` in size
5. Returns a list of dicts with `task_id`, `folder`, and `params` for each failure

This is also the function the combine/cleanup job calls internally to detect
failures before retrying and again after the retry to decide which tasks to
exclude from `combined_history.hdf5`.

#### Resubmitting failed tasks manually

To resubmit only the failed task IDs as a new array job:

````bash
FAILED=$(python -m generate_star_grid.submit_grid check-failed \
    --dest /path/to/batch_dir --keys M,Y,Z,alpha)

FAILED_IDS=$(echo "$FAILED" | cut -d'|' -f1 | paste -sd, -)

# Clear DATA/ for each failed run before retrying
echo "$FAILED" | while IFS='|' read -r tid folder params; do
    rm -rf "/path/to/batch_dir/$folder/DATA"
    mkdir -p "/path/to/batch_dir/$folder/DATA"
done

sbatch --array=$FAILED_IDS /path/to/batch_dir/run_array.sh
````
