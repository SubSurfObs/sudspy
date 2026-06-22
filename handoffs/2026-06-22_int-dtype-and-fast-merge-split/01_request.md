# Handoff to sudspy — Preserve int dtype on SUDS read + add `fast_merge_split`

**From:** eqserver_2_seiscomp (Dan)
**Date:** 2026-06-22
**Priority:** Medium — two concrete wins, neither blocking the current MVP test run, but both compound across the catalogue and unblock the LRWS 2020 timeout cases.

## TL;DR

Two changes in one handoff, both touching the SUDS read/merge path:

1. **Bug fix in `sudspy/obspy.py:129`** — the SUDS read path unconditionally coerces native int dtypes to float32. Digitizer counts are integers; coercing to float is wrong on principle and creates downstream cleanup work (`disk_to_sds/scripts/suds_convert.py:177-181` has to cast back to int32 before STEIM2 writing).

2. **New function: `fast_merge_split(stream) -> Stream`** — a numpy-direct replacement for `obspy.Stream.merge(method=1, fill_value=None) + .split()` that the downstream pipeline currently uses. Measured **492× speedup** on a representative day (HOLS 2018-06-15: 26.98 s → 0.05 s) with byte-equal output. Also unblocks two days at LRWS 2020 that hit the 600-s phase3 wall-clock timeout under ObsPy's O(N²) merge.

The two changes are related (both about the SUDS-side numerics path), so I'm bundling them.

## Why this is worth doing

We're running an MVP test suite over 220 representative station-days from VW (categorize_source representative-per-bucket × 41 stations). Today's full sweep:

| Outcome | Days | Note |
|---|---|---|
| `ok` | 185 | normal |
| `qc_flagged` | 11 | corrupt mseed record → bulk-read fallback, graceful |
| `timeout` | 2 | **LRWS 2020-03-19 (717 s) and 2020-03-21 (2041 s) — both exceeded 600s** |
| `no_files` | 2 | sparse days with nothing to convert |
| `parse_error` | 1 | (under investigation, separate) |
| `error` | 1 | STBK 2012-01-01 bogus 88-billion-sample header — edge case |

Profile on HOLS 2018-06-15 (1440 SUDS files, 4320 traces in, 3 traces out):

```
Step 1 convert_suds_files (read+remap)   10.26s
Step 2 stream.merge(method=1)            99.80s   <- bottleneck
Step 3 stream.split()                     0.05s
Step 4 stream.sort()                      0.00s
Step 5 write_sds()                        1.39s
```

Re-tested cleanly with no concurrent load:

```
Step 2 stream.merge(method=1)            22-23s
```

ObsPy's merge is O(N²): pairwise comparison of every trace against every other trace looking for connection points. For 4320 traces that's ~9M comparisons. The fix is O(N log N) sort + O(N) walk-and-stitch — implemented and benchmarked locally:

```
[1] obspy merge(method=1, fill_value=None) + split():
    26.98s -> 3 traces
[2] fast_merge prototype (numpy direct):
    0.05s -> 3 traces

Speedup: 492.1x
[3] correctness check (byte equality):
    OK    VW.HOLS.00.CHE  npts=21600000
    OK    VW.HOLS.00.CHN  npts=21600000
    OK    VW.HOLS.00.CHZ  npts=21600000
Overall: PASS
```

Prototype code is at `eqserver_2_seiscomp/handoffs/sudspy/2026-06-22_int-dtype-and-fast-merge-split/proposed_fast_merge_split.py` (mirror of this handoff dir, attached in this thread).

## Bug #1: SUDS read float32 coercion

### Location and current code

`sudspy/sudspy/obspy.py:129` (inside the SUDS → Stream conversion):

```python
# Current
data = np.frombuffer(payload[:npts * bps], dtype=np_dt).astype(np.float32, copy=False)
```

The native `np_dt` from `_datatype_to_numpy(datatype)` in `parsers.py` is:

| SUDS datatype | numpy dtype | Note |
|---|---|---|
| `i`         | `<i2` (int16)   | typical 16-bit ADC counts |
| `l` or `2`  | `<i4` (int32)   | 24/32-bit ADC counts |
| `f`         | `<f4` (float32) | rare — already float |

The cast to `float32` is unconditional, so all int data is upcast.

### Why it's wrong

- Digitizer counts are integers. Representing them as float is semantically incorrect.
- For int16 data this doubles memory use for no benefit.
- For int32 data the cast is lossless **only** within float32's 24-bit mantissa range (~±16.7 M). Counts beyond that lose precision.
- The merge step then operates on float arithmetic, which can introduce sub-1-count errors at overlapping-sample boundaries when ObsPy averages.
- `disk_to_sds/scripts/suds_convert.py` (`_to_int32`, lines 177-181) explicitly casts float32 back to int32 before STEIM2 encoding — round-trip is wasted work and only lossless for the practically-valid int24 range.

### Proposed fix

```python
# Proposed
data = np.frombuffer(payload[:npts * bps], dtype=np_dt).copy()
```

`.copy()` because `np.frombuffer` returns a read-only view; downstream ObsPy operations expect writable arrays.

### Scope of downstream impact

- **disk_to_sds**: `_to_int32` becomes a no-op for already-int data. For the rare native-`f` case, the cast still fires. **No code change required downstream.**
- **eqserver_2_seiscomp**: phase3 engines and write_sds chain unchanged.
- **Any other sudspy callers**: they get native int dtypes instead of float32. If a caller specifically relies on float (e.g. uses NaN to represent something), that breaks. I'd argue any such caller was relying on a side-effect, but worth a heads-up before adoption.

### Risk

Low. The `_to_int32` cast at write time was the historical mitigation. After this fix, the cast is just defensive (still correct for `f`-type SUDS files, no-op for the new int path).

