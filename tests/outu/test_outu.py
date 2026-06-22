"""OUTU EchoPro PC-SUDS tests for sudspy.

Fixtures are REAL clean minute-files from EchoPro USB OUTU_026 (station VW.OUTU,
2024-12-10, 250 sps). Each .dmx holds 3 components: c01/c02/c03.

NOTE: the USB files that errored during the bulk PoC were *transient USB read
failures*, not corrupt data (copied to local disk they read fine, byte-identical).
So corruption here is SYNTHESIZED from a clean file purely to exercise the
resync code path; the resync tests xfail until that change lands in sudspy.

Run in an env where obspy + sudspy import, e.g.:
    pytest test_outu.py -v
"""
import pathlib
import pytest
import sudspy

HERE = pathlib.Path(__file__).parent
CLEAN = sorted((HERE / "clean").glob("*.dmx"))


def test_have_fixtures():
    assert CLEAN, "no clean/*.dmx fixtures found"


def test_clean_files_read_three_components():
    for f in CLEAN:
        st = sudspy.read_suds_stream(str(f))
        assert len(st) == 3, f"{f.name}: expected 3 traces (c01/c02/c03), got {len(st)}"
        for tr in st:
            assert tr.stats.sampling_rate == 250.0
            assert tr.stats.station == "OUTU"
            assert tr.stats.channel in {"c01", "c02", "c03"}


def test_concatenated_clean_reads_all(tmp_path):
    """A cat of N clean files reads all N*3 traces in one pass (concat already works)."""
    cat = tmp_path / "cat.dmx"
    cat.write_bytes(b"".join(f.read_bytes() for f in CLEAN[:3]))
    st = sudspy.read_suds_stream(str(cat))
    assert len(st) == 9


# ----- resync: SYNTHETIC corruption; defines the TARGET behaviour -----
# xfail until sudspy gains resync-on-bad-sync (then they xpass).

def _truncate_mid(b: bytes) -> bytes:
    return b[: int(len(b) * 0.5)]            # cut mid-structure -> short read / EOF


def _inject_garbage(b: bytes) -> bytes:
    mid = len(b) // 2
    return b[:mid] + (b"\xff" * 64) + b[mid:]  # bad-sync region in the middle


@pytest.mark.xfail(reason="needs sudspy resync-on-bad-sync", strict=False)
def test_resync_truncated_mid_stream(tmp_path):
    a, b = CLEAN[0].read_bytes(), CLEAN[1].read_bytes()
    cat = tmp_path / "c.dmx"
    cat.write_bytes(a + _truncate_mid(a) + b)   # clean -> truncated -> clean
    st = sudspy.read_suds_stream(str(cat))       # must NOT raise after resync
    assert len(st) >= 6                          # both full clean files recovered (incl. trailing)


@pytest.mark.xfail(reason="needs sudspy resync-on-bad-sync", strict=False)
def test_resync_badsync_mid_stream(tmp_path):
    a, b = CLEAN[0].read_bytes(), CLEAN[1].read_bytes()
    cat = tmp_path / "c.dmx"
    cat.write_bytes(a + _inject_garbage(a) + b)  # clean -> garbage -> clean
    st = sudspy.read_suds_stream(str(cat))
    assert len(st) >= 6
