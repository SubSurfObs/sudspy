# sudspy — Design Notes

---

# Part 1: Updates to sudspy (library)

## 1.1 Changes to existing code

### `iter_suds_blocks` (blocks.py) — DONE
Added two keyword-only parameters:
- `skip_data=True` — reads and discards data payloads via `f.read()` rather than storing them; avoids numpy allocation during metadata-only scans. Works with gzip (no seek needed).
- `strict=False` — stops iteration cleanly on bad sync bytes or truncated reads instead of raising. Suitable for partial/edge-case files.

Also added transparent gzip support: detects `.gz` extension and uses `gzip.open`; all existing callers unchanged.

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

`tests/` now has 34 passing tests:
- `tests/conftest.py` — shared fixtures and data paths
- `tests/test_blocks.py` — `iter_suds_blocks`: skip_data, gzip, strict mode, truncation
- `tests/test_io.py` — `parse_echopro_filename` (all filename variants), `scan_suds_file` (channel count, timing, gzip round-trip, gap detection)

Run with: `conda run -n obs-nb-fdsn-access python -m pytest tests/ -v`

Note: base conda env has broken numpy (missing `libgfortran.5.dylib`); use `obs-nb-fdsn-access` env.

## 1.5 Remaining known issues

- `suds_to_inventory_single_station()` in `obspy.py` appears superseded by `read_suds_inv()` — likely dead code
- Informal smoke-test helpers (`test_read_suds_inv`, `test_collect_comments`) are mixed into library code — should move to tests
- `collect_stations` not exported from `__init__.py`
- Duplicate `from __future__ import annotations` at top of `obspy.py`
- `linear_to_db_power()` uses voltage formula (20×log10) but is named `_power` — naming inconsistency
- `fast_merge_safe` `replace` mode bug (see 1.1)

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

### Data quality issues
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

## 2.5 Parallelism

Processing is embarrassingly parallel at the station-day level. Each worker:
1. Takes a `(station, year, month, day)` job
2. Runs the three-stage pipeline independently
3. Writes one MiniSEED file per channel

Use `multiprocessing.Pool` over the job list. Profile before adding Rust — parallelism across station-days may be sufficient given the I/O-bound nature of the work.

## 2.6 Configuration

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

## 2.7 Open questions

- Does the seconds offset (`SS`) stay consistent across channels within the same recording session?
- Are there extensions other than `.dmx` in the archive?
- Gap policy confirmation: split traces (no fill) acceptable for all downstream uses?
- Should triggered data be archived separately rather than discarded?
