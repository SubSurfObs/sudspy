# parsers.py

from typing import Dict, Any, List, Optional, Tuple
import struct
from .blocks import SudsBlock


# -------------------------
# Layer 2: struct-body decoders (parity with Java/manual)
# -------------------------

def _datatype_to_numpy(datatype: str) -> Tuple[int, str]:
    """
    Return (bytes_per_sample, numpy_dtype_str)
    """
    if datatype == "i":      # int16
        return 2, "<i2"
    if datatype in ("l", "2"):  # int32
        return 4, "<i4"
    if datatype == "f":      # float32
        return 4, "<f4"
    raise ValueError(f"Unsupported datatype: {datatype!r}")

def _clean_code(s: str) -> str:
    # SUDS often fixed-width, space + NUL padded
    return s.strip("\x00 ").strip()


def normalize_seed_code(s: str, max_len: Optional[int]) -> str:
    s = _clean_code(s)
    if max_len is not None and len(s) > max_len:
        s = s[:max_len]
    return s

def normalize_seed_codes(
    network,
    station,
    location="",
    expected_network_len=2,
):
    net = (network or "").strip()
    sta = (station or "").strip()
    loc = (location or "").strip()

    if expected_network_len and len(net) > expected_network_len:
        net = net[:expected_network_len]

    return net, sta, loc


def parse_statident(raw12: bytes) -> Dict[str, str]:
    if len(raw12) != 12:
        raise ValueError(f"STATIDENT must be 12 bytes, got {len(raw12)}")

    network = raw12[0:4].decode("ascii", errors="ignore")
    station = raw12[4:9].decode("ascii", errors="ignore")
    component = raw12[9:10].decode("ascii", errors="ignore")

    return {
        "network": _clean_code(network),
        "station": _clean_code(station),
        "component": _clean_code(component),
    }


def parse_longident(raw32: bytes) -> Dict[str, str]:
    if len(raw32) != 32:
        raise ValueError(f"LONGIDENT must be 32 bytes, got {len(raw32)}")

    net = raw32[0:8].decode("ascii", errors="ignore")
    sta = raw32[8:24].decode("ascii", errors="ignore")
    comp = raw32[24:32].decode("ascii", errors="ignore")

    return {
        "network": _clean_code(net),
        "station": _clean_code(sta),
        "component": _clean_code(comp),
    }


def parse_stationcomp_struct(block: SudsBlock) -> Dict[str, Any]:
    raw = block.struct_body
    if len(raw) < 12 + 64:
        raise ValueError("STATIONCOMP struct too short")

    statident = parse_statident(raw[:12])
    # STATIONCOMP in your files is typically 108 bytes: 12 STATIDENT + 64 guts + 32 LONGIDENT
    longident = parse_longident(raw[-32:]) if len(raw) >= 108 else None

    off = 12
    sb: Dict[str, Any] = {}

    sb["azim"]        = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["incid"]       = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["st_lat"]      = struct.unpack_from("<d", raw, off)[0]; off += 8
    sb["st_long"]     = struct.unpack_from("<d", raw, off)[0]; off += 8
    sb["elev"]        = struct.unpack_from("<f", raw, off)[0]; off += 4

    sb["enclosure"]   = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["annotation"]  = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["recorder"]    = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["rockclass"]   = raw[off:off+1].decode("ascii", "ignore"); off += 1

    sb["rocktype"]      = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["sitecondition"] = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["sensor_type"]   = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["data_type"]     = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["data_units"]    = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["polarity"]      = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["st_status"]     = raw[off:off+1].decode("ascii", "ignore"); off += 1

    sb["max_gain"]     = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["clip_value"]   = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["con_mvolts"]   = struct.unpack_from("<f", raw, off)[0]; off += 4

    sb["channel"]       = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["atod_gain"]     = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["effective"]     = struct.unpack_from("<i", raw, off)[0]; off += 4
    sb["clock_correct"] = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["station_delay"] = struct.unpack_from("<f", raw, off)[0]; off += 4

    return {
        "struct_type": 5,
        "statident": statident,
        "longident": longident,
        "struct_body": sb,
        "offset": block.offset,
        "raw_len": len(raw),
    }


def parse_descriptrace_struct(block: SudsBlock) -> Dict[str, Any]:
    raw = block.struct_body

    if len(raw) < 12 + 52:
        raise ValueError("DESCRIPTRACE struct too short")

    statident = parse_statident(raw[:12])
    # DESCRIPTRACE "guts" length is 52 after STATIDENT; some files may append LONGIDENT (32)
    longident = parse_longident(raw[-32:]) if len(raw) >= 12 + 52 + 32 else None

    off = 12
    sb: Dict[str, Any] = {}

    sb["begintime"]     = struct.unpack_from("<d", raw, off)[0]; off += 8
    sb["localtime"]     = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["datatype"]      = raw[off:off+1].decode("ascii", errors="ignore"); off += 1
    sb["descriptor"]    = raw[off:off+1].decode("ascii", errors="ignore"); off += 1
    sb["digi_by"]       = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["processed"]     = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["length"]        = struct.unpack_from("<i", raw, off)[0]; off += 4
    sb["rate"]          = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["mindata"]       = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["maxdata"]       = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["avenoise"]      = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["numclip"]       = struct.unpack_from("<i", raw, off)[0]; off += 4
    sb["time_correct"]  = struct.unpack_from("<d", raw, off)[0]; off += 8
    sb["rate_correct"]  = struct.unpack_from("<f", raw, off)[0]; off += 4

    return {
        "struct_type": 7,
        "statident": statident,
        "longident": longident,
        "struct_body": sb,
        "offset": block.offset,
        "raw_len": len(raw),
    }


