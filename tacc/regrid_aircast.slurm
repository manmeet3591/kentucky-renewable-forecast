#!/bin/bash
#SBATCH -J aircast-pv-wind
#SBATCH -p normal            # CPU queue is enough: this is sampling + pvlib
#SBATCH -N 1 -n 1 -c 32
#SBATCH -t 04:00:00
#SBATCH -A YOUR_ALLOCATION
#SBATCH -o logs/%x-%j.out
# Run the physics on DSOC Aircast SR fields instead of HRRR/GFS.
# AIRCAST_DIR: directory of Aircast SR NetCDF/Zarr output (1 km, hourly, issue time + lead in metadata).
set -euo pipefail
module load python3
source $WORK/envs/ky-renew/bin/activate
cd $WORK/kentucky-renewable-forecast
AIRCAST_DIR=${AIRCAST_DIR:?set AIRCAST_DIR}
# 1) sample Aircast at each weather point (or each Kentucky plant) for leads 0-3 days
#    -> data/raw/weather/<point>__aircast__<start>.json with keys
#       shortwave_radiation[_previous_dayN], temperature_2m[_previous_dayN], wind_speed_80m[_previous_dayN]
python tacc/sample_aircast.py --aircast "$AIRCAST_DIR" --points data/proc/points.csv --out data/raw/weather
# 2) physics on Aircast inputs -> data/proc/phys_ba_aircast.parquet, phys_plant_month_aircast.parquet
python pipeline/05_physics.py aircast
