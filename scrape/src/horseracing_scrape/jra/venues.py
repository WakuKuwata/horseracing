"""競馬場名 → JRA 2 桁コード(Feature 176, research D11).

公式ページの見出し「4回東京11日」の競馬場名を ``build_race_id`` が受ける track_code に写す。
未知の名前は None(そのレースは書かない)。値は netkeiba 側の ``NETKEIBA_TO_JRAVAN_VENUE`` と同じ
2 桁コードであり、単体テストで一致を固定する。
"""

from __future__ import annotations

VENUE_NAME_TO_CODE: dict[str, str] = {
    "札幌": "01",
    "函館": "02",
    "福島": "03",
    "新潟": "04",
    "東京": "05",
    "中山": "06",
    "中京": "07",
    "京都": "08",
    "阪神": "09",
    "小倉": "10",
}

CODE_TO_VENUE_NAME: dict[str, str] = {v: k for k, v in VENUE_NAME_TO_CODE.items()}


def venue_code(name: str | None) -> str | None:
    """2 桁コード、未知名は None。前後の空白だけ許す(表記ゆれの吸収はしない=fail-closed)。"""
    if name is None:
        return None
    return VENUE_NAME_TO_CODE.get(name.strip())