def parse_instrument_struct(block: SudsBlock) -> Dict[str, Any]:
    raw = block.struct_body
    if len(raw) < 12 + 72:
        raise ValueError("INSTRUMENT struct too short")

    statident = parse_statident(raw[:12])
    # (Instrument may or may not have a LONGIDENT; don’t assume.)
    longident = parse_longident(raw[-32:]) if len(raw) >= 12 + 72 + 32 else None

    off = 12

    def sh():
        nonlocal off
        v = struct.unpack_from("<h", raw, off)[0]; off += 2
        return v

    def lg():
        nonlocal off
        v = struct.unpack_from("<i", raw, off)[0]; off += 4
        return v

    def fl():
        nonlocal off
        v = struct.unpack_from("<f", raw, off)[0]; off += 4
        return v

    def ch():
        nonlocal off
        v = raw[off:off+1].decode("ascii", errors="ignore"); off += 1
        return v

    def strn(n):
        nonlocal off
        v = raw[off:off+n].decode("ascii", errors="ignore").strip("\x00 "); off += n
        return v

    sb = {
        "in_serial": sh(),
        "comps": sh(),
        "channel": sh(),
        "sens_type": ch(),
        "datatype": ch(),
        "void_samp": lg(),
        "dig_con": fl(),
        "aa_corner": fl(),
        "aa_poles": fl(),
        "nat_freq": fl(),
        "damping": fl(),
        "mot_con": fl(),
        "gaindb": fl(),
        "local_x": fl(),
        "local_y": fl(),
        "local_z": fl(),
        "effective": lg(),
        "pre_event": fl(),
        "trig_num": sh(),
        "study": strn(6),
        "sn_serial": sh(),
    }

    return {
        "struct_type": 31,
        "statident": statident,
        "longident": longident,
        "struct_body": sb,
        "offset": block.offset,
        "raw_len": len(raw),
    }





def parse_feature_struct(block: SudsBlock):
    """
    Parse a SUDS_FEATURE (struct_type = 10) block.

    IMPORTANT IDENTITY SEMANTICS
    ----------------------------
    The STATIDENT contained *inside* a FEATURE block is NOT authoritative
    for waveform identity.

    Empirical testing on SRC-generated SUDS files shows that:
      • FEATURE.statident.network is often a placeholder (e.g. "0")
      • FEATURE.statident.component is only a single character (e.g. "Z", "N")
      • FEATURE.statident does NOT uniquely identify a SEED channel

    Instead, FEATURE blocks must be associated with waveform identity via
    CONTEXT, using the nearest preceding waveform-defining blocks:

      1. Nearest preceding DESCRIPTRACE (struct_type = 7)
         → provides authoritative longident (NET.STA.CHAN)

      2. Nearest preceding STATIONCOMP (struct_type = 5)
         → provides consistent channel-level metadata and longident

    Verified behaviour:
      • Picks on different channels within the same file correctly attach
        to different DESCRIPTRACE blocks
      • FEATURE blocks inherit the correct channel identity from context,
        NOT from their own STATIDENT

    Therefore:
      • FEATURE.statident is parsed and preserved for reference/debugging
      • WaveformStreamID for ObsPy Picks MUST be derived from the associated
        DESCRIPTRACE / STATIONCOMP longident, not from FEATURE.statident

    This behaviour has been validated using multi-channel test files with
    picks on CHZ, CHN, etc., and is REQUIRED to avoid systematic misassignment
    of picks to channels.

    Returns
    -------
    dict with keys:
        struct_type : int
        statident   : dict   # non-authoritative FEATURE STATIDENT
        struct_body : dict   # parsed FEATURE fields
        offset      : int
        raw_len     : int
    """
    raw = block.struct_body

    if len(raw) < 48:
        raise ValueError("FEATURE struct too short")

    statident = parse_statident(raw[:12])
    off = 12
    sb = {}

    sb["obs_phase"]     = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["onset"]         = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["direction"]     = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["sig_noise"]     = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["data_source"]   = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["tim_qual"]      = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["amp_qual"]      = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["ampunits"]      = raw[off:off+1].decode("ascii", "ignore"); off += 1
    sb["gain_range"]    = struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["time"]          = struct.unpack_from("<d", raw, off)[0]; off += 8
    sb["amplitude"]     = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["period"]        = struct.unpack_from("<f", raw, off)[0]; off += 4
    sb["time_of_pick"]  = struct.unpack_from("<i", raw, off)[0]; off += 4
    sb["pick_authority"]= struct.unpack_from("<h", raw, off)[0]; off += 2
    sb["pick_reader"]   = struct.unpack_from("<h", raw, off)[0]; off += 2

    return {
        "struct_type": 10,
        "statident": statident,
        "struct_body": sb,
        "offset": block.offset,
        "raw_len": len(raw),
    }




# ----------------------------
# COMMENT (struct_type = 20)
# ----------------------------
def parse_comment_struct(block: SudsBlock) -> dict:
    """
    Parse a SUDS_COMMENT structure.

    NOTE:
    - The comment text lives in block.data
    - COMMENT has no STATIDENT
    - Association to channels/stations must be done by context
      (nearest preceding DESCRIPTRACE / STATIONCOMP)
    """
    raw = block.struct_body

    if len(raw) < 8:
        raise ValueError("COMMENT struct too short")

    refer, item, length, unused = struct.unpack_from("<hhhh", raw, 0)

    text = block.data[:length].decode("utf-8", errors="replace")

    return {
        "struct_type": 20,
        "statident": None,
        "longident": None,
        "struct_body": {
            "refer": refer,
            "item": item,
            "length": length,
            "unused": unused,
            "text": text,
        },
        "offset": block.offset,
        "raw_len": len(raw),
    }

