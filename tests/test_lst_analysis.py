from pathlib import Path

import pytest
from shapely.geometry import Polygon

from analysis.runner import (
    LST_VIS_MAX_C,
    LST_VIS_MIN_C,
    SamplingPoint,
    generate_grid_points,
    load_scene,
    point_to_feature,
    sample_scene,
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


def test_point_to_feature_includes_mean_lst_c() -> None:
    point = SamplingPoint(
        point_id=1,
        lon=136.9,
        lat=35.1,
        x_metric=1_000.0,
        y_metric=2_000.0,
        x_6668=1_000.0,
        y_6668=2_000.0,
        sum_lst_c=72.0,
        valid_count=3,
    )

    feature = point_to_feature(point, 1000)

    assert feature["properties"]["valid_count"] == 3
    assert feature["properties"]["mean_lst_c"] == 24.0
