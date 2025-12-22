# sudspy

`sudspy` is a lightweight Python package for reading and converting **PC‑SUDS / SRC SUDS** seismic data files into modern **ObsPy** objects (Streams, Picks, and Inventory metadata).  
It is designed for robustness, transparency, and batch‑style archive conversion rather than “black box” magic.

---

## 1. What is PC-SUDS (very briefly)

SUDS (Seismic Unified Data System) files, as produced by SRC instuments (e.g. EchoPro / Gecko / Waves), are **self‑describing binary streams** made up of a sequence of tagged blocks.

Each block has:

- a **struct tag** (type + sizes),
- a **struct body** (metadata),
- optionally a **data payload** (e.g. waveform samples).

Important properties:

- SUDS files are **appendable** → simple UNIX concatenation (`cat *.dmx > combined.sud`) is valid.
- There is **no global header**.
- Context is defined by **ordering** (e.g. FEATURE follows DESCRIPTRACE).
- There is **no true location code concept** in SUDS.

Because SUDS is a linear tagged format:

- Each block is self‑contained.
- Block sizes are explicit.
- Readers scan sequentially until EOF.

As a result, concatenating minute‑long files into day‑long files works without rewriting metadata.

---

## 2. sudspy design philosophy

`sudspy` is intentionally layered:

### Layer 1 — Raw blocks

- Read binary SUDS files.
- Yield `SudsBlock` objects with:
  - `struct_type`
  - `struct_body`
  - raw `data`
  - file offset

No interpretation beyond parsing.

### Layer 2 — Struct parsers & collectors

- Decode specific SUDS structs (STATIONCOMP, DESCRIPTRACE, FEATURE, INSTRUMENT, COMMENT).
- Collect related metadata using **ordering rules**, not assumptions.

Examples:

- `collect_instruments()`
- `collect_comments()`
- `collect_stations()`

Still no ObsPy objects here.

### Layer 3 — ObsPy adapters

High‑level convenience functions that convert SUDS → ObsPy:

- **Waveforms**
  - `read_suds_stream()` → `Stream`
- **Picks**
  - `read_suds_picks()` → list of `Pick`
- **Metadata**
  - `read_suds_inv()` → `Inventory`

This is the only layer that depends on ObsPy.

---

## 3. Location codes

SUDS has no native location code.

`sudspy` handles this consistently:

- All internal processing uses `(NET, STA, CHAN)` only.
- Location codes are injected **once**, at the ObsPy boundary:
  - either as `""` (empty),
  - or via `default_location="00"`,
  - or via a user‑supplied mapping.

This avoids ambiguity and metadata drift.

---

## 4. Typical usage

### Read waveforms

```python
import sudspy

st = sudspy.read_suds_stream(
    "data/day01.sud",
    default_location="00"
)

print(st)
```

### Read picks

```python
picks = sudspy.read_suds_picks(
    "data/day01.sud",
    location_code="00"
)

print(len(picks), "picks")
```

### Build StationXML inventory

```python
inv = sudspy.read_suds_inv(
    "data/day01.sud",
    default_location="00"
)

print(inv)
```

### Remove instrument response

```python
st.remove_response(
    inventory=inv,
    output="VEL",
    zero_mean=True,
    taper=True
)
```

### Fast merging of concatenated minute files

```python
from sudspy import fast_merge_safe
from obspy import Stream

merged = Stream()

for ch in sorted({tr.stats.channel for tr in st}):
    traces = [tr for tr in st if tr.stats.channel == ch]
    merged += fast_merge_safe(traces, gap_fill="nan")
```

---

