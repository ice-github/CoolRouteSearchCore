import json
import sys
from datetime import datetime, timezone
from math import ceil, floor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from administrative_division import AdministrativeDivisionInfo, get_bbox_from_shapefile

PREFECTURES = [
    "北海道",
    "青森",
    "岩手",
    "宮城",
    "秋田",
    "山形",
    "福島",
    "茨城",
    "栃木",
    "群馬",
    "埼玉",
    "千葉",
    "東京",
    "神奈川",
    "新潟",
    "富山",
    "石川",
    "福井",
    "山梨",
    "長野",
    "岐阜",
    "静岡",
    "愛知",
    "三重",
    "滋賀",
    "京都",
    "大阪",
    "兵庫",
    "奈良",
    "和歌山",
    "鳥取",
    "島根",
    "岡山",
    "広島",
    "山口",
    "徳島",
    "香川",
    "愛媛",
    "高知",
    "福岡",
    "佐賀",
    "長崎",
    "熊本",
    "大分",
    "宮崎",
    "鹿児島",
    "沖縄",
]


def canonical_prefecture_name(name: str) -> str:
    if name == "北海道":
        return name
    if name == "東京":
        return f"{name}都"
    if name in {"京都", "大阪"}:
        return f"{name}府"
    return f"{name}県"


def build_prefecture_bboxes(download_dir: str = "download", workspace_dir: str = "workspace") -> dict:
    division_info = AdministrativeDivisionInfo(download_dir, workspace_dir)
    prefecture_bboxes: dict[str, list[int]] = {}

    for prefecture_name in PREFECTURES:
        division = division_info.get_administrative_division(prefecture_name)
        min_lon, min_lat, max_lon, max_lat = get_bbox_from_shapefile(division.shp_path)
        prefecture_bboxes[canonical_prefecture_name(prefecture_name)] = [
            floor(min_lon),
            floor(min_lat),
            ceil(max_lon),
            ceil(max_lat),
        ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "国土数値情報 行政区域（ポリゴン）",
        "bbox_definition": "[floor(min_lon), floor(min_lat), ceil(max_lon), ceil(max_lat)]",
        "prefectures": prefecture_bboxes,
    }


def main() -> None:
    output_path = REPO_ROOT / "data" / "prefecture_bboxes.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = build_prefecture_bboxes()
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
