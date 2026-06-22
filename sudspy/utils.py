# processing.py

from typing import List
import numpy as np
from obspy import Stream, Trace
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


def fast_merge_split(stream, overlap: str = "trim") -> Stream:
    """O(N log N) numpy-direct replacement for the
    ``Stream.merge(method=1, fill_value=None) + .split()`` combo.

    Groups input traces by id; within each group:

      1. sort by starttime               -> O(N log N)
      2. walk forward keeping a running concat buffer
         - contiguous (within half-sample tol) -> append
         - gap                                  -> emit current run, start new
         - overlap                              -> see ``overlap`` policy
      3. flush the final run

    Returns a multi-trace ``Stream`` with no internal gaps within any trace
    and no overlapping samples across traces of the same id — the same shape
    that ``Stream.merge(method=1) + .split()`` produces today, but:

      - O(N log N) sort + O(N) walk, not O(N^2) pairwise merge;
      - preserves the input dtype (no float coercion);
      - configurable overlap policy.

    Parameters
    ----------
    stream : obspy.Stream
        Input traces. May contain traces from multiple ids; each id is
        merged independently.
    overlap : {"trim", "error", "ignore"}, default "trim"
        Policy for overlapping samples:
        - "trim"    : drop the leading overlapping samples from the later
                      trace (matches ``merge(method=1)`` on typical seismic
                      data where overlap is sub-sample clock drift).
        - "error"   : raise ``ValueError`` on detected overlap.
        - "ignore"  : skip the later overlapping trace entirely.

    Notes
    -----
    The only semantic divergence from obspy's merge+split is the overlap
    policy: obspy's ``method=1`` averages overlapping samples; this trims
    them from the later trace (prefer earlier). For typical clock-drift
    overlap (sub-sample) the choice produces byte-identical output on real
    data; for intentional multi-source overlap (disk + telemetry covering
    the same minutes), dedup upstream before calling.
    """
    if overlap not in ("trim", "error", "ignore"):
        raise ValueError(f"Unknown overlap mode: {overlap!r}")

    out = Stream()

    # Group by trace id
    by_id: dict = {}
    for tr in stream:
        by_id.setdefault(tr.id, []).append(tr)

    for trace_id, traces in by_id.items():
        traces.sort(key=lambda t: t.stats.starttime)

        # Per-run state — template tracks the trace that started the run
        # so we use the right sampling_rate/etc when emitting, even if a
        # later trace in the same id arrives at a different rate.
        run_template: Trace | None = None
        run_start = None
        run_samples: list = []
        run_end = None  # endtime of the last sample in current run

        for tr in traces:
            if len(tr.data) == 0:
                continue

            tr_start = tr.stats.starttime
            tr_end = tr.stats.endtime

            if run_template is None:
                run_template = tr
                run_start = tr_start
                run_samples = [tr.data]
                run_end = tr_end
                continue

            # Rate change within an id is anomalous but tolerated: flush
            # the current run and start a new one with the new template.
            if tr.stats.sampling_rate != run_template.stats.sampling_rate:
                out += _emit_run(run_template, run_start, run_samples)
                run_template = tr
                run_start = tr_start
                run_samples = [tr.data]
                run_end = tr_end
                continue

            delta = run_template.stats.delta
            gap_tol = delta * 0.5
            gap_seconds = float(tr_start - run_end) - delta  # >0 gap, <0 overlap

            if gap_seconds > gap_tol:
                # Real gap → flush current run, start a new one.
                out += _emit_run(run_template, run_start, run_samples)
                run_template = tr
                run_start = tr_start
                run_samples = [tr.data]
                run_end = tr_end
            elif gap_seconds < -gap_tol:
                # Overlap.
                if overlap == "error":
                    raise ValueError(
                        f"Overlap detected at {tr_start} for {trace_id} "
                        f"({-gap_seconds:.6f} s)"
                    )
                if overlap == "ignore":
                    continue
                # overlap == "trim": drop leading samples from the later trace.
                n_skip = int(round(-gap_seconds / delta))
                if n_skip < len(tr.data):
                    run_samples.append(tr.data[n_skip:])
                    run_end = tr_end
                # else: the later trace is fully overlapped — drop entirely.
            else:
                # Contiguous (within half-sample tolerance).
                run_samples.append(tr.data)
                run_end = tr_end

        # Flush the final run.
        if run_template is not None and run_samples:
            out += _emit_run(run_template, run_start, run_samples)

    return out


def _emit_run(template: Trace, start, samples_list: list) -> Trace:
    """Build a Trace by concatenating ``samples_list``.

    Non-data metadata is copied from ``template.stats``; ``starttime`` and
    ``npts`` are recomputed. Output dtype follows ``np.concatenate`` (common
    dtype of the inputs), which preserves int dtypes on the SUDS read path.
    """
    data = np.concatenate(samples_list) if len(samples_list) > 1 else samples_list[0]
    stats = template.stats.copy()
    stats.starttime = start
    stats.npts = len(data)
    return Trace(data=data, header=stats)


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


