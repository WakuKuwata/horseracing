# Feature Specification: モデルトレーニングと校正 (Model Training & Calibration)

**Feature Branch**: `005-model-training`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Model Training & Calibration — LightGBM を Predictor として学習・校正・採用判定"

## 概要

LightGBM を学習して Feature 003 の Predictor として差し、評価ハーネスで walk-forward 評価して baseline
を超えるか測定、校正 (ECE 改善) し、`model_versions` に採用判定して保存する。**最大リスクは校正の fold
漏れ (過去 035/036 の片側校正ミス) と確率整合性破壊**。

スコープは「学習 + 校正 + 採用判定 + 成果物保存」。予測 serving・買い目・UI は別フィーチャー。MVP では
スキーマ変更なし (`model_versions` の既存列 `metrics_summary`/`weights_uri`/`calibrator_uri` を使う)。

「利用者」は人間ではなく、評価を実行するオペレーターと、学習済みモデルを消費する将来の予測 serving。

データ前提 (Feature 001-004 で実在): `model_versions`、Feature 003 の Predictor 契約・評価ハーネス・
baseline・`report.compare`、Feature 004 の leak-safe 特徴量 (`build_feature_matrix` /
`model_input_features()`)、2007+ 取込データ。なお `labels.derive_labels` は **finished のみ** で、評価採点・
baseline 母集団向け。本 feature の **学習ラベルはこれを再利用せず**、`race_results` から started 全頭・
DNF=0 で独立導出する (data-model「win ラベル規則」)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - LightGBM win モデルを walk-forward 学習し Predictor として評価できる (Priority: P1) 🎯 MVP

fold ごとに leak-safe 特徴量 (started 全頭、DNF win=0) で単一 win LightGBM を学習し、レース内正規化 +
Harville で top2/top3 を導出する Predictor を実装。評価ハーネスで walk-forward 評価し baseline と比較。

**Why this priority**: 評価先行 (憲法 III) の到達点。これまでの全部品 (DB・取込・評価・特徴量) が初めて
噛み合い、「baseline を超える学習モデル」を測れる。プロジェクトの中核価値。

**Independent Test**: 合成データで Predictor が確率整合性を満たし、walk-forward 評価が完走して label 別
指標が出ること、リーク検査 (特徴に未来/同日が混入しない、ResultMarket をモデルが参照しない) を検証。

**Acceptance Scenarios**:

1. **Given** fold-train の race-horse 特徴 (started 全頭、win=finished かつ 1着 else 0)、**When**
   `fit(train_races)` を呼ぶ、**Then** seed 固定で win LightGBM が学習され、決定論的。
2. **Given** valid レース、**When** `predict_race` を呼ぶ、**Then** raw win → clip → レース内正規化 →
   Harville で top2/top3 が導出され、各馬 `0<=win<=top2<=top3<=1`、レース内合計が許容内 (整合性 fail-fast
   を通る)。
3. **Given** モデル特徴量、**When** 学習・推論する、**Then** Feature 004 の `model_input_features()`
   (post_result/識別列/結果確定オッズ除外) のみを使い、ResultMarket を参照しない。
4. **Given** walk-forward 評価、**When** 実行する、**Then** label 別 (win/top2/top3) 指標が算出され、
   baseline と同一条件で比較できる。

---

### User Story 2 - 校正器を train-only で fit し ECE を改善できる (Priority: P1)

win 確率の校正 (Platt 既定) を train fold 内 held-out / OOF でのみ fit し、valid/test を見ない。校正後に
clip → 正規化 → Harville。

**Why this priority**: 憲法 III の採用条件は ECE 確認。校正の fold 漏れは過去 035/036 の失敗要因であり、
本フィーチャー最大の品質リスク。MVP は US1+US2 で「整合性を保った校正済みモデル」を成立させる。

**Independent Test**: 校正器が valid 期間データを使わないこと (fold 漏れ検査)、校正後も確率整合性を満たす
こと、端点崩れに対し clip が効くこと、校正で win の ECE が改善することを検証。

**Acceptance Scenarios**:

1. **Given** fold-train、**When** 校正器を fit する、**Then** train 内 held-out / OOF のみを使い、valid/test
   の結果を一切使わない (fold 漏れなし)。
2. **Given** 校正済み win 確率、**When** 推論する、**Then** raw → 校正 → clip → レース内正規化 → Harville の
   順で処理され、Σwin が校正後も保たれる (校正後に正規化)。
3. **Given** 端点に寄る確率 (win≈0/1)、**When** Harville を適用する、**Then** clip/floor により Σtop3 等が
   許容を割らない。
4. **Given** 校正前後、**When** ECE を比較する、**Then** 校正で win の ECE が改善する。

