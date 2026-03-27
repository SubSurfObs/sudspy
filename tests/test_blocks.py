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
