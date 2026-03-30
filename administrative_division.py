import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests
import shapefile
from bs4 import BeautifulSoup, element


class TopInfo:
    _url = "https://nlftp.mlit.go.jp/ksj/index.html"

    def __init__(self) -> None:
        response = requests.get(self._url, timeout=120)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, "html.parser")

        main_tag = soup.find("main")
        if main_tag is None:
            raise RuntimeError("failed to parse MLIT top page")

        collapsibles = main_tag.find_all(class_="collapsible")
        data: dict[str, dict[str, list[tuple[str, str]]]] = {}

        for collapsible in collapsibles:
            header = collapsible.find("div", class_="collapsible-header")
            if header is None:
                continue

            title = header.find("p")
            if title is None:
                continue

            category_name = "".join(
                str(item) for item in title.contents if isinstance(item, str)
            ).strip()

            body = collapsible.find("div", class_="collapsible-body")
            if body is None:
                continue

            sub_category = body.find("div", class_="paddingAll")
            if sub_category is None:
                continue

            sub_data: dict[str, list[tuple[str, str]]] = {}
            while sub_category is not None:
                name_tag = sub_category.find("span")
                if name_tag is None:
                    break

                sub_category_name = name_tag.text.strip()
                sub_category_top = sub_category.find_next("div", class_="row")
                if sub_category_top is None:
                    break

                item_links: list[element.Tag] = sub_category_top.find_all("a")
                sub_data[sub_category_name] = [
                    (link.text.strip(), urljoin(self._url, link["href"]))
                    for link in item_links
                    if link.has_attr("href")
                ]
                sub_category = sub_category.find_next("div", class_="paddingAll")

            if sub_data:
                data[category_name] = sub_data

        self._data = data

    def get_category_names(self) -> list[str]:
        return list(self._data.keys())

    def get_sub_category_names(self, category_name: str) -> list[str]:
        return list(self._data.get(category_name, {}).keys())

    def get_items(self, category_name: str, sub_category_name: str) -> list[tuple[str, str]]:
        return self._data.get(category_name, {}).get(sub_category_name, [])


@dataclass
class AdministrativeDivision:
    prefecture_name: str
    shp_path: Path


class AdministrativeDivisionInfo:
    @dataclass
    class ZipFileInfo:
        prefecture_name: str
        url: str
        date_str: str
        size_str: str
        filename: str

    def __init__(self, download_dir: str, workspace_dir: str) -> None:
        self._download_dir = Path(download_dir)
        self._workspace_dir = Path(workspace_dir)
        self._download_dir.mkdir(parents=True, exist_ok=True)
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

        top_info = TopInfo()
        category_name = "2. 政策区域"
        sub_category_name = "行政地域"
        items = top_info.get_items(category_name, sub_category_name)
        prefecture_list_url = next(
            (url for name, url in items if name == "行政区域（ポリゴン）"),
            None,
        )
        if prefecture_list_url is None:
            raise RuntimeError("failed to find MLIT administrative division item")

        self._zip_files = self._parse_prefecture_urls(prefecture_list_url)

    def get_prefecture_names(self) -> list[str]:
        return list(self._zip_files.keys())

    def _get_zip_url(self, data_str: str) -> str:
        values = [value.strip().strip("'") for value in data_str.split(",")]
        if len(values) < 3:
            raise RuntimeError("failed to parse MLIT download url")
        return values[2]

    def _parse_prefecture_urls(self, url: str) -> dict[str, ZipFileInfo]:
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, "html.parser")

        main = soup.find("main")
        if main is None:
            raise RuntimeError("failed to parse MLIT prefecture page")

        jmap = main.find(id="Jmap")
        if jmap is None:
            raise RuntimeError("failed to find MLIT prefecture map section")

        table = jmap.find_next_sibling("table", class_="responsive-table")
        if table is None:
            raise RuntimeError("failed to find MLIT prefecture table")

        zip_files: dict[str, AdministrativeDivisionInfo.ZipFileInfo] = {}
        for row in table.find_all("tr")[1:]:
            cells: list[element.Tag] = row.find_all("td")
            if len(cells) < 6:
                continue

            prefecture_name = cells[0].text.strip()
            date_str = cells[2].text.strip()
            size_str = cells[3].text.strip()
            filename = cells[4].text.strip()
            button = cells[5].find("a")
            if button is None or not button.has_attr("onclick"):
                continue

            onclick = button["onclick"]
            start = onclick.find("DownLd(")
            end = onclick.find(");", start)
            if start == -1 or end == -1:
                continue

            data_str = onclick[start + len("DownLd(") : end]
            zip_files[prefecture_name] = self.ZipFileInfo(
                prefecture_name=prefecture_name,
                url=urljoin(url, self._get_zip_url(data_str)),
                date_str=date_str,
                size_str=size_str,
                filename=filename,
            )

        return zip_files

    def _download_file(self, zip_info: ZipFileInfo, save_path: Path) -> None:
        if save_path.exists():
            return

        response = requests.get(zip_info.url, timeout=300)
        response.raise_for_status()
        save_path.write_bytes(response.content)

    def _extract_file(self, zip_path: Path, extract_path: Path) -> None:
        extract_path.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            zip_file.extractall(extract_path)

    def _find_files_in_dir(self, directory: Path, extension: str) -> list[Path]:
        matches: list[Path] = []
        for root, _, files in os.walk(directory):
            for filename in files:
                if filename.endswith(extension):
                    matches.append(Path(root) / filename)
        return matches

    def get_administrative_division(self, prefecture_keyword: str) -> AdministrativeDivision:
        matched_name = next(
            (name for name in self.get_prefecture_names() if prefecture_keyword in name),
            None,
        )
        if matched_name is None:
            raise ValueError(f"prefecture not found for keyword: {prefecture_keyword}")

        zip_file_info = self._zip_files[matched_name]
        directory_name = Path(zip_file_info.filename).stem
        target_path = self._workspace_dir / directory_name

        if not target_path.exists():
            zip_download_path = self._download_dir / zip_file_info.filename
            self._download_file(zip_file_info, zip_download_path)
            self._extract_file(zip_download_path, target_path)

        shp_paths = self._find_files_in_dir(target_path, ".shp")
        if not shp_paths:
            raise FileNotFoundError(f"shapefile not found under: {target_path}")

        return AdministrativeDivision(prefecture_name=matched_name, shp_path=shp_paths[0])


def get_bbox_from_shapefile(shp_path: Path) -> list[float]:
    with shapefile.Reader(str(shp_path)) as reader:
        min_lon, min_lat, max_lon, max_lat = reader.bbox
    return [min_lon, min_lat, max_lon, max_lat]
