import json
import math
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

import rasterio
import requests
import shapefile
import plotly.graph_objects as go
from bs4 import BeautifulSoup, element
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
from pyproj import Transformer
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import from_bounds, rowcol
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep
import warnings


warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)

METRIC_CRS = "ESRI:53008"
WGS84_CRS = "EPSG:4326"
JGD2011_CRS = "EPSG:6668"
GCOM_PIXEL_SIZE_M = 3000
STRICT_INVALID_QA_BITS = (0, 1, 5, 11, 12, 13)
LST_VIS_MIN_C = 20.0
LST_VIS_MAX_C = 40.0
LST_VIS_MISSING_COLOR = (185, 185, 185)
LST_VIS_STATS = ("max", "mean", "min")
SAMPLING_SURFACE_Z_SCALE = 2.0
SAMPLING_SPHERE_MARKER_SIZE = 3.5
LST_VIS_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (49, 54, 149)),
    (0.18, (69, 117, 180)),
    (0.42, (116, 173, 209)),
    (0.58, (171, 217, 233)),
    (0.75, (254, 224, 144)),
    (0.9, (253, 174, 97)),
    (1.0, (165, 0, 38)),
]

_WGS84_TO_METRIC = Transformer.from_crs(WGS84_CRS, METRIC_CRS, always_xy=True)
_METRIC_TO_WGS84 = Transformer.from_crs(METRIC_CRS, WGS84_CRS, always_xy=True)
_WGS84_TO_6668 = Transformer.from_crs(WGS84_CRS, JGD2011_CRS, always_xy=True)


def _log(message: str) -> None:
    print(message, flush=True)


@dataclass
class ZipFileInfo:
    prefecture_name: str
    year: int
    url: str
    size_str: str
    filename: str


@dataclass
class SamplingPoint:
    point_id: int
    lon: float
    lat: float
    x_metric: float
    y_metric: float
    x_6668: float
    y_6668: float
    inside_city: bool = True
    sum_lst_c: float = 0.0
    min_lst_c: float | None = None
    max_lst_c: float | None = None
    valid_count: int = 0


@dataclass
class SceneRaster:
    array: object
    transform: object
    tags: dict[str, str]
    band_tags: dict[str, str]
    width: int
    height: int
    footprint_metric: Polygon


@dataclass(frozen=True)
class ScenePointSample:
    point_index: int
    lst_c: float


def ensure_parent(path_str: str) -> Path:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def parse_float_tag(tags: dict[str, str], key: str, default: float = 0.0) -> float:
    value = tags.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def parse_int_tag(tags: dict[str, str], key: str, default: int = 0) -> int:
    value = tags.get(key)
    if value is None:
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def project_wgs84_to_metric(lon: float, lat: float) -> tuple[float, float]:
    return _WGS84_TO_METRIC.transform(lon, lat)


def project_metric_to_wgs84(x: float, y: float) -> tuple[float, float]:
    return _METRIC_TO_WGS84.transform(x, y)


def project_wgs84_to_6668(lon: float, lat: float) -> tuple[float, float]:
    return _WGS84_TO_6668.transform(lon, lat)


def tag_corner_points(tags: dict[str, str]) -> list[tuple[float, float]]:
    return [
        (
            parse_float_tag(tags, "Geometry_data_Upper_left_longitude"),
            parse_float_tag(tags, "Geometry_data_Upper_left_latitude"),
        ),
        (
            parse_float_tag(tags, "Geometry_data_Upper_right_longitude"),
            parse_float_tag(tags, "Geometry_data_Upper_right_latitude"),
        ),
        (
            parse_float_tag(tags, "Geometry_data_Lower_right_longitude"),
            parse_float_tag(tags, "Geometry_data_Lower_right_latitude"),
        ),
        (
            parse_float_tag(tags, "Geometry_data_Lower_left_longitude"),
            parse_float_tag(tags, "Geometry_data_Lower_left_latitude"),
        ),
    ]


def project_polygon_to_metric(polygon: Polygon) -> Polygon:
    return transform(_WGS84_TO_METRIC.transform, polygon)


def transform_polygon_to_wgs84(polygon: Polygon) -> Polygon:
    return transform(_METRIC_TO_WGS84.transform, polygon)


def build_metric_footprint_polygon(tags: dict[str, str]) -> Polygon:
    corners = tag_corner_points(tags)
    return project_polygon_to_metric(Polygon(corners))


def compute_projected_bounds(tags: dict[str, str]) -> tuple[float, float, float, float]:
    polygon = build_metric_footprint_polygon(tags)
    min_x, min_y, max_x, max_y = polygon.bounds
    return min_x, min_y, max_x, max_y


def open_hdf5_subdataset(hdf5_path: str, sub_key: str) -> rasterio.io.DatasetReader:
    return rasterio.open(f"HDF5:{hdf5_path}://{sub_key}")


def load_scene(hdf5_path: str, sub_key: str) -> SceneRaster:
    with open_hdf5_subdataset(hdf5_path, sub_key) as dataset:
        array = dataset.read(1)
        tags = {key: str(value) for key, value in dataset.tags().items()}
        band_tags = {key: str(value) for key, value in dataset.tags(1).items()}
        width = dataset.width
        height = dataset.height

    min_x, min_y, max_x, max_y = compute_projected_bounds(tags)
    transform_obj = from_bounds(min_x, min_y, max_x, max_y, width, height)
    footprint_metric = build_metric_footprint_polygon(tags)
    return SceneRaster(
        array=array,
        transform=transform_obj,
        tags=tags,
        band_tags=band_tags,
        width=width,
        height=height,
        footprint_metric=footprint_metric,
    )


def scene_nodata(scene: SceneRaster) -> int:
    return parse_int_tag(scene.band_tags, "Error_DN", 65535)


def scene_slope(scene: SceneRaster) -> float:
    return parse_float_tag(scene.band_tags, "Slope", 1.0)


def scene_offset(scene: SceneRaster) -> float:
    return parse_float_tag(scene.band_tags, "Offset", 0.0)


