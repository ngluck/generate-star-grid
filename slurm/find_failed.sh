#!/bin/bash

# --- CONFIGURATION ---
THRESHOLD=13       # minimum expected history.data size in MB
# ----------------------

if [ "$1" == "clean" ]; then
    CLEANUP=true
    echo "!!! RUNNING IN CLEANUP MODE !!!"
else
    CLEANUP=false
    echo "--- Running in Safe Mode (No files will be deleted) ---"
    echo "To clean corrupted folders, run: bash ../find_failed.sh clean"
fi

# ---------------------

failed_ids=""

echo "Checking runs for history.data < ${THRESHOLD}M..."
echo "-----------------------------------------------"

for logfile in LOGS/log_*_TASK_*.txt; do
    
    # Extract task ID and reconstructed folder name. mass_prefix is the exact
    # 'M_<value>' string used in both the log filename and the run directory
    # (same dynamic precision from compute_param_formats), so it alone
    # identifies the directory regardless of the Y/Z/alpha precision used.
    task_id=$(echo "$logfile" | grep -oP 'TASK_\K[0-9]+')
    mass_prefix=$(echo "$logfile" | grep -oP 'log_M_\K[0-9.]+')
    folder_name=$(ls -d M_${mass_prefix}_* 2>/dev/null | head -n 1)

    if [[ -z "$folder_name" ]]; then
        echo "Task $task_id: Could not find a directory for Mass $mass_prefix"
        failed_ids="${failed_ids}${task_id},"
        continue
    fi
    
    history_file="${folder_name}/DATA/history.data"
    
    # Check if failed or missing
    if [[ ! -f "$history_file" ]] || [[ $(du -m "$history_file" | cut -f1) -lt $THRESHOLD ]]; then
        size=$(du -m "$history_file" | cut -f1 2>/dev/null || echo "0")
        echo "Task $task_id: FAILED (${size}M) - $folder_name"
        failed_ids="${failed_ids}${task_id},"

        # --- THE CLEANUP LINE ---
        if [ "$CLEANUP" = true ]; then
            if [ -d "${folder_name}/DATA" ]; then
                echo "  --> Cleaning ${folder_name}/DATA/..."
                rm -rf "${folder_name}/DATA"/*
            fi
            # Optional: Clear the MESA LOGS folder inside the run directory too
            if [ -d "${folder_name}/LOGS" ]; then
                rm -rf "${folder_name}/LOGS"/*
            fi
        fi
    fi
done

failed_ids=$(echo "$failed_ids" | sed 's/,$//')

echo "-----------------------------------------------"
if [ -z "$failed_ids" ]; then
    echo "All runs look good!"
else
    if [ "$CLEANUP" = false ]; then
        echo "NOTE: Cleanup is currently OFF. No files were deleted."
        echo "Set CLEANUP=true in the script to wipe failed DATA folders."
    else
        echo "SUCCESS: Corrupted DATA folders have been cleared."
    fi
    echo ""
    echo "Resubmit command:"
    echo "sbatch --array=$failed_ids generate_grid_week_array.sh"
fi
