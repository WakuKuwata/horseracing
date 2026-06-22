# horseracing-features

リーク安全な特徴量生成。固定スキーマの FeatureMatrix を出力し、評価ハーネス(Feature 003)と将来の
学習(Feature 005)が消費する。`horseracing-db` に依存。

- 仕様: [specs/004-feature-engineering](../specs/004-feature-engineering/)
- スタック: Python 3.12, pandas, numpy, SQLAlchemy 2.0

## リーク防止 / 責務境界 (FR-013, 憲法 II)

- **過去成績は as-of `race_date < R`(同日除外)**。日単位集約 + (cumsum − 当日) と
  `merge_asof(allow_exact_matches=False)` の二機構で、未来・同日の結果が漏れない。
- **結果確定時の `odds`/`popularity` はモデル特徴量に使わない**(評価専用)。FeatureRegistry に登録しない
  ため、混入したら未登録列として fail-fast で検出される。
- `availability_timing='post_result'` の特徴量は `model_input_features()` から機械的に除外。
- **target encoding(P2)** は train 境界より前のみで fit(`fit_target_encoding(train_cutoff=...)`)。

## セットアップ

```bash
cd features
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 001 適用 + 002 取込済み
```

## 特徴量生成

```python
import datetime
from sqlalchemy.orm import Session
from horseracing_db.session import create_db_engine
from horseracing_features.builder import build_feature_matrix
from horseracing_features.registry import model_input_features

with Session(create_db_engine()) as s:
    fm = build_feature_matrix(s, start_date=datetime.date(2008, 1, 1))
    X = fm[model_input_features()]   # post_result / 識別列を除いたモデル入力
```

CLI(materialize、P2):

```bash
uv run python -m horseracing_features build-features --from 2008-01-01 --to 2008-12-31 --out fm.parquet
```

## テスト

Docker 必須(testcontainers が PostgreSQL を起動し `db/` migration を head まで適用)。

```bash
uv run pytest                 # 全テスト
uv run pytest tests/unit      # リーク検査・欠損/フラグ・registry・encoding・materialize(合成データ)
uv run pytest -m integration  # 実 DB で as-of リーク検査
```

## 欠損・固定スキーマ

- 過去成績ゼロ(新馬)は過去成績系が `NaN`(Unknown、0 と区別)。`is_debut`/`has_past_race`/
  `past_race_count`/`is_low_history`(実出走 1〜2 走)フラグを併設。
- 完走前提系(avg_finish/win_rate/prev_last3f)は finished のみ。career_starts は started(中止/失格含む、
  取消/除外除く)。取消/除外/中止は件数系で別保持(0 可)。
- 列定義・metadata は [data-model.md](../specs/004-feature-engineering/data-model.md) を正本。
