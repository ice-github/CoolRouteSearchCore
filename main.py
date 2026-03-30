import os
from datetime import datetime

from gcom import CSWWrapper, GcomDownloader
from prefecture_bbox import get_prefecture_bbox


def gportal_username_and_password_from_env() -> tuple[str | None, str | None]:
    return os.environ.get("GPORTAL_USER"), os.environ.get("GPORTAL_PASS")


def run_example_download() -> list[str]:
    dataset_id = "10002019"
    utc_start = datetime(2024, 1, 1)
    utc_end = datetime(2024, 1, 2)
    bbox = get_prefecture_bbox("愛知")

    csw_wrapper = CSWWrapper()
    hdf5_urls = csw_wrapper.get_hdf5_urls(dataset_id, utc_start, utc_end, bbox)
    if not hdf5_urls:
        raise RuntimeError("no HDF5 URLs were found for the example query")

    username, password = gportal_username_and_password_from_env()
    downloader = GcomDownloader("download", "workspace", username or "", password or "")
    return downloader.get_downloaded_file_paths(hdf5_urls[:1])


def main() -> None:
    for path in run_example_download():
        print(path)


if __name__ == "__main__":
    main()
