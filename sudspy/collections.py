from typing import Dict, Any, List, Optional, Tuple

from .blocks import iter_suds_blocks
from .parsers import (
    parse_stationcomp_struct,
    parse_instrument_struct,
    parse_comment_struct,
    parse_descriptrace_struct,
)
from .constants import SUDS_STRUCT_TYPES


def collect_instruments(
    path,
    expected_network_len=2,
):
    """
    Collect INSTRUMENT metadata from a SUDS file.

    Returns a dict keyed by NET.STA.CHAN, e.g.:

    {
        "VW.LOCU.CHZ": {
            "network": "VW",
            "station": "LOCU",
            "channel": "CHZ",
            "struct_body": {...},
            "offset": int,
        },
        ...
    }

    Notes
    -----
    * INSTRUMENT.statident is authoritative for (network, station, component-letter),
      but usually only provides a 1-letter component (Z/N/E).
    * If a matching DESCRIPTRACE (or STATIONCOMP) with longident has appeared earlier
      in the file for the same (network, station, component-letter), we inherit the
      full SEED-ish channel code from that longident (e.g. CHZ, CHN, CHE).
    * No attempt is made to merge channels or stations.
    * This function does NOT create ObsPy Inventory objects.
    """

    def _norm_net(net: str) -> str:
        net = (net or "").strip("\x00 ").strip()
        if expected_network_len and len(net) > expected_network_len:
            net = net[:expected_network_len]
        return net

    def _norm_sta(sta: str) -> str:
        return (sta or "").strip("\x00 ").strip()

    def _norm_comp(comp: str) -> str:
        return (comp or "").strip("\x00 ").strip()

    instruments = {}

    # Context cache:
    # Map (net, sta, component_letter) -> full channel code from longident (e.g. CHZ)
    last_longident_channel = {}

    for block in iter_suds_blocks(path):

        # Update context from STATIONCOMP/DESCRIPTRACE as we pass them
        if block.struct_type == 5:  # STATIONCOMP
            stc = parse_stationcomp_struct(block)
            lid = stc.get("longident")
            sid = stc.get("statident")

            if lid and sid:
                net = _norm_net(lid.get("network", sid.get("network", "")))
                sta = _norm_sta(lid.get("station", sid.get("station", "")))
                full_chan = _norm_comp(lid.get("component", ""))  # e.g. CHZ
                comp_letter = _norm_comp(sid.get("component", ""))  # e.g. Z

                if net and sta and comp_letter and full_chan:
                    last_longident_channel[(net, sta, comp_letter)] = full_chan

            continue

        if block.struct_type == 7:  # DESCRIPTRACE
            dsc = parse_descriptrace_struct(block)
            lid = dsc.get("longident")
            sid = dsc.get("statident")

            if lid and sid:
                net = _norm_net(lid.get("network", sid.get("network", "")))
                sta = _norm_sta(lid.get("station", sid.get("station", "")))
                full_chan = _norm_comp(lid.get("component", ""))  # e.g. CHZ
                comp_letter = _norm_comp(sid.get("component", ""))  # e.g. Z

                if net and sta and comp_letter and full_chan:
                    last_longident_channel[(net, sta, comp_letter)] = full_chan

            continue

        # Actual target
        if block.struct_type != 31:  # INSTRUMENT
            continue

        inst = parse_instrument_struct(block)
        sid = inst["statident"]

        net = _norm_net(sid.get("network", ""))
        sta = _norm_sta(sid.get("station", ""))
        comp_letter = _norm_comp(sid.get("component", ""))  # usually Z/N/E

        # Prefer inherited full channel code from nearest preceding DESCRIPTRACE/STATIONCOMP
        full_chan = last_longident_channel.get((net, sta, comp_letter), comp_letter)

        key = f"{net}.{sta}.{full_chan}"

        # Keep first occurrence; later duplicates should be identical
        if key not in instruments:
            instruments[key] = {
                "network": net,
                "station": sta,
                "channel": full_chan,
                "struct_body": inst["struct_body"],
                "offset": inst["offset"],
            }

    return instruments





# ----------------------------
# Collector (like collect_instruments)
# ----------------------------
def collect_comments(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Collect SUDS_COMMENT blocks and attach them to channels using the
    nearest preceding DESCRIPTRACE (struct_type=7).

    Returns:
        dict keyed by "NET.STA.CHAN" (from DESCRIPTRACE.longident if present,
        else DESCRIPTRACE.statident), with value = list of parsed comment dicts.

    Assumption (backed by your empirical tests):
      - In SRC-produced files, COMMENT blocks appear in per-trace context,
        so "nearest preceding DESCRIPTRACE" is the correct attachment rule.
    """
    comments_by_chan: Dict[str, List[Dict[str, Any]]] = {}

    last_desc: Optional[Dict[str, Any]] = None  # parsed DESCRIPTRACE

    for b in iter_suds_blocks(path):
        if b.struct_type == 7:
            # Use your existing DESCRIPTRACE parser (already returns statident/longident)
            last_desc = parse_descriptrace_struct(b)
            continue

        if b.struct_type == 20:
            c = parse_comment_struct(b)

            # Attach to the most recent DESCRIPTRACE context
            if last_desc is not None:
                lid = last_desc.get("longident") or last_desc.get("statident") or {}
                net = (lid.get("network") or "").strip()
                sta = (lid.get("station") or "").strip()
                chan = (lid.get("component") or "").strip()
                key = f"{net}.{sta}.{chan}"
            else:
                # No context available (should be rare); keep but mark as unknown
                key = "UNKNOWN.UNKNOWN.UNKNOWN"

            comments_by_chan.setdefault(key, []).append(c)

    return comments_by_chan



def collect_stations(
    path,
    expected_network_len=2,
):
    """
    Collect STATIONCOMP metadata keyed by channel.

    Returns a dict keyed by NET.STA.CHAN containing:
        network, station, location, channel,
        latitude_deg, longitude_deg, elevation_m,
        channel_meta: {...}

    Semantics identical to previous collect_stations,
    but flattened to channel-keyed form.
    """

    stations_by_chan = {}

    for block in iter_suds_blocks(path):
        if block.struct_type != 5:  # STATIONCOMP
            continue

        sc = parse_stationcomp_struct(block)

        # Authoritative identity
        ident = sc.get("longident") or sc["statident"]

        network = (ident["network"] or "").strip()
        station = (ident["station"] or "").strip()
        channel = (ident["component"] or "").strip()

        if expected_network_len and len(network) > expected_network_len:
            network = network[:expected_network_len]

        location = ""  # SUDS has no native location

        key = f"{network}.{station}.{channel}"

        sb = sc["struct_body"]

        stations_by_chan[key] = {
            "network": network,
            "station": station,
            "location": location,
            "channel": channel,
            "latitude_deg": sb["st_lat"],
            "longitude_deg": sb["st_long"],
            "elevation_m": sb["elev"],
            "channel_meta": {
                "channel_number": sb["channel"],
                "sensor_type": sb["sensor_type"],
                "data_type": sb["data_type"],
                "data_units": sb["data_units"],
                "polarity": sb["polarity"],
            },
        }

    return stations_by_chan