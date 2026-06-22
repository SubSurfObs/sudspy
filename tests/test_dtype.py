"""Tests for the SUDS read path's dtype preservation.

Digitizer counts are integers. The SUDS read path returns Trace.data with the
native int dtype (int16 / int32) from the payload, never silently upcasting to
float32. Float coercion loses precision beyond ~16.7M counts and made the merge
step introduce sub-count drift at overlap boundaries.
"""
from pathlib import Path
import numpy as np
import pytest
import sudspy

OUTU_CLEAN = Path(__file__).parent / "outu" / "clean"


def test_outu_returns_int16():
    """OUTU EchoPro fixtures are 16-bit ADC counts."""
    p = OUTU_CLEAN / "min_0000.dmx"
    st = sudspy.read_suds_stream(str(p))
    assert len(st) == 3
    for tr in st:
        assert tr.data.dtype == np.int16, f"{tr.id}: expected int16, got {tr.data.dtype}"


def test_locu_returns_int32(single_chan_file):
    """LOCU Seismosphere fixture is 32-bit (datatype 'l' / '2')."""
    st = sudspy.read_suds_stream(str(single_chan_file))
    assert len(st) >= 1
    for tr in st:
        assert tr.data.dtype == np.int32, f"{tr.id}: expected int32, got {tr.data.dtype}"


def test_data_is_writable(single_chan_file):
    """np.frombuffer returns a read-only view; .copy() in the read path must
    yield a writable array (ObsPy operations expect that)."""
    st = sudspy.read_suds_stream(str(single_chan_file))
    for tr in st:
        assert tr.data.flags.writeable, f"{tr.id}: Trace.data is read-only"


def test_no_float_coercion(single_chan_file):
    """Defensive: the data type is *never* silently float32 for int payloads."""
    st = sudspy.read_suds_stream(str(single_chan_file))
    for tr in st:
        assert not np.issubdtype(tr.data.dtype, np.floating), (
            f"{tr.id}: unexpected float coercion ({tr.data.dtype})"
        )
