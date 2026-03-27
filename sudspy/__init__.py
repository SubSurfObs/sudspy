# sudspy/__init__.py

from .obspy import (
    read_suds_stream,
    read_suds_picks,
    read_suds_inv,
)

from .collections import (
    collect_instruments,
    collect_comments,
)

from .utils import (
    fast_merge_safe,
    print_suds_block_structure,
)

from .io import (
    parse_echopro_filename,
    scan_suds_file,
)

__all__ = [
    "read_suds_stream",
    "read_suds_picks",
    "read_suds_inv",
    "collect_instruments",
    "collect_comments",
    "fast_merge_safe",
    "parse_echopro_filename",
    "scan_suds_file",
]