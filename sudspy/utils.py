# processing.py

from typing import List
import numpy as np
from .blocks import iter_suds_blocks
from .parsers import (
    parse_descriptrace_struct,
    parse_feature_struct,
    parse_comment_struct,
)
from .constants import SUDS_STRUCT_TYPES, SRC_PHASE_MAP
from .collections import collect_comments

def print_suds_block_structure(path, max_blocks=None):
    """
    Print a simple linear listing of SUDS blocks:
    struct_type, struct name, and byte offset.
    """

    print(f"\nSUDS block listing for: {path}\n")

    count = 0
    for block in iter_suds_blocks(path):
        stype = block.struct_type
        name = SUDS_STRUCT_TYPES.get(stype, "UNKNOWN")

        print(f"{stype:3d}  {name:<14}  offset={block.offset}")

        count += 1
        if max_blocks and count >= max_blocks:
            print("\n[truncated]")
            break

def fast_merge_safe(
    traces,
    gap_fill="error",   # "error" | "zeros" | "nan"
    overlap="trim",     # "error" | "trim" | "replace" | "ignore"
    tol=1e-6,
):
    """
    Fast merge of ObsPy Traces assuming constant sample rate.

    Parameters
    ----------
    traces : list[Trace]
        Traces must be same channel, sorted or unsorted.
    gap_fill : str
        How to handle gaps:
        - "error": raise ValueError on gap
        - "zeros": fill gaps with zeros
        - "nan": fill gaps with NaNs
    tol : float
        Time tolerance in seconds for gap detection.

    Returns
    -------
    Trace
        Single merged Trace with correct timing.
    """

    if not traces:
        raise ValueError("No traces provided")

    # Sort by start time
    traces = sorted(traces, key=lambda tr: tr.stats.starttime)

    # Reference trace
    out = traces[0].copy()
    sr = out.stats.sampling_rate
    dt = out.stats.delta

    data = [out.data.astype(np.float32)]
    npts = out.stats.npts

    for tr in traces[1:]:
        if tr.stats.sampling_rate != sr:
            raise ValueError("Sample rate mismatch")

        expected_start = out.stats.starttime + npts * dt
        gap = tr.stats.starttime - expected_start

        if abs(gap) <= tol:
            # contiguous
            pass

        elif gap > tol:
            # true gap
            if gap_fill == "error":
                raise ValueError(f"Gap detected at {tr.stats.starttime}")

            ngap = int(round(gap / dt))
            if ngap <= 0:
                raise ValueError("Computed non-positive gap length")

            if gap_fill == "zeros":
                filler = np.zeros(ngap, dtype=np.float32)
            elif gap_fill == "nan":
                filler = np.full(ngap, np.nan, dtype=np.float32)
            else:
                raise ValueError(f"Unknown gap_fill mode: {gap_fill}")

            data.append(filler)
            npts += ngap

        else:
            # overlap
            if overlap == "error":
                raise ValueError(f"Overlap detected at {tr.stats.starttime}")
        
            overlap_samples = int(round((-gap) / dt))
            if overlap_samples <= 0:
                continue
        
            if overlap == "ignore":
                continue
        
            elif overlap == "trim":
                tr_data = tr.data[overlap_samples:]
                data.append(tr_data.astype(np.float32))
                npts += len(tr_data)
                continue
        
            elif overlap == "replace":
                # remove overlapping samples from existing data
                for i in range(len(data)):
                    data[i] = data[i][:-overlap_samples]
                npts -= overlap_samples
                data.append(tr.data.astype(np.float32))
                npts += tr.stats.npts
                continue
        
            else:
                raise ValueError(f"Unknown overlap mode: {overlap}")

        data.append(tr.data.astype(np.float32))
        npts += tr.stats.npts

    out.data = np.concatenate(data)
    out.stats.npts = len(out.data)

    return out


    # --- tiny smoke test helper ---
def test_read_suds_inv(path, loc="00"):
    inv = read_suds_inv(path, default_location=loc)
    print(inv)
    # show a couple of channel codes and whether response exists
    for net in inv.networks:
        for sta in net.stations:
            for ch in sta.channels[:3]:
                code = f"{net.code}.{sta.code}.{ch.location_code}.{ch.code}"
                has_resp = ch.response is not None
                print(f"{code}  response={has_resp}")
    return inv


def pretty_print_comment(comment: dict, indent: int = 2) -> None:
    pad = " " * indent
    sb = comment["struct_body"]

    print("SUDS_COMMENT")
    print(f"{pad}offset : {comment['offset']}")
    print(f"{pad}refer  : {sb['refer']}")
    print(f"{pad}item   : {sb['item']}")
    print(f"{pad}length : {sb['length']}")
    print(f"{pad}text   :")

    for line in sb["text"].splitlines():
        print(f"{pad*2}{line}")
        


def test_collect_comments(path: str, max_print: int = 3) -> None:
    d = collect_comments(path)
    print(f"Found {sum(len(v) for v in d.values())} COMMENT blocks across {len(d)} channel keys")
    n = 0
    for key, lst in d.items():
        print(f"\n== {key} ==  ({len(lst)} comment(s))")
        pretty_print_comment(lst[0])
        n += 1
        if n >= max_print:
            break


