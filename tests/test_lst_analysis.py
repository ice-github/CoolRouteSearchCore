from pathlib import Path

import pytest
from PIL import Image
from shapely.geometry import Polygon

from analysis.runner import (
    LST_VIS_MAX_C,
    LST_VIS_MIN_C,
    SamplingPoint,
    build_sampling_surface_figure,
    draw_points,
    generate_grid_points,
    load_scene,
    point_to_feature,
    preview_point_radius,
    sample_scene,
    write_sampling_preview_set,
    write_sampling_surface_set,
    write_sampling_surface_views,
    temperature_to_color,
    transform_polygon_to_metric,
)
from lst_analysis import deduplicate_urls, infer_prefecture_name


SAMPLE_HDF5 = Path("/home/ubuntu/workspace/CoolRouteSearchCore/download/GC1SG1_20240101A01D_T0529_L2SG_LST_Q_3000.h5")


def test_deduplicate_urls_preserves_order() -> None:
    assert deduplicate_urls(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_infer_prefecture_name_uses_area_prefix() -> None:
    assert infer_prefecture_name("愛知県名古屋市") == "愛知県"


def test_infer_prefecture_name_raises_for_unknown_area() -> None:
    with pytest.raises(ValueError):
        infer_prefecture_name("名古屋市")


def test_load_scene_reads_hdf5_metadata() -> None:
    scene = load_scene(str(SAMPLE_HDF5), "Image_data/LST")

    assert scene.width == 4800
    assert scene.height == 4800
    assert scene.band_tags["Slope"] == "0.02"
    assert scene.band_tags["Error_DN"] == "65535"
    assert scene.footprint_metric.bounds[0] < scene.footprint_metric.bounds[2]


def test_sample_scene_returns_value_for_nagoya_point() -> None:
    scene = load_scene(str(SAMPLE_HDF5), "Image_data/LST")

    sample = sample_scene(scene, 136.8855, 35.1077)

    assert sample is not None
    value, row, col = sample
    assert value >= 0
    assert row >= 0
    assert col >= 0


def test_generate_grid_points_uses_metric_spacing() -> None:
    polygon = Polygon([(0, 0), (3000, 0), (3000, 3000), (0, 3000)])

    points = generate_grid_points(polygon, 1000)

    assert len(points) == 9
    assert points[0].x_metric == 500
    assert points[0].y_metric == 500


def test_transform_polygon_to_metric_preserves_polygon_shape() -> None:
    polygon = Polygon([(136.8, 35.0), (136.9, 35.0), (136.9, 35.1), (136.8, 35.1)])

    transformed = transform_polygon_to_metric(polygon)

    assert transformed.area > 0


def test_temperature_to_color_clips_to_fixed_scale() -> None:
    low = temperature_to_color(LST_VIS_MIN_C - 10)
    mid = temperature_to_color((LST_VIS_MIN_C + LST_VIS_MAX_C) / 2)
    high = temperature_to_color(LST_VIS_MAX_C + 10)

    assert low == temperature_to_color(LST_VIS_MIN_C)
    assert high == temperature_to_color(LST_VIS_MAX_C)
    assert low != high
    assert mid != low
    assert mid != high


def test_point_to_feature_includes_temperature_stats() -> None:
    point = SamplingPoint(
        point_id=1,
        lon=136.9,
        lat=35.1,
        x_metric=1_000.0,
        y_metric=2_000.0,
        x_6668=1_000.0,
        y_6668=2_000.0,
        sum_lst_c=72.0,
        min_lst_c=20.0,
        max_lst_c=28.0,
        valid_count=3,
    )

    feature = point_to_feature(point, 1000)

    assert feature["properties"]["valid_count"] == 3
    assert feature["properties"]["min_lst_c"] == 20.0
    assert feature["properties"]["mean_lst_c"] == 24.0
    assert feature["properties"]["max_lst_c"] == 28.0


def test_write_sampling_preview_set_writes_min_mean_max_images(tmp_path: Path) -> None:
    polygon = Polygon([(136.8, 35.0), (136.9, 35.0), (136.9, 35.1), (136.8, 35.1)])
    point = SamplingPoint(
        point_id=1,
        lon=136.85,
        lat=35.05,
        x_metric=1_000.0,
        y_metric=2_000.0,
        x_6668=1_000.0,
        y_6668=2_000.0,
        sum_lst_c=72.0,
        min_lst_c=20.0,
        max_lst_c=28.0,
        valid_count=3,
    )

    outputs = write_sampling_preview_set(tmp_path, "sampling_preview", polygon, [point], 1000)

    assert set(outputs) == {"min", "mean", "max"}
    for path_str in outputs.values():
        assert Path(path_str).exists()


def test_write_sampling_surface_set_writes_min_mean_max_html(tmp_path: Path) -> None:
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.85,
            lat=35.05,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=72.0,
            min_lst_c=20.0,
            max_lst_c=28.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.86,
            lat=35.05,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=84.0,
            min_lst_c=24.0,
            max_lst_c=30.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.85,
            lat=35.06,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=90.0,
            min_lst_c=25.0,
            max_lst_c=31.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.86,
            lat=35.06,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=96.0,
            min_lst_c=26.0,
            max_lst_c=32.0,
            valid_count=3,
        ),
    ]

    outputs = write_sampling_surface_set(tmp_path, "sampling_surface", points, 1000)

    assert set(outputs) == {"min", "mean", "max"}
    for path_str in outputs.values():
        path = Path(path_str)
        assert path.exists()
        assert path.suffix == ".html"