### Test plan

1. Existing sudspy tests should pass unchanged (they don't assert dtype IIRC, but worth checking).
2. Add a test: read a known int16-typed SUDS file, assert returned `Trace.data.dtype == np.int16`.
3. Run `disk_to_sds`'s `convert_suds_files` smoke tests — byte-equal output expected.

## Enhancement #2: `fast_merge_split(stream) -> Stream`

### What it does

Numpy-direct replacement for the `Stream.merge(method=1, fill_value=None) + .split()` combo. Returns a `Stream` of contiguous non-overlapping `Trace` objects per stream id — same shape that `obspy.merge() + split()` produces today.

```python
def fast_merge_split(stream):
    """O(N log N) sort + O(N) walk-and-stitch replacement for
    Stream.merge(method=1, fill_value=None) + .split().

    Groups by trace id; within each group:
      - sort by starttime
      - walk forward keeping a running concat buffer
      - on contiguous traces: append
      - on overlap: trim leading samples from the later trace (prefer earlier)
      - on real gap: emit current buffer as a Trace, start new

    Preserves input dtype (no float coercion).
    """
```

Semantics vs `Stream.merge(method=1) + .split()`:

| Behaviour | ObsPy merge(method=1) | `fast_merge_split` |
|---|---|---|
| Result shape | masked stream → split() → multi-trace | Stream → multi-trace directly |
| dtype | preserves (mostly) | preserves explicitly |
| Overlap policy | average overlapping samples | trim from later trace (prefer earlier) |
| Gap behaviour | masked array | new trace |
| Time complexity | O(N²) | O(N log N) |
| Memory | masked-array overhead | numpy concat only |

**Overlap policy difference is the only semantic divergence**. For typical seismic data at 250 Hz, overlap occurs only at clock-drift boundaries (sub-sample). For 4320-trace HOLS day the byte-equality test PASSED — meaning the two approaches produce identical output on real data, because there's no measurable overlap to average over. The divergence would matter for *intentional* multi-source merging (e.g. disk + tele both covering the same minutes with different drift) — but that case is currently handled upstream by `cross_source.select_files_for_day` before merge ever sees both copies.

### Where it should live

`sudspy/sudspy/utils.py` alongside `fast_merge_safe`, exported in `__init__.py`. Leave `fast_merge_safe` untouched for backwards compat with existing callers.

### Signature options

Option A (minimal):
```python
def fast_merge_split(stream): ...
```

Option B (configurable overlap, matching `fast_merge_safe`):
```python
def fast_merge_split(stream, overlap="trim"):  # "trim" | "error" | "ignore"
```

I'd suggest Option B for symmetry with `fast_merge_safe` and so future callers needing strict overlap detection can opt in.

### Prototype

Standalone working implementation attached as `proposed_fast_merge_split.py` in this handoff dir. Tested on HOLS 2018-06-15 — byte-equal, 492× faster.

### Test plan / acceptance criteria

1. **Byte-equality corpus** — for each of the following days, verify
   `obspy.merge(method=1, fill_value=None) + split()` and
   `fast_merge_split()` produce traces with:
   - same set of `(net, sta, loc, chan)` ids
   - same npts per trace
   - same starttime per trace (within ±1 sample)
   - same data array (np.array_equal)

   Corpus:
   - HOLS 2018-06-15 (clean EchoPro disk, 1440 files × 3 channels)
   - STBK 2022-10-23 (3-way duplication: known b2_canonical test case)
   - BRTH 2019-10-25 (clean Gecko, mseed input, int32 native)
   - DDBE / DDWB 2019 (Minimus per-channel files — many traces of one channel)
   - LRWS 2020-03-19 + 2020-03-21 (the timeout cases — these are the regression tests)

2. **Speedup floor** — `fast_merge_split` must be >10× faster than the ObsPy combo on HOLS 2018-06-15. (Measured locally: 492×.)

3. **Doesn't introduce regression in `fast_merge_safe`** — existing tests for `fast_merge_safe` continue to pass.

## Implementation note

After both changes land in sudspy:
- `disk_to_sds/scripts/suds_convert.py` swaps `stream.merge(method=1, fill_value=None); stream = stream.split()` for `stream = sudspy.fast_merge_split(stream)` in `write_sds` and in `convert_*_day` engines.
- `_to_int32` becomes a no-op for the SUDS path (was already a cast-if-needed; just stops firing).
- A re-run of the 220-day MVP test confirms byte-equality at LT level (apply.py dry-run output for each day matches what we get today, modulo the LRWS timeouts which now produce data).

We'll coordinate the disk_to_sds + eqserver_2_seiscomp pin bump after sudspy tags a release.

## What I'm NOT asking for in this handoff

- Removing or changing `fast_merge_safe` itself (the known `replace` mode bug is separate).
- Configurable gap policy in `fast_merge_split` — for our pipeline we always want "split on gap". Can add later if other callers need different behaviour.
- Any change to the `f`-type SUDS path — that legitimately is float and should stay float.

## References

- Profile log: `eqserver_2_seiscomp/test_env_classb/logs/profile_echopro.py` output
- Prototype + benchmark: `eqserver_2_seiscomp/test_env_classb/logs/fast_merge_prototype.py`
- MVP sweep log: `eqserver_2_seiscomp/test_env_classb/logs/convert_all_20260622T162331Z.log`
- Current downstream cast: `disk_to_sds/scripts/suds_convert.py:177-181` (`_to_int32`)
- The LRWS 2020 timeout days: 2020-03-19 (n_files=1440, elapsed=717s, status=timeout, 0 traces written), 2020-03-21 (n_files=1440, elapsed=2041s, status=timeout, 0 traces written)
