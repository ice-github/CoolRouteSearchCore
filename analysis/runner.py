import json
import math
import os
import zipfile
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

import rasterio
import requests
import shapefile
from bs4 import BeautifulSoup, element
from PIL import Image, ImageChops, ImageDraw, ImageFont
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
LST_VIS_OUTLINE_COLOR = (55, 55, 55)
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
    mean_lst_c = None
    if point.valid_count > 0:
        mean_lst_c = point.sum_lst_c / point.valid_count
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
            "mean_lst_c": None if mean_lst_c is None else round(mean_lst_c, 6),
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
            outline=LST_VIS_OUTLINE_COLOR,
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
    point_radius = 1 if spacing_m <= 100 else 2 if spacing_m <= 500 else 3
    padding = 16 if spacing_m <= 100 else 48
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
) -> dict:
    points = generate_grid_points(metric_polygon, spacing_m)
    scene_count = 0
    footprint_metric: Polygon | None = None

    for hdf5_path in hdf5_file_paths:
        lst_scene = load_scene(hdf5_path, "Image_data/LST")
        qa_scene = load_scene(hdf5_path, "Image_data/QA_flag")
        scene_count += 1
        if footprint_metric is None:
            footprint_metric = lst_scene.footprint_metric

        for point in points:
            sample = sample_scene(lst_scene, point.lon, point.lat)
            qa_sample = sample_scene(qa_scene, point.lon, point.lat)
            if sample is None or qa_sample is None:
                continue

            lst_value, _, _ = sample
            qa_value, _, _ = qa_sample
            if not is_valid_qa_value(qa_value):
                continue

            point.sum_lst_c += lst_dn_to_celsius(lst_value, lst_scene)
            point.valid_count += 1

    output_path_obj = ensure_parent(output_path)
    with output_path_obj.open("w", encoding="utf-8") as handle:
        handle.write("point_id,lon,lat,x_6668,y_6668,valid_count,mean_lst_c\n")
        for point in points:
            mean_lst_c = ""
            if point.valid_count > 0:
                mean_lst_c = f"{point.sum_lst_c / point.valid_count:.6f}"
            handle.write(
                f"{point.point_id},{point.lon},{point.lat},{point.x_6668},{point.y_6668},{point.valid_count},{mean_lst_c}\n"
            )

    output_dir = output_path_obj.parent
    points_geojson_path = output_dir / f"sampling_points_{spacing_m}m.geojson"
    boundary_geojson_path = output_dir / f"sampling_boundary_{spacing_m}m.geojson"
    preview_path = output_dir / f"sampling_preview_{spacing_m}m.png"
    scene_preview_path = output_dir / f"scene_coverage_preview_{spacing_m}m.png"
    summary_path = output_dir / f"sampling_summary_{spacing_m}m.json"
    polygon_wgs84 = transform_polygon_to_wgs84(metric_polygon)

    write_geojson(str(points_geojson_path), [point_to_feature(point, spacing_m) for point in points])
    write_geojson(
        str(boundary_geojson_path),
        [polygon_to_feature(polygon_wgs84, {"area_name": area_name, "prefecture_name": prefecture_name})],
    )
    write_sampling_preview(
        str(preview_path),
        polygon_wgs84,
        points,
        spacing_m,
        value_fn=lambda point: None if point.valid_count == 0 else point.sum_lst_c / point.valid_count,
    )
    if footprint_metric is not None:
        write_sampling_preview(
            str(scene_preview_path),
            polygon_wgs84,
            points,
            spacing_m,
            transform_polygon_to_wgs84(footprint_metric),
            value_fn=lambda point: None if point.valid_count == 0 else point.sum_lst_c / point.valid_count,
        )

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
        "preview_path": str(preview_path),
        "scene_preview_path": str(scene_preview_path),
        "csv_path": str(output_path_obj),
        "approx_unique_pixels_per_scene": estimate_unique_pixels(area_m2, spacing_m),
        "lst_visualization_min_c": LST_VIS_MIN_C,
        "lst_visualization_max_c": LST_VIS_MAX_C,
    }
    write_summary(str(summary_path), summary)
    summary["summary_path"] = str(summary_path)
    return summary