def test_build_sampling_surface_figure_uses_temperature_for_height_and_color() -> None:
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.85,
            lat=35.05,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=72.0,
            min_lst_c=20.0,
            max_lst_c=28.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.86,
            lat=35.05,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=84.0,
            min_lst_c=24.0,
            max_lst_c=30.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.85,
            lat=35.06,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=90.0,
            min_lst_c=25.0,
            max_lst_c=31.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.86,
            lat=35.06,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=96.0,
            min_lst_c=26.0,
            max_lst_c=32.0,
            valid_count=3,
        ),
    ]

    figure = build_sampling_surface_figure(points, "mean")
    trace = figure.data[0]

    assert trace.type == "surface"
    assert trace.z == trace.surfacecolor
    assert trace.cmin is None
    assert trace.cmax is None
    assert figure.layout.scene.zaxis.range is None


def test_build_sampling_surface_figure_uses_actual_range_for_high_values() -> None:
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.85,
            lat=35.05,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=160.0,
            min_lst_c=150.0,
            max_lst_c=180.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.86,
            lat=35.05,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=150.0,
            min_lst_c=145.0,
            max_lst_c=170.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.85,
            lat=35.06,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=155.0,
            min_lst_c=140.0,
            max_lst_c=175.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.86,
            lat=35.06,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=165.0,
            min_lst_c=148.0,
            max_lst_c=182.0,
            valid_count=3,
        ),
    ]

    figure = build_sampling_surface_figure(points, "max")
    trace = figure.data[0]

    assert trace.type == "surface"
    assert trace.cmin is None
    assert trace.cmax is None
    assert figure.layout.scene.zaxis.range is None


def test_write_sampling_surface_views_renders_multiple_pngs(tmp_path: Path) -> None:
    points = [
        SamplingPoint(
            point_id=1,
            lon=136.85,
            lat=35.05,
            x_metric=1_000.0,
            y_metric=2_000.0,
            x_6668=1_000.0,
            y_6668=2_000.0,
            sum_lst_c=72.0,
            min_lst_c=20.0,
            max_lst_c=28.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=2,
            lon=136.86,
            lat=35.05,
            x_metric=2_000.0,
            y_metric=2_000.0,
            x_6668=2_000.0,
            y_6668=2_000.0,
            sum_lst_c=84.0,
            min_lst_c=24.0,
            max_lst_c=30.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=3,
            lon=136.85,
            lat=35.06,
            x_metric=1_000.0,
            y_metric=3_000.0,
            x_6668=1_000.0,
            y_6668=3_000.0,
            sum_lst_c=90.0,
            min_lst_c=25.0,
            max_lst_c=31.0,
            valid_count=3,
        ),
        SamplingPoint(
            point_id=4,
            lon=136.86,
            lat=35.06,
            x_metric=2_000.0,
            y_metric=3_000.0,
            x_6668=2_000.0,
            y_6668=3_000.0,
            sum_lst_c=96.0,
            min_lst_c=26.0,
            max_lst_c=32.0,
            valid_count=3,
        ),
    ]

    outputs = write_sampling_surface_views(tmp_path, "sampling_surface", points, 1000)

    assert set(outputs) == {"iso", "low_north", "low_east"}
    rendered = {name: Image.open(path) for name, path in outputs.items()}
    try:
        for image in rendered.values():
            assert image.size == (2400, 1800)
        assert rendered["iso"].tobytes() != rendered["low_north"].tobytes()
        assert rendered["iso"].tobytes() != rendered["low_east"].tobytes()
    finally:
        for image in rendered.values():
            image.close()


def test_draw_points_uses_fill_only_without_outline() -> None:
    class DummyDraw:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[float, float, float, float], dict]] = []

        def ellipse(self, bbox, **kwargs) -> None:
            self.calls.append((bbox, kwargs))

    dummy_draw = DummyDraw()
    polygon = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
    point = SamplingPoint(
        point_id=1,
        lon=0.5,
        lat=0.5,
        x_metric=500.0,
        y_metric=500.0,
        x_6668=500.0,
        y_6668=500.0,
        sum_lst_c=24.0,
        min_lst_c=24.0,
        max_lst_c=24.0,
        valid_count=1,
    )

    draw_points(
        dummy_draw,
        [point],
        polygon.bounds,
        100,
        100,
        0,
        2,
        lambda sampled_point: (sampled_point.lon, sampled_point.lat),
        value_fn=lambda sampled_point: sampled_point.sum_lst_c / sampled_point.valid_count,
    )

    assert len(dummy_draw.calls) == 1
    _, kwargs = dummy_draw.calls[0]
    assert "outline" not in kwargs


def test_preview_point_radius_scales_for_100m_and_1000m() -> None:
    assert preview_point_radius(100) == 2
    assert preview_point_radius(1000) == 15
