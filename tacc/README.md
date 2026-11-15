# TACC SLURM templates (neural work goes here, not on the laptop)

Nothing in this repository trains a neural network locally. These templates are for the next steps
that do, on TACC (Lonestar6 / Frontera GPU queues). Edit the allocation (`-A`), queue and paths.

- `regrid_aircast.slurm` — sample DSOC Aircast SR 1 km / 1 h fields (GHI, T2m, 80/100 m wind) at
  every plant location into the per-point JSON layout `05_physics.py` reads
  (`data/raw/weather/<point>__aircast__<start>.json`), then run the physics and scoring.
- `train_correction_nn.slurm` — a probabilistic fleet-level correction (small MLP / temporal CNN
  with quantile loss) on physics output + forecast weather, using the same locked split in
  `config/split.json`. Train on `train`, select on `valid`, score once on `test`.

Status: the SLURM files are templates. `tacc/sample_aircast.py` and `tacc/train_correction_nn.py` are
not written yet: the first depends on the Aircast SR output format (variables, grid, issue/lead
metadata), the second on whether DSOC wants per-area or per-plant probabilistic output. The
interfaces they must meet are fixed: the per-point JSON layout above and the locked split.
