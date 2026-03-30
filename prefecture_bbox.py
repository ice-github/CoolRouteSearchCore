import json
from pathlib import Path


_DATA_PATH = Path(__file__).resolve().parent / "data" / "prefecture_bboxes.json"


def load_prefecture_bboxes() -> dict[str, list[int]]:
    with _DATA_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data["prefectures"]


def get_prefecture_bbox(prefecture_keyword: str) -> list[int]:
    prefectures = load_prefecture_bboxes()
    matched_name = next(
        (name for name in prefectures if prefecture_keyword in name),
        None,
    )
    if matched_name is None:
        raise ValueError(f"prefecture not found for keyword: {prefecture_keyword}")
    return prefectures[matched_name]
