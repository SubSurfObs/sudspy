# sudspy — Design Notes

---

# Part 1: Updates to sudspy (library)

## 1.1 Changes to existing code

### `iter_suds_blocks` (blocks.py) — DONE
Added two keyword-only parameters:
- `skip_data=True` — reads and discards data payloads via `f.read()` rather than storing them; avoids numpy allocation during metadata-only scans. Works with gzip (no seek needed).
- `strict=False` — stops iteration cleanly on bad sync bytes or truncated reads instead of raising. Suitable for partial/edge-case files.

Also added transparent gzip support: detects `.gz` extension and uses `gzip.open`; all existing callers unchanged.

### Resync — INVESTIGATED, NOT ADOPTED (2026-05-25)
A `resync`-on-bad-sync path (skip a corrupt region, scan forward to the next valid `b"S6"` marker) was prototyped and then **reverted**. Reasoning, in order of discovery:
1. The bulk-PoC "corrupt files" turned out to be **transient USB read failures**, not bad bytes — copied to local disk they read fine and are byte-identical. So there is no corrupt on-disk data to recover from.
2. The genuine fix is at the **adapter** level: copy USB→local first, then parse (robust + sequential-read speed). Not a sudspy change.
3. Verified empirically: the native parser **does** abort on *synthetic* mid-stream corruption (the OUTU bundle's two resync tests `XFAIL` against unmodified sudspy, and only `XPASS` when the resync code is loaded). So resync is not a no-op — but it only ever helps hypothetical truncation (e.g. power-cut mid-write) we have no example of. Per YAGNI it stays out until such a file actually turns up. The bundle's resync tests are `xfail(strict=False)`, so they stay green either way and mark the target behaviour if it's ever needed.

### `fast_merge_safe` (utils.py) — KNOWN ISSUE, NOT YET FIXED
The `replace` overlap mode has a logical issue: it attempts to trim from all previous data segments rather than just the trailing edge of the last one. Review before use in production pipeline.

## 1.2 New functions — DONE

### `scan_suds_file(path) -> list[dict]`  (`io.py`)
Fast metadata-only scan using `skip_data=True` and `strict=False`. Returns a list of dicts — one per waveform channel in the file:
```python
{
    "channel":     "NET.STA.CHA",
    "start_time":  UTCDateTime,
    "end_time":    UTCDateTime,   # start + (npts-1) / sample_rate
    "npts":        int,
    "sample_rate": float,
}
```
An EchoPro disk file typically returns 3 entries (3-channel); a telemetry file may return 1.

### `parse_echopro_filename(fname) -> dict | None`  (`io.py`)
Parses EchoPro `.dmx` / `.dmx.gz` filenames. Returns:
```python
{
    "date":        "2023-11-24",
    "hhmm":        "2057",
    "ss":          "02",
    "station":     "ABM5Y",
    "source_type": "disk" | "telemetry",
    "is_gzip":     bool,
    "is_triggered": bool,   # True only if ".trig." in filename; SS-based detection is pipeline-level
}
```
Returns `None` for non-EchoPro files (Gecko, mseed.zip, etc.).

Both functions are exported from `sudspy.__init__`.

## 1.3 `io.py` — PARTIALLY DONE

Implemented: `parse_echopro_filename`, `scan_suds_file`.

Still to add (pipeline-level logic, needed for `eqserver_2_seiscomp`):
- `walk_archive(root)` — traverse `station/year/month/day/` tree, group files by `(station, year, month, day)`
- `check_fast_path(file_list)` — True if 1440 disk files with single dominant `SS`
- `group_by_session(file_list)` — group by `SS`, sort by HHMM, identify gaps

## 1.4 Test suite — DONE

`tests/` has 34 passing core tests:
- `tests/conftest.py` — shared fixtures and data paths
- `tests/test_blocks.py` — `iter_suds_blocks`: skip_data, gzip, strict mode, truncation
- `tests/test_io.py` — `parse_echopro_filename` (all filename variants), `scan_suds_file` (channel count, timing, gzip round-trip, gap detection)

Plus the **OUTU EchoPro bundle** under `tests/outu/` (real `VW.OUTU` 2024-12-10, 250 sps):
- `tests/outu/clean/` — 5 real clean minute-files (committed, ~0.46 MB; each → 3 traces c01/c02/c03)
- `tests/outu/test_outu.py` — clean-read correctness + concat-reads-all (3 pass) + 2 synthetic-resync tests (`xfail(strict=False)` → currently `XFAIL`, since resync isn't adopted)
- `tests/outu/bench_outu.py` — per-file vs concatenated read timing over a whole local day (forces sample decode for honest numbers)
- `tests/outu/README.md` — channel map, the USB-flakiness finding, copy-local-first recommendation
- `tests/outu/day_*/` — whole-day speed fixture (~130 MB), **gitignored**; recreate per `tests/outu/README.md`

Run with: `conda run -n obs-nb-fdsn-access python -m pytest tests/ -v`

Note: base conda env has broken numpy (missing `libgfortran.5.dylib`); use `obs-nb-fdsn-access` env.

## 1.5 Remaining known issues

- `suds_to_inventory_single_station()` in `obspy.py` appears superseded by `read_suds_inv()` — likely dead code
- Informal smoke-test helpers (`test_read_suds_inv`, `test_collect_comments`) are mixed into library code — should move to tests
- `collect_stations` not exported from `__init__.py`
- Duplicate `from __future__ import annotations` at top of `obspy.py`
- `linear_to_db_power()` uses voltage formula (20×log10) but is named `_power` — naming inconsistency
- `fast_merge_safe` `replace` mode bug (see 1.1)

### From EQServer_2_Seiscomp downstream (2026-06-12) — open follow-ups
- **Expose raw STATIDENT fields.** Add `network_raw` / `station_raw` / `component_raw` to `parse_statident` alongside the existing stripped keys (backward-compatible audit trail). Driven by the 'AB' vs 'ABC' question — the 4-byte field is operator-typed scratch within a fixed-width slot, and downstream needs both the raw bytes for auditing and the cleaned form for matching.
- **Document the operator-scratch caveat.** Add an explicit note in `parsers.py::parse_statident` (and Part 2.1 here) that `STATIDENT.network` is operator-typed scratch within a 4-byte field, not a reliable network code — trust the station registry. Empirical evidence: this repo's fixtures already span `VW` / `AB` / `UM` / `S1` / `ABC` in `STATIDENT[0:4]`, all spec-valid.
- **Byte-diff the 2016-10-15 → 2017-02-19 OUTU anomaly window.** Hypothesis (well-supported but unconfirmed): a non-Kelunji loaner recorder was swapped in for those ~4 months. Signals: `recorder='_'` (0x5F) where every Kelunji file in the corpus has `'K'` at struct_body offset 39, and `atod_gain=-32767` (`0x01 0x80` LE — one bit off the conventional INT16_MIN sentinel `0x00 0x80`) where Kelunji files have small positive ints. Confirm by getting one anomaly file + one pre-anomaly OUTU file from EQServer_2_Seiscomp and byte-diffing against `tests/outu/clean/min_0000.dmx` (struct_length, byte 39, bytes 64-65 of first STATIONCOMP struct_body). -32767 is not a sudspy sentinel; `constants.py` has no such value. If the hypothesis holds, document it in Part 2.1 as a known anomaly window with explanation rather than treating those files as corrupt.

---

# Part 2: Archive conversion strategy

## 2.1 Archive characteristics

### Directory structure
```
station/year/month/day/*.dmx[.gz]
```
- Station identity is known from directory path
- Files are ~1 minute long, **not clock-aligned** — start time is `HHMM:SS` where `SS` is a recorder-session constant
- Files are usually gzipped (`.dmx.gz`); occasionally uncompressed (`.dmx`)
- Compression is consistent across long time periods

### Filename format
```
2023-11-24_0001_02_ABM5Y.dmx.gz
             ^^^^  ^^  ^^^^^
             HHMM  SS  station
```
- `HHMM`: hour+minute of file start
- `SS`: seconds offset — constant within a recording session, changes on recorder restart
- `station`: station code — allows wrong-station detection at filename level

### Two duplicate sources
- **Disk files** (underscore in filename): more complete, preferred
- **Telemetry files** (space in filename): may be incomplete, fallback only

### Channel naming (Kelunji EchoPro)
Components are stored as `c01`/`c02`/`c03`/`c04`. Per the EchoPro manual:
`c01` → CHN (longitudinal), `c02` → CHE (transverse), `c03` → CHZ (vertical),
`c04` → microphone amplitude (**exclude**). Network `VW` (station registry),
location `00`. (Confirmed on `VW.OUTU`; see `tests/outu/README.md`.)

### Data quality issues
- Transient USB read failures — the dominant real-world failure (NOT corrupt data); fix by copying USB→local first (see 2.5)
- Missing files (power loss, telemetry dropout) — partial days common
- Wrong-station files — a file from a different station appears in the wrong directory; detectable from filename station code and header station identity
- Triggered/accelerometer files (e.g. `BN*` channels) — duplicate time windows, not required for continuous archive; discard

## 2.2 Processing strategy

### Deduplication priority
For any time window, prefer in this order:
1. Disk + continuous channel
2. Telemetry + continuous channel
3. Triggered/accelerometer → discard

### Three-stage processing per station-day

**Stage 1 — Filename scan (no decompression)**
- Parse filenames: extract HHMM, SS, station
- Reject wrong-station files (filename station ≠ directory station)
- Reject excluded channels if deducible from filename
- Classify source type (disk vs telemetry)
- Group by `SS` (session groups) → identify session boundaries
- Within each session: sort by HHMM, find missing slots → gap map

**Fast path**: if exactly 1440 disk files exist all sharing the same `SS` → complete single-session day → skip Stage 2, go directly to Stage 3.

**Stage 2 — Metadata scan (decompress headers only, skip data payloads)**
- Call `scan_suds_file()` on files surviving Stage 1
- Validate header station against directory station (catches wrong-station files that passed filename check)
- Extract precise start/end times
- At session boundaries (SS changes): use header timestamps to determine exact gap size
- Resolve deduplication: for overlapping time windows, apply priority rules

**Stage 3 — Full parse + merge + write**
- Call `read_suds_stream()` on selected files only
- Sort traces by start time
- Group into contiguous segments (gap tolerance = 0.5 × sample period)
- Each contiguous segment → one `Trace`
- Write all traces to day MiniSEED: `Stream.write(path, format="MSEED", reclen=4096)`
- No gap filling — gaps are preserved as separate traces within the day file (standard SDS behaviour)

## 2.3 Contiguous group detection

Determined from header timestamps after Stage 2 deduplication:

```python
segments sorted by start_time
new group if: segment[i].start_time > segment[i-1].end_time + 0.5 * delta
```

At filename level (within a single session group), contiguity can be inferred from consecutive HHMM values with the same SS — useful for the fast path and gap map, but header timestamps are authoritative for the merge.

## 2.4 In-memory merge — rationale

Files are converted to ObsPy `Trace` objects in memory and merged before writing. No intermediate MiniSEED files per minute.

- Memory cost is trivial: 24h × 100sps × 4 bytes × 3 channels ≈ 100 MB per station
- Avoids tens of millions of small intermediate file writes across a decadal archive
- `Stream.write()` handles multi-trace (gappy) day files in a single call
- Restartable at day granularity

## 2.5 Batch reads — the real wins (2026-05-25)

Root cause of the bulk-PoC failures: **transient USB read failures**, not corrupt
SUDS. The files that errored read fine once copied to local disk (byte-identical).
So the high-value adapter changes are, in priority order:

1. **Copy USB→local first, then parse.** A robust bulk `cp`/`rsync` off the flaky
   USB eliminates the per-file transient read failures *and* gives sequential-read
   speed. This is the single highest-value change — and it directly serves the
   larger EqServer historical conversion too. (Adapter-level, not sudspy.)
2. **Concatenated read — already works, no sudspy change.** A `cat` of clean
   1-minute files parses to true EOF in one pass (one `Stream` build), amortising
   per-file Python/ObsPy overhead. Feed it as a single path. (`.gz` must be
   decompressed before concatenating — raw gzip can't be `cat`-ed.)
3. **STEIM2 on write** — `Stream.write(path, format="MSEED", encoding="STEIM2", reclen=4096)`.
4. **resync — investigated, not adopted** (see Part 1.1): no corrupt on-disk data
   exists to recover from; revisit only if a genuinely truncated file appears.

**Benchmark caveat (important):** measure with a **full decode +
`st.write(path, format="MSEED")`**, *not* `len(stream)` or a header-only scan.
Header reads are ~free; the real work (and the PoC's 286 s) is sample decode and
write. A header-only micro-benchmark overstates the win by orders of magnitude.
`tests/outu/bench_outu.py` does this correctly (forces `tr.data` decode). Note a
*local* bench mostly measures Python/ObsPy + local-FS overhead; the USB
random-access penalty is only visible reading from the USB itself.

## 2.6 Parallelism

Processing is embarrassingly parallel at the station-day level. Each worker:
1. Takes a `(station, year, month, day)` job
2. Runs the three-stage pipeline independently
3. Writes one MiniSEED file per channel

Use `multiprocessing.Pool` over the job list. Profile before adding Rust — parallelism across station-days may be sufficient given the I/O-bound nature of the work.

## 2.7 Configuration

```yaml
network_map:
  OLD_NET: NEW_NET

channel_exclude:          # channels to discard (triggered/accelerometer)
  - "BN*"

prefer_disk: true         # disk over telemetry when overlapping

location_map:             # NET.STA.CHA -> location code
  VW.ABM5Y.CHZ: "00"

gap_tolerance_factor: 0.5  # fraction of sample period
```

## 2.8 Open questions

- Does the seconds offset (`SS`) stay consistent across channels within the same recording session?
- Are there extensions other than `.dmx` in the archive?
- Gap policy confirmation: split traces (no fill) acceptable for all downstream uses?
- Should triggered data be archived separately rather than discarded?
