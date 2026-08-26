"""【重要な契約: ラベルを一度も読まない】時間重みの半減期候補を日付だけで比較する。

このスクリプトが読むのは、レースの日付と ``entry_status='started'`` の出走頭数だけである。
着順・勝敗・winner NLL・払戻・オッズは一切読まず、結果テーブルにも接続しない。これにより、
半減期の選択に成績を使う「選択リーク」が構造的に起こらないようにする。

出力は候補ごとの記述統計だけであり、半減期の良否や採否は判定しない。新レジーム質量が
事前登録範囲 20〜35% に入るかという真偽値も、レース日だけから決まる補助情報である。

実行例:
    uv run --project training python scripts/recency_halflife.py
    uv run --project training python scripts/recency_halflife.py --json /tmp/halflife.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, text

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
)
HALF_LIFE_CANDIDATES_DAYS = (365, 2 * 365, 3 * 365, 5 * 365, 8 * 365, 12 * 365)
FLOOR = 0.05
NEW_REGIME_FROM = datetime.date(2025, 7, 1)
RECENT_YEARS_DAYS = 3 * 365
PREREGISTERED_REGIME_MASS_RANGE = (0.20, 0.35)

# SELECT 句を日付と started 頭数に限定する。race_id はレース単位に集約するためだけに使い、
# 結果・払戻・オッズを持つテーブルや列には一切触れない。
RACE_DISTRIBUTION_SQL = """
    SELECT r.race_date,
           count(*) AS started_count
    FROM races AS r
    JOIN race_horses AS rh
      ON rh.race_id = r.race_id
     AND rh.entry_status = 'started'
    GROUP BY r.race_id, r.race_date
    ORDER BY r.race_date
"""

# 主要カテゴリ別 ESS 用。**ここも日付とカテゴリだけ**で、結果・払戻・オッズには触れない。
# 見るのは target encoding の対象(騎手・調教師)と競馬場 — 時間重みで実効件数が痩せると
# TE の smoothing が prior へ寄る先が変わるため。
CATEGORY_SQL = """
    SELECT r.race_id, r.race_date, r.venue_code, rh.jockey_id, rh.trainer_id
    FROM races AS r
    JOIN race_horses AS rh
      ON rh.race_id = r.race_id
     AND rh.entry_status = 'started'
    ORDER BY r.race_date, r.race_id
