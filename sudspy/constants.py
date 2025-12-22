# constants.py
import numpy as np
from typing import Dict

SUDS_STRUCT_TYPES = {
    0:  "NO_STRUCT",
    1:  "STAT_IDENT",
    2:  "STRUCTTAG",
    3:  "TERMINATOR",
    4:  "EQUIPMENT",
    5:  "STATIONCOMP",
    6:  "MUXDATA",
    7:  "DESCRIPTRACE",
    8:  "LOCTRACE",
    9:  "CALIBRATION",
    10: "FEATURE",
    11: "RESIDUAL",
    12: "EVENT",
    13: "EV_DESCRIPT",
    14: "ORIGIN",
    15: "ERROR",
    16: "FOCALMECH",
    17: "MOMENT",
    18: "VELMODEL",
    19: "LAYERS",
    20: "COMMENT",
    21: "PROFILE",
    22: "SHOTGATHER",
    23: "CALIB",
    24: "COMPLEX",
    25: "TRIGGERS",
    26: "TRIGSETTING",
    27: "EVENTSETTING",
    28: "DETECTOR",
    29: "ATODINFO",
    30: "TIMECORRECTION",
    31: "INSTRUMENT",
    32: "CHANSET",
}


SRC_PHASE_MAP = {
    50: "P",
    51: "PG",
    100: "S",
    101: "S",
    10: "AMP",
    13: "S-Amp",
}

def linear_to_db_power(linear_gain):
    """Convert linear power gain to dB."""
    # Ensure the input is positive to avoid math domain errors
    if linear_gain <= 0:
        return float('-inf') 
    return 20 * np.log10(linear_gain)



def db_to_linear(db):
    return 10 ** (db / 20.0)