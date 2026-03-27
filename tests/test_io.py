"""Tests for io.py: parse_echopro_filename and scan_suds_file."""

import pytest
from obspy import UTCDateTime
from sudspy.io import parse_echopro_filename, scan_suds_file


# ---------------------------------------------------------------------------
# parse_echopro_filename
# ---------------------------------------------------------------------------

class TestParseEchoproFilename:

    def test_disk_uncompressed(self):
        r = parse_echopro_filename("2023-11-24_2057_02_ABM5Y.dmx")
        assert r is not None
        assert r["date"]        == "2023-11-24"
        assert r["hhmm"]        == "2057"
        assert r["ss"]          == "02"
        assert r["station"]     == "ABM5Y"
        assert r["source_type"] == "disk"
        assert r["is_gzip"]     is False
        assert r["is_triggered"] is False

    def test_disk_gzipped(self):
        r = parse_echopro_filename("2023-11-24_2057_02_ABM5Y.dmx.gz")
        assert r["source_type"] == "disk"
        assert r["is_gzip"]     is True

    def test_telemetry_uncompressed(self):
        r = parse_echopro_filename("2023-11-24 2057 02 ABM5Y.dmx")
        assert r is not None
        assert r["source_type"] == "telemetry"
        assert r["ss"]          == "02"
        assert r["station"]     == "ABM5Y"
        assert r["is_gzip"]     is False

    def test_telemetry_gzipped(self):
        r = parse_echopro_filename("2023-11-24 2057 02 ABM5Y.dmx.gz")
        assert r["source_type"] == "telemetry"
        assert r["is_gzip"]     is True

    def test_explicit_trig_disk(self):
        r = parse_echopro_filename("2023-11-24_0317_55_ABM5Y.trig.dmx")
        assert r is not None
        assert r["is_triggered"] is True
        assert r["ss"]           == "55"

    def test_explicit_trig_gz(self):
        r = parse_echopro_filename("2023-11-24_0317_55_ABM5Y.trig.dmx.gz")
        assert r["is_triggered"] is True
        assert r["is_gzip"]      is True

    def test_different_ss_not_triggered_by_name(self):
        """SS=48 file has no trig marker — is_triggered is False at filename level."""
        r = parse_echopro_filename("2023-11-24_2057_48_ABM5Y.dmx")
        assert r is not None
        assert r["ss"]           == "48"
        assert r["is_triggered"] is False  # SS-based detection is pipeline-level

    def test_non_dmx_returns_none(self):
        assert parse_echopro_filename("20231029_0001_ABM1Y.ms.zip") is None

    def test_gecko_underscore_returns_none(self):
        # Gecko files have 2 underscores (no SS field) — should not match
        assert parse_echopro_filename("20231029_0001_ABM1Y.ms.zip") is None

    def test_unrelated_file_returns_none(self):
        assert parse_echopro_filename("README.md") is None

    def test_basename_extracted_from_path(self):
        r = parse_echopro_filename("/some/path/2023-11-24_2057_02_ABM5Y.dmx")
        assert r is not None
        assert r["station"] == "ABM5Y"

    def test_sequence_filenames(self, sequence_dir):
        """All files in the sequence directory should parse as disk SS=02."""
        files = sorted(sequence_dir.glob("*.dmx"))
        assert len(files) > 0
        for f in files:
            r = parse_echopro_filename(f.name)
            assert r is not None, f"Failed to parse: {f.name}"
            assert r["source_type"] == "disk"
            assert r["ss"] == "02"

    def test_overlaps_filenames(self, overlaps_dir):
        """Overlaps directory has disk SS=02, telemetry SS=02, and disk SS=48."""
        files = list(overlaps_dir.glob("*.dmx"))
        parsed = [parse_echopro_filename(f.name) for f in files]
        parsed = [p for p in parsed if p is not None]
        source_types = {p["source_type"] for p in parsed}
        ss_values    = {p["ss"] for p in parsed}
        assert "disk"      in source_types
        assert "telemetry" in source_types
        assert "02" in ss_values
        assert "48" in ss_values  # the triggered/accelerometer file


