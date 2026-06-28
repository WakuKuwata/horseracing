# Quickstart / Validation: 血統適性特徴 (026)

前提: branch `026-pedigree-aptitude`、DB `horseracing`（localhost:15432, aiuma/aiuma）、025 基盤マージ済み。

## 1. 単体テスト（DB-free）
```bash
cd features
uv run ruff check src tests
uv run pytest -q                      # 既存 + 血統(集計/leak/parity/staleness/columns)
```
期待: 全緑。特に
- `tests/unit/test_pedigree_features.py`: 他産駒集計・距離/馬場条件付き・min_starts→NaN・debut に値。
- `tests/unit/test_pedigree_leak.py`: 自馬過去/今走・同日他産駒・未来 を変えても不変。
- materialize parity/columns/staleness が血統列込みで緑。

## 2. 実 DB 生成スモーク（materialize）
```bash
export DATABASE_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
cd features
uv run python -m horseracing_features materialize --out /tmp/feat026.parquet
```
期待: feature_version=features-007、血統列を含む materialized_columns、生成時間が予算内（~90s 目安）、fingerprint が horses 反映。

## 3. パリティ（実 DB, materialize==in-memory）
```bash
uv run python - <<'PY'
import datetime as dt; from pathlib import Path
from pandas.testing import assert_frame_equal
from horseracing_db.session import create_db_engine; from sqlalchemy.orm import Session
from horseracing_features.builder import build_feature_matrix
eng=create_db_engine(); win=dict(start_date=dt.date(2024,12,1),end_date=dt.date(2024,12,31))
with Session(eng) as s: a=build_feature_matrix(s,use_materialized=True,materialized_path=Path("/tmp/feat026.parquet"),**win)
with Session(eng) as s: b=build_feature_matrix(s,use_materialized=False,**win)
assert_frame_equal(a,b,check_exact=True,check_dtype=True); print("PARITY OK", a.shape, [c for c in a.columns if c.startswith(("sire_","damsire_"))])
PY
```
期待: PARITY OK、血統列が出力に含まれ非全 NaN（実データで sire_name から値が付く）。

## 4. 血統カバレッジ確認（値が付いているか）
```bash
uv run python - <<'PY'
import datetime as dt; from horseracing_db.session import create_db_engine; from sqlalchemy.orm import Session
from horseracing_features.builder import build_feature_matrix
eng=create_db_engine()
with Session(eng) as s:
    m=build_feature_matrix(s,start_date=dt.date(2024,1,1),end_date=dt.date(2024,12,31))
print("rows",len(m))
for c in ["sire_win_rate","sire_dist_band_win_rate","sire_starts","damsire_win_rate"]:
    print(c,"non-null %.1f%%"%(100*m[c].notna().mean()))
PY
```
期待: sire_win_rate の非null率が高い（~大半）、条件付きは min_starts でやや下がる、sire_starts は ZERO_OK。

## 5. 採用評価（OOS, 実データ）
```bash
cd training
uv run python -m horseracing_training feature-eval --drop-groups sire_aptitude,damsire_aptitude
# baseline=features-006 vs 候補=features-007
```
期待: AdoptionReport（平均 win LogLoss 差・ECE 差・fold 別勝敗・worst-fold 判定）。
SECONDARY 診断: market_edge / prior_starts バンド別 OOS（採否に使わない）。
**注意**: 採用は OOS 全体ゲートで機械判定。血統は効きどころが限定的なら全体改善は薄い可能性 — その場合 prior_starts セグメント診断で価値を確認し、採否は 020/023 と同じ客観ゲートに従う。

## 6. リーク/スキーマ不変の最終確認
```bash
cd features && uv run pytest -q -k "leak or parity or staleness or columns or schema"
```
期待: 全緑（憲法 II/III/V、スキーマ変更なし）。
