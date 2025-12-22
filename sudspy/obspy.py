# obspy.py
from __future__ import annotations

# sudspy/obspy.py
from __future__ import annotations

from typing import Dict, Optional, Iterable, Tuple, List
import numpy as np
from collections import defaultdict


from obspy import UTCDateTime, Stream, Trace
from obspy.core.event import Pick, WaveformStreamID
from obspy.core.event.header import PickOnset, PickPolarity
from obspy.core.inventory import Inventory, Network, Station, Channel, Site
from obspy.core.inventory.response import Response, InstrumentSensitivity, ResponseStage

from .blocks import SudsBlock, iter_suds_blocks
from .parsers import (
    parse_stationcomp_struct,
    parse_descriptrace_struct,
    parse_feature_struct,
    normalize_seed_code, normalize_seed_codes,
    _clean_code, _datatype_to_numpy
)
from .constants import SRC_PHASE_MAP
from .collections import collect_stations, collect_instruments



LocationMap = Dict[str, str]  # keys like "NET.STA.CHA" -> "00"


def resolve_location(
    net: str,
    sta: str,
    cha: str,
    location_map: Optional[LocationMap],
    default_location: str,
) -> str:
    if not location_map:
        return default_location

    # Try most-specific to least-specific
    keys = [
        f"{net}.{sta}.{cha}",
        f"{net}.{sta}",
        f"{sta}.{cha}",
        f"{sta}",
    ]
    for k in keys:
        if k in location_map:
            return location_map[k]

    return default_location

def suds_blocks_to_stream(
    blocks: Iterable[SudsBlock],
    *,
    default_location: str = "",
    location_map: Optional[LocationMap] = None,
    expected_network_len: Optional[int] = 2,
    expected_channel_len: Optional[int] = None,
) -> Stream:
    """
    Convert a sequence of SUDS blocks into an ObsPy Stream.
    Assumes the common pattern: STATIONCOMP (5) then DESCRIPTRACE (7) with data payload.
    """
    traces: List[Trace] = []
    last_stationcomp_parsed: Optional[Dict[str, Any]] = None

    for block in blocks:

        # STATIONCOMP updates channel metadata context
        if block.struct_type == 5:
            try:
                last_stationcomp_parsed = parse_stationcomp_struct(block)
            except Exception:
                last_stationcomp_parsed = None
            continue

        # DESCRIPTRACE provides timing + datatype + length; payload holds samples
        if block.struct_type == 7:
            if last_stationcomp_parsed is None:
                continue

            desc = parse_descriptrace_struct(block)

            # Codes: prefer LONGIDENT component when present (matches your working approach)
            stat = last_stationcomp_parsed["statident"]
            lng = last_stationcomp_parsed.get("longident")

            net = (lng["network"] if lng else stat["network"])
            sta = (lng["station"] if lng else stat["station"])
            cha = (lng["component"] if lng else stat["component"])

            net = normalize_seed_code(net, expected_network_len)
            sta = normalize_seed_code(sta, None)
            cha = normalize_seed_code(cha, expected_channel_len)

            loc = resolve_location(net, sta, cha, location_map, default_location)

            sb = desc["struct_body"]
            datatype = _clean_code(sb["datatype"])
            bps, np_dt = _datatype_to_numpy(datatype)

            # number of samples: prefer header length if consistent with payload
            payload = block.data
            total_samples = len(payload) // bps
            npts = int(sb["length"]) if (0 < int(sb["length"]) <= total_samples) else total_samples

            # sampling rate
            samprate = float(sb["rate"]) if float(sb["rate"]) > 0 else 0.0

            # start time (your files: seconds since epoch stored as double)
            t0 = float(sb["begintime"])
            starttime = UTCDateTime(t0)

            data = np.frombuffer(payload[:npts * bps], dtype=np_dt).astype(np.float32, copy=False)

            tr = Trace(
                data=data,
                header={
                    "network": net,
                    "station": sta,
                    "location": loc,
                    "channel": cha,
                    "starttime": starttime,
                    "sampling_rate": samprate,
                },
            )
            traces.append(tr)

            continue

        # everything else ignored at waveform layer

    return Stream(traces=traces)




