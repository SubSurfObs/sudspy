"""Proposed implementation of fast_merge_split for sudspy/sudspy/utils.py.

Tested standalone on eqserver_2_seiscomp's test_env:
    HOLS 2018-06-15 (1440 SUDS files, 4320 traces)
    obspy.merge(method=1, fill_value=None) + .split() : 26.98s -> 3 traces
    fast_merge_split                                  : 0.05s -> 3 traces
    Byte-equal: PASS  (all 3 channels, 21,600,000 samples each)
    Speedup: 492.1x

The implementation is naive (no error handling beyond what the
algorithm needs); please harden as appropriate to sudspy's style.

Notes for adoption:
  - Drop into sudspy/sudspy/utils.py alongside fast_merge_safe.
  - Export from sudspy/sudspy/__init__.py.
  - The "trim" overlap policy (prefer earlier trace) matches what
    obspy.merge produces on real seismic data with sub-sample drift.
    No measurable byte diff on the HOLS test.

Author: eqserver_2_seiscomp / Dan, 2026-06-22.
"""
from __future__ import annotations
import numpy as np
from obspy import Stream, Trace


def fast_merge_split(stream, overlap: str = "trim") -> Stream:
    """Numpy-direct equivalent of:
        stream.merge(method=1, fill_value=None)
        stream = stream.split()

    Per stream id:
      1. Sort traces by starttime  -> O(N log N)
      2. Walk forward keeping a running buffer of contiguous samples
         - gap     -> flush buffer as a Trace, start a new one
         - overlap -> drop overlapping samples from the LATER trace
         - contiguous -> append (within half-sample tolerance)
      3. Flush final buffer

    Parameters
    ----------
    stream : obspy.Stream
        Input traces. May contain traces from multiple ids; each id is
        merged independently.
    overlap : {"trim", "error", "ignore"}
        Policy for overlapping samples:
        - "trim"    : drop leading samples from the later trace (default;
                      matches obspy.merge(method=1) on typical seismic data
                      where overlap is sub-sample drift)
        - "error"   : raise ValueError on detected overlap
        - "ignore"  : skip the later overlapping trace entirely

    Returns
    -------
    obspy.Stream
        Multi-trace Stream, with no internal gaps within any trace and
        no overlapping samples across traces of the same id. Equivalent
        to obspy's merge+split combo, but:
          - O(N log N) instead of O(N²)
          - Preserves input dtype (no float32 coercion)
          - Configurable overlap policy
    """
    out = Stream()

    # Group by trace id
    by_id: dict = {}
    for tr in stream:
        by_id.setdefault(tr.id, []).append(tr)

    for trace_id, traces in by_id.items():
        traces.sort(key=lambda t: t.stats.starttime)

        rate = traces[0].stats.sampling_rate
        delta = 1.0 / rate
        gap_tol = delta * 0.5

        current_start = None
        current_samples: list = []
        current_end = None       # timestamp of last sample in current run

        for tr in traces:
            if len(tr.data) == 0:
                continue

            # Defensive: rate change within an id is anomalous but allow
            # it by flushing and starting fresh.
            if tr.stats.sampling_rate != rate:
                if current_samples:
                    out += _emit_trace(traces[0], current_start, current_samples)
                    current_samples = []
                rate = tr.stats.sampling_rate
                delta = 1.0 / rate
                gap_tol = delta * 0.5
                current_start = tr.stats.starttime
                current_samples = [tr.data]
                current_end = tr.stats.endtime
                continue

            tr_start = tr.stats.starttime
            tr_end = tr.stats.endtime

            if current_start is None:
                current_start = tr_start
                current_samples = [tr.data]
                current_end = tr_end
                continue

            # Positive: gap; negative: overlap.
            gap_seconds = float(tr_start - current_end) - delta

            if gap_seconds > gap_tol:
                # Real gap → flush + start new run.
                out += _emit_trace(traces[0], current_start, current_samples)
                current_start = tr_start
                current_samples = [tr.data]
                current_end = tr_end
            elif gap_seconds < -gap_tol:
                # Overlap.
                if overlap == "error":
                    raise ValueError(
                        f"Overlap detected at {tr_start} for {trace_id} "
                        f"({-gap_seconds:.6f} s)"
                    )
                if overlap == "ignore":
                    continue
                # overlap == "trim": drop leading samples from new trace
                overlap_secs = -gap_seconds
                n_skip = int(round(overlap_secs / delta))
                if n_skip < len(tr.data):
                    current_samples.append(tr.data[n_skip:])
                    current_end = tr_start + (len(tr.data) - 1) * delta
                # else: new trace fully overlapped — drop entirely
            else:
                # Contiguous (within half-sample tolerance).
                current_samples.append(tr.data)
                current_end = tr_end

        # Flush final.
        if current_samples:
            out += _emit_trace(traces[0], current_start, current_samples)

    return out


def _emit_trace(template: Trace, start, samples_list: list) -> Trace:
    """Build a Trace by concatenating sample arrays.

    Uses ``template.stats`` for non-data metadata; ``starttime`` and
    ``npts`` are recomputed. Dtype of the output matches the dtype of
    the input arrays (np.concatenate preserves common dtype).
    """
    data = np.concatenate(samples_list) if len(samples_list) > 1 else samples_list[0]
    stats = template.stats.copy()
    stats.starttime = start
    stats.npts = len(data)
    return Trace(data=data, header=stats)
