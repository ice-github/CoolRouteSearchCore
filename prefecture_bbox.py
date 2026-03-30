import json
from pathlib import Path


_DATA_PATH = Path(__file__).resolve().parent / "data" / "prefecture_bboxes.json"


def load_administrative_bboxes() -> dict[str, dict[str, list[int]]]:
    with _DATA_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_prefecture_bboxes() -> dict[str, list[int]]:
    return load_administrative_bboxes()["prefectures"]


def load_municipality_bboxes() -> dict[str, list[int]]:
    return load_administrative_bboxes().get("municipalities", {})


def get_administrative_bbox(area_keyword: str) -> list[int]:
    data = load_administrative_bboxes()
    if area_keyword in data["prefectures"]:
        return data["prefectures"][area_keyword]
    if area_keyword in data.get("municipalities", {}):
        return data["municipalities"][area_keyword]
    raise ValueError(f"administrative area not found: {area_keyword}")


def get_prefecture_bbox(prefecture_keyword: str) -> list[int]:
    prefectures = load_prefecture_bboxes()
    matched_name = next(
        (name for name in prefectures if prefecture_keyword in name),
        None,
    )
    if matched_name is None:
        raise ValueError(f"prefecture not found for keyword: {prefecture_keyword}")
    return prefectures[matched_name]
