# -------------------------
# Layer 1: raw blocks (immutable)
# -------------------------



from dataclasses import dataclass
from typing import Optional, Dict, Any, Iterator, Iterable, List, Tuple, Union
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


def print_suds_block_structure(path, max_blocks=None):
    """
    Diagnostic utility: print the linear block structure of a SUDS file,
    grouped by DESCRIPTRACE context.

    This function DOES NOT infer relationships – it only shows ordering.
    """

    current_trace = None
    count = 0

    print(f"\nSUDS structure walk for: {path}\n")

    for block in iter_suds_blocks(path):
        stype = block.struct_type
        name = SUDS_STRUCT_TYPES.get(stype, f"UNKNOWN({stype})")

        # ---- DESCRIPTRACE defines a new waveform context ----
        if stype == 7:
            desc = parse_descriptrace_struct(block)
            lid = desc.get("longident") or desc.get("statident")

            current_trace = lid
            print("\n" + "-" * 72)
            print(f"DESCRIPTRACE @ offset {block.offset}")
            print(f"  waveform: {lid['network']}.{lid['station']}..{lid['component']}")
            print("-" * 72)

        else:
            indent = "  "
            print(
                f"{indent}{name:<12} @ offset {block.offset}"
                + (f"   [context={current_trace['component']}]" if current_trace else "")
            )

            # Optional: show key details for some structs
            if stype == 10:  # FEATURE
                feat = parse_feature_struct(block)
                sb = feat["struct_body"]
                phase = SRC_PHASE_MAP.get(sb["obs_phase"], sb["obs_phase"])
                print(
                    f"{indent*2}phase={phase}  "
                    f"onset={sb['onset']}  "
                    f"time={sb['time']}"
                )

            elif stype == 20:  # COMMENT
                com = parse_comment_struct(block)
                print(
                    f"{indent*2}length={com['struct_body']['length']}"
                )

        count += 1
        if max_blocks and count >= max_blocks:
            print("\n[truncated]")
            break



def iter_suds_blocks(path: str) -> Iterable[SudsBlock]:
    """
    Layer 1 (IMMUTABLE): Yield raw SUDS blocks: tag + struct_body + data.
    No interpretation, no parsing.
    """
    with open(path, "rb") as f:
        offset = 0
        while True:
            tag_raw = f.read(12)
            if len(tag_raw) < 12:
                break

            tag = parse_structtag(tag_raw)
            if tag["sync"] != b"S" or tag["machine"] != b"6":
                raise RuntimeError(f"Bad SUDS sync/machine at offset {offset}: {tag}")

            struct_body = f.read(tag["struct_length"])
            if len(struct_body) != tag["struct_length"]:
                raise EOFError("Unexpected EOF reading struct_body")

            data = f.read(tag["data_length"]) if tag["data_length"] > 0 else b""
            if len(data) != tag["data_length"]:
                raise EOFError("Unexpected EOF reading data")

            yield SudsBlock(
                struct_type=tag["struct_type"],
                tag=tag,
                struct_body=struct_body,
                data=data,
                offset=offset,
            )

            offset += 12 + tag["struct_length"] + tag["data_length"]
