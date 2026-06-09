#!/bin/bash
# Template SLURM script for a MESA grid job array.
# Copy this file into your grid run directory and update:
#   - --job-name, --array (must match --num_points), --time, --mem as needed
#   - GRID_DIR: subdirectory name of your grid run (relative to where you submit)
#   - mass range, --num_points, and any fixed parameters below

#SBATCH --job-name=mesa_grid
#SBATCH --array=0-199
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition=week
#SBATCH --nodes=1
#SBATCH --time=6-00:00:00
#SBATCH --mem=8G
#SBATCH --mail-type=ALL

# --- CONFIGURATION: update per run ---
GRID_DIR=my_grid_run_dir   # subdirectory containing inlist_template, rn, star, etc.
# -------------------------------------

cd "$GRID_DIR"

module purge
module load miniconda
conda activate py311

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

python -m generate_star_grid.grid_utils \
    --min_mass=1.138 --max_mass=1.145 \
    --grid_type linear --num_points=200 \
    --task_id=$SLURM_ARRAY_TASK_ID