"""


class RecencyModuleUnavailable(RuntimeError):
    """並行実装中の重みモジュールをまだ読み込めないことを表す。"""


def _load_recency_api():
    """重みの定義を一元化するため、必ず学習パッケージの実装を読み込む。"""
    try:
        from horseracing_training.recency import (
            RecencyWeightSpec,
            build_recency_weights,
            effective_sample_size,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise RecencyModuleUnavailable(
            "horseracing_training.recency はまだ無いか、まだ import できません。"
            "別担当による module の実装完了後に再実行してください。"
        ) from exc
    return RecencyWeightSpec, build_recency_weights, effective_sample_size


def _load_race_distribution(database_url: str) -> list[tuple[datetime.date, int]]:
    """ラベルへ接続せず、レース日と started 頭数だけを DB から取得する。"""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(RACE_DISTRIBUTION_SQL)).all()
    finally:
        engine.dispose()

    distribution: list[tuple[datetime.date, int]] = []
    for race_date, started_count in rows:
        # 時刻を黙って切り捨てると schema/driver の変化を隠すため、date 型以外は拒否する。
        if isinstance(race_date, datetime.datetime) or not isinstance(
            race_date, datetime.date
        ):
            raise TypeError(f"race_date が date ではありません: {race_date!r}")
        count = int(started_count)
        if count <= 0:
            raise ValueError(f"started 頭数が正ではありません: {count}")
        distribution.append((race_date, count))

    if not distribution:
        raise RuntimeError("started の出走馬がいるレースを DB から取得できませんでした。")
    return distribution


def _calculate_candidates(
    distribution,
    RecencyWeightSpec,
    build_recency_weights,
    effective_sample_size,
):
    """日付分布を馬行へ展開し、学習時と同じ関数で候補ごとの統計を計算する。"""
    race_dates = np.asarray([race_date for race_date, _ in distribution], dtype=object)
    started_counts = np.asarray([count for _, count in distribution], dtype=np.int64)

    # LightGBM が消費する単位は馬行なので、started 頭数だけ日付を反復する。synthetic race id は
    # 同一レースの行を束ねるためだけの連番であり、DB から新たな列を読む必要はない。
    row_race_ids = np.repeat(np.arange(len(distribution), dtype=np.int64), started_counts)
    row_race_dates = np.repeat(race_dates, started_counts)
    cutoff = max(race_dates)
    recent_from = cutoff - datetime.timedelta(days=RECENT_YEARS_DAYS)
    new_regime_mask = np.fromiter(
        (race_date >= NEW_REGIME_FROM for race_date in row_race_dates),
        dtype=bool,
        count=len(row_race_dates),
    )
    recent_mask = np.fromiter(
        (race_date >= recent_from for race_date in row_race_dates),
        dtype=bool,
        count=len(row_race_dates),
    )

    candidates = []
    for half_life_days in HALF_LIFE_CANDIDATES_DAYS:
        weights = np.asarray(
            build_recency_weights(
                row_race_ids,
                row_race_dates,
                cutoff=cutoff,
                spec=RecencyWeightSpec(
                    half_life_days=half_life_days,
                    floor=FLOOR,
                ),
            ),
            dtype=np.float64,
        )
        if weights.shape != row_race_dates.shape:
            raise RuntimeError(
                "build_recency_weights の返却行数が入力行数と一致しません: "
                f"{len(weights)} != {len(row_race_dates)}"
            )

        weight_sum = float(weights.sum())
        ess_total = float(effective_sample_size(weights))
        regime_mass = float(weights[new_regime_mask].sum() / weight_sum)
        recent_mass = float(weights[recent_mask].sum() / weight_sum)
        lower, upper = PREREGISTERED_REGIME_MASS_RANGE
        candidates.append(
            {
                "half_life_days": half_life_days,
                "ess_total": ess_total,
                "ess_ratio": ess_total / len(weights),
                "new_regime_mass_ratio": regime_mass,
                "recent_3y_mass_ratio": recent_mass,
                "new_regime_mass_in_20_35pct": lower <= regime_mass <= upper,
            }
        )

    report = {
        "contract": "race_date と started 頭数だけを使用し、ラベル・結果・払戻・オッズは不使用",
        "cutoff": cutoff.isoformat(),
        "new_regime_from": NEW_REGIME_FROM.isoformat(),
        "recent_3y_from": recent_from.isoformat(),
        "floor": FLOOR,
        "race_count": len(distribution),
        "row_count": len(row_race_dates),
        "candidates": candidates,
    }
    return report


def _print_report(report: dict) -> None:
    """人間が候補を比較できる表を出すが、良否の判定は加えない。"""
    print("半減期候補の時間重み統計（ラベル不使用）")
    print(
        f"cutoff={report['cutoff']}  races={report['race_count']:,}  "
        f"rows={report['row_count']:,}  floor={report['floor']:.2f}"
    )
    print(
        f"新レジーム={report['new_regime_from']} 以降  "
        f"直近3年={report['recent_3y_from']} 以降"
    )
    print()

    columns = (
        ("half_life_days", "half_life_days", lambda value: f"{value:,}"),
        ("ess_total", "ess_total", lambda value: f"{value:,.1f}"),
        ("ess_ratio", "ess/all", lambda value: f"{value:.2%}"),
        ("new_regime_mass_ratio", "new_regime_mass", lambda value: f"{value:.2%}"),
        ("recent_3y_mass_ratio", "recent_3y_mass", lambda value: f"{value:.2%}"),
        (
            "new_regime_mass_in_20_35pct",
            "regime_20_35pct",
            lambda value: "true" if value else "false",
        ),
    )
    rendered = [
        [formatter(candidate[key]) for key, _, formatter in columns]
        for candidate in report["candidates"]
    ]
    widths = [
        max(len(title), *(len(row[index]) for row in rendered))
        for index, (_, title, _) in enumerate(columns)
    ]
    print(" | ".join(title.ljust(width) for width, (_, title, _) in zip(widths, columns)))
    print("-+-".join("-" * width for width in widths))
    for row in rendered:
        print(" | ".join(value.rjust(width) for value, width in zip(row, widths)))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        help="表と同じ統計を JSON で保存するパス",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        RecencyWeightSpec, build_recency_weights, effective_sample_size = _load_recency_api()
    except RecencyModuleUnavailable as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    distribution = _load_race_distribution(database_url)
    report = _calculate_candidates(
        distribution,
        RecencyWeightSpec,
        build_recency_weights,
        effective_sample_size,
    )
    _print_report(report)

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\nJSON: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