---

### User Story 3 - baseline 比較 + ECE で採用判定し model_versions に保存できる (Priority: P1)

学習・校正済みモデルを評価ハーネスで baseline と同一条件評価し、採用ゲート (全 label 指標 + ECE 閾値) で
candidate→active を判定して保存する。

**Why this priority**: 憲法 III の「採用は baseline 比較 + ECE」を成立させる。MVP の締めくくりとして、
モデルが正式に採用される/されない判定が出る。

**Independent Test**: ゲートを満たすモデルが active、満たさないモデルが candidate のままになること、
同一条件比較が `report.compare` で出ることを検証。

**Acceptance Scenarios**:

1. **Given** 評価結果、**When** 採用ゲートを適用する、**Then** baseline (market/uniform) を win LogLoss で
   上回り、top2/top3 が劣化せず、ECE <= 事前固定閾値 のときのみ `adoption_status='active'`、それ以外は
   `candidate` のまま。
2. **Given** 採用判定、**When** 保存する、**Then** `model_versions` に 1 行 (model_family='lightgbm'、
   feature_version、label_schema='win_top2_top3'、adoption_status、metrics_summary) が登録され、LightGBM
   モデルが `weights_uri`、校正器が `calibrator_uri`、再現メタ (seed/params/fold 境界/校正方式/feature
   hash) が artifacts に保存される (スキーマ変更なし)。
3. **Given** 2 モデル、**When** 比較する、**Then** 同一評価条件の指標差分が `report.compare` で確認できる。

---

### User Story 4 - ハイパーパラメータ探索と OOF target encoding (Priority: P2)

ハイパラ探索 (valid を選択に使わない、train 内 CV) と、OOF target encoding の正しい統合。

**Why this priority**: 性能向上だが、固定ハイパラの MVP が baseline を超えた後の改善。リーク経路が増える
ため慎重に追加する。

**Independent Test**: ハイパラ選択が valid を使わないこと、OOF encoding が train 内未来を漏らさないことを
検証。

**Acceptance Scenarios**:

1. **Given** ハイパラ探索、**When** 実行する、**Then** train 内 CV のみで選択し valid を一切使わない。
2. **Given** OOF target encoding、**When** train に適用する、**Then** fit-all-train→apply-all-train を避け、
   train 内未来ラベルを早期行に漏らさない。

---

### Edge Cases

- 少データ年 (初期 valid 年の学習データ不足)。
- 校正端点 (win≈1/0) で Harville Σ が許容を割る → clip/floor。
- 少頭数・同着 (`derive_labels` に従う)。
- DNF を win=0 で学習するが評価ハーネスは finished のみ採点する母集団ミスマッチ (既知の評価バイアス、
  要記録)。
- 年跨ぎの特徴量・履歴。
- valid を使ったハイパラ選択の禁止。
- 全馬非完走レース・空 fold (評価ハーネスの扱いに従う)。

## Requirements *(mandatory)*

### Functional Requirements

**学習・Predictor (US1)**

- **FR-001**: システムは単一の win 確率 LightGBM を fold ごとに学習し、Feature 003 の Predictor 契約
  (`fit(train_races)` / `predict_race`) を満たさなければならない。
- **FR-002**: システムは学習母集団を started 全頭 (取消・除外を除外) とし、win ラベルを
  「finished かつ finish_order==1 なら 1、それ以外 (DNF 含む) は 0」としなければならない。
- **FR-003**: システムは `predict_race` で raw win → clip → レース内正規化 (Σwin=1) → Harville で
  top2/top3 を導出し、確率整合性 (`0<=win<=top2<=top3<=1`、レース内合計許容内) を機構的に満たさなければ
  ならない (評価ハーネスの fail-fast を通る)。
- **FR-004**: システムは特徴量に Feature 004 の `model_input_features()` のみを使い、結果確定
  `odds`/`popularity`・`ResultMarket` を参照してはならない。
- **FR-005**: システムは学習・推論を決定論的にしなければならない (seed 固定、同一データ・同一 fold で
  同一結果)。
- **FR-006**: システムは fold ごとに再学習し、特徴量は as-of (race_date < R)。fold 境界の片側適用漏れ
  (例: `<=` 境界) を起こしてはならない。

**校正 (US2)**

- **FR-007**: システムは win 確率の校正器を train fold 内の held-out / OOF でのみ fit し、valid/test の
  結果を一切使ってはならない (035/036 の fold 漏れ回避)。
- **FR-008**: システムは校正方式を Platt / isotonic で設定可能とし、**既定を Platt** とする。isotonic は
  少データ年で 0/1 を出し Harville を壊すため既定にしないが、選択肢として残し clip で端点崩れを防ぐ。
- **FR-009**: システムは推論順序を raw → 校正 → clip → レース内正規化 → Harville とし、校正後に正規化
  することで Σwin を保たなければならない。端点 (win≈0/1) は clip/floor で Harville の Σ 割れを防ぐ。
- **FR-010**: システムは校正前後の win ECE を比較でき、改善を確認できなければならない。

**採用判定・保存 (US3)**

- **FR-011**: システムは採用ゲートを「全 label 指標 + ECE」で構成しなければならない: win の LogLoss が
  baseline (market/uniform) を**厳密に下回り**、top2/top3 の LogLoss が baseline 以下 (劣化なし)、かつ
  win ECE <= 閾値。ゲートの**構造は spec で固定**し、ECE 閾値などの具体数値は**設定可能**として research/
  実データの分布を見て確定する (候補を見てから決めない=事前固定の原則は保つ)。
- **FR-012**: システムはゲート合格時に `adoption_status='active'`、不合格時に `candidate` のままとし、
  同一評価条件で baseline と比較できなければならない。
- **FR-013**: システムは `model_versions` に 1 行 (model_family='lightgbm'、feature_version、
  label_schema='win_top2_top3'、adoption_status、metrics_summary) を保存し、LightGBM モデルを `weights_uri`、
  校正器を `calibrator_uri`、再現メタ (seed/params/fold 境界/校正方式/feature hash/git sha) を artifacts に
  保存しなければならない (スキーマ変更なし)。

**ハイパラ・encoding (US4, P2)**

- **FR-014**: システムはハイパラ探索を train 内 CV のみで行い valid を選択に使ってはならない。OOF target
  encoding は fit-all-train→apply-all-train を避け train 内未来を漏らしてはならない。

### Key Entities *(include if feature involves data)*

新規テーブルは MVP では作らない。論理対象:

- **WinModel (logical)**: fold ごとの LightGBM win 確率モデル (seed/params 固定)。
- **Calibrator (logical)**: train-only で fit した校正器 (Platt 既定)。
- **TrainedPredictor (logical)**: WinModel + Calibrator + 正規化 + Harville を束ね Feature 003 の Predictor
  契約を満たす。
- **採用ゲート (logical)**: 全 label 指標 + ECE の事前固定基準。
- **artifacts (logical)**: weights_uri / calibrator_uri / metadata.json (再現情報)。
- **保存先 (model_versions)**: metrics_summary + adoption_status + 上記 uri。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Predictor が確率整合性 fail-fast を通る (全 valid レースで `0<=win<=top2<=top3<=1`、Σ 許容内)。
- **SC-002**: 校正器が valid/test を一切使わない (fold 漏れ検査 100%)。校正で win の ECE が改善する。
- **SC-003**: LightGBM モデルが walk-forward 評価で baseline (uniform) を win LogLoss で上回る。
- **SC-004**: 採用ゲート (全 label + ECE 閾値) が機能し、合格モデルが active、不合格が candidate のまま。
- **SC-005**: `model_versions` に metrics_summary + weights_uri + calibrator_uri が保存され、再現情報
  (seed/params/fold/校正方式/feature_version/feature hash/git sha; FR-013 と同一集合) が揃う。
- **SC-006**: 同一データ・同一 fold・同一 seed で学習・評価が決定論的に再現する (2 回実行で一致)。

## Assumptions

- Feature 001 (model_versions/labels/validation)、002 (取込データ)、003 (Predictor 契約・harness・baseline・
  report.compare)、004 (leak-safe 特徴量・model_input_features) に依存する。
- 実装は新パッケージ (学習、`horseracing-db`/`horseracing-features`/`horseracing-eval` にパス依存)。LightGBM
  + scikit-learn (校正) + numpy を使う。具体は plan で確定。
- **学習母集団は started 全頭・DNF→win=0** を採用する (finished-only は非完走リスク馬を過大評価するため)。
  ただし評価ハーネスが finished のみ採点する母集団ミスマッチは**既知バイアスとして記録**し、必要なら後続で
  評価母集団を再検討する。
- モデル設計は **単一 win LightGBM + 正規化 + Harville** で確率整合性を機構保証する (3 ラベル別学習 +
  reconcile は校正・fold 境界が 3 倍になり 035/036 型の漏れを増やすため不採用)。
- ECE 採用閾値・baseline 超え条件・ハイパラ既定値の具体数値は research/実データで確定する。
- ハイパラ探索 (US4) と OOF target encoding は P2。MVP はスキーマ変更なし・固定ハイパラ・MVP は target
  encoding 不使用 (Feature 004 の leak-safe 特徴量のみ)。
- テストは合成データ中心。実データ学習・評価はローカルスモーク。
