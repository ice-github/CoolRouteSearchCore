import os
from datetime import datetime
from pathlib import Path

import pytest
from shapely.geometry import Point

from gcom import CSWWrapper, GcomDownloader
from prefecture_bbox import get_prefecture_bbox


def _integration_enabled() -> bool:
    return os.environ.get("RUN_GPORTAL_INTEGRATION") == "1"


def _integration_credentials() -> tuple[str, str]:
    username = os.environ.get("GPORTAL_USER")
    password = os.environ.get("GPORTAL_PASS")
    if not username or not password:
        pytest.skip("GPORTAL_USER and GPORTAL_PASS are required for the integration test")
    return username, password


@pytest.fixture(scope="session")
def downloaded_hdf5_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not _integration_enabled():
        pytest.skip("set RUN_GPORTAL_INTEGRATION=1 to run the real G-Portal integration tests")

    username, password = _integration_credentials()
    scenes = CSWWrapper().get_hdf5_scenes(
        "10002019",
        datetime(2024, 1, 1),
        datetime(2024, 1, 2),
        get_prefecture_bbox("愛知県"),
    )
    assert scenes, "expected at least one GCOM-C HDF5 scene from CSW"

    nagoya_point = Point(136.8855, 35.1077)
    target_scene = next((scene for scene in scenes if scene.geometry_wgs84.covers(nagoya_point)), None)
    assert target_scene is not None, (
        "expected a GCOM-C HDF5 scene covering the Nagoya integration point; "
        f"candidates={[scene.identifier for scene in scenes]}"
    )

    base_dir = tmp_path_factory.mktemp("gportal_integration")
    downloader = GcomDownloader(str(base_dir / "download"), str(base_dir / "workspace"), username, password)
    paths = downloader.get_downloaded_file_paths([target_scene.url])

    assert len(paths) == 1
    downloaded_path = Path(paths[0])
    assert downloaded_path.exists()
    assert downloaded_path.stat().st_size > 0
    return downloaded_path
