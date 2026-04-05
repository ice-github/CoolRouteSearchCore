from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_gportal_example_download_writes_a_real_hdf5(downloaded_hdf5_path: Path) -> None:
    downloaded_path = downloaded_hdf5_path
    assert downloaded_path.exists()
    assert downloaded_path.stat().st_size > 0
