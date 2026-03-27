# io.py
# File discovery, filename parsing, and fast metadata scanning for the
# EqServer → SDS archive conversion pipeline.

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Any

from obspy import UTCDateTime

from .blocks import iter_suds_blocks
from .parsers import parse_stationcomp_struct, parse_descriptrace_struct


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

# Matches EchoPro filenames with either underscore (disk) or space (telemetry)
# separators.  Examples:
#   2023-11-24_2057_02_ABM5Y.dmx        disk, uncompressed
#   2023-11-24_2057_02_ABM5Y.dmx.gz     disk, gzipped
#   2023-11-24 2057 02 ABM5Y.dmx        telemetry, uncompressed
#   2023-11-24_2057_02_ABM5Y.trig.dmx   explicit triggered (disk)
_ECHOPRO_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})([ _])(\d{4})\2(\d{2})\2(\S+?)(?:\.trig)?\.dmx(\.gz)?$"
)


def parse_echopro_filename(fname: str) -> Optional[Dict[str, Any]]:
    """
    Parse an EchoPro (.dmx / .dmx.gz) filename.

    Returns a dict with keys:
        date        : str   e.g. "2023-11-24"
        hhmm        : str   e.g. "2057"
        ss          : str   e.g. "02"
        station     : str   e.g. "ABM5Y"
        source_type : str   "disk" or "telemetry"
        is_gzip     : bool
        is_triggered: bool  True if ".trig" appears before .dmx

    Returns None if the filename does not match the EchoPro pattern.
    """
    base = os.path.basename(fname)
    m = _ECHOPRO_RE.match(base)
    if m is None:
        return None

    date_str, sep, hhmm, ss, station, gz = m.groups()
    return {
        "date": date_str,
        "hhmm": hhmm,
        "ss": ss,
        "station": station,
        "source_type": "disk" if sep == "_" else "telemetry",
        "is_gzip": gz is not None,
        "is_triggered": ".trig." in base,
    }


# ---------------------------------------------------------------------------
# Fast metadata scan
# ---------------------------------------------------------------------------

def scan_suds_file(path: str) -> List[Dict[str, Any]]:
    """
    Fast metadata-only scan of a SUDS file.

    Reads STATIONCOMP and DESCRIPTRACE header blocks only; data payloads are
    skipped (no numpy allocation, minimal decompression work for .gz files).

    Returns a list of dicts — one per waveform segment — with keys:
        channel     : str           "NET.STA.CHA"
        start_time  : UTCDateTime
        end_time    : UTCDateTime   start + (npts-1) / sample_rate
        npts        : int
        sample_rate : float         samples per second

    A single EchoPro file typically contains 3 or 6 channels, so the list
    will have one entry per channel.
    """
    results: List[Dict[str, Any]] = []
    last_stationcomp: Optional[Dict] = None

    for block in iter_suds_blocks(path, skip_data=True, strict=False):

        if block.struct_type == 5:  # STATIONCOMP
            try:
                last_stationcomp = parse_stationcomp_struct(block)
            except Exception:
                last_stationcomp = None
            continue

        if block.struct_type == 7:  # DESCRIPTRACE
            if last_stationcomp is None:
                continue
            try:
                desc = parse_descriptrace_struct(block)
            except Exception:
                continue

            stat = last_stationcomp["statident"]
            lng  = last_stationcomp.get("longident")

            net = ((lng["network"]   if lng else stat["network"])   or "").strip()
            sta = ((lng["station"]   if lng else stat["station"])   or "").strip()
            cha = ((lng["component"] if lng else stat["component"]) or "").strip()

            sb   = desc["struct_body"]
            t0   = float(sb["begintime"])
            npts = int(sb["length"])
            rate = float(sb["rate"])

            start = UTCDateTime(t0)
            end   = start + (npts - 1) / rate if (rate > 0 and npts > 0) else start

            results.append({
                "channel":     f"{net}.{sta}.{cha}",
                "start_time":  start,
                "end_time":    end,
                "npts":        npts,
                "sample_rate": rate,
            })

    return results
