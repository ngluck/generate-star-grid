#!/bin/bash

#SBATCH --job-name=time2.0
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
##SBATCH --gpu=1
#SBATCH --partition=week
#SBATCH --nodes=1
#SBATCH --time=3-00:00:00
#SBATCH --mem=100G
##SBATCH --constraint "v100"
#SBATCH --mail-type=ALL

# Nagivating to current working directory where files will be saved:
cd mod3_time_delta_coeff_2p00

module purge
module load miniconda
conda activate py311


#export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
#export MKL_NUM_THREADS=$SLURM_CPUS_PER_TASK

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

workers=${SLURM_CPUS_PER_TASK:-8}

python /gpfs/gibbs/pi/nagai/mesa_ml/my_library/grid_utils.py --grid_type linear --num_points=200 --max_workers="${workers}"
#python /gpfs/gibbs/pi/nagai/mesa_ml/my_library/grid_utils_cont_debug.py --grid_type linear --num_points=8 --max_workers=8 --resume --resume_edit_path ../../scripts/update_inlist.py
