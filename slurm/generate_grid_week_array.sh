#!/bin/bash

#SBATCH --job-name=mesa_transition_dense
#SBATCH --array=0-199
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
##SBATCH --gpu=1
#SBATCH --partition=week
#SBATCH --nodes=1
#SBATCH --time=6-00:00:00
#SBATCH --mem=8G
##SBATCH --constraint "v100"
#SBATCH --mail-type=ALL

# Nagivating to current working directory where files will be saved:
cd mod4_M_1p138_1p145_dense

module purge
module load miniconda
conda activate py311


#export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
#export MKL_NUM_THREADS=$SLURM_CPUS_PER_TASK

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1


python /home/ng474/project_pi_epb37/ng474/mesa_ml/my_library/grid_utils.py --min_mass=1.138 --max_mass=1.145 --grid_type linear --num_points=200 --task_id=$SLURM_ARRAY_TASK_ID
#python /gpfs/gibbs/pi/nagai/mesa_ml/my_library/grid_utils_cont_debug.py --grid_type linear --num_points=8 --max_workers=8 --resume --resume_edit_path ../../scripts/update_inlist.py
