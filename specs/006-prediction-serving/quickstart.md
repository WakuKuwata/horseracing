# Quickstart: 予測 serving の検証

実装後に「推論 → 永続化 → 監査」が end-to-end で動き、リーク無し・決定論・スキーマ整合を確認する手順。

## 前提

- Feature 001 適用 + 002 取込済み + 005 で **active モデル + 成果物**(model.txt/calibrator.pkl/
  preprocessor.pkl)が保存済みの PostgreSQL。
- Docker(testcontainers 用)。
- `serving/` の依存をインストール(`uv sync`、db/features/eval/training にパス依存)。

## セットアップ

```bash
cd serving
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 取込済み + active モデル保存済み DB
```

## 推論(ローカルスモーク)

```bash
# レース指定
uv run python -m horseracing_serving predict --race-id 200801010101
# 日付指定(当日の対象レース全件)
uv run python -m horseracing_serving predict --date 2008-01-05
# モデル明示(active が複数/不在のとき)
uv run python -m horseracing_serving predict --race-id 200801010101 --model-version lightgbm-win-v1
```

期待: active モデルをロード → 対象レースの as-of 特徴 → 校正済み win/top2/top3 → `prediction_runs` /
`race_predictions` / `feature_snapshots` に保存。出走全頭の整合的な確率と保存件数を表示。

## テスト

Docker 必須(testcontainers が PostgreSQL を起動し `db/` migration を head まで適用)。

```bash
cd serving
uv run pytest                 # 全テスト
uv run pytest tests/unit      # 整合性・前処理器往復・スキーマ不一致 fail-fast・logic_version(合成データ)
uv run pytest -m integration  # 実 DB で推論→保存→監査→決定論→リーク→未来レース as-of
```

検証する受け入れ基準:

- **SC-001 (整合性+保存)**: 任意の対象レースで出走全頭の `0<=win<=top2<=top3<=1`・Σ 許容内の予測が 3 テーブルに
  保存される(`PROB_MONOTONIC` を満たす)。
- **SC-002 (決定論)**: 同一(race, model, logic_version)で 2 回推論 → `race_predictions` が完全一致(append-only で
  2 run)。
- **SC-003 (未来レース)**: `race_results` 無しのレースでも推論・保存が完了。
- **SC-004 (リーク無し+スキーマ整合)**: 結果確定オッズ/人気/着順を変えても予測不変。学習 feature_hash 不一致 or
  TE モデルの前処理器欠落で fail-fast(保存しない)。
- **SC-005 (監査)**: `feature_snapshots` の前処理後 model-input ベクトルから推論を再現でき、`prediction_runs` の
  model_version/logic_version/computed_at で追跡できる。
- **SC-006 (active 解決)**: active が 0/複数のとき定義どおりエラー + `--model-version` 明示要求。

## リーク検査の考え方(SC-004 の具体)

対象レースの `race_results`(着順)や race_horses の odds/popularity(ResultMarket 相当)を極端に変更しても、
保存される `race_predictions` が一切変化しないことを assert する。変化すれば結果由来情報がモデル入力に漏れている。
未来 as-of は、対象日より後のレース/結果を挿入しても当該レースの予測が不変であることで確認する。
