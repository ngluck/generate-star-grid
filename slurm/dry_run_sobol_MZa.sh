#!/bin/bash
# Dry-run preview for a joint Sobol grid varying mass, metallicity, and
# mixing-length alpha. Prints the plan (swept params, model count, disk
# estimate, example filenames) WITHOUT building or running any MESA models.
#
# Run in the background, logging to dry_run_sobol_MZa.log:
#   nohup bash slurm/dry_run_sobol_MZa.sh > dry_run_sobol_MZa.log 2>&1 &
#
# No SLURM / MESA needed -- this is a fast pure-Python preview.

set -euo pipefail

# --- CONFIGURATION: edit ranges/resolution for the grid you plan to run ---
MASS_RANGE=0.7:1.2       # initial_mass  MIN:MAX  (continuous -> Sobol)
Z_RANGE=0.001:0.04       # initial_z     MIN:MAX  (continuous -> Sobol)
ALPHA_RANGE=1.5:2.5      # mixing_length_alpha MIN:MAX (continuous -> Sobol)
NUM_POINTS=256           # total Sobol samples; MUST be a power of 2
SOBOL_SEED=0             # fixed seed => reproducible cloud across array tasks
# -------------------------------------------------------------------------

# Prefer the documented cluster pattern; fall back to the known env python.
if command -v module >/dev/null 2>&1; then
    module purge || true
    module load miniconda || true
fi
if command -v conda >/dev/null 2>&1; then
    conda activate py311 || true
    PY=python
else
    PY="${PY:-/home/ng474/.conda/envs/py311/bin/python}"
fi

echo "Dry-run started: $(date)"
echo "Interpreter: $($PY -c 'import sys; print(sys.executable)')"
echo

"$PY" -m generate_star_grid.grid_utils \
    --mass "$MASS_RANGE" \
    --initial_Z "$Z_RANGE" \
    --alpha_MLT "$ALPHA_RANGE" \
    --grid_type sobol \
    --num_points "$NUM_POINTS" \
    --sobol_seed "$SOBOL_SEED" \
    --dry_run

echo
echo "Dry-run finished: $(date)"
