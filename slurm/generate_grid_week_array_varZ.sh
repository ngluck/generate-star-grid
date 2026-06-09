#!/bin/bash

#SBATCH --job-name=M1.0_varZ
#SBATCH --array=0-9
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
##SBATCH --gpu=1
#SBATCH --partition=week
#SBATCH --nodes=1
#SBATCH --time=6-00:00:00
#SBATCH --mem=8G
##SBATCH --constraint "v100"
#SBATCH --mail-type=ALL

Z_LIST=(0.0010 0.00145923 0.00212936 0.00310723 0.00453418 0.00661642 0.00965489 0.01408874 0.02055875 0.03)
SELECTED_Z=${Z_LIST[$SLURM_ARRAY_TASK_ID]}
SINGLE_MASS=1.0
echo "Running mass: ${SINGLE_MASS} and Z: ${SELECTED_Z}"
# Nagivating to current working directory where files will be saved:
cd mod4_M_1p0_varZ

module purge
module load miniconda
conda activate py311

#export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
#export MKL_NUM_THREADS=$SLURM_CPUS_PER_TASK

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1


python /gpfs/gibbs/pi/nagai/mesa_ml/my_library/grid_utils.py --min_mass=${SINGLE_MASS} --max_mass=${SINGLE_MASS} --initial_Z=${SELECTED_Z} --grid_type linear --num_points=1 --task_id=0
#python /gpfs/gibbs/pi/nagai/mesa_ml/my_library/grid_utils_cont_debug.py --grid_type linear --num_points=8 --max_workers=8 --resume --resume_edit_path ../../scripts/update_inlist.py
