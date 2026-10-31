"""Tests for iter_suds_blocks: gzip support, skip_data, strict mode."""

import gzip
import pytest
from sudspy.blocks import iter_suds_blocks, SudsBlock


def test_basic_read(single_chan_file):
    blocks = list(iter_suds_blocks(str(single_chan_file)))
    assert len(blocks) > 0
    for b in blocks:
        assert isinstance(b, SudsBlock)
        assert b.struct_type >= 0


def test_block_types_present(single_chan_file):
    types = {b.struct_type for b in iter_suds_blocks(str(single_chan_file))}
    assert 5 in types   # STATIONCOMP
    assert 7 in types   # DESCRIPTRACE


def test_skip_data_same_block_count(single_chan_file):
    full  = list(iter_suds_blocks(str(single_chan_file)))
    skipped = list(iter_suds_blocks(str(single_chan_file), skip_data=True))
    assert len(full) == len(skipped)


def test_skip_data_struct_types_match(single_chan_file):
    full    = list(iter_suds_blocks(str(single_chan_file)))
    skipped = list(iter_suds_blocks(str(single_chan_file), skip_data=True))
    assert [b.struct_type for b in full] == [b.struct_type for b in skipped]


def test_skip_data_empties_payload(single_chan_file):
    """Blocks with data_length > 0 should have empty data when skip_data=True."""
    for b in iter_suds_blocks(str(single_chan_file), skip_data=True):
        assert b.data == b""


def test_skip_data_struct_body_intact(single_chan_file):
    """struct_body (headers) must be identical whether or not data is skipped."""
    full    = list(iter_suds_blocks(str(single_chan_file)))
    skipped = list(iter_suds_blocks(str(single_chan_file), skip_data=True))
    for bf, bs in zip(full, skipped):
        assert bf.struct_body == bs.struct_body


def test_gzip_transparent(single_chan_file, tmp_path):
    """Gzip-compressed file should yield identical blocks to the original."""
    gz_path = tmp_path / "locu.sud.gz"
    with open(single_chan_file, "rb") as src, gzip.open(gz_path, "wb") as dst:
        dst.write(src.read())

    orig = list(iter_suds_blocks(str(single_chan_file)))
    comp = list(iter_suds_blocks(str(gz_path)))

    assert len(orig) == len(comp)
    for bo, bc in zip(orig, comp):
        assert bo.struct_type  == bc.struct_type
        assert bo.struct_body  == bc.struct_body
        assert bo.data         == bc.data


def test_gzip_skip_data(single_chan_file, tmp_path):
    gz_path = tmp_path / "locu.sud.gz"
    with open(single_chan_file, "rb") as src, gzip.open(gz_path, "wb") as dst:
        dst.write(src.read())

    orig    = list(iter_suds_blocks(str(single_chan_file)))
    gz_skip = list(iter_suds_blocks(str(gz_path), skip_data=True))

    assert len(orig) == len(gz_skip)
    for bo, bg in zip(orig, gz_skip):
        assert bo.struct_body == bg.struct_body
        assert bg.data == b""


def test_strict_false_truncated(tmp_path):
    """strict=False should stop cleanly on a truncated file, not raise."""
    # Write a valid 12-byte tag followed by a truncated struct_body
    import struct
    tag = struct.pack("<ccHII", b"S", b"6", 5, 108, 0)  # STATIONCOMP, 108 body bytes, 0 data
    truncated = tag + b"\x00" * 10  # only 10 of 108 body bytes
    bad_file = tmp_path / "truncated.sud"
    bad_file.write_bytes(truncated)

    # strict=True should raise
    with pytest.raises(EOFError):
        list(iter_suds_blocks(str(bad_file), strict=True))

    # strict=False should yield nothing (truncated before first complete block) and not raise
    result = list(iter_suds_blocks(str(bad_file), strict=False))
    assert result == []


def test_multi_station_block_count(multi_station_file):
    """Spot-check that a known multi-station file reads the expected block types."""
    types = [b.struct_type for b in iter_suds_blocks(str(multi_station_file))]
    assert types.count(5) >= 2   # at least 2 STATIONCOMP (one per station per channel)
    assert types.count(7) >= 2   # at least 2 DESCRIPTRACE


def _gz_bytes(src):
    import gzip as _g
    return _g.compress(open(src, "rb").read())


