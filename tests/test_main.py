from datetime import datetime

import main


def test_gportal_username_and_password_from_env_reads_expected_variables(monkeypatch) -> None:
    monkeypatch.setenv("GPORTAL_USER", "demo-user")
    monkeypatch.setenv("GPORTAL_PASS", "demo-pass")

    assert main.gportal_username_and_password_from_env() == ("demo-user", "demo-pass")


def test_download_command_uses_prefecture_keyword_and_limit(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyCswWrapper:
        def get_hdf5_urls(self, dataset_id, utc_start, utc_end, bbox):
            captured["dataset_id"] = dataset_id
            captured["utc_start"] = utc_start
            captured["utc_end"] = utc_end
            captured["bbox"] = bbox
            return ["url-1", "url-2", "url-3"]

    class DummyDownloader:
        def __init__(self, download_dir, workspace_dir, username, password):
            captured["download_dir"] = download_dir
            captured["workspace_dir"] = workspace_dir
            captured["username"] = username
            captured["password"] = password

        def get_downloaded_file_paths(self, urls):
            captured["urls"] = urls
            return ["/tmp/file-1.h5", "/tmp/file-2.h5"]

    monkeypatch.setattr(main, "CSWWrapper", DummyCswWrapper)
    monkeypatch.setattr(main, "GcomDownloader", DummyDownloader)
    monkeypatch.setattr(main, "get_prefecture_bbox", lambda keyword: [1, 2, 3, 4])
    monkeypatch.setenv("GPORTAL_USER", "demo-user")
    monkeypatch.setenv("GPORTAL_PASS", "demo-pass")

    exit_code = main.main(
        [
            "download",
            "--prefecture",
            "京都",
            "--dataset-id",
            "dataset-123",
            "--start",
            "2024-01-03",
            "--end",
            "2024-01-04",
            "--limit",
            "2",
        ]
    )

    assert exit_code == 0
    assert captured["dataset_id"] == "dataset-123"
    assert captured["utc_start"] == datetime(2024, 1, 3)
    assert captured["utc_end"] == datetime(2024, 1, 4)
    assert captured["bbox"] == [1, 2, 3, 4]
    assert captured["urls"] == ["url-1", "url-2"]
    assert captured["download_dir"] == "download"
    assert captured["workspace_dir"] == "workspace"
    assert captured["username"] == "demo-user"
    assert captured["password"] == "demo-pass"


def test_analyze_command_uses_area_name_spacing_and_dates(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_compute_lst_point_means(area_name, start, end, spacing_m, output_path):
        captured["area_name"] = area_name
        captured["start"] = start
        captured["end"] = end
        captured["spacing_m"] = spacing_m
        captured["output_path"] = output_path
        return output_path

    monkeypatch.setattr(main, "compute_lst_point_means", fake_compute_lst_point_means)

    exit_code = main.main(
        [
            "analyze",
            "--area-name",
            "京都府京都市",
            "--start",
            "2025-07-01",
            "--end",
            "2025-08-31",
            "--spacing-m",
            "1000",
        ]
    )

    assert exit_code == 0
    assert captured["area_name"] == "京都府京都市"
    assert captured["start"] == datetime(2025, 7, 1)
    assert captured["end"] == datetime(2025, 8, 31)
    assert captured["spacing_m"] == 1000
    assert captured["output_path"] == "workspace/analysis/京都府京都市/lst_mean_local_1000m.csv"
