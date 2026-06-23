# horseracing-serving

採用済み(active)モデルを成果物からロードし、対象レース(結果未確定の未来レース含む)について
校正済み win/top2/top3 を算出して `prediction_runs` / `race_predictions` / `feature_snapshots` に
永続化する**推論専用**パッケージ。学習はしない。`db` / `features` / `eval` / `training` にパス依存。

## 設計の要点

- **モデルロード**: `model_versions.adoption_status='active'` を解決(0/複数は明示要求)。成果物
  `model.txt`(booster)/ `calibrator.pkl` / `preprocessor.pkl`(特徴量列順・categorical 方針・
  target encoder)をロード。退化モデルは `model.txt` が JSON(定数)。**学習時 feature_hash と現行
  `model_input_features()` 不一致**、または **TE モデルで preprocessor 欠落**は fail-fast。
- **特徴量**: Feature 004 `build_feature_matrix(end_date=対象日)`(started 母集団、as-of `race_date<R`・
  同日除外、結果非依存)。学習用 `build_training_matrix`(race_results を読む)は使わない。
- **推論順序**: `booster raw → 校正 → clip([eps,1-eps]) → レース内正規化(Σ=1) → Harville`
  (Feature 005 の純部品を再利用)。出走馬は `horse_id` 昇順で安定整列(浮動小数の順序依存を排除)。
  raw は `predict_proba[:,1]` と一致する `Booster.predict`(binary)。
- **整合性**: `check_consistency` + 永続化前に `win<=top2<=top3` 単調修復 + `Decimal` 変換で DB の
  `PROB_MONOTONIC` を保証。
- **リーク防止**: 結果確定オッズ/人気(ResultMarket)・着順(race_results)をモデル入力に使わない。
  未来レースでも当該レース当日以降を混ぜない。
- **永続化**: `prediction_runs`(uuid, logic_version, computed_at)→ `race_predictions` /
  `feature_snapshots` を append-only(再実行は新 run)。`feature_snapshots.features` は**前処理後の
  model-input ベクトル** + `_raw_win` / `_calibrated_win`(TE モデルも再現可能)。
- **logic_version**: `feat=<feature_version>;serve=<SERVING_LOGIC_VERSION>`。

## CLI

```bash
cd serving
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 取込済み + active モデル保存済み DB
uv run python -m horseracing_serving predict --race-id 200801010101
uv run python -m horseracing_serving predict --date 2008-01-05
uv run python -m horseracing_serving predict --race-id 200801010101 --model-version lightgbm-win-v1
```

## テスト

```bash
cd serving
uv run pytest tests/unit       # 整合性・前処理器往復/parity・スキーマ不一致 fail-fast(Docker 不要)
uv run pytest -m integration   # 実 DB で推論→保存→監査→決定論→リーク→未来 as-of→active 解決
```

最重要テスト: `tests/unit/test_loader_validate.py`(booster/predict_proba parity + 前処理器 fail-fast)、
`tests/integration/test_leak_asof.py`(リーク無し・未来 as-of)、`tests/integration/test_determinism.py`
(決定論・append-only)。