def sample_scene(scene: SceneRaster, lon: float, lat: float) -> tuple[int, int, int] | None:
    x_metric, y_metric = project_wgs84_to_metric(lon, lat)
    row, col = rowcol(scene.transform, x_metric, y_metric)
    if row < 0 or col < 0 or row >= scene.height or col >= scene.width:
        return None

    value = int(scene.array[row, col])
    if value == scene_nodata(scene):
        return None

    return value, row, col


def lst_dn_to_celsius(value: int | float, scene: SceneRaster) -> float:
    return float(value) * scene_slope(scene) + scene_offset(scene) - 273


def decode_qa_flag(value: int) -> dict[int, int]:
    masked = int(value) & 0xFFFF
    return {bit: (masked >> bit) & 1 for bit in range(16)}


def is_valid_qa_value(value: int) -> bool:
    bits = decode_qa_flag(value)
    return all(bits[index] == 0 for index in STRICT_INVALID_QA_BITS)


def parse_download_url(onclick: str) -> str:
    start = onclick.find("DownLd(")
    end = onclick.find(");", start)
    if start == -1 or end == -1:
        raise RuntimeError("failed to parse MLIT download url")
    values = [value.strip().strip("'") for value in onclick[start + len("DownLd(") : end].split(",")]
    if len(values) < 3:
        raise RuntimeError("unexpected MLIT download onclick payload")
    return values[2]


def parse_latest_prefecture_zip_files() -> dict[str, ZipFileInfo]:
    url = "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2025.html"
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    soup = BeautifulSoup(response.text, "html.parser")
    main = soup.find("main")
    if main is None:
        raise RuntimeError("failed to parse MLIT latest administrative division page")
    jmap = main.find(id="Jmap")
    if jmap is None:
        raise RuntimeError("failed to find MLIT prefecture table")
    table = jmap.find_next_sibling("table", class_="responsive-table")
    if table is None:
        raise RuntimeError("failed to find MLIT download table")

    zip_files: dict[str, ZipFileInfo] = {}
    for row in table.find_all("tr")[1:]:
        cells: list[element.Tag] = row.find_all("td")
        if len(cells) < 6:
            continue
        prefecture_name = cells[0].get_text(strip=True)
        year_text = cells[2].get_text(strip=True)
        filename = cells[4].get_text(strip=True)
        download_link = cells[5].find("a")
        if not prefecture_name or prefecture_name == "全国":
            continue
        if download_link is None or not download_link.has_attr("onclick"):
            continue
        digits = "".join(ch for ch in year_text if ch.isdigit())
        year = int(digits[:4]) if len(digits) >= 4 else 0
        current = zip_files.get(prefecture_name)
        if current and current.year >= year:
            continue
        zip_files[prefecture_name] = ZipFileInfo(
            prefecture_name=prefecture_name,
            year=year,
            url=urljoin(url, parse_download_url(download_link["onclick"])),
            size_str=cells[3].get_text(strip=True),
            filename=filename,
        )
    return zip_files


def download_and_extract_prefecture(prefecture_name: str, download_dir: Path, workspace_dir: Path) -> Path:
    zip_files = parse_latest_prefecture_zip_files()
    normalized_name = prefecture_name.removesuffix("都").removesuffix("道").removesuffix("府").removesuffix("県")
    zip_info = zip_files.get(prefecture_name) or zip_files.get(normalized_name)
    if zip_info is None:
        raise ValueError(f"prefecture not found in latest administrative data: {prefecture_name}")

    zip_path = download_dir / zip_info.filename
    extract_dir = workspace_dir / Path(zip_info.filename).stem

    if not zip_path.exists():
        response = requests.get(zip_info.url, timeout=300)
        response.raise_for_status()
        zip_path.write_bytes(response.content)

    if not extract_dir.exists():
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zip_handle:
            zip_handle.extractall(extract_dir)

    for root, _, files in os.walk(extract_dir):
        for filename in files:
            if filename.endswith(".shp"):
                return Path(root) / filename
    raise FileNotFoundError(f"shapefile not found under: {extract_dir}")


def _record_area_keys(record: dict[str, str]) -> list[str]:
    prefecture = record.get("N03_001", "").strip()
    municipality = (record.get("N03_003", "").strip() or record.get("N03_004", "").strip())
    district = (record.get("N03_004", "").strip() if record.get("N03_003", "").strip() else record.get("N03_005", "").strip())
    keys = []
    if prefecture and municipality:
        keys.append(f"{prefecture}{municipality}")
    if prefecture and municipality and district:
        keys.append(f"{prefecture}{municipality}{district}")
    return keys


def load_area_polygon(area_name: str, prefecture_name: str, download_dir: Path, workspace_dir: Path) -> tuple[Polygon, Path]:
    shp_path = download_and_extract_prefecture(prefecture_name, download_dir, workspace_dir)
    polygons: list[Polygon] = []
    last_error: Exception | None = None

    for encoding in ("utf-8", "cp932", "shift_jis", "latin1"):
        try:
            with shapefile.Reader(str(shp_path), encoding=encoding) as reader:
                polygons = []
                for shape_record in reader.iterShapeRecords():
                    record = shape_record.record.as_dict()
                    if area_name not in _record_area_keys(record):
                        continue
                    polygons.append(shape(shape_record.shape.__geo_interface__))
            if polygons:
                break
        except UnicodeDecodeError as error:
            last_error = error
            polygons = []

    if not polygons:
        if last_error is not None:
            raise RuntimeError(f"failed to decode shapefile records for {shp_path}") from last_error
        raise ValueError(f"area polygon not found: {area_name}")

    return unary_union(polygons), shp_path


def transform_polygon_to_metric(polygon: Polygon) -> Polygon:
    return transform(_WGS84_TO_METRIC.transform, polygon)


def transform_metric_to_wgs84(x: float, y: float) -> tuple[float, float]:
    return _METRIC_TO_WGS84.transform(x, y)


def transform_polygon_to_6668(polygon: Polygon) -> Polygon:
    return transform(_WGS84_TO_6668.transform, polygon)