def test_gzip_trailing_filler_is_lossless(single_chan_file, tmp_path):
    """A complete, CRC-verified member followed by filler bytes (EqServer
    MRAT: deadbeefcafecafe) yields exactly the original blocks and records
    the filler (eqserver 2026-10-31)."""
    p = tmp_path / "filler.sud.gz"
    p.write_bytes(_gz_bytes(single_chan_file) + bytes.fromhex("deadbeefcafecafe"))
    orig = list(iter_suds_blocks(str(single_chan_file)))
    for strict in (True, False):
        diag = {}
        got = list(iter_suds_blocks(str(p), strict=strict, diag=diag))
        assert [b.data for b in got] == [b.data for b in orig]
        assert [b.struct_body for b in got] == [b.struct_body for b in orig]
        assert diag["gzip_trailing_bytes"] == "deadbeefcafecafe"
        assert diag["stop_reason"] == "clean_eof"


def test_gzip_truncated_member_keeps_only_complete_blocks(single_chan_file, tmp_path):
    """A member that genuinely ends early: strict raises; tolerant keeps
    only complete blocks (never a partial one) and flags the truncation."""
    gz = _gz_bytes(single_chan_file)
    p = tmp_path / "cut.sud.gz"
    p.write_bytes(gz[: len(gz) * 2 // 3])
    with pytest.raises(EOFError):
        list(iter_suds_blocks(str(p), strict=True))
    orig = list(iter_suds_blocks(str(single_chan_file)))
    diag = {}
    got = list(iter_suds_blocks(str(p), strict=False, diag=diag))
    assert diag.get("gzip_truncated") is True
    assert 0 < len(got) < len(orig)
    for g, o in zip(got, orig):
        assert g.data == o.data and g.struct_body == o.struct_body


def test_gzip_corrupt_data_raises(single_chan_file, tmp_path):
    gz = bytearray(_gz_bytes(single_chan_file))
    for i in range(20, 60):
        gz[i] ^= 0xFF
    p = tmp_path / "corrupt.sud.gz"
    p.write_bytes(bytes(gz))
    with pytest.raises(Exception):
        list(iter_suds_blocks(str(p), strict=False))


def test_gzip_truncated_second_member_is_not_filler(single_chan_file, tmp_path):
    """Codex e41bebb: member 1 complete + member 2 cut short must NOT be
    accepted as 'member 1 + filler' — strict raises, tolerant flags it."""
    g = _gz_bytes(single_chan_file)
    p = tmp_path / "two.sud.gz"
    p.write_bytes(g + g[: len(g) // 2])
    with pytest.raises(EOFError):
        list(iter_suds_blocks(str(p), strict=True))
    diag = {}
    list(iter_suds_blocks(str(p), strict=False, diag=diag))
    assert diag.get("gzip_truncated") is True
    assert "gzip_trailing_bytes" not in diag


def test_gzip_two_complete_members_then_filler(single_chan_file, tmp_path):
    """Two complete members + filler: both decoded (content doubled), filler recorded."""
    g = _gz_bytes(single_chan_file)
    p = tmp_path / "two_ok.sud.gz"
    p.write_bytes(g + g + bytes.fromhex("deadbeefcafecafe"))
    import io as _io
    from sudspy.blocks import _gunzip_bytes
    diag = {}
    out = _gunzip_bytes(str(p), strict=True, diag=diag)
    assert out == open(single_chan_file, "rb").read() * 2
    assert diag["gzip_trailing_bytes"] == "deadbeefcafecafe"


def test_recover_truncated_waveform_prefix(single_chan_file, tmp_path):
    """Opt-in recovery of a DESCRIPTRACE cut mid-data (Codex 1b27f4f): the
    complete sample prefix is returned, never padded, and recorded in diag;
    without the opt-in the block is dropped as before."""
    from sudspy.obspy import read_suds_stream
    raw = open(single_chan_file, "rb").read()
    blocks = list(iter_suds_blocks(str(single_chan_file)))
    wave = next(b for b in blocks if b.struct_type == 7 and b.data)
    cut = wave.offset + 12 + wave.tag["struct_length"] + len(wave.data) // 2 + 1
    p = tmp_path / "cut.sud"
    p.write_bytes(raw[:cut])
    full = read_suds_stream(str(single_chan_file))
    plain = read_suds_stream(str(p))
    diag = {}
    rec = read_suds_stream(str(p), diag=diag, recover_truncated=True)
    full_tr = [t for t in full if len(t.data)][0]
    assert sum(len(t.data) for t in plain) < sum(len(t.data) for t in rec)
    tr = rec[-1]
    assert 0 < len(tr.data) < len(full_tr.data)
    assert (tr.data == full_tr.data[: len(tr.data)]).all()
    assert diag["truncated_trace"]["available_bytes"] < diag["truncated_trace"]["declared_bytes"]
