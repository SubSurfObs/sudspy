# OUTU EchoPro PC-SUDS test data (for sudspy)

Real EchoPro data from USB **OUTU_026**, station **VW.OUTU**, day **2024-12-10**, 250 sps.
Each `.dmx` is one minute and holds 3 components (`c01`/`c02`/`c03`).

## Key finding (2026-05-25)
The files that errored during the bulk PoC were **transient USB read failures**,
not corrupt data — copied to local disk they all read fine and are byte-identical.
So:
- the adapter should **copy USB→local first, then parse** (robust + faster — avoids the flaky per-file USB reads and gets sequential-read speed);
- **resync** (skip a bad region, scan to next sync) is only needed defensively for
  genuinely truncated files (power-cut mid-write), which we don't have here — so the
  resync tests use **synthetic** corruption and `xfail` until that lands in sudspy.

## Channel map (Kelunji EchoPro manual)
`c01` → CHN (longitudinal), `c02` → CHE (transverse), `c03` → CHZ (vertical),
`c04` → microphone amplitude (exclude). Network `VW` (from station registry), loc `00`.

## Contents
- `clean/` — 5 real clean minute-files (committable). Each → 3 traces.
- `day_2024-12-10/` — the **whole day** (~1440 files, ~130 MB) for speed tests.
  **gitignored** (too big); recreate with the `cp` below.
- `test_outu.py` — pytest: clean correctness + concat + (xfail) synthetic resync.
- `bench_outu.py` — per-file vs concatenated read timing over the whole day.

## Run (env with obspy + sudspy importable)
```
pytest test_outu.py -v
python bench_outu.py
```

## Recreate the whole-day fixture (if deleted)
```
cp -R /Volumes/OUTU_026/LocalArchive/cont0/2024/12/10 day_2024-12-10
```