def generate_grid_points(metric_polygon: Polygon, spacing_m: int) -> list[SamplingPoint]:
    if spacing_m <= 0:
        raise ValueError("spacing_m must be positive")

    min_x, min_y, max_x, max_y = metric_polygon.bounds
    prepared = prep(metric_polygon)
    start_x = math.floor(min_x / spacing_m) * spacing_m + spacing_m / 2
    start_y = math.floor(min_y / spacing_m) * spacing_m + spacing_m / 2

    points: list[SamplingPoint] = []
    point_id = 1
    y = start_y
    while y <= max_y:
        x = start_x
        while x <= max_x:
            point = Point(x, y)
            if prepared.covers(point):
                lon, lat = transform_metric_to_wgs84(x, y)
                x_6668, y_6668 = project_wgs84_to_6668(lon, lat)
                points.append(
                    SamplingPoint(
                        point_id=point_id,
                        lon=lon,
                        lat=lat,
                        x_metric=x,
                        y_metric=y,
                        x_6668=x_6668,
                        y_6668=y_6668,
                    )
                )
                point_id += 1
            x += spacing_m
        y += spacing_m
    return points


def write_geojson(path_str: str, features: list[dict]) -> str:
    path = ensure_parent(path_str)
    with path.open("w", encoding="utf-8") as handle:
        json.dump({"type": "FeatureCollection", "features": features}, handle, ensure_ascii=False)
    return str(path)


def point_to_feature(point: SamplingPoint, spacing_m: int) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [point.lon, point.lat]},
        "properties": {
            "point_id": point.point_id,
            "inside_city": point.inside_city,
            "spacing_m": spacing_m,
            "x_6668": point.x_6668,
            "y_6668": point.y_6668,
            "x_metric_m": point.x_metric,
            "y_metric_m": point.y_metric,
            "valid_count": point.valid_count,
            "min_lst_c": None if point.min_lst_c is None else round(point.min_lst_c, 6),
            "mean_lst_c": None if point.valid_count == 0 else round(point.sum_lst_c / point.valid_count, 6),
            "max_lst_c": None if point.max_lst_c is None else round(point.max_lst_c, 6),
        },
    }


def polygon_to_feature(geometry: Polygon, properties: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": mapping(geometry),
        "properties": properties,
    }


def scale_xy(x: float, y: float, bounds: tuple[float, float, float, float], width: int, height: int, padding: int) -> tuple[int, int]:
    min_x, min_y, max_x, max_y = bounds
    usable_width = width - padding * 2
    usable_height = height - padding * 2
    scale_x = usable_width / max(max_x - min_x, 1)
    scale_y = usable_height / max(max_y - min_y, 1)
    scale = min(scale_x, scale_y)
    drawn_width = (max_x - min_x) * scale
    drawn_height = (max_y - min_y) * scale
    offset_x = padding + (usable_width - drawn_width) / 2
    offset_y = padding + (usable_height - drawn_height) / 2
    px = offset_x + (x - min_x) * scale
    py = height - offset_y - (y - min_y) * scale
    return int(px), int(py)


def draw_polygon_outline(draw: ImageDraw.ImageDraw, polygon: Polygon, bounds: tuple[float, float, float, float], width: int, height: int, padding: int) -> None:
    polygons = [polygon] if polygon.geom_type == "Polygon" else list(polygon.geoms)
    for single_polygon in polygons:
        exterior = [scale_xy(x, y, bounds, width, height, padding) for x, y in single_polygon.exterior.coords]
        draw.polygon(exterior, fill=(236, 242, 248), outline=(60, 80, 100))
        for interior in single_polygon.interiors:
            ring = [scale_xy(x, y, bounds, width, height, padding) for x, y in interior.coords]
            draw.polygon(ring, fill=(255, 255, 255), outline=(60, 80, 100))


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def interpolate_rgb(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    fraction: float,
) -> tuple[int, int, int]:
    return tuple(
        int(round(start[index] + (end[index] - start[index]) * fraction))
        for index in range(3)
    )


def temperature_to_color(
    value_c: float,
    minimum_c: float = LST_VIS_MIN_C,
    maximum_c: float = LST_VIS_MAX_C,
) -> tuple[int, int, int]:
    if maximum_c <= minimum_c:
        return LST_VIS_STOPS[-1][1]
    normalized = clamp((value_c - minimum_c) / (maximum_c - minimum_c), 0.0, 1.0)
    for index in range(len(LST_VIS_STOPS) - 1):
        left_position, left_color = LST_VIS_STOPS[index]
        right_position, right_color = LST_VIS_STOPS[index + 1]
        if normalized <= right_position:
            span = right_position - left_position
            if span <= 0:
                return right_color
            local = (normalized - left_position) / span
            return interpolate_rgb(left_color, right_color, local)
    return LST_VIS_STOPS[-1][1]


def draw_points(
    draw: ImageDraw.ImageDraw,
    points: list[SamplingPoint],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    padding: int,
    radius: int,
    coord_fn: Callable[[SamplingPoint], tuple[float, float]],
    value_fn: Callable[[SamplingPoint], float | None] | None = None,
) -> None:
    for point in points:
        x, y = coord_fn(point)
        px, py = scale_xy(x, y, bounds, width, height, padding)
        if value_fn is None:
            color = (102, 102, 102) if point.valid_count == 0 else (47, 128, 237)
        else:
            value_c = value_fn(point)
            color = LST_VIS_MISSING_COLOR if value_c is None else temperature_to_color(value_c)
        draw.ellipse(
            (px - radius, py - radius, px + radius, py + radius),
            fill=color,
        )


