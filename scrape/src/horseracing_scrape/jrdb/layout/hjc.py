"""HJC 払戻情報データ — 第４a版 (2024.09.29). Transcribed from the public spec
``https://jrdb.com/program/Hjc/hjcdata_doc.txt`` (fetched 2026-11-08). Record length 444.

Repeating groups are expanded into numbered fields (``win_1_number`` …). 枠連 (3 × 9 bytes at
相対 81) is ``skip``: the DB has no bet type for it (7 券種, contracts §1).
"""

from __future__ import annotations

from . import Field, Layout, race_key_fields


def _group(prefix: str, start: int, count: int, key_len: int, pay_len: int, *, target="current"):
    out: list[Field] = []
    size = key_len + pay_len
    for i in range(count):
        s = start + i * size
        key_name = "number" if key_len == 2 else "combo"
        out.append(
            Field(
                f"{prefix}_{i + 1}_{key_name}",
                s,
                key_len,
                "X",
                "9" * key_len,
                codec="code",
                target=target,
            )
        )
        out.append(
            Field(
                f"{prefix}_{i + 1}_yen",
                s + key_len,
                pay_len,
                "Z",
                "Z" * (pay_len - 1) + "9",
                target=target,
            )
        )
    return out


LAYOUT = Layout(
    kind="HJC",
    version="4",
    record_length=444,
    doc="https://jrdb.com/program/Hjc/hjcdata_doc.txt 第４a版 2024.09.29 (fetched 2026-11-08)",
    fields=(
        *race_key_fields(),
        *_group("win", 9, 3, 2, 7),  # 単勝 3 × (馬番 2 + 払戻 7) → 〜35
        *_group("place", 36, 5, 2, 7),  # 複勝 5 × 9 → 〜80
        *_group("wakuren", 81, 3, 2, 7, target="skip"),  # 枠連 3 × 9 → 〜107
        *_group("quinella", 108, 3, 4, 8),  # 馬連 3 × 12 → 〜143
        *_group("wide", 144, 7, 4, 8),  # ワイド 7 × 12 → 〜227
        *_group("exacta", 228, 6, 4, 8),  # 馬単 6 × 12 → 〜299
        *_group("trio", 300, 3, 6, 8),  # ３連複 3 × 14 → 〜341
        *_group("trifecta", 342, 6, 6, 9),  # ３連単 6 × 15 → 〜431 (払戻 9 桁)
        Field("reserved_432", 432, 10, "X", target="skip"),
        Field("reserved_442", 442, 1, "X", target="skip"),
    ),
)