def read_suds_stream(
    path: str,
    *,
    default_location: str = "",
    location_map: Optional[LocationMap] = None,
    expected_network_len: Optional[int] = 2,
    expected_channel_len: Optional[int] = None,
) -> Stream:
    """
    ObsPy-style reader for PC-SUDS waveform files.
    """
    return suds_blocks_to_stream(
        iter_suds_blocks(path),
        default_location=default_location,
        location_map=location_map,
        expected_network_len=expected_network_len,
        expected_channel_len=expected_channel_len,
    )




def read_suds_picks(
    path,
    location_code="",
):
    """
    Read all SUDS_FEATURE blocks from a SUDS file and return a list of ObsPy Picks.

    FEATURE blocks do NOT carry authoritative waveform identity.
    Picks are attached to the nearest preceding DESCRIPTRACE block,
    which provides the authoritative longident (NET.STA.CHAN).
    """

    picks = []

    current_descriptrace = None

    for block in iter_suds_blocks(path):

        # Parse DESCRIPTRACE (waveform context)
        if block.struct_type == 7:
            current_descriptrace = parse_descriptrace_struct(block)
            continue

        # Parse FEATURE (pick)
        if block.struct_type == 10:
            if current_descriptrace is None:
                # No waveform context → cannot attach pick safely
                continue

            feature = parse_feature_struct(block)

            pick = feature_to_pick(
                feature,
                current_descriptrace,
                location_code=location_code,
            )

            picks.append(pick)

    return picks



def read_suds_inv(
    path,
    *,
    default_location="",
):
    """
    Build an ObsPy Inventory from a SUDS file.

    Uses:
      - collect_stations()  → channel-keyed station metadata
      - collect_instruments() → channel-keyed instrument metadata
    """

    stations_by_chan = collect_stations(path)
    instruments_by_chan = collect_instruments(path)

    # Group channels back into NET.STA
    grouped = defaultdict(list)
    for key, sta_chan in stations_by_chan.items():
        net = sta_chan["network"]
        sta = sta_chan["station"]
        grouped[(net, sta)].append((key, sta_chan))

    networks = []

    for (net_code, sta_code), chan_items in grouped.items():

        net = Network(code=net_code)

        # Use first channel for station-level coords
        first = chan_items[0][1]

        sta = Station(
            code=sta_code,
            latitude=first["latitude_deg"],
            longitude=first["longitude_deg"],
            elevation=first["elevation_m"],
            site=Site(name=sta_code),
        )

        for chan_key, chan_meta in chan_items:

            chan_code = chan_meta["channel"]
            loc = default_location

            chan = Channel(
                code=chan_code,
                location_code=loc,
                latitude=chan_meta["latitude_deg"],
                longitude=chan_meta["longitude_deg"],
                elevation=chan_meta["elevation_m"],
                depth=0.0,
            )

            # ---- attach response if instrument exists ----
            inst = instruments_by_chan.get(chan_key)
            if inst is not None:
                sb = inst["struct_body"]

                dig_con = sb.get("dig_con")
                mot_con = sb.get("mot_con")
                gain_db = sb.get("gaindb", 0.0)

                gain_lin = 10 ** (gain_db / 20.0)
                total_sens = dig_con * mot_con * gain_lin

                stages = [
                    ResponseStage(
                        stage_sequence_number=1,
                        stage_gain=gain_lin,
                        stage_gain_frequency=1.0,
                        input_units="V",
                        output_units="V",
                    ),
                    ResponseStage(
                        stage_sequence_number=2,
                        stage_gain=dig_con,
                        stage_gain_frequency=1.0,
                        input_units="V",
                        output_units="COUNTS",
                    ),
                    ResponseStage(
                        stage_sequence_number=3,
                        stage_gain=mot_con,
                        stage_gain_frequency=1.0,
                        input_units="M/S",
                        output_units="V",
                    ),
                ]

                sensitivity = InstrumentSensitivity(
                    value=total_sens,
                    frequency=1.0,
                    input_units="M/S",
                    output_units="COUNTS",
                )

                chan.response = Response(
                    response_stages=stages,
                    instrument_sensitivity=sensitivity,
                )

            sta.channels.append(chan)

        net.stations.append(sta)
        networks.append(net)

    return Inventory(networks=networks, source="SUDS")  




