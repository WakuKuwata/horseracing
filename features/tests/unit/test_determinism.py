"""US1 (SC-005): deterministic feature generation."""

from __future__ import annotations

import pandas as pd

from horseracing_features.builder import assemble_feature_matrix
from tests._frames import make_frames

_SPECS = [
    {"race_id": "200801010101", "race_date": "2008-01-01",
     "horses": [{"horse_id": "H1", "horse_number": 1, "finish_order": 1},
                {"horse_id": "H2", "horse_number": 2, "finish_order": 2}]},
    {"race_id": "200802010101", "race_date": "2008-02-01",
     "horses": [{"horse_id": "H1", "horse_number": 1, "finish_order": 2},
                {"horse_id": "H2", "horse_number": 2, "finish_order": 1}]},
]


def test_assemble_deterministic():
    a = assemble_feature_matrix(make_frames(_SPECS))
    b = assemble_feature_matrix(make_frames(_SPECS))
    pd.testing.assert_frame_equal(a, b)


# --- 入力の行順に依存しないこと(2026-08-23) --------------------------------------------------
#
# 上の `test_assemble_deterministic` は同じ frames を 2 回組み立てるだけなので、
# **繰り返し可能性**しか見ていない。順序依存はそこを素通りする。
#
# 実際に素通りしていた: `_recent_rate` の並べ替えキーが [jockey_id, race_date] だけで、
# 同日内の順序は read_sql が返した順(race_horses / race_results に ORDER BY は無い)。
# 騎手は 1 開催日あたり平均 4.11 回騎乗するので 20 件窓の境界はほぼ常に日の途中に落ちる。
# 実 DB で入力行をシャッフルして再ビルドすると jockey_recent_win_rate が
# **12.21% の行で変化し、最大差 0.15**(平均 ~0.08 の率に対して)。
# PostgreSQL は行が書き換わるまで heap 順を返すので気づかれなかったが、
# entries/odds の upsert は日常的に行を書き換える。

def _same_day_specs():
    """1 人の騎手が同じ日に多数騎乗する構成(20 件窓の境界が日の途中に落ちる)."""
    specs = []
    for day in range(1, 7):
        for r in range(6):
            specs.append({
                "race_id": f"20080{day}0101{r:02d}",
                "race_date": f"2008-0{day}-01",
                "race_number": r + 1,
                "horses": [
                    # 同一騎手 J1 が毎レース騎乗し、勝ったり負けたりする
                    {"horse_id": f"H{day}{r}a", "horse_number": 1, "jockey_id": "J1",
                     "finish_order": 1 if (day + r) % 2 == 0 else 2},
                    {"horse_id": f"H{day}{r}b", "horse_number": 2, "jockey_id": "J2",
                     "finish_order": 2 if (day + r) % 2 == 0 else 1},
                ],
            })
    return specs


def _shuffled(frames, seed: int):
    from horseracing_features.loader import Frames

    def sh(df):
        return df.sample(frac=1, random_state=seed).reset_index(drop=True)

    return Frames(races=sh(frames.races), race_horses=sh(frames.race_horses),
                  race_results=sh(frames.race_results), horses=sh(frames.horses))


def test_assemble_is_invariant_to_input_row_order():
    """入力の行順を変えても出力がビット一致すること。

    read_sql には ORDER BY が無いので、行順は「たまたま安定しているだけ」の性質。
    それに依存した特徴は、データが 1 行も変わらなくても値が変わる。
    """
    specs = _same_day_specs()
    base = assemble_feature_matrix(make_frames(specs))
    for seed in (1, 7, 99):
        other = assemble_feature_matrix(_shuffled(make_frames(specs), seed))
        pd.testing.assert_frame_equal(base, other, check_exact=True)