def draw_point_spheres(
    draw: ImageDraw.ImageDraw,
    points: list[SamplingPoint],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    padding: int,
    radius: int,
    value_fn: Callable[[SamplingPoint], float | None] | None = None,
) -> None:
    outer_radius = max(1, radius + 1)
    inner_radius = max(1, radius - 1)
    highlight_radius = max(1, radius // 2)
    for point in points:
        px, py = scale_xy(point.lon, point.lat, bounds, width, height, padding)
        if value_fn is None:
            middle_color = (47, 128, 237)
        else:
            value_c = value_fn(point)
            middle_color = LST_VIS_MISSING_COLOR if value_c is None else temperature_to_color(value_c)
        outer_color = tuple(max(0, int(channel * 0.7)) for channel in middle_color)
        inner_color = tuple(min(255, int(channel + (255 - channel) * 0.35)) for channel in middle_color)
        highlight_color = tuple(min(255, int(channel + (255 - channel) * 0.7)) for channel in middle_color)
        draw.ellipse(
            (px - outer_radius, py - outer_radius, px + outer_radius, py + outer_radius),
            fill=outer_color,
        )
        draw.ellipse(
            (px - radius, py - radius, px + radius, py + radius),
            fill=middle_color,
        )
        draw.ellipse(
            (px - inner_radius, py - inner_radius, px + inner_radius, py + inner_radius),
            fill=inner_color,
        )
        draw.ellipse(
            (px - highlight_radius, py - highlight_radius, px + highlight_radius, py + highlight_radius),
            fill=highlight_color,
        )


def preview_canvas_size(
    bounds: tuple[float, float, float, float],
    spacing_m: int,
    min_size: int = 420,
) -> tuple[int, int]:
    min_x, min_y, max_x, max_y = bounds
    width_range = max(max_x - min_x, 1e-9)
    height_range = max(max_y - min_y, 1e-9)
    max_size = 1200
    if spacing_m <= 100:
        max_size = 9000
        min_size = 3600
    elif spacing_m <= 1000:
        max_size = 6000
        min_size = 2200
    elif spacing_m <= 250:
        max_size = 1800
        min_size = 700
    if width_range >= height_range:
        width = max_size
        height = max(min_size, int(round(max_size * height_range / width_range)))
    else:
        height = max_size
        width = max(min_size, int(round(max_size * width_range / height_range)))
    return width, height


def preview_point_radius(spacing_m: int) -> int:
    if spacing_m <= 100:
        return 2
    if spacing_m <= 500:
        return 2
    if spacing_m <= 1000:
        return 15
    return 3


def write_sampling_preview(
    path_str: str,
    polygon_wgs84: Polygon,
    points: list[SamplingPoint],
    spacing_m: int,
    footprint_wgs84: Polygon | None = None,
    value_fn: Callable[[SamplingPoint], float | None] | None = None,
    legend_title: str = "Mean LST (°C)",
) -> str:
    path = ensure_parent(path_str)
    point_radius = preview_point_radius(spacing_m)
    padding = max(16 if spacing_m <= 100 else 48, point_radius * 2 + 8)
    image = _render_preview_panel(
        polygon_wgs84,
        points,
        spacing_m,
        point_radius=point_radius,
        width=preview_canvas_size(polygon_wgs84.bounds, spacing_m)[0],
        height=preview_canvas_size(polygon_wgs84.bounds, spacing_m)[1],
        padding=padding,
        coord_fn=lambda point: (point.lon, point.lat),
        value_fn=value_fn,
        footprint_wgs84=footprint_wgs84,
    )
    if spacing_m <= 1000:
        trim_margin = 12
        background = Image.new("RGB", image.size, (255, 255, 255))
        diff = ImageChops.difference(image, background)
        bbox = diff.getbbox()
        if bbox is not None:
            left = max(0, bbox[0] - trim_margin)
            upper = max(0, bbox[1] - trim_margin)
            right = min(image.size[0], bbox[2] + trim_margin)
            lower = min(image.size[1], bbox[3] + trim_margin)
            image = image.crop((left, upper, right, lower))
    if value_fn is not None:
        image = append_temperature_legend(
            image,
            title=legend_title,
            minimum_c=LST_VIS_MIN_C,
            maximum_c=LST_VIS_MAX_C,
        )
    image.save(path)
    return str(path)


def write_sampling_point_cloud(
    path_str: str,
    polygon_wgs84: Polygon,
    points: list[SamplingPoint],
    spacing_m: int,
    value_fn: Callable[[SamplingPoint], float | None] | None = None,
) -> str:
    path = ensure_parent(path_str)
    point_radius = preview_point_radius(spacing_m)
    padding = max(16 if spacing_m <= 100 else 48, point_radius * 2 + 8)
    width, height = preview_canvas_size(polygon_wgs84.bounds, spacing_m)
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw_polygon_outline(draw, polygon_wgs84, polygon_wgs84.bounds, width, height, padding)
    draw_point_spheres(draw, points, polygon_wgs84.bounds, width, height, padding, point_radius, value_fn=value_fn)
    if spacing_m <= 1000:
        trim_margin = 12
        background = Image.new("RGB", image.size, (255, 255, 255))
        diff = ImageChops.difference(image, background)
        bbox = diff.getbbox()
        if bbox is not None:
            left = max(0, bbox[0] - trim_margin)
            upper = max(0, bbox[1] - trim_margin)
            right = min(image.size[0], bbox[2] + trim_margin)
            lower = min(image.size[1], bbox[3] + trim_margin)
            image = image.crop((left, upper, right, lower))
    image.save(path)
    return str(path)


def _render_preview_panel(
    polygon: Polygon,
    points: list[SamplingPoint],
    spacing_m: int,
    point_radius: int,
    width: int,
    height: int,
    padding: int,
    coord_fn,
    value_fn: Callable[[SamplingPoint], float | None] | None = None,
    footprint_wgs84: Polygon | None = None,
    bounds_override: tuple[float, float, float, float] | None = None,
) -> Image.Image:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    bounds = bounds_override or polygon.bounds
    draw_polygon_outline(draw, polygon, bounds, width, height, padding)
    if footprint_wgs84 is not None:
        draw_polygon_outline(draw, footprint_wgs84, bounds, width, height, padding)
    draw_points(draw, points, bounds, width, height, padding, point_radius, coord_fn, value_fn=value_fn)
    return image


def sampling_stat_value(point: SamplingPoint, stat: str) -> float | None:
    if point.valid_count == 0:
        return None
    if stat == "min":
        return point.min_lst_c
    if stat == "mean":
        return point.sum_lst_c / point.valid_count
    if stat == "max":
        return point.max_lst_c
    raise ValueError(f"unknown sampling stat: {stat}")


def sampling_stat_label(stat: str) -> str:
    if stat == "min":
        return "Minimum LST (°C)"
    if stat == "mean":
        return "Mean LST (°C)"
    if stat == "max":
        return "Maximum LST (°C)"
    raise ValueError(f"unknown sampling stat: {stat}")


def sampling_preview_path(prefix: str, spacing_m: int, stat: str) -> str:
    if stat == "mean":
        return f"{prefix}_{spacing_m}m.png"
    return f"{prefix}_{stat}_{spacing_m}m.png"


def sampling_surface_path(prefix: str, spacing_m: int, stat: str) -> str:
    if stat == "mean":
        return f"{prefix}_{spacing_m}m.html"
    return f"{prefix}_{stat}_{spacing_m}m.html"


def _temperature_colorscale() -> list[tuple[float, str]]:
    return [
        (position, f"rgb({red}, {green}, {blue})")
        for position, (red, green, blue) in LST_VIS_STOPS
    ]


def _grid_key(value: float) -> float:
    return round(value, 6)


def _polygon_outline_traces(polygon_wgs84: Polygon, z_value: float) -> list[go.Scatter3d]:
    polygons = list(polygon_wgs84.geoms) if hasattr(polygon_wgs84, "geoms") else [polygon_wgs84]
    traces: list[go.Scatter3d] = []
    for polygon in polygons:
        coords = list(polygon.exterior.coords)
        traces.append(
            go.Scatter3d(
                x=[coord[0] for coord in coords],
                y=[coord[1] for coord in coords],
                z=[z_value] * len(coords),
                mode="lines",
                line=dict(color="rgb(60, 60, 60)", width=6),
                hoverinfo="skip",
                name="Boundary",
            )
        )
    return traces


def _sampling_scene_aspectratio(
    lons: list[float],
    lats: list[float],
    temperatures: list[float],
) -> dict[str, float]:
    lon_span = max(max(lons) - min(lons), 1e-9)
    lat_span = max(max(lats) - min(lats), 1e-9)
    mean_lat = sum(lats) / len(lats)
    x_span_km = lon_span * 111.32 * max(math.cos(math.radians(mean_lat)), 1e-6)
    y_span_km = lat_span * 111.32
    horizontal_base = max(x_span_km, y_span_km, 1e-6)
    z_span_c = max(max(temperatures) - min(temperatures), 1.0)
    z_ratio = max(0.18, min(0.42, z_span_c / (horizontal_base * 25.0)))
    return {
        "x": max(x_span_km / horizontal_base, 0.3) * 2.4,
        "y": max(y_span_km / horizontal_base, 0.3) * 2.4,
        "z": z_ratio * SAMPLING_SURFACE_Z_SCALE,
    }


def _sampling_surface_grid(
    points: list[SamplingPoint],
    stat: str,
) -> tuple[list[list[float | None]], list[list[float | None]], list[list[float | None]]]:
    point_lookup = {
        (point.x_metric, point.y_metric): point
        for point in points
        if sampling_stat_value(point, stat) is not None
    }
    x_coords = sorted({point.x_metric for point in point_lookup.values()})
    y_coords = sorted({point.y_metric for point in point_lookup.values()})

    x_grid: list[list[float | None]] = []
    y_grid: list[list[float | None]] = []
    z_grid: list[list[float | None]] = []
    for y_metric in y_coords:
        x_row: list[float | None] = []
        y_row: list[float | None] = []
        z_row: list[float | None] = []
        for x_metric in x_coords:
            point = point_lookup.get((x_metric, y_metric))
            if point is None:
                x_row.append(None)
                y_row.append(None)
                z_row.append(None)
                continue
            x_row.append(point.lon)
            y_row.append(point.lat)
            z_row.append(sampling_stat_value(point, stat))
        x_grid.append(x_row)
        y_grid.append(y_row)
        z_grid.append(z_row)
    return x_grid, y_grid, z_grid


def build_sampling_surface_figure(
    polygon_wgs84: Polygon,
    points: list[SamplingPoint],
    stat: str,
) -> go.Figure:
    title = sampling_stat_label(stat)
    valid_points = [point for point in points if sampling_stat_value(point, stat) is not None]
    temperatures = [sampling_stat_value(point, stat) for point in valid_points]
    lons = [point.lon for point in valid_points]
    lats = [point.lat for point in valid_points]
    aspectratio = _sampling_scene_aspectratio(lons, lats, temperatures)

    z_min = min(temperatures)
    z_max = max(temperatures)
    z_range = max(z_max - z_min, 1.0)
    outline_z = z_min - z_range * 0.08
    surface_x, surface_y, surface_z = _sampling_surface_grid(valid_points, stat)

    surface = go.Surface(
        x=surface_x,
        y=surface_y,
        z=surface_z,
        surfacecolor=surface_z,
        colorscale=_temperature_colorscale(),
        cmin=z_min,
        cmax=z_max,
        opacity=0.82,
        showscale=False,
        hovertemplate="lon=%{x:.6f}<br>lat=%{y:.6f}<br>temperature=%{z:.2f} °C<extra></extra>",
        name="Surface",
    )

    spheres = go.Scatter3d(
        x=lons,
        y=lats,
        z=temperatures,
        mode="markers",
        marker=dict(
            size=SAMPLING_SPHERE_MARKER_SIZE,
            color=temperatures,
            colorscale=_temperature_colorscale(),
            colorbar=dict(title=title),
            opacity=0.95,
            line=dict(color="rgb(255, 255, 255)", width=1),
        ),
        hovertemplate="lon=%{x:.6f}<br>lat=%{y:.6f}<br>temperature=%{z:.2f} °C<extra></extra>",
        name="Samples",
    )

    figure = go.Figure(data=[*_polygon_outline_traces(polygon_wgs84, outline_z), surface, spheres])
    figure.update_layout(
        title=title,
        template="plotly_white",
        margin=dict(l=0, r=0, t=50, b=0),
        scene=dict(
            camera=sampling_surface_default_camera(),
            xaxis_title="Longitude",
            yaxis_title="Latitude",
            zaxis_title="LST (°C)",
            aspectmode="manual",
            aspectratio=aspectratio,
        ),
        showlegend=False,
    )
    return figure


def sampling_surface_view_path(prefix: str, spacing_m: int, stat: str, view_name: str) -> str:
    return f"{prefix}_{stat}_{spacing_m}m_{view_name}.png"


def sampling_compare_path(prefix: str, spacing_m: int, stat: str) -> str:
    return f"{prefix}_{stat}_{spacing_m}m.png"


def sampling_surface_topdown_camera() -> dict:
    return {
        "eye": {"x": 0.0, "y": 0.0, "z": 2.5},
        "up": {"x": 0.0, "y": 1.0, "z": 0.0},
        "projection": {"type": "orthographic"},
    }


def sampling_surface_default_camera() -> dict:
    return {
        "eye": {"x": 1.7, "y": 1.7, "z": 0.95},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "projection": {"type": "perspective"},
    }


def sampling_surface_camera_presets() -> dict[str, dict]:
    return {
        "topdown": sampling_surface_topdown_camera(),
        "iso": sampling_surface_default_camera(),
        "low_north": {
            "eye": {"x": 0.0, "y": -2.2, "z": 0.55},
            "up": {"x": 0, "y": 0, "z": 1},
        },
        "low_east": {
            "eye": {"x": 2.2, "y": 0.0, "z": 0.55},
            "up": {"x": 0, "y": 0, "z": 1},
        },
    }


def write_sampling_surface(
    path_str: str,
    polygon_wgs84: Polygon,
    points: list[SamplingPoint],
    stat: str,
) -> str:
    path = ensure_parent(path_str)
    valid_points = [point for point in points if sampling_stat_value(point, stat) is not None]
    if not valid_points:
        path.write_text(
            "<html><body><p>No valid temperature samples available for 3D surface.</p></body></html>",
            encoding="utf-8",
        )
        return str(path)

    figure = build_sampling_surface_figure(polygon_wgs84, points, stat)
    figure.write_html(str(path), include_plotlyjs=True, full_html=True)
    return str(path)


def write_sampling_surface_views(
    output_dir: Path,
    prefix: str,
    polygon_wgs84: Polygon,
    points: list[SamplingPoint],
    spacing_m: int,
    stat: str = "mean",
    camera_presets: dict[str, dict] | None = None,
) -> dict[str, str]:
    figure = build_sampling_surface_figure(polygon_wgs84, points, stat)
    output_paths: dict[str, str] = {}
    presets = camera_presets or sampling_surface_camera_presets()
    for view_name, camera in presets.items():
        view_figure = go.Figure(figure)
        view_figure.update_layout(scene_camera=camera)
        path = output_dir / sampling_surface_view_path(prefix, spacing_m, stat, view_name)
        view_figure.write_image(str(path), format="png", width=1200, height=900, scale=2)
        output_paths[view_name] = str(path)
    return output_paths


def _trim_white_image(image: Image.Image, margin: int = 12) -> Image.Image:
    background = Image.new("RGB", image.size, (255, 255, 255))
    bbox = ImageChops.difference(image.convert("RGB"), background).getbbox()
    if bbox is None:
        return image.copy()
    left = max(0, bbox[0] - margin)
    upper = max(0, bbox[1] - margin)
    right = min(image.size[0], bbox[2] + margin)
    lower = min(image.size[1], bbox[3] + margin)
    return image.crop((left, upper, right, lower))


def write_sampling_compare_image(
    path_str: str,
    preview_path: str,
    topdown_path: str,
) -> str:
    path = ensure_parent(path_str)
    preview = Image.open(preview_path).convert("RGB")
    topdown = Image.open(topdown_path).convert("RGB")
    try:
        preview_trim = _trim_white_image(preview)
        topdown_trim = _trim_white_image(topdown)
        panel_size = (1600, 1200)
        preview_panel = ImageOps.contain(preview_trim, panel_size, method=Image.Resampling.LANCZOS)
        topdown_panel = ImageOps.contain(topdown_trim, panel_size, method=Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (panel_size[0] * 2, panel_size[1] + 70), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        draw.text((20, 20), "2D sampling preview", fill=(0, 0, 0))
        draw.text((panel_size[0] + 20, 20), "3D sampling topdown (orthographic)", fill=(0, 0, 0))
        canvas.paste(preview_panel, ((panel_size[0] - preview_panel.width) // 2, 70 + (panel_size[1] - preview_panel.height) // 2))
        canvas.paste(
            topdown_panel,
            (
                panel_size[0] + (panel_size[0] - topdown_panel.width) // 2,
                70 + (panel_size[1] - topdown_panel.height) // 2,
            ),
        )
        canvas.save(path)
    finally:
        preview.close()
        topdown.close()
    return str(path)


def write_sampling_surface_set(
    output_dir: Path,
    prefix: str,
    polygon_wgs84: Polygon,
    points: list[SamplingPoint],
    spacing_m: int,
    stats: tuple[str, ...] = LST_VIS_STATS,
) -> dict[str, str]:
    output_paths: dict[str, str] = {}
    for stat in stats:
        path = output_dir / sampling_surface_path(prefix, spacing_m, stat)
        output_paths[stat] = write_sampling_surface(str(path), polygon_wgs84, points, stat)
    return output_paths


def write_sampling_compare_set(
    output_dir: Path,
    prefix: str,
    polygon_wgs84: Polygon,
    points: list[SamplingPoint],
    spacing_m: int,
    stats: tuple[str, ...] = LST_VIS_STATS,
) -> tuple[dict[str, str], dict[str, str]]:
    topdown_paths: dict[str, str] = {}
    compare_paths: dict[str, str] = {}
    for stat in stats:
        preview_path = output_dir / f"{prefix}_sampling_preview_{stat}.png"
        if not preview_path.exists():
            preview_path = output_dir / sampling_preview_path(prefix, spacing_m, stat)
        topdown_path = output_dir / sampling_surface_view_path(f"{prefix}_sampling_topdown", spacing_m, stat, "topdown")
        write_sampling_point_cloud(
            str(topdown_path),
            polygon_wgs84,
            points,
            spacing_m,
            value_fn=lambda point, stat=stat: sampling_stat_value(point, stat),
        )
        compare_path = output_dir / sampling_compare_path(f"{prefix}_sampling_compare", spacing_m, stat)
        write_sampling_compare_image(str(compare_path), str(preview_path), topdown_path)
        topdown_paths[stat] = str(topdown_path)
        compare_paths[stat] = str(compare_path)
    return topdown_paths, compare_paths


def write_sampling_preview_set(
    output_dir: Path,
    prefix: str,
    polygon_wgs84: Polygon,
    points: list[SamplingPoint],
    spacing_m: int,
    footprint_wgs84: Polygon | None = None,
    stats: tuple[str, ...] = LST_VIS_STATS,
) -> dict[str, str]:
    output_paths: dict[str, str] = {}
    for stat in stats:
        path = output_dir / sampling_preview_path(prefix, spacing_m, stat)
        write_sampling_preview(
            str(path),
            polygon_wgs84,
            points,
            spacing_m,
            footprint_wgs84=footprint_wgs84,
            value_fn=lambda point, stat=stat: sampling_stat_value(point, stat),
            legend_title=sampling_stat_label(stat),
        )
        output_paths[stat] = str(path)
    return output_paths


def _load_legend_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def append_temperature_legend(
    image: Image.Image,
    title: str,
    minimum_c: float,
    maximum_c: float,
) -> Image.Image:
    legend_width = 270
    canvas = Image.new("RGB", (image.width + legend_width, image.height), (255, 255, 255))
    canvas.paste(image, (0, 0))
    draw = ImageDraw.Draw(canvas)
    font_title = _load_legend_font(24)
    font_label = _load_legend_font(18)
    font_small = _load_legend_font(16)

    left = image.width + 24
    top = 32
    draw.text((left, top), title, fill=(25, 25, 25), font=font_title)
    draw.text((left, top + 30), f"Fixed scale: {minimum_c:.0f} to {maximum_c:.0f} °C", fill=(90, 90, 90), font=font_small)

    bar_top = top + 74
    bar_bottom = min(canvas.height - 70, bar_top + 420)
    bar_left = left + 32
    bar_right = bar_left + 28

    for y in range(bar_top, bar_bottom):
        fraction = 1.0 - ((y - bar_top) / max(bar_bottom - bar_top - 1, 1))
        value_c = minimum_c + (maximum_c - minimum_c) * fraction
        color = temperature_to_color(value_c, minimum_c, maximum_c)
        draw.line((bar_left, y, bar_right, y), fill=color, width=1)

    draw.rectangle((bar_left, bar_top, bar_right, bar_bottom), outline=(50, 50, 50), width=1)

    tick_count = 6
    for index in range(tick_count):
        fraction = index / (tick_count - 1)
        value_c = minimum_c + (maximum_c - minimum_c) * fraction
        y = int(round(bar_bottom - fraction * (bar_bottom - bar_top)))
        draw.line((bar_right + 2, y, bar_right + 12, y), fill=(50, 50, 50), width=1)
        draw.text((bar_right + 16, y - 9), f"{value_c:.0f}", fill=(30, 30, 30), font=font_label)

    draw.text((bar_left, bar_bottom + 16), "cold", fill=(90, 90, 90), font=font_small)
    draw.text((bar_right - 18, bar_top - 28), "hot", fill=(90, 90, 90), font=font_small)
    draw.rectangle(
        (left + 8, bar_top - 8, canvas.width - 18, min(canvas.height - 24, bar_bottom + 56)),
        outline=(210, 210, 210),
        width=1,
    )
    return canvas


def write_summary(path_str: str, summary: dict) -> str:
    path = ensure_parent(path_str)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return str(path)


def estimate_unique_pixels(area_m2: float, spacing_m: int) -> int:
    effective_resolution = max(spacing_m, GCOM_PIXEL_SIZE_M)
    return max(1, math.ceil(area_m2 / (effective_resolution * effective_resolution)))


def compute_polygon_area_m2(metric_polygon: Polygon) -> float:
    return float(metric_polygon.area)


def estimate_sampling_load_for_polygon(
    area_name: str,
    prefecture_name: str,
    metric_polygon: Polygon,
    scene_count: int,
    spacings_m: list[int],
) -> dict:
    area_m2 = compute_polygon_area_m2(metric_polygon)
    estimates = []
    for spacing_m in spacings_m:
        points = generate_grid_points(metric_polygon, int(spacing_m))
        estimates.append(
            {
                "spacing_m": int(spacing_m),
                "candidate_point_count": len(points),
                "scene_count": int(scene_count),
                "estimated_total_samples": len(points) * int(scene_count),
                "approx_unique_pixels_per_scene": estimate_unique_pixels(area_m2, int(spacing_m)),
            }
        )

    return {
        "area_name": area_name,
        "prefecture_name": prefecture_name,
        "approx_area_m2": round(area_m2, 3),
        "approx_area_km2": round(area_m2 / 1_000_000, 6),
        "estimates": estimates,
    }


def compute_point_means_for_scenes(
    area_name: str,
    prefecture_name: str,
    metric_polygon: Polygon,
    hdf5_file_paths: list[str],
    spacing_m: int,
    output_path: str,
    parallelism: int = 4,
    log_fn: Callable[[str], None] | None = None,
) -> dict:
    log = log_fn or _log
    if parallelism < 1:
        raise ValueError("parallelism must be at least 1")
    points = generate_grid_points(metric_polygon, spacing_m)
    scene_count = 0
    total_scenes = len(hdf5_file_paths)
    log(
        f"[analyze] aggregating point means area={area_name} prefecture={prefecture_name} "
        f"point_count={len(points)} scene_count={total_scenes} spacing={spacing_m}m parallelism={parallelism}"
    )

    def process_scene(index_and_path: tuple[int, str]) -> tuple[int, str, list[ScenePointSample]]:
        index, hdf5_path = index_and_path
        log(f"[analyze] loading scene {index}/{total_scenes}: {hdf5_path}")
        lst_scene = load_scene(hdf5_path, "Image_data/LST")
        qa_scene = load_scene(hdf5_path, "Image_data/QA_flag")
        samples: list[ScenePointSample] = []
        for point_index, point in enumerate(points):
            sample = sample_scene(lst_scene, point.lon, point.lat)
            qa_sample = sample_scene(qa_scene, point.lon, point.lat)
            if sample is None or qa_sample is None:
                continue

            lst_value, _, _ = sample
            qa_value, _, _ = qa_sample
            if not is_valid_qa_value(qa_value):
                continue

            samples.append(ScenePointSample(point_index=point_index, lst_c=lst_dn_to_celsius(lst_value, lst_scene)))
        return index, hdf5_path, samples

    max_workers = min(parallelism, total_scenes) if total_scenes > 0 else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_scene, (index, hdf5_path))
            for index, hdf5_path in enumerate(hdf5_file_paths, start=1)
        ]
        for future in as_completed(futures):
            index, _, samples = future.result()
            scene_count += 1
            for sample in samples:
                point = points[sample.point_index]
                point.sum_lst_c += sample.lst_c
                point.min_lst_c = sample.lst_c if point.min_lst_c is None else min(point.min_lst_c, sample.lst_c)
                point.max_lst_c = sample.lst_c if point.max_lst_c is None else max(point.max_lst_c, sample.lst_c)
                point.valid_count += 1

            valid_points_so_far = sum(1 for point in points if point.valid_count > 0)
            log(
                f"[analyze] finished scene {index}/{total_scenes}: "
                f"valid_points={valid_points_so_far} scene_count={scene_count}"
            )

    output_path_obj = ensure_parent(output_path)
    with output_path_obj.open("w", encoding="utf-8") as handle:
        handle.write("point_id,lon,lat,x_6668,y_6668,valid_count,min_lst_c,mean_lst_c,max_lst_c\n")
        for point in points:
            min_lst_c = ""
            mean_lst_c = ""
            max_lst_c = ""
            if point.valid_count > 0:
                min_lst_c = f"{point.min_lst_c:.6f}" if point.min_lst_c is not None else ""
                mean_lst_c = f"{point.sum_lst_c / point.valid_count:.6f}"
                max_lst_c = f"{point.max_lst_c:.6f}" if point.max_lst_c is not None else ""
            handle.write(
                f"{point.point_id},{point.lon},{point.lat},{point.x_6668},{point.y_6668},{point.valid_count},{min_lst_c},{mean_lst_c},{max_lst_c}\n"
            )

    output_dir = output_path_obj.parent
    output_stem = output_path_obj.stem
    points_geojson_path = output_dir / f"{output_stem}_sampling_points.geojson"
    boundary_geojson_path = output_dir / f"{output_stem}_sampling_boundary.geojson"
    summary_path = output_dir / f"{output_stem}_sampling_summary.json"
    polygon_wgs84 = transform_polygon_to_wgs84(metric_polygon)

    write_geojson(str(points_geojson_path), [point_to_feature(point, spacing_m) for point in points])
    write_geojson(
        str(boundary_geojson_path),
        [polygon_to_feature(polygon_wgs84, {"area_name": area_name, "prefecture_name": prefecture_name})],
    )
    preview_paths: dict[str, str] = {}
    surface_paths: dict[str, str] = {}
    for stat in LST_VIS_STATS:
        preview_path = output_dir / f"{output_stem}_sampling_preview_{stat}.png"
        surface_path = output_dir / f"{output_stem}_sampling_surface_{stat}.html"
        write_sampling_preview(
            str(preview_path),
            polygon_wgs84,
            points,
            spacing_m,
            value_fn=lambda point, stat=stat: sampling_stat_value(point, stat),
            legend_title=sampling_stat_label(stat),
        )
        write_sampling_surface(str(surface_path), polygon_wgs84, points, stat)
        preview_paths[stat] = str(preview_path)
        surface_paths[stat] = str(surface_path)
    topdown_paths, compare_paths = write_sampling_compare_set(output_dir, output_stem, polygon_wgs84, points, spacing_m)

    valid_points = sum(1 for point in points if point.valid_count > 0)
    area_m2 = compute_polygon_area_m2(metric_polygon)
    summary = {
        "area_name": area_name,
        "prefecture_name": prefecture_name,
        "spacing_m": spacing_m,
        "point_count": len(points),
        "valid_point_count": valid_points,
        "scene_count": scene_count,
        "approx_area_km2": round(area_m2 / 1_000_000, 6),
        "points_geojson_path": str(points_geojson_path),
        "boundary_geojson_path": str(boundary_geojson_path),
        "preview_path": preview_paths["mean"],
        "preview_paths": preview_paths,
        "surface_path": surface_paths["mean"],
        "surface_paths": surface_paths,
        "topdown_path": topdown_paths["mean"],
        "topdown_paths": topdown_paths,
        "compare_path": compare_paths["mean"],
        "compare_paths": compare_paths,
        "csv_path": str(output_path_obj),
        "approx_unique_pixels_per_scene": estimate_unique_pixels(area_m2, spacing_m),
        "lst_visualization_min_c": LST_VIS_MIN_C,
        "lst_visualization_max_c": LST_VIS_MAX_C,
    }
    write_summary(str(summary_path), summary)
    summary["summary_path"] = str(summary_path)
    log(
        f"[analyze] aggregation complete scene_count={scene_count} "
        f"valid_point_count={valid_points} point_count={len(points)}"
    )
    return summary
