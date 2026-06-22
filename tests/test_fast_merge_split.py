"""Tests for sudspy.fast_merge_split.

The function is a numpy-direct replacement for the
``Stream.merge(method=1, fill_value=None) + .split()`` combo used downstream.
The core requirement is byte-equality with that combo on real seismic data
(the only semantic divergence is the overlap policy, but for typical sub-sample
clock drift it produces identical output).
"""
from pathlib import Path

import numpy as np
import pytest
from obspy import Stream, Trace, UTCDateTime

import sudspy

OUTU_CLEAN = Path(__file__).parent / "outu" / "clean"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _read_many(paths) -> Stream:
    """Read each path with read_suds_stream and concatenate into one Stream."""
    st = Stream()
    for p in paths:
        st += sudspy.read_suds_stream(str(p))
    return st


def _obspy_merge_split(stream: Stream) -> Stream:
    """The reference combo we're replacing."""
    s = stream.copy()
    s.merge(method=1, fill_value=None)
    return s.split()


def _streams_equal(a: Stream, b: Stream) -> None:
    """Assert two Streams are byte-equal: same set of (id, starttime, npts, data)."""
    a_keys = sorted((tr.id, str(tr.stats.starttime), tr.stats.npts) for tr in a)
    b_keys = sorted((tr.id, str(tr.stats.starttime), tr.stats.npts) for tr in b)
    assert a_keys == b_keys, f"trace summaries differ:\n  a={a_keys}\n  b={b_keys}"

    by_key_a = {(tr.id, str(tr.stats.starttime)): tr for tr in a}
    by_key_b = {(tr.id, str(tr.stats.starttime)): tr for tr in b}
    for k, tr_a in by_key_a.items():
        tr_b = by_key_b[k]
        assert np.array_equal(tr_a.data, tr_b.data), (
            f"{k}: data differs (max diff={np.abs(tr_a.data.astype(float) - tr_b.data.astype(float)).max()})"
        )


# ----------------------------------------------------------------------------
# Byte-equality vs the obspy combo
# ----------------------------------------------------------------------------

class TestByteEqualityVsObspy:

    def test_outu_no_gap(self):
        """5 OUTU minute files × 3 channels = 15 traces -> 3 contiguous traces."""
        paths = sorted(OUTU_CLEAN.glob("*.dmx"))
        assert len(paths) == 5
        stream = _read_many(paths)
        assert len(stream) == 15

        ref = _obspy_merge_split(stream)
        fast = sudspy.fast_merge_split(stream)

        assert len(fast) == 3
        _streams_equal(fast, ref)

    def test_sequence_with_gap(self, sequence_dir):
        """10 sequence files × 3 channels with gap at minute 0005 -> 6 traces
        (2 contiguous runs per channel: pre-gap and post-gap)."""
        paths = sorted(sequence_dir.glob("*.dmx"))
        assert len(paths) == 10
        stream = _read_many(paths)
        assert len(stream) == 30

        ref = _obspy_merge_split(stream)
        fast = sudspy.fast_merge_split(stream)

        # 2 contiguous runs × 3 channels = 6 traces
        assert len(fast) == 6
        _streams_equal(fast, ref)


# ----------------------------------------------------------------------------
# Dtype preservation
# ----------------------------------------------------------------------------

class TestDtypePreservation:

    def test_int16_preserved(self):
        """OUTU is int16 in -> int16 out (no float upcast in fast_merge_split)."""
        paths = sorted(OUTU_CLEAN.glob("*.dmx"))
        stream = _read_many(paths)
        for tr in stream:
            assert tr.data.dtype == np.int16

        fast = sudspy.fast_merge_split(stream)
        for tr in fast:
            assert tr.data.dtype == np.int16, f"{tr.id}: got {tr.data.dtype}"


# ----------------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_stream(self):
        out = sudspy.fast_merge_split(Stream())
        assert len(out) == 0

    def test_single_trace_passthrough(self):
        path = OUTU_CLEAN / "min_0000.dmx"
        stream = sudspy.read_suds_stream(str(path))
        out = sudspy.fast_merge_split(stream)
        assert len(out) == 3
        for tr_in, tr_out in zip(sorted(stream, key=lambda t: t.id),
                                  sorted(out, key=lambda t: t.id)):
            assert tr_in.id == tr_out.id
            assert tr_in.stats.npts == tr_out.stats.npts
            assert np.array_equal(tr_in.data, tr_out.data)

    def test_zero_length_trace_skipped(self):
        """A zero-length trace in input must not break the walk."""
        path = OUTU_CLEAN / "min_0000.dmx"
        stream = sudspy.read_suds_stream(str(path))
        # inject an empty trace
        empty = Trace(
            data=np.array([], dtype=np.int16),
            header={
                "network": "VW", "station": "OUTU", "location": "",
                "channel": "c01", "starttime": UTCDateTime(0),
                "sampling_rate": 250.0,
            },
        )
        stream += empty
        out = sudspy.fast_merge_split(stream)
        assert len(out) == 3
        for tr in out:
            assert tr.stats.npts > 0


# ----------------------------------------------------------------------------
# Overlap policy
# ----------------------------------------------------------------------------

class TestOverlapPolicy:

    def _make_overlap_stream(self):
        """Two traces, same id, second overlaps the first by 10 samples."""
        rate = 100.0
        delta = 1.0 / rate
        data1 = np.arange(100, dtype=np.int16)
        data2 = np.arange(100, 200, dtype=np.int16)
        t0 = UTCDateTime(0)
        # second starts 10 samples before first ends → 10-sample overlap
        t1 = t0 + (100 - 10) * delta
        common_header = {
            "network": "VW", "station": "TEST", "location": "",
            "channel": "BHZ", "sampling_rate": rate,
        }
        tr1 = Trace(data=data1, header={**common_header, "starttime": t0})
        tr2 = Trace(data=data2, header={**common_header, "starttime": t1})
        return Stream(traces=[tr1, tr2])

    def test_trim_default(self):
        stream = self._make_overlap_stream()
        out = sudspy.fast_merge_split(stream)
        assert len(out) == 1
        # earlier trace is preferred; later trace contributes only its post-overlap tail
        assert out[0].stats.npts == 100 + (100 - 10)

    def test_error_raises(self):
        stream = self._make_overlap_stream()
        with pytest.raises(ValueError, match="Overlap detected"):
            sudspy.fast_merge_split(stream, overlap="error")

    def test_ignore_drops_later(self):
        stream = self._make_overlap_stream()
        out = sudspy.fast_merge_split(stream, overlap="ignore")
        assert len(out) == 1
        assert out[0].stats.npts == 100  # only the first trace's data

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="Unknown overlap mode"):
            sudspy.fast_merge_split(Stream(), overlap="bogus")
