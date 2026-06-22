# -------------------------
# Layer 1: raw blocks (immutable)
# -------------------------



from dataclasses import dataclass
from typing import Optional, Dict, Any, Iterator, Iterable, List, Tuple, Union
try:
    from isal import igzip as gzip   # 2-3x faster than stdlib zlib on AVX2 CPUs
except ImportError:                  # graceful fallback if isal isn't installed
    import gzip
import struct
from .constants import SRC_PHASE_MAP, SUDS_STRUCT_TYPES

@dataclass
class SudsBlock:
    struct_type: int
    tag: Dict[str, Any]          # decoded SUDS_STRUCTTAG
    struct_body: bytes           # exactly tag["struct_length"] bytes
    data: bytes                  # exactly tag["data_length"] bytes (may be empty)
    offset: int                  # file offset of tag


def parse_structtag(raw12: bytes) -> Dict[str, Any]:
    # PC-SUDS manual tag struct: <ccHII  (little endian)
    sync, machine, struct_type, struct_length, data_length = struct.unpack("<ccHII", raw12)
    return {
        "sync": sync,
        "machine": machine,
        "struct_type": int(struct_type),
        "struct_length": int(struct_length),
        "data_length": int(data_length),
    }

def list_suds_struct_types(path):
    """
    Return sorted unique SUDS struct_type IDs found in a file.
    """
    ids = set()
    for block in iter_suds_blocks(path):
        ids.add(block.struct_type)
    return sorted(ids)

def print_suds_struct_summary(path):
    ids = list_suds_struct_types(path)

    print(f"SUDS structure summary for: {path}")
    for sid in ids:
        name = SUDS_STRUCT_TYPES.get(sid, "UNKNOWN")
        print(f"  {sid:2d}  {name}")






def iter_suds_blocks(
    path: str,
    *,
    skip_data: bool = False,
    strict: bool = True,
    diag: dict = None,
) -> Iterable[SudsBlock]:
    """
    Layer 1 (IMMUTABLE): Yield raw SUDS blocks: tag + struct_body + data.
    No interpretation, no parsing.

    Parameters
    ----------
    path : str
        Path to SUDS file. Gzip-compressed files (.gz) are handled transparently.
    skip_data : bool
        If True, read and discard data payloads rather than returning them.
        block.data will be b"" for all blocks. Use for fast metadata-only scans.
    strict : bool
        If True (default), raise on bad sync bytes or truncated reads.
        If False, stop iteration cleanly on first error (useful for partial files
        and for recovering files with valid data followed by trailing junk).
    diag : dict, optional
        If provided, populated with stop diagnostics so the caller can tell
        "clean EOF" from "stopped early at trailing junk / truncation":
          n_blocks        : number of blocks yielded
          last_good_offset : byte offset after the last good block
          stop_reason     : 'clean_eof' | 'bad_sync' | 'short_tag'
                            | 'short_struct' | 'short_data'
        Only meaningful when strict=False (strict=True raises instead).
    """
    if diag is not None:
        diag["n_blocks"] = 0
        diag["last_good_offset"] = 0
        diag["stop_reason"] = "clean_eof"

    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as f:
        offset = 0
        while True:
            tag_raw = f.read(12)
            if len(tag_raw) < 12:
                if diag is not None and len(tag_raw) != 0:
                    diag["stop_reason"] = "short_tag"
                break

            tag = parse_structtag(tag_raw)
            if tag["sync"] != b"S" or tag["machine"] != b"6":
                if strict:
                    raise RuntimeError(f"Bad SUDS sync/machine at offset {offset}: {tag}")
                if diag is not None:
                    diag["stop_reason"] = "bad_sync"
                break

            struct_body = f.read(tag["struct_length"])
            if len(struct_body) != tag["struct_length"]:
                if strict:
                    raise EOFError("Unexpected EOF reading struct_body")
                if diag is not None:
                    diag["stop_reason"] = "short_struct"
                break

            if tag["data_length"] > 0:
                if skip_data:
                    f.read(tag["data_length"])  # read and discard (gzip-compatible)
                    data = b""
                else:
                    data = f.read(tag["data_length"])
                    if len(data) != tag["data_length"]:
                        if strict:
                            raise EOFError("Unexpected EOF reading data")
                        if diag is not None:
                            diag["stop_reason"] = "short_data"
                        break
            else:
                data = b""

            yield SudsBlock(
                struct_type=tag["struct_type"],
                tag=tag,
                struct_body=struct_body,
                data=data,
                offset=offset,
            )

            offset += 12 + tag["struct_length"] + tag["data_length"]
            if diag is not None:
                diag["n_blocks"] += 1
                diag["last_good_offset"] = offset
