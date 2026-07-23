#!/bin/bash
#SBATCH --job-name=mesa_Y0270Z002000
#SBATCH --array=0-7
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --partition=day
#SBATCH --nodes=1
#SBATCH --time=1-00:00:00
#SBATCH --mem=8G
#SBATCH --mail-type=NONE
#SBATCH --output=TMP/grid/src_Y_0p27_Z_0p02/slurm_%A_%a.out

cd "TMP/grid/src_Y_0p27_Z_0p02" || { echo "FATAL: cannot cd to TMP/grid/src_Y_0p27_Z_0p02" >&2; exit 1; }

module purge
module load miniconda
conda activate mesa

export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

"/usr/bin/python3" -m generate_star_grid.grid_utils \
    --initial_Y \
    0.270 \
    --initial_Z \
    0.02000 \
    --mass \
    0.7:1.8 \
    --mixing_length_alpha \
    1.0:3.0 \
    --grid_type \
    sobol \
    --num_points \
    8 \
    --restart_photos \
    --task_id=$SLURM_ARRAY_TASK_ID
