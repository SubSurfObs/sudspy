"""Speed bench: per-file vs concatenated SUDS reading over a whole LOCAL day.

Reads from local disk (not the flaky USB) to measure true parse/decode
throughput. Forces sample decode (tr.data) so timings are honest -- header-only
reads are misleadingly fast.

Usage:
    python bench_outu.py [day_dir]      # default: ./day_2024-12-10
"""
import sys
import time
import glob
import os
import tempfile
import sudspy

day = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "day_2024-12-10")
files = sorted(glob.glob(os.path.join(day, "**", "*.dmx"), recursive=True))
if not files:
    sys.exit(f"no .dmx under {day} (recreate the whole-day fixture -- see README)")
nbytes = sum(os.path.getsize(f) for f in files)
print(f"{len(files)} files, {nbytes/1e6:.1f} MB under {day}\n")


def decoded(st):
    return sum(len(tr.data) for tr in st)   # force decode for an honest timing


# --- per-file ---
t = time.time()
ntr = nsamp = errs = 0
for f in files:
    try:
        st = sudspy.read_suds_stream(f)
        ntr += len(st)
        nsamp += decoded(st)
    except Exception:
        errs += 1
per_s = time.time() - t
print(f"per-file : {ntr} traces, {nsamp} samples, {errs} read-errors, {per_s:.1f}s "
      f"({nbytes/1e6/per_s:.2f} MB/s)")

# --- concat: cat all files into one temp file, single read.
#     (Mirrors the adapter approach: copy/cat local files, then one read.) ---
t = time.time()
tmp = tempfile.NamedTemporaryFile(suffix=".dmx", delete=False)
for f in files:
    with open(f, "rb") as fh:
        tmp.write(fh.read())
tmp.close()
build_s = time.time() - t
t = time.time()
try:
    st = sudspy.read_suds_stream(tmp.name)
    n2, s2, cat_err = len(st), decoded(st), None
except Exception as e:
    n2, s2, cat_err = -1, -1, f"{type(e).__name__}: {e}"
read_s = time.time() - t
os.unlink(tmp.name)
print(f"concat   : build {build_s:.1f}s + read {read_s:.1f}s, {n2} traces, {s2} samples, err={cat_err}")
if cat_err is None and read_s > 0:
    if n2 == ntr and s2 == nsamp:
        print(f"  concat read vs per-file (read only): {per_s/read_s:.1f}x")
    else:
        # strict=False (sudspy's default) stops at the first inter-file bad-sync,
        # so a cat-of-day silently truncates on real EchoPro data — printing a
        # speedup over a partial read would be misleading.
        print(f"  WARNING: concat returned {n2}/{ntr} traces, {s2}/{nsamp} samples "
              f"({100*s2/nsamp:.1f}% of per-file). Speedup suppressed: "
              f"strict=False stops at the first inter-file bad-sync.")
