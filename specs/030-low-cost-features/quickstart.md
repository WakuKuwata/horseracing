# Quickstart / Validation: 低コスト特徴拡充 (030)

前提: branch `030-low-cost-features`、DB `horseracing`、025/026 マージ済み(features-007/lgbm-026)。

## 1. 単体テスト(DB-free)
```bash
cd features && uv run ruff check src tests && uv run pytest -q
```
期待: 全緑。特に
- `test_lowcost_features.py`: 斤量(carried_weight/_change/_ratio/_rel)・place/show率・dist_band複勝・人(複勝/コンビ/乗り替わり)・venue率・season。
- `test_lowcost_leak.py`: 今走結果(着順/corner/running_style)・同日・未来 を変えても 030 列不変。**running_style を参照しない**(grep でソース確認)。
- materialize parity/columns が 030 列込みで緑、features-008 リテラル更新済み。

## 2. 実 DB 生成スモーク
```bash
export DATABASE_URL="postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
cd features && uv run python -m horseracing_features materialize --out /tmp/feat030.parquet
```
期待: feature_version=features-008、as-of 7 列収録(静的 5 列は materialize されない)。

## 3. パリティ(実 DB)
```bash
uv run python - <<'PY'
import datetime as dt; from pathlib import Path
from pandas.testing import assert_frame_equal
from horseracing_db.session import create_db_engine; from sqlalchemy.orm import Session
from horseracing_features.builder import build_feature_matrix
eng=create_db_engine(); win=dict(start_date=dt.date(2024,12,1),end_date=dt.date(2024,12,31))
with Session(eng) as s: a=build_feature_matrix(s,use_materialized=True,materialized_path=Path("/tmp/feat030.parquet"),**win)
with Session(eng) as s: b=build_feature_matrix(s,use_materialized=False,**win)
assert_frame_equal(a,b,check_exact=True,check_dtype=True); print("PARITY OK", a.shape)
PY
```

## 4. カバレッジ確認
```bash
# carried_weight 非null ~100%(斤量)、place_rate は非デビューで高い、venue 率は母数で一部 NaN
```

## 5. 採用評価(per-group, 事前登録)
```bash
cd training
# baseline=features-007（030 全群 drop）
GROUPS="handicap,season,place_rate,human_form_plus,course_aptitude"
# 各 group g 単独: candidate=features-007+g
for g in handicap season place_rate human_form_plus course_aptitude; do
  others=$(echo $GROUPS | tr ',' '\n' | grep -v "^$g$" | paste -sd, -)
  echo "== group $g =="
  uv run python -m horseracing_training feature-eval --drop-groups "$GROUPS" --candidate-drop-groups "$others"
done
```
期待: 各 group の AdoptionReport（win LogLoss 差・ECE・fold・worst-fold）。通過群の和集合を features-008 として採用→ serving 再学習(lgbm-030)。`feature-ablation` は診断。

## 6. リーク/スキーマ不変
```bash
cd features && uv run pytest -q -k "leak or parity or columns or schema or lowcost"
```
期待: 全緑(憲法 II/III/V、head 0006)。
