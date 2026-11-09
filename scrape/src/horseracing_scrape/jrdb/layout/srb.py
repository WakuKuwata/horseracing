"""SRB 成績レースデータ — 第２版b (2015.06.01). Transcribed from the public spec
``https://jrdb.com/program/Sed/srb_doc.txt`` (fetched 2026-11-08). Record length 852.
Shipped inside the SED archive.

Adopted: 先頭馬のハロンタイム 18 × 3 (0.1 秒; a non-200 m remainder makes the FIRST split 100/150 m,
see the doc's ※１) and the six トラックバイアス cells. コーナー位置取り (64 bytes × 4),
ペースアップ位置
and レースコメント are ``skip`` (no column; the comment is JRDB editorial).
"""

from __future__ import annotations

from . import Field, Layout, race_key_fields

LAYOUT = Layout(
    kind="SRB",
    version="2",
    record_length=852,
    doc="https://jrdb.com/program/Sed/srb_doc.txt 第２版b 2015.06.01 (fetched 2026-11-08)",
    fields=(
        *race_key_fields(),
        *(Field(f"lap_{i + 1:02d}", 9 + 3 * i, 3, "9", "999", scale=10) for i in range(18)),
        Field("corner_positions_1", 63, 64, "X", target="skip"),
        Field("corner_positions_2", 127, 64, "X", target="skip"),
        Field("corner_positions_3", 191, 64, "X", target="skip"),
        Field("corner_positions_4", 255, 64, "X", target="skip"),
        Field("pace_up_position", 319, 2, "9", target="skip"),
        Field("bias_corner_1", 321, 3, "X", codec="code"),  # 内・中・外 1 byte each
        Field("bias_corner_2", 324, 3, "X", codec="code"),
        Field("bias_backstretch", 327, 3, "X", codec="code"),
        Field("bias_corner_3", 330, 3, "X", codec="code"),
        Field("bias_corner_4", 333, 5, "X", codec="code"),  # 最内・内・中・外・大外
        Field("bias_straight", 338, 5, "X", codec="code"),
        Field("race_comment", 343, 500, "X", target="skip"),
        Field("reserved_843", 843, 8, "X", target="skip"),
    ),
)
