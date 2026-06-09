#!/bin/bash

# --- CONFIGURATION: update to match your grid's fixed parameters ---
THRESHOLD=13       # minimum expected history.data size in MB
FIXED_Y=0.270      # fixed initial_y used in this grid (used to reconstruct dir name)
FIXED_Z=0.0200     # fixed initial_z used in this grid
FIXED_ALPHA=2.00   # fixed mixing_length_alpha used in this grid
# -------------------------------------------------------------------

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
    
    # Extract task ID and reconstructed folder name
    task_id=$(echo "$logfile" | grep -oP 'TASK_\K[0-9]+')
    mass_prefix=$(echo "$logfile" | grep -oP 'log_M_\K[0-9.]+')
    folder_name=$(ls -d M_${mass_prefix}*_Y_${FIXED_Y}_Z_${FIXED_Z}_alpha_${FIXED_ALPHA} 2>/dev/null | head -n 1)

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