# ---------------------------------------------------------------------------
# scan_suds_file
# ---------------------------------------------------------------------------

class TestScanSudsFile:

    def test_returns_list(self, single_chan_file):
        result = scan_suds_file(str(single_chan_file))
        assert isinstance(result, list)
        assert len(result) > 0

    def test_result_keys(self, single_chan_file):
        for entry in scan_suds_file(str(single_chan_file)):
            assert "channel"     in entry
            assert "start_time"  in entry
            assert "end_time"    in entry
            assert "npts"        in entry
            assert "sample_rate" in entry

    def test_result_types(self, single_chan_file):
        for entry in scan_suds_file(str(single_chan_file)):
            assert isinstance(entry["channel"],     str)
            assert isinstance(entry["start_time"],  UTCDateTime)
            assert isinstance(entry["end_time"],    UTCDateTime)
            assert isinstance(entry["npts"],        int)
            assert isinstance(entry["sample_rate"], float)

    def test_end_after_start(self, single_chan_file):
        for entry in scan_suds_file(str(single_chan_file)):
            assert entry["end_time"] >= entry["start_time"]

    def test_npts_positive(self, single_chan_file):
        for entry in scan_suds_file(str(single_chan_file)):
            assert entry["npts"] > 0

    def test_sample_rate_positive(self, single_chan_file):
        for entry in scan_suds_file(str(single_chan_file)):
            assert entry["sample_rate"] > 0

    def test_multi_channel_disk_file(self, overlaps_dir):
        """Disk 3-channel EchoPro file should return 3 channel entries."""
        path = overlaps_dir / "2023-11-24_2057_02_ABM5Y.dmx"
        result = scan_suds_file(str(path))
        assert len(result) == 3

    def test_telemetry_single_channel(self, overlaps_dir):
        """Telemetry file in overlaps dir is 1-channel (Z only)."""
        path = overlaps_dir / "2023-11-24 2057 02 ABM5Y.dmx"
        result = scan_suds_file(str(path))
        assert len(result) == 1

    def test_scan_matches_stream_timing(self, overlaps_dir):
        """scan_suds_file start_time should match read_suds_stream for same file."""
        import sudspy
        path = overlaps_dir / "2023-11-24_2057_02_ABM5Y.dmx"
        scan = scan_suds_file(str(path))
        st   = sudspy.read_suds_stream(str(path))

        scan_starts = sorted(e["start_time"] for e in scan)
        stream_starts = sorted(tr.stats.starttime for tr in st)

        for s, t in zip(scan_starts, stream_starts):
            assert abs(s - t) < 0.001  # within 1 ms

    def test_gzip_same_as_uncompressed(self, single_chan_file, tmp_path):
        import gzip
        gz_path = tmp_path / "test.sud.gz"
        with open(single_chan_file, "rb") as src, gzip.open(gz_path, "wb") as dst:
            dst.write(src.read())

        result_orig = scan_suds_file(str(single_chan_file))
        result_gz   = scan_suds_file(str(gz_path))

        assert len(result_orig) == len(result_gz)
        for ro, rg in zip(result_orig, result_gz):
            assert ro["channel"]    == rg["channel"]
            assert ro["npts"]       == rg["npts"]
            assert abs(ro["start_time"] - rg["start_time"]) < 0.001

    def test_sequence_files_timing(self, sequence_dir):
        """Consecutive sequence files (SS=02) should have contiguous start times."""
        files = sorted(sequence_dir.glob("*.dmx"))
        scans = []
        for f in files:
            entries = scan_suds_file(str(f))
            scans.append((f.name, entries))

        # Each file should have at least one channel
        for fname, entries in scans:
            assert len(entries) > 0, f"No channels found in {fname}"

        # Collect all start times for one representative channel
        # The sequence has 10 files (0000-0010) with a gap at 0005
        all_starts = sorted(
            e["start_time"]
            for _, entries in scans
            for e in entries
        )
        # There should be a gap somewhere (0005 missing)
        gaps = [
            all_starts[i+1] - all_starts[i]
            for i in range(len(all_starts) - 1)
        ]
        assert any(g > 70 for g in gaps), "Expected a gap > 70s for the missing 0005 file"
