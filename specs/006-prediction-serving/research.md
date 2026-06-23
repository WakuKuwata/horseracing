# Research: 予測 serving

Phase 0。NEEDS CLARIFICATION なし(spec で確定済み)。codex second opinion を反映した設計判断を記録する。

## R1. 未来レース(結果未確定)での as-of 特徴量

- **Decision**: Feature 004 の `build_feature_matrix(session, end_date=対象日)` をそのまま再利用する。母集団は
  `entry_status='started'` でフィルタされ `race_results` を必要としない。履歴特徴は対象レース日より厳密に過去
  (`race_date < R`、同日除外)のみを参照する。
- **Rationale**: codex 確認 — `history.py` は cumsum−当日 / `allow_exact_matches=False` で同日・当日以降を除外。
  対象レースに `race_results` 行が無くてもモデル入力特徴は結果列を含まないため null パターンが学習時と乖離しない。
- **Alternatives considered**: serving 専用の特徴ビルダー → Feature 004 と二重実装になりリーク方針が分岐するため却下。

## R2. 採用済みモデルの検証付きロード経路

- **Decision**: serving に `load_serving_model(session, model_version=None)` を新設。active 解決(R4 のルール)→
  成果物(`model.txt`=booster、`calibrator.pkl`、`preprocessor.pkl`)をロード→`ServingModel` を返す。推論は
  training の純部品を再利用: booster.predict(raw)→`Calibrator.transform`→clip→`assemble_predictions`
  (正規化+`harville_topk`)。training の `LightGBMPredictor.predict_race`(session 依存・matrix 再構築)は
  serving では使わない。
- **Rationale**: codex BLOCKER — serving 用の独立ロード/推論 API が training に無い。session から matrix を毎回
  再構築する設計は serving に不適。純部品を組み合わせた軽量経路が決定論・テスト容易。
- **Alternatives considered**: `LightGBMPredictor` をそのまま流用 → 学習用 fit 経路・session 結合を持ち込み、
  保存済み booster を使わず再学習しかねないため却下。

## R3. 前処理器の成果物保存(BLOCKER 解消)

- **Decision**: Feature 005 の `artifacts.save_model_version` を**非破壊拡張**し、`preprocessor.pkl` を追加保存する。
  内容 = `{feature_cols(列順), categorical_cols, target_encode_cols, te_smoothing, encoders(TargetEncoder dict),
  feature_version, feature_hash}`。serving ロード時にこれを復元して推論に使う。
  - **後方互換**: `preprocessor.pkl` が無い既存成果物は、metadata.json の `target_encode_cols` が空(TE 不使用)
    なら `feature_cols=model_input_features()`・`categorical_cols=CATEGORICAL_FEATURES∩`・encoders 無しで再構成し、
    `feature_hash` 一致を検証して serving 可。TE 使用かつ前処理器欠落は **fail-fast**(誤推論を防ぐ)。
- **Rationale**: codex BLOCKER — `encoders_` は `calibrator.pkl` にも metadata にも保存されず TE モデルは復元不能。
  前処理器を明示成果物化すれば serving が学習時と同一の入力行列を再構成でき、列順・語彙・エンコーダが一致する。
- **Alternatives considered**: calibrator.pkl に encoders を相乗り → 校正器と前処理器の責務混在で将来の校正差し替えに
  弱い。別 `preprocessor.pkl` に分離。

## R4. active モデルの解決と母集団

- **Decision**: `adoption_status='active'` が厳密に 1 つならそれを使う。0 個 → エラー(採用モデル無し)。複数 →
  エラーで `--model-version` 明示を要求。明示指定時はその model_version を使う(active でなくてもよい)。
  母集団は `entry_status='started'` の出走馬(取消・除外を除外、結果非依存)。
- **Rationale**: codex RISK — `started` が確定出走を意味するかは ingest タイミング依存。serving は出走情報を信頼し、
  混入防止は ingest の責務(本フィーチャー外)。複数 active は運用事故になりうるため明示解決を強制。
- **Alternatives considered**: 複数 active で最新 `registered_at` を自動選択 → 暗黙選択は監査時に紛れるため却下
  (明示指定を促す)。

## R5. 決定論・冪等性・logic_version

- **Decision**: 推論は決定論的(成果物固定 → 同一出力)。再実行は破壊的 upsert せず**新しい `prediction_run`
  (uuid)として追記**(監査履歴)。`logic_version` = `feat=<feature_version>;serve=<SERVING_LOGIC_VERSION>` の
  文字列で、特徴ロジック版 + serving/後処理(校正・正規化・Harville)ロジック版を表す。
- **Rationale**: codex RISK — `(race_id, model_version, logic_version)` に一意制約は無く append-only。logic_version を
  明示定義し、同一 logic_version の 2 回実行で per-horse 確率が完全一致することをテストで担保。
- **Alternatives considered**: 冪等 upsert(同一キーで上書き)→ 監査証跡喪失。append-only を採用。

## R6. feature_snapshots の保存内容

- **Decision**: `features` jsonb に**前処理後の model-input ベクトル**(target encoding 適用後の実値、特徴量名で
  キー付け)を保存。補助として `_raw_win`(booster 生スコア)・`_calibrated_win`(正規化前の校正値)を併記。
  `feature_version` 列を設定。
- **Rationale**: codex RISK — raw 特徴だけでは TE 依存モデルを再現できない。モデルが実際に見た入力ベクトルを残せば
  encoder 成果物なしでも推論を再現・監査でき、将来の推奨(007)の入力にもなる。校正後確率は `race_predictions` に
  分離し重複させない。
- **Alternatives considered**: raw registry 特徴のみ → TE モデルで再現不可。前処理後ベクトルを正とする。

## R7. リーク検査(serving 版)

- **Decision**: serving が ResultMarket(結果確定オッズ/人気)・`race_results`(着順)をモデル入力に使わないことを
  テストで固定: 当該レースの結果データを変更しても予測が不変。特徴は `model_input_features()` のみ。
- **Rationale**: 憲法 II。serving は本番に最も近く、リーク混入の影響が最大。
- **Alternatives considered**: なし(必須)。
