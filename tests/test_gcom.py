from datetime import datetime
from pathlib import Path

import pytest

from gcom import CSWWrapper, GcomDownloader


def test_split_intervals_chunks_by_day_window() -> None:
    wrapper = CSWWrapper()

    intervals = wrapper._split_intervals(
        datetime(2024, 1, 1, 0, 0, 0),
        datetime(2024, 1, 8, 0, 0, 0),
        3,
    )

    assert intervals == [
        (datetime(2024, 1, 1, 0, 0, 0), datetime(2024, 1, 4, 0, 0, 0)),
        (datetime(2024, 1, 4, 0, 0, 0), datetime(2024, 1, 7, 0, 0, 0)),
        (datetime(2024, 1, 7, 0, 0, 0), datetime(2024, 1, 8, 0, 0, 0)),
    ]


def test_get_string_from_date_formats_milliseconds() -> None:
    wrapper = CSWWrapper()

    assert wrapper._get_string_from_date(datetime(2024, 1, 2, 3, 4, 5, 678900)) == "2024-01-02T03:04:05.678Z"


def test_fetch_data_raises_clear_error_for_non_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = CSWWrapper()

    class FakeResponse:
        status_code = 200
        text = "<html>bad gateway</html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            raise ValueError("no json")

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(RuntimeError, match="CSW request did not return JSON"):
        wrapper._fetch_data("https://example.com")


def test_get_filename_from_url_returns_basename(tmp_path: Path) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")

    assert downloader._get_filename_from_url("https://example.com/files/test-data.h5") == "test-data.h5"


def test_get_filename_from_url_raises_for_invalid_url(tmp_path: Path) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")

    with pytest.raises(ValueError):
        downloader._get_filename_from_url("https://example.com/")


def test_create_job_file_contains_login_and_urls(tmp_path: Path) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")

    job_file = downloader._create_job_file(["https://example.com/a.h5"])
    try:
        assert job_file.exists()
        contents = job_file.read_text(encoding="utf-8")
    finally:
        if job_file.exists():
            job_file.unlink()

    assert '"url": "https://gportal.jaxa.jp/gpr/auth?"' in contents
    assert '"username_selector": "#auth_account"' in contents
    assert '"download_dir": "/downloads"' in contents
    assert '"urls": ["https://example.com/a.h5"]' in contents


def test_run_playwright_download_builds_expected_docker_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    downloader = GcomDownloader(str(tmp_path / "download"), str(tmp_path / "workspace"), "user", "pass")
    job_file = tmp_path / "workspace" / "job.json"
    job_file.parent.mkdir(parents=True, exist_ok=True)
    job_file.write_text("{}", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run(command: list[str], cwd: Path, check: bool) -> object:
        captured["command"] = command
        captured["cwd"] = cwd
        captured["check"] = check

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    downloader._run_playwright_download(job_file)

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:3] == ["docker", "run", "--rm"]
    assert f"GPORTAL_USER={downloader._username}" in command
    assert f"GPORTAL_PASS={downloader._password}" in command
    assert f"JOB_PATH=/jobs/{job_file.name}" in command
    assert "bash" in command
    assert "-lc" in command
    assert "python3 -m pip install --quiet playwright==1.58.0 && python3 /work/playwright/download_gportal.py" in command
    assert "mcr.microsoft.com/playwright/python:v1.58.0-noble" in command
    assert captured["cwd"] == downloader._repo_root
    assert captured["check"] is False


def test_get_downloaded_file_paths_logs_progress_and_skips_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    download_dir = tmp_path / "download"
    workspace_dir = tmp_path / "workspace"
    download_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    existing_url = "https://example.com/files/existing.h5"
    missing_url = "https://example.com/files/missing.h5"
    (download_dir / "existing.h5").write_text("present", encoding="utf-8")

    downloader = GcomDownloader(str(download_dir), str(workspace_dir), "user", "pass")

    monkeypatch.setattr(downloader, "_ensure_docker", lambda: None)

    def fake_run_playwright_download(job_file: Path) -> None:
        job = job_file.read_text(encoding="utf-8")
        assert missing_url in job
        (download_dir / "missing.h5").write_text("downloaded", encoding="utf-8")

    monkeypatch.setattr(downloader, "_run_playwright_download", fake_run_playwright_download)

    paths = downloader.get_downloaded_file_paths([existing_url, missing_url])

    assert paths == [str(download_dir / "existing.h5"), str(download_dir / "missing.h5")]
    stdout = capsys.readouterr().out
    assert "[download] resolved 2 file path(s); 1 missing, 1 already present" in stdout
    assert "[download] downloading 1 missing file(s)" in stdout
    assert "[download] verified 2 downloaded file(s)" in stdout
