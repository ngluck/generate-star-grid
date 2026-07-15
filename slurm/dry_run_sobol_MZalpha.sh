#!/bin/bash
# Dry-run preview for the production grid: a pure 3-D Sobol cloud jointly
# varying mass, metallicity (Z, log-sampled), and mixing-length alpha. Prints
# the plan only -- no MESA models are built or run.
#
#   bash slurm/dry_run_sobol_MZalpha.sh
#
# No SLURM / MESA needed -- fast pure-Python preview.

set -euo pipefail

# --- CONFIGURATION -------------------------------------------------------
MASS_RANGE=0.7:1.8       # initial_mass         MIN:MAX      (linear Sobol)
Z_RANGE=1e-4:0.04:log    # initial_z            MIN:MAX:log  (log-sampled Sobol)
ALPHA_RANGE=1:3          # mixing_length_alpha  MIN:MAX      (linear Sobol)
NUM_POINTS=8192          # total Sobol samples; MUST be a power of 2
SOBOL_SEED=0             # fixed => reproducible cloud across array tasks

# Intended grid run (parent) directory name for the real run, mirroring the
# most recent grid (mod4_M_0p7_1p2_500tracks_varM_varZ) with alpha added as
# varAlpha. For the actual run, copy the clean template into $RUN_DIR and run
# `python -m generate_star_grid.grid_utils ...` (minus --dry_run) from inside it;
# the 8192 model subdirs land flat under it (Z is Sobol-sampled, not batched).
RUN_DIR=mod4_M_0p7_1p8_sobol_varM_varZ_varAlpha
# -------------------------------------------------------------------------

if command -v module >/dev/null 2>&1; then module purge || true; module load miniconda || true; fi
if command -v conda >/dev/null 2>&1; then conda activate py311 || true; PY=python; else
    PY="${PY:-/home/ng474/.conda/envs/py311/bin/python}"; fi

echo "Dry-run started: $(date)"
echo

"$PY" -m generate_star_grid.grid_utils \
    --mass "$MASS_RANGE" \
    --initial_Z "$Z_RANGE" \
    --alpha_MLT "$ALPHA_RANGE" \
    --grid_type sobol --num_points "$NUM_POINTS" --sobol_seed "$SOBOL_SEED" \
    --dry_run

echo
echo "Dry-run finished: $(date)"
