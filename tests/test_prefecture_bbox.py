import pytest

from prefecture_bbox import get_administrative_bbox, get_prefecture_bbox
from scripts.update_prefecture_bboxes import build_municipality_bboxes


def test_get_prefecture_bbox_keeps_partial_match_behavior() -> None:
    assert get_prefecture_bbox("愛知") == [136, 34, 138, 36]


def test_get_administrative_bbox_returns_nagoya_city_bbox() -> None:
    assert get_administrative_bbox("愛知県名古屋市") == [136, 35, 137, 36]


def test_get_administrative_bbox_raises_for_unknown_area() -> None:
    with pytest.raises(ValueError):
        get_administrative_bbox("名古屋市")


def test_build_municipality_bboxes_uses_only_supported_current_record_patterns() -> None:
    records = [
        {
            "municipality": "名古屋市",
            "ward_or_county": "",
            "area": "東区",
            "end_date": "",
            "bbox": [136.90, 35.16, 136.94, 35.19],
        },
        {
            "municipality": "名古屋市",
            "ward_or_county": "",
            "area": "西区",
            "end_date": "",
            "bbox": [136.86, 35.16, 136.91, 35.19],
        },
        {
            "municipality": "",
            "ward_or_county": "",
            "area": "岡崎市",
            "end_date": "",
            "bbox": [137.10, 34.90, 137.20, 35.00],
        },
        {
            "municipality": "海部郡",
            "ward_or_county": "",
            "area": "大治村",
            "end_date": "",
            "bbox": [136.80, 35.10, 136.85, 35.15],
        },
        {
            "municipality": "その他",
            "ward_or_county": "",
            "area": "対象外",
            "end_date": "",
            "bbox": [130.00, 30.00, 131.00, 31.00],
        },
    ]

    assert build_municipality_bboxes("愛知県", records) == {
        "愛知県名古屋市": [136, 35, 137, 36],
        "愛知県岡崎市": [137, 34, 138, 35],
        "愛知県海部郡大治村": [136, 35, 137, 36],
    }
