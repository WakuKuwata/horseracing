# Contract: 前処理器成果物と後方互換ロード

Feature 005 の成果物保存を非破壊拡張し、serving が学習時の入力行列を再構成できるようにする。

## 保存(training 側の拡張)

```python
# training/artifacts.py save_model_version() に追加:
def write_preprocessor(predictor, path) -> None:
    # preprocessor.pkl に dump:
    #   feature_cols       = predictor.fit_info_["feature_cols"]        # 学習時の列順
    #   categorical_cols   = predictor.fit_info_["categorical_cols"]    # native categorical (TE 除外後)
    #   target_encode_cols = list(predictor.te_cols_)
    #   te_smoothing       = predictor.te_smoothing
    #   encoders           = predictor.encoders_                        # col -> TargetEncoder (空可)
    #   feature_version    = FEATURE_VERSION
    #   feature_hash       = feature_hash(feature_cols)
```

- 既存の `model.txt` / `calibrator.pkl` / `metadata.json` は変更しない(列追加・破壊なし)。
- `model_versions` の DB スキーマは変更しない。`weights_uri` / `calibrator_uri` はそのまま。

## ロード(serving 側、後方互換)

```python
def load_preprocessor(art_dir, metadata) -> Preprocessor:
    if (art_dir / "preprocessor.pkl").exists():
        return pickle.load(...)
    # 後方互換: preprocessor.pkl が無い既存成果物
    if metadata.get("target_encode_cols"):           # TE 使用なのに前処理器が無い
        raise ServingError("TE モデルだが preprocessor.pkl が無い。再保存が必要")
    # TE 不使用: 再構成
    feature_cols = model_input_features()
    if feature_hash(feature_cols) != metadata["feature_hash"]:
        raise ServingError("feature_hash 不一致。学習時と特徴スキーマが異なる")
    return Preprocessor(feature_cols=feature_cols, categorical_cols=..., encoders={})
```

## 保証

- TE を使って学習したモデルは `preprocessor.pkl` から encoders/列順/categorical 方針を完全復元して serving できる。
- TE 不使用の既存モデル(例: `lightgbm-win-v1`)は `preprocessor.pkl` 無しでも feature_hash 一致を条件に serving 可。
- 学習時 `feature_hash` と現行 `model_input_features()` のハッシュ不一致は fail-fast。
- 拡張は後方互換(既存 artifacts/DB を壊さない)。
