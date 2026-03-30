import os
from datetime import datetime
from pathlib import Path

import pytest

from gcom import CSWWrapper, GcomDownloader
from prefecture_bbox import get_prefecture_bbox


pytestmark = pytest.mark.integration


def _integration_enabled() -> bool:
    return os.environ.get("RUN_GPORTAL_INTEGRATION") == "1"


def test_gportal_example_download_writes_a_real_hdf5(tmp_path: Path) -> None:
    if not _integration_enabled():
        pytest.skip("set RUN_GPORTAL_INTEGRATION=1 to run the real G-Portal download test")

    username = os.environ.get("GPORTAL_USER")
    password = os.environ.get("GPORTAL_PASS")
    if not username or not password:
        pytest.skip("GPORTAL_USER and GPORTAL_PASS are required for the integration test")

    urls = CSWWrapper().get_hdf5_urls(
        "10002019",
        datetime(2024, 1, 1),
        datetime(2024, 1, 2),
        get_prefecture_bbox("愛知"),
    )
    assert urls, "expected at least one GCOM-C HDF5 URL from CSW"

    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), username, password)
    paths = downloader.get_downloaded_file_paths(urls[:1])

    assert len(paths) == 1
    downloaded_path = Path(paths[0])
    assert downloaded_path.exists()
    assert downloaded_path.stat().st_size > 0
