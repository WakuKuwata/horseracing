# horseracing-training

単一 win **LightGBM** を Feature 003 の Predictor 契約として学習・校正・評価・採用・保存する
パッケージ。`db` / `features` / `eval` にパス依存。

## 設計の要点

- **母集団 / ラベル**: started 全頭。`win = 1` iff `result_status='finished'` かつ `finish_order==1`、
  それ以外は 0（DNF=stopped/disqualified 含む）。評価採点用の `labels.derive_labels`（finished-only）
  とは別物で、学習はこれを再利用しない（`dataset.py`）。
- **特徴量**: Feature 004 の `model_input_features()` のみ。as-of（race_date < R）で leak-safe。
  全レースを一度だけ matrix 化してキャッシュ（履歴は行ごとに as-of なので将来レースに依存しない）。
  結果確定 odds/popularity・`ResultMarket` はモデルが一切参照しない（リーク検査 `test_leak.py`）。
- **校正（最重要）**: 校正器（既定 **Platt** / isotonic 可）は **train 内の時系列 held-out** だけで fit。
  race 単位で時系列分割し、同一レースが model-fit と calibration-fit に跨らない。valid/test は一切
  参照しない（INV-T3、035/036 の再発防止）。退化スライス（単一クラス等）は identity+clip に fallback。
- **推論順序（INV-T1）**: `raw win → 校正 → clip([eps,1-eps]) → レース内正規化(Σwin=1) → Harville top2/top3`。
  Harville は `horseracing_eval.baselines.harville_topk` を再利用し market baseline と同一導出。
  これで `0<=win<=top2<=top3<=1`・Σ 許容内を機構保証。
- **採用ゲート**: 従来のLogLoss/ECE比較だけではACTIVEにしない。通常昇格には、確認評価のADOPT、
  完全な部分群保証、候補・比較基準それぞれについて評価時と登録時の実学習契約一致、
  登録時の本体SHA一致を要求する。
  探索結果・必要な証拠がないモデルはcandidateとして保存する。統計閾値は変更していない。
- **保存（スキーマ変更なし）**: `model_versions` に upsert（`metrics_summary` + `weights_uri` +
  `calibrator_uri`）し、`artifacts/model_versions/{model_version}/` に `model.txt` /
  `calibrator.pkl` / `metadata.json`（seed/params/fold 境界/校正方式/feature_version/feature hash/
  git sha）を書く。保存する成果物は **全履歴で学習した serving モデル**、報告指標は walk-forward。

## CLI

```bash
cd training
uv sync
export DATABASE_URL=postgresql+psycopg://...   # 取込済み + baseline 保存済み DB
uv run python -m horseracing_training train-evaluate \
    --first-valid-year 2008 --calibration platt --ece-threshold 0.05 \
    --baseline uniform --model-version lightgbm-win-v1 --artifacts-dir artifacts
```

walk-forward で fold ごとに LightGBM 学習 + train-only 校正 → harness 評価 → baseline と比較 →
採用判定 → `model_versions` + artifacts に保存。label 別指標と採用結果を表示。

### 通常昇格の証拠

新しい `paired-eval --confirmatory --gate-config ... --gate-config-hash ... --from ... --to ...`
出力には `promotion_evidence_v1` が入る。標準評価と体重情報別評価の両経路で、探索・動作確認の
出力は `eligible_for_verdict=false` / `can_adopt=false` とする。既存のrecipe hash、凍結設定、
過去の評価結果は書き換えない。

登録時には、実fitの特徴列・版、seed、params、TE、校正方式・分割・OOF数、体重mask等を
`fitted_training_contract_v1` として保存する。walk-forwardと最終fitは学習期間が異なるため
booster自体のSHAは一致させず、この学習契約を比較する。HPOではデータごとのbest paramsの差を
許し、同じ順序の解決済み探索候補・seed・分割数を比較する。HPO以外のparamsは実値で一致を要求する。
model・calibrator・preprocessor・metadataのSHAは登録時に別途固定し、通常昇格の計画時と実行時に
実ファイルを確認する。昇格記録にも候補・旧ACTIVEのbindingとgate config hashを残す。

対象は標準 `LightGBMPredictor` とisotonicの標準 `OofCalibratedPredictor`。
派生builder、market/EV/教師変調等の未対応手順、必要なfit情報がない旧artifactを同じ手順と推測しない。
来歴を持たない旧モデル／旧reportも、理由を明示する `promote-model --override-reason ...` による
例外昇格・rollbackは引き続き利用できる。

既存ACTIVEがいる場合、`train-evaluate` の通常登録はcandidateに止め、切替は
`promote-model --model-version ... --verdict ... --apply` で行う。比較基準はその時点のACTIVEと照合する。
ACTIVEと同じmodel-versionへの再登録はファイル書込み前に拒否する。登録と昇格のDB遷移は同じ
テーブルロックで直列化し、計画後にACTIVEや登録本体が変わった場合は再計画を要求する。

### US4: ハイパラ探索 + OOF target encoding（opt-in）

```bash
uv run python -m horseracing_training train-evaluate \
    --first-valid-year 2008 --hpo --target-encode \
    --baseline baseline-uniform-v1 --model-version lightgbm-win-us4
```

- `--hpo`: model-fit 行だけで expanding・race-level CV を回しハイパラ選択（valid/test 不使用）。
- `--target-encode [COLS]`: 指定列（既定 `jockey_id,trainer_id,venue_code`）を **leak-safe** に
  target-encode。学習行は OOF（自分のラベルで自分を encode しない）、校正/推論行は model-fit のみで
  fit した最終エンコーダを apply。prior（model-fit の win 率）を OOF/最終/推論で共有。**HPO の CV では
  各 fold の train 側だけで TE を再 fit**（fold 漏れ防止）。学習ラベルは started-all/DNF=0（評価採点の
  finished-only TE は流用しない）。
- 既定は両方 OFF（検証済み MVP 経路を bit 単位で維持）。実データ 2007→2008 では HPO+OOF が MVP に対し
  win LogLoss 0.2388→0.2281、top2/top3 も改善（2 回実行で指標完全一致＝決定論）。

## テスト

```bash
cd training
uv run pytest tests/unit       # 整合性・校正 fold 漏れ・ECE 改善・採用ゲート・HPO/OOF（Docker 不要）
uv run pytest -m integration   # 実 DB（testcontainers）で学習→評価→保存・決定論・リーク検査
```

最重要テスト: `tests/unit/test_calibration_foldleak.py`（校正 fold 漏れ）、
`tests/unit/test_consistency.py`（確率整合性）、`tests/integration/test_train_eval.py`
（baseline 超え + 校正が valid 不変）。
