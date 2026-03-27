from pathlib import Path
import pytest

DATA = Path(__file__).parent.parent / "data"

@pytest.fixture
def data_dir():
    return DATA

@pytest.fixture
def single_chan_file(data_dir):
    return data_dir / "single_chan" / "locu_seismosphere.sud"

@pytest.fixture
def multi_station_file(data_dir):
    return data_dir / "multi_stations" / "2_stations.sud"

@pytest.fixture
def sequence_dir(data_dir):
    """10 one-minute files, SS=02, with gap at minute 0005."""
    return data_dir / "multi_files" / "sequence"

@pytest.fixture
def overlaps_dir(data_dir):
    """Three files: disk 3-ch (SS=02), telemetry 1-ch (SS=02), triggered accel (SS=48)."""
    return data_dir / "multi_files" / "overlaps"