def feature_to_pick(
    feature,
    descripttrace,
    location_code="",
):
    """
    Convert a parsed SUDS_FEATURE + its contextual DESCRIPTRACE
    into an ObsPy Pick.
    """

    sb = feature["struct_body"]

    # --- Phase mapping ---
    phase = SRC_PHASE_MAP.get(sb["obs_phase"], None)

    # --- Onset ---
    onset = None
    if sb["onset"] == "i":
        onset = PickOnset.impulsive
    elif sb["onset"] == "e":
        onset = PickOnset.emergent

    # --- Polarity ---
    polarity = None
    if sb["direction"] in ("U", "+"):
        polarity = PickPolarity.positive
    elif sb["direction"] in ("D", "-"):
        polarity = PickPolarity.negative

    # --- Waveform identity (AUTHORITATIVE) ---
    lid = descripttrace["longident"]

    waveform_id = WaveformStreamID(
        network_code=lid["network"],
        station_code=lid["station"],
        channel_code=lid["component"],
        location_code=location_code or "",
    )

    # --- Build Pick ---
    pick = Pick(
        time=UTCDateTime(sb["time"]),
        waveform_id=waveform_id,
        phase_hint=phase,
        onset=onset,
        polarity=polarity,
    )

    return pick




def suds_to_inventory_single_station(
    station_meta,
    instruments,
    location_code=""
):
    net_code = station_meta["network"]
    sta_code = station_meta["station"]

    net = Network(code=net_code)

    sta = Station(
        code=sta_code,
        latitude=station_meta["latitude_deg"],
        longitude=station_meta["longitude_deg"],
        elevation=station_meta["elevation_m"],
        site=Site(name=sta_code)
    )

    for chan_code, chan_meta in station_meta["channels"].items():

        key = f"{net_code}.{sta_code}.{chan_code}"
        inst = instruments.get(key)
        if inst is None:
            continue

        sb = inst["struct_body"]

        # ---- extract SUDS parameters ----
        dig_con = sb.get("dig_con")
        mot_con = sb.get("mot_con")
        gain_db = sb.get("gaindb", 0.0)

        # dB → linear
        gain_lin = 10 ** (gain_db / 20.0)

        # total sensitivity (counts per m/s)
        total_sensitivity = dig_con * mot_con * gain_lin

        # ---- Channel ----
        chan = Channel(
            code=chan_code,
            location_code=location_code,
            latitude=sta.latitude,
            longitude=sta.longitude,
            elevation=sta.elevation,
            depth=0.0
        )

        # NOTE: sampling rate must be set AFTER init
        if "sample_rate" in chan_meta:
            chan.sample_rate = chan_meta["sample_rate"]

        # ---- Response stages ----
        stages = []

        # Stage 1: digitizer gain (linear)
        stages.append(
            ResponseStage(
                stage_sequence_number=1,
                stage_gain=gain_lin,
                stage_gain_frequency=1.0,
                input_units="V",
                output_units="V"
            )
        )

        # Stage 2: counts per volt
        stages.append(
            ResponseStage(
                stage_sequence_number=2,
                stage_gain=dig_con,
                stage_gain_frequency=1.0,
                input_units="V",
                output_units="COUNTS"
            )
        )

        # Stage 3: volts per m/s (sensor)
        stages.append(
            ResponseStage(
                stage_sequence_number=3,
                stage_gain=mot_con,
                stage_gain_frequency=1.0,
                input_units="M/S",
                output_units="V"
            )
        )

        # ---- Instrument sensitivity ----
        sensitivity = InstrumentSensitivity(
            value=total_sensitivity,
            frequency=1.0,
            input_units="M/S",
            output_units="COUNTS"
        )

        chan.response = Response(
            response_stages=stages,
            instrument_sensitivity=sensitivity
        )

        sta.channels.append(chan)

    net.stations.append(sta)

    return Inventory(
        networks=[net],
        source="SUDS"
    )