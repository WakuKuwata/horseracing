# Research: モデルトレーニングと校正

Phase 0 調査。最重要は校正の fold 安全 (035/036 回避) と確率整合性。

## R1. モデル設計 — 単一 win + 正規化 + Harville

- **Decision**: 単一 win 確率 LightGBM を学習。推論で `raw win → 校正 → clip → レース内正規化 (Σ=1) →
  Harville で top2/top3`。これで憲法 IV の整合性 (各馬 0<=win<=top2<=top3<=1、Σ≈1/2/3) を機構保証。
- **Rationale**: 3 ラベル別学習 + reconcile は校正器・fold 境界が 3 倍になり 035/036 型の片側漏れを増やす。
  単一 win は harness の `check_consistency` を機械的に通しやすい (codex)。
- **Alternatives considered**: 3 モデル別 + 単調 reconcile → 複雑・漏れ増。ランキング学習 → 確率校正が困難。

## R2. 校正の fold 安全 (最重要)

- **Decision**: 校正器は **train fold 内の時系列 held-out** で fit する。train を race_date で前半 (model-fit)
  と後半 (calibration-fit) に分割し、model は前半で学習、校正器は後半の model 予測で fit。valid/test は
  一切使わない。既定方式は **Platt (sigmoid)**、isotonic は選択可。順序は `raw → 校正 → clip → 正規化 →
  Harville` (校正後に正規化しないと Σwin が壊れる)。
- **Rationale**: 035/036 の校正ミスは valid/test 混入が原因。時系列 held-out は単純かつ明確に fold 安全。
  Platt は端点 (0/1) を避け Harville を壊しにくい。
- **Alternatives considered**: train 内 OOF (CV) → データ効率は良いが複雑、P2 (US4)。全 train で校正 fit →
  model 学習データと同一で楽観的になりやすい。
- **検証**: fold 漏れ検査テスト (校正器が valid 期間の race を一切参照しない) を SC-002 で担保。

## R3. 学習母集団とラベル

- **Decision**: 学習母集団 = started 全頭 (取消・除外を除外)。win ラベル = `result_status='finished' かつ
  finish_order==1` なら 1、それ以外 (DNF=stopped/disqualified 含む) は 0。
- **Rationale**: finished-only 学習は「完走条件付き確率」になり非完走リスク馬を過大評価 (codex)。serving は
  started 全頭に予測するため母集団を合わせる。
- **既知バイアス**: 評価ハーネス (Feature 003) は finished のみ採点 (derive_labels)。学習 started-all と
  評価 finished-only の母集団ミスマッチを**既知バイアスとして記録**し、必要なら後続で評価母集団を再検討。

## R4. walk-forward 連携

- **Decision**: LightGBMPredictor は session を保持し、**leak-safe feature matrix を一度だけ計算してキャッシュ**
  (Feature 004 の as-of)。`fit(train_races)` は train race_id 行を選択し win LightGBM + 校正器を学習。
  `predict_race(valid race)` はその race の行を引き、raw win→校正→clip→正規化→Harville。
- **Rationale**: 特徴は as-of で leak-safe なため全レース一括計算してよい (fold 非依存)。per-race 再計算より
  高速。fold 依存は校正の held-out のみ。
- **注意 (codex)**: fold 境界の片側適用漏れ。train = race_date < valid 年初。`build_feature_matrix` の
  end_date は使わず全 pool を計算し、train/valid は race_id 集合で厳密に分ける。TE は MVP 不使用。

## R5. 確率整合性 (端点 clip)

- **Decision**: 正規化前に win を `[eps, 1-eps]` (eps 例 1e-6) に clip。正規化後 Harville。端点 (win≈1) で
  Harville の Σtop3 が割れるのを防ぐ。harness の許容 (0.05/0.10/0.15) 内に収める。
- **Rationale**: 非退化 win なら Σ は理論一致だが、`[1,0,0]` 近傍で Σtop3=1 になる例がある (codex)。

## R6. 採用ゲート

- **Decision**: ゲート = `win LogLoss(model) < win LogLoss(baseline)` (厳密) かつ `top2/top3 LogLoss(model)
  <= baseline` (劣化なし) かつ `win ECE(model) <= 閾値`。閾値は設定可能 (既定は research/実データで確定)。
  baseline は Feature 003 で保存済みの market/uniform の `model_versions.metrics_summary` を参照。合格→
  `adoption_status='active'`、不合格→`candidate`。
- **Rationale**: 憲法 III は LogLoss/Brier/AUC/NDCG/ECE + baseline 比較。win 単体では不足 (codex)。閾値は
  候補を見てから決めない (事前固定原則)。
- **確定した ECE 閾値 = 0.05** (2026-06-23、実データ 2007→2008 スモークで確定・事前固定)。
  根拠: 同一評価条件で uniform baseline の win ECE ≈ 0.0011、market (参照線) ≈ 0.0226。win 確率の
  較正ずれ上限として 0.05 は market 参照線の約 2 倍を許容し、退化・小頭数 fold の揺れを吸収しつつ、
  著しい未較正 (>5%) は不合格にできる水準。候補モデルの指標を見る前にこの値を固定 (pre-registration)。
  T027 実測では採用モデルの win ECE = 0.0054 ≪ 0.05 で合格。閾値は CLI `--ece-threshold` で上書き可能。
- **Alternatives considered**: 複合スコア → 不透明。win のみ → top2/top3 劣化を見逃す。閾値 0.02 (market 相当)
  → 小頭数/少データ fold の揺れで誤って不合格になりやすく却下。

## R7. 成果物保存

- **Decision**: スキーマ変更なし。`model_versions` に行 (model_family='lightgbm'、feature_version、
  label_schema='win_top2_top3'、adoption_status、metrics_summary)。`weights_uri` →
  `artifacts/model_versions/{model_version}/model.txt` (per-fold は複数 or 代表)、`calibrator_uri` →
  `calibrator.pkl`、`metadata.json` に seed/params/fold 境界/校正方式/feature hash/git sha。
- **Rationale**: 既存列で十分 (codex)。再現情報を metadata に集約 (憲法 V)。
- **未確定 (実装で確定)**: walk-forward は fold ごとにモデルが異なる。保存は「最終 fold モデル」または
  「全 fold バンドル」。MVP は評価用の per-fold 学習 + 代表 (最新 fold) モデルを保存する方針を実装時に固定。

## R8. Harville の再利用

- **Decision**: Feature 003 (`eval/`) の Harville 実装を再利用する。`eval/baselines.py` の `_harville_topk`
  を公開関数 `harville_topk` にリネーム (非破壊、後方互換) し、training から import する。
- **Rationale**: 二重実装で divergence を避ける。market baseline と同一の順位分布導出を使う。

## R9. 決定論

- **Decision**: LightGBM の seed 固定 + `deterministic=True` + 単一スレッド (or 固定) で再現性確保。校正・
  正規化・Harville は決定論的。同一データ・同一 fold・同一 seed で完全一致 (SC-006)。
- **Rationale**: 採用判定の前提 (憲法 V)。

## R10. 特徴量・ラベル結合 (dataset)

- **Decision**: `dataset.py` が Feature 004 の `build_feature_matrix` (started 母集団・固定スキーマ) に、
  race_results から導いた win ラベル (started 全頭、finished&1着=1 else 0) を結合。X = `model_input_features()`。
- **Rationale**: ラベルは finished-only の derive_labels ではなく started-all で計算 (R3)。X は leak-safe
  特徴のみ (結果確定オッズ・ResultMarket 非参照)。
