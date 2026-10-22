"""Timestamp fidelity of fast_merge_split (operator ruling 2026-10-22):
every emitted sample must lie within half a sample of its source trace's
stated time. A drifting recorder clock (DU.LKHRT style, ~10 ms/hour)
must yield multiple segments, never one welded trace with silently
shifted times."""
import numpy as np
from obspy import Trace, UTCDateTime
from sudspy import fast_merge_split


def _minute(start, value, rate=100.0, n=6000):
    tr = Trace(np.full(n, value, dtype=np.int32))
    tr.stats.sampling_rate = rate
    tr.stats.starttime = start
    tr.stats.network, tr.stats.station, tr.stats.channel = "XX", "TEST", "HHZ"
    return tr


def test_drifting_clock_splits_and_keeps_stated_times():
    t0 = UTCDateTime(2023, 7, 28)
    # 120 minute-files whose stated starts drift +0.3 ms per file:
    # per-step jitter is far under half a sample (5 ms at 100 Hz), but
    # accumulates to 36 ms (3.6 samples) by the end.
    traces = [_minute(t0 + i * 60 + i * 0.0003, i) for i in range(120)]
    merged = fast_merge_split(traces.__class__ and __import__("obspy").Stream(traces))
    # The old (stated-endtime) rule welded all of this into ONE trace.
    assert len(merged) > 1, "drift must split segments, not weld"
    # Fidelity: each source file's first sample must sit within half a
    # sample of its stated time in whichever emitted segment holds it.
    for i, src in enumerate(traces):
        s = float(src.stats.starttime.timestamp)
        ok = False
        for seg in merged:
            g0 = float(seg.stats.starttime.timestamp)
            dt = seg.stats.delta
            idx = round((s - g0) / dt)
            if 0 <= idx < seg.stats.npts:
                err = abs(s - (g0 + idx * dt))
                if err <= dt / 2 + 1e-9 and seg.data[idx] == i:
                    ok = True
                    break
        assert ok, f"file {i}: stated time not honoured within half a sample"


def test_clean_contiguous_unchanged():
    t0 = UTCDateTime(2023, 7, 28)
    traces = __import__("obspy").Stream([_minute(t0 + i * 60, i) for i in range(60)])
    merged = fast_merge_split(traces)
    assert len(merged) == 1
    assert merged[0].stats.npts == 60 * 6000
