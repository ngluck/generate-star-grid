#!/bin/bash
#SBATCH --job-name=combine_Y0270Z002000
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition=day
#SBATCH --nodes=1
#SBATCH --time=2:00:00
#SBATCH --mem=16G
#SBATCH --mail-type=NONE
#SBATCH --output=TMP/grid/src_Y_0p27_Z_0p02/combine_%j.out

DEST="TMP/grid/src_Y_0p27_Z_0p02"
RETRY_DONE="${RETRY_DONE:-0}"

cd "$DEST" || { echo "FATAL: cannot cd to $DEST" >&2; exit 1; }

module purge
module load miniconda
conda activate mesa

echo "Checking for failed tasks (retry_done=$RETRY_DONE)..."
FAILED=$("/usr/bin/python3" -m generate_star_grid.submit_grid check-failed --dest "$DEST" --keys M,alpha)

if [ -n "$FAILED" ] && [ "$RETRY_DONE" -eq 0 ] && [ "0" -eq 1 ]; then
    echo "Failed tasks detected:"
    echo "$FAILED"

    echo "Preserving DATA/ and photos/ for photo restart."

    FAILED_IDS=$(echo "$FAILED" | cut -d'|' -f1 | paste -sd, -)
    echo "Retrying failed tasks once: $FAILED_IDS"
    RETRY_JOB=$(sbatch --parsable --job-name=retry_Y0270Z002000 --array=$FAILED_IDS "$DEST/run_array.sh")
    sbatch --dependency=afterany:$RETRY_JOB --export=ALL,RETRY_DONE=1 "$DEST/run_combine_cleanup.sh"
    echo "Handed off to retry chain (array job $RETRY_JOB); exiting without finalizing."
    exit 0
fi

FAILED_FOLDERS=""
if [ -n "$FAILED" ]; then
    N=$(echo "$FAILED" | wc -l)
    echo "WARNING: $N task(s) still failed. Excluding from HDF5; logging to notes.txt."
    FAILED_FOLDERS=$(echo "$FAILED" | cut -d'|' -f2 | tr '
' ' ')
fi

echo "Building combined_history.hdf5..."
if [ -n "$FAILED_FOLDERS" ]; then
    "/usr/bin/python3" -m generate_star_grid.make_grid \
        --parent_dir "$DEST" \
        --constants Y Z \
        --save \
        --exclude_dirs $FAILED_FOLDERS
else
    "/usr/bin/python3" -m generate_star_grid.make_grid \
        --parent_dir "$DEST" \
        --constants Y Z \
        --save
fi

if [ -n "$FAILED" ]; then
    {
        echo ""
        echo "Failed stars (excluded from combined_history.hdf5):"
        echo "$FAILED" | while IFS='|' read -r tid folder params; do
            echo "  TASK_$tid ($folder): $params"
        done
    } >> "$DEST/notes.txt"
fi

if [ -n "$SEISTRON_BASE_DIR" ]; then
    echo "Plotting HR diagram..."
    PYTHONPATH="$SEISTRON_BASE_DIR:$PYTHONPATH" "/usr/bin/python3" -m my_library.grid_builders.plot_grid_hr_diagram \
        --combined_history "$DEST/combined_history.hdf5" || echo "WARNING: HR diagram plotting failed; continuing."
else
    echo "SEISTRON_BASE_DIR not set; skipping optional HR diagram plot."
fi

# Cleanup runs only after make_grid has written failure_report.txt, which is
# built from LOGS/ and grid_TAMS/. Everything a still-failed task needs for
# diagnosis is kept: its run directory, MESA log, SLURM output and archived
# inlist. Only artifacts of tasks that succeeded are removed.
echo "Deleting run directories and artifacts..."
if [ -n "$FAILED" ]; then
    FAILED_FOLDERS=$(echo "$FAILED" | cut -d'|' -f2)
    FAILED_IDS=$(echo "$FAILED" | cut -d'|' -f1)
    for dir in "$DEST"/M_*/; do
        folder=$(basename "$dir")
        if ! echo "$FAILED_FOLDERS" | grep -qx "$folder"; then
            rm -rf "$dir"
        fi
    done
    for f in "$DEST"/grid_inlists/inlist_*; do
        [ -e "$f" ] || continue
        folder=$(basename "$f" | sed 's/^inlist_//')
        echo "$FAILED_FOLDERS" | grep -qx "$folder" || rm -f "$f"
    done
    for f in "$DEST"/LOGS/*; do
        [ -e "$f" ] || continue
        tid=$(basename "$f" .txt | awk -F_TASK_ '{print $2}')
        if [ -z "$tid" ] || ! echo "$FAILED_IDS" | grep -qx "$tid"; then
            rm -f "$f"
        fi
    done
    for f in "$DEST"/slurm_*.out; do
        [ -e "$f" ] || continue
        tid=$(basename "$f" .out | awk -F_ '{print $NF}')
        if [ -z "$tid" ] || ! echo "$FAILED_IDS" | grep -qx "$tid"; then
            rm -f "$f"
        fi
    done
    rm -f "$DEST"/grid_TAMS/TAMS_*.mod
    echo "Kept run dir, MESA log, SLURM output and archived inlist for each still-failed task."
else
    rm -rf "$DEST"/M_*/
    rm -f  "$DEST"/grid_TAMS/TAMS_*.mod
    rm -f  "$DEST"/grid_inlists/inlist_*
    find   "$DEST/LOGS" -type f -delete
    rm -f  "$DEST"/slurm_*.out
fi

echo "Done. HDF5 saved at $DEST/combined_history.hdf5"

echo "Triggering next batch in queue..."
"/usr/bin/python3" -m generate_star_grid.submit_grid next --queue_file "TMP/queue.json"
