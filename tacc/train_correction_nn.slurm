#!/bin/bash
#SBATCH -J renew-corr-nn
#SBATCH -p gpu-a100          # Lonestar6 A100 queue
#SBATCH -N 1 -n 1
#SBATCH -t 06:00:00
#SBATCH -A YOUR_ALLOCATION
#SBATCH -o logs/%x-%j.out
# Probabilistic neural correction of the physics forecast (quantile loss), per BA x tech x lead.
# Inputs: data/proc/phys_ba.parquet (or phys_ba_aircast.parquet), data/proc/eia930.parquet,
#         per-point weather in data/raw/weather; split from config/split.json (locked).
set -euo pipefail
module load cuda python3
source $WORK/envs/ky-renew-gpu/bin/activate   # torch + pandas + pyarrow
cd $WORK/kentucky-renewable-forecast
python tacc/train_correction_nn.py --phys data/proc/phys_ba.parquet --actual data/proc/eia930.parquet \
  --split config/split.json --quantiles 0.05 0.25 0.5 0.75 0.95 --out data/proc/nn_correction
