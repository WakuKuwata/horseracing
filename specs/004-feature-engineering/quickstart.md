# Quickstart: 特徴量生成の検証

実装後に特徴量生成が end-to-end で動き、リーク安全であることを確認する手順。

## 前提

- Feature 001 適用済み + Feature 002 で取込済みの PostgreSQL (`DATABASE_URL`)。
- Docker (testcontainers 用)。
- `features/` パッケージの依存をインストール (`uv sync`、`horseracing-db` にパス依存)。

## セットアップ

```bash
cd features
uv sync
export DATABASE_URL=postgresql+psycopg://...
```

## 特徴量生成 (ローカルスモーク)

```bash
uv run python -c "
import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from horseracing_features.builder import build_feature_matrix
from horseracing_features.registry import model_input_features
from horseracing_db.session import create_db_engine
with Session(create_db_engine()) as s:
    fm = build_feature_matrix(s, start_date=datetime.date(2007,1,1))
    print('rows', len(fm), 'cols', list(fm.columns))
    print('model input features', model_input_features())
"
```

期待: 固定スキーマの FeatureMatrix。新馬行の過去成績系が NaN(0 でない)、is_debut=True。結果確定
odds/popularity が列に含まれない。

## テスト

Docker 必須(testcontainers が PostgreSQL を起動し `db/` migration を head まで適用)。

```bash
cd features
uv run pytest                 # 全テスト
uv run pytest tests/unit      # history as-of・欠損/フラグ・registry 強制・決定論(合成データ)
uv run pytest -m integration  # 実 DB で as-of リーク検査
```

検証する受け入れ基準:

- **SC-001 (リーク)**: レース R の過去成績特徴量が `race_date >= R` の race を 1 件も使わない(合成データで
  「未来の好成績を仕込んでも R の特徴が変わらない」ことを確認)。
- **SC-002 (Unknown≠0)**: 新馬の過去成績系が NaN、is_debut/has_past_race/past_race_count が正しい。
- **SC-003 (完走前提)**: avg_finish/win_rate が非完走・非出走を除外、件数系は 0 埋めされない。
- **SC-004 (メタデータ)**: 全列が registry に metadata を持ち、未登録列・結果確定オッズ混入が fail-fast。
- **SC-005 (決定論)**: 同一入力・同一 as-of で 2 回生成して完全一致。
- **SC-006 (P2)**: target encoding が train 境界より前のみで fit。

## リーク検査の考え方(SC-001 の具体)

合成データで、ある馬の「R より後のレースで 1 着」を追加しても、R の `avg_finish`/`win_rate`/`prev_finish`
が変化しないことを assert する(未来が漏れていれば変化する)。同日レースについても同様に確認する。
