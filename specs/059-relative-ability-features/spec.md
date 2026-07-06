# Feature Specification: Within-race relative-ability features

**Feature Branch**: `059-relative-ability-features`

**Created**: 2026-07-06

**Status**: Implemented — ADOPTED (lgbm-057 active)

**結果サマリ**: 実装完了・全パッケージ緑(features 156 / training 67 / eval 72 / serving 34)・bit-parity・
leak-guard 緑。**採用ゲート実 DB**: (T011 binary feature-eval, 実 build) LogLoss 0.23105→0.22991
(−0.00114)・AUC 0.75567→0.75965(+0.00398)・ECE 0.00957→0.00912・19/19 fold=spike 再現。
**(T012 本番 pl_topk 再学習 lgbm-057, baseline=lgbm-056)** 4/4 ゲート機械 PASS: win LogLoss
**0.21615→0.21597**・top2 0.34003→0.33988・top3 0.43037→0.43021・win ECE 0.00070→0.00063=全指標単調改善。
overlap で binary の −0.00114 は pl_topk で −0.00018 に縮小(codex 予測どおり)だが全指標非悪化で採用。
ユーザー承認 → lgbm-057 active / lgbm-056 retired。migration/API/OpenAPI 不変。

**Input**: within-race 相対能力特徴群 (features-013 → features-014). 既存 as-of 能力列を「そのレースの started フィールド内で相対化」した新群 `relative_ability` を追加し、モデルが構造的に持てない within-race 文脈(相手関係の中での格)を特徴側で明示する。

## Overview & Motivation

現行モデル (lgbm-056, features-013, 目的関数 pl_topk = race-internal softmax) は各馬の as-of 能力
(win_rate / recent_win_rate / dist_band_win_rate / rel_time_avg 等) を**絶対値**で持つ。softmax は
学習スコアをレース内で相対化するが、**決定木は特徴の絶対値で分割する**ため、「この馬はこの相手
関係の中で相対的に格上か」という per-row の within-race 文脈を木が単一分割で拾えない。

Feature 031 (pace_scenario) は**脚質**を started フィールドで leave-one-out 集約して展開文脈を作った。
本 feature はその**能力版**: 既に build 済みの as-of 能力列を、同じ leave-one-out 機構で「自分 −
自分を除いたフィールド平均」に相対化し、加えて中核 2 軸 (総合勝率・スピード) の **field 内
percentile rank** を足す。

**新情報の性質**: netkeiba 新規取得ゼロ・DB 内既存データのみ・**新ソース列なし**。既存の
strictly-before as-of 列の **within-race 後処理のみ**なので新規リーク面ゼロ。①「枠×トラック状態の
経験的バイアス」は spike の単独 ablation で inert (030 draw_bias 棄却の再確認) と判明したため
**スコープ外**、相対能力に絞る。

### Pre-registered spike evidence (before this spec — 憲法 III)

18-fold binary feature-eval (features-013 baseline vs +relative_ability 群, 同一 fold):

| 指標 | baseline | candidate | 差 |
|---|---|---|---|
| win LogLoss | 0.23104 | 0.22986 | **−0.00118** |
| AUC | 0.75572 | 0.75994 | **+0.00421** (056 の +0.0044 に匹敵) |
| ECE | 0.00956 | 0.00886 | **−0.00071** (改善) |
| winning folds | — | — | **19/19** |
| worst_fold dLogLoss / dECE | — | — | −0.00022 (全 fold 改善) / +0.00063 (< 2e-3) |

→ 機械ゲート **ADOPTED=True**。spike は de-risk 用 (binary・A/B 注入)。本 feature は同型の
`feature-eval --drop-groups relative_ability` を**採用ゲート**として再実行し、加えて **本番 pl_topk
構成での再学習検証** (下記 US2) を必須とする。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 相対能力特徴群を build に組み込む (Priority: P1)

`build_feature_matrix` が `relative_ability` 群を生成し、feature registry・FEATURE_VERSION
(features-014)・materialize 経路に整合する。予測/学習が新 13 列でリーク安全に走る。

**Why this priority**: 特徴が build されなければ採否も本番化もできない。全ての土台。

**Independent Test**: `features materialize` + `build_feature_matrix(use_materialized=True/False)` の
**bit-parity** (assert_frame_equal check_exact) が新群込みで成立し、leak-guard テスト
(今走結果/オッズ/同日他馬の値を変えても新群が不変) が緑。

**Acceptance Scenarios**:

1. **Given** features-013 の as-of 能力列が揃った行列, **When** relative_ability builder を通す,
   **Then** 各 `<col>_vs_field` = 自分 − (started フィールドの自分を除く平均) が per-horse で算出され、
   フィールド定数ではない (レース内で馬ごとに異なる)。
2. **Given** 同一 DB, **When** materialized 経路と in-memory 経路で行列を build,
   **Then** 新群込みで bit 一致 (check_exact=True, check_dtype=True)。
3. **Given** あるレースの結果/オッズ/同日他レースの結果を改変, **When** 対象レースの
   relative_ability を再計算, **Then** 値は不変 (strictly-before as-of 列のみ入力・leak-guard)。
4. **Given** フィールドで対象能力列が全馬 NaN (例: 全馬デビュー), **When** 相対化,
   **Then** `<col>_vs_field` は NaN (0 埋めしない・Unknown 維持)。

---

### User Story 2 - 採用ゲート + 本番 pl_topk 再学習で採否を確定 (Priority: P1)

事前登録ゲート (`feature-eval --drop-groups relative_ability`) を実 DB で再実行し、加えて **本番
構成 (pl_topk + isotonic + OOF-TE jockey_id/trainer_id)** で候補モデルを学習して現行 active
(lgbm-056, win LogLoss 0.21615) と比較する。両方が改善のときのみ本番採用。

**Why this priority**: spike は binary。本群の機構は softmax が既に行う相対化と**重なる**ため、
pl_topk 本番でゲインが縮む/消える overlap リスクがある。ここを潰さないと採用できない
(憲法 III: 評価モデル==デプロイモデル、絶対品質改善≠本番改善)。

**Independent Test**: `training feature-eval --drop-groups relative_ability` が
primary_pass=True かつ fold ガード通過を再現し、`model-eval` (pl_topk) の候補 win LogLoss が
lgbm-056 の 0.21615 を下回る。

**Acceptance Scenarios**:

1. **Given** features-014 の build, **When** 18/19-fold feature-eval を回す,
   **Then** mean win LogLoss 改善 かつ mean ECE 非悪化 かつ 過半 fold 勝ち (spike と整合)。
2. **Given** pl_topk 本番構成, **When** 候補モデルを学習し walk-forward OOS 評価,
   **Then** win LogLoss が lgbm-056 (0.21615) を下回り、top2/top3 が非悪化。
3. **Given** ゲート結果, **When** 機械判定が False だが指標が総合改善 (023/039/056 前例型),
   **Then** ユーザー判断に委ねる (自動採用を強制しない)。

---

### Edge Cases

- **単騎/2頭立て等の極小フィールド**: leave-one-out の分母 (自分を除く有効頭数) が 0 →
  `<col>_vs_field` は NaN (031 と同じ扱い)。`<col>_field_rank` は単騎で pandas 既定 1.0 を返す
  (退化・決定論)。JRA で started 1 頭のレースは事実上皆無かつ softmax が自明なので **許容**
  (特別扱いしない)。
- **入力 as-of 列が疎** (入力列 `venue_win_rate` 11%, `dist_band_win_rate` 79%, `surface_win_rate`
  83%): 低カバレッジ列は research D1 で採否確定済み (`venue_win_rate` は**除外**、残りは採用)。
- **rank の同値**: percentile rank は pandas 既定 (平均順位) で決定論。
- **NaN を含むフィールド**: leave-one-out 平均は NaN を除いて計算、対象馬自身が NaN なら
  `<col>_vs_field` も NaN。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST 新 feature 群 `relative_ability` を registry に登録し、各列に source /
  timing (PRE_ENTRY) / missing_policy (NULL) を宣言する。
- **FR-002**: System MUST 各対象 as-of 能力列 (11 列) について `<col>_vs_field` = 自分の値 −
  (同一 started フィールドの自分を除く平均) を per-horse で算出する (フィールド定数を作らない)。
- **FR-003**: System MUST 中核 2 軸 (win_rate, rel_time_avg) について field 内 percentile rank
  `<col>_field_rank` を **started 母集団のみ**で算出する。
- **FR-004**: System MUST 相対化の入力を **strictly-before as-of 列のみ**に限定し、今走結果・
  オッズ・同日他馬の今走値を一切参照しない (leak-guard テストで機械固定)。
- **FR-005**: System MUST 新群を 025 materialization に整合させる (build_asof_features 単一源、
  materialize/in-memory の bit-parity、source_fingerprint は新ソース列なしで不変)。
- **FR-006**: System MUST FEATURE_VERSION を features-013 → features-014 に上げる。
- **FR-007**: System MUST 欠損は NaN 維持 (0 埋め禁止)、float64 固定 (プール依存 dtype ドリフト防止)。
- **FR-008**: System MUST 事前登録採用ゲート (`feature-eval --drop-groups relative_ability`) を
  実 DB で再実行可能にする。
- **FR-009**: System MUST 009 win→joint 導出・Unknown 方針・API/OpenAPI/スキーマを不変に保つ
  (特徴追加のみ、migration なし)。
- **FR-010**: System MUST 最終列集合を **13 列 (11 deviation + 2 rank)** に事前固定する
  (低カバレッジ入力列 `venue_win_rate` は除外)。列集合は research D1 で確定済み・実 DB ゲート後に
  足し引きしない (結果を見た後の列選択はしない=憲法 III)。

### Key Entities

- **relative_ability 特徴群 (13 列)**: 11 個の `<col>_vs_field` (leave-one-out 偏差) + 2 個の
  `<col>_field_rank` (field percentile: win_rate, rel_time_avg)。全て float64・PRE_ENTRY・
  missing=NULL。モデル入力特徴。列定義は data-model.md に固定。
- **対象 as-of 能力列 (入力・11 列)**: win_rate, recent_win_rate, place_rate, show_rate,
  dist_band_win_rate, surface_win_rate, rel_time_avg, rel_last3f_avg, finish_diff_best,
  jockey_win_rate, trainer_win_rate (`venue_win_rate` は低カバレッジで除外)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: features-014 の materialize/in-memory 行列が新群込みで **bit 一致** (check_exact)。
- **SC-002**: leak-guard テストが緑 (結果/オッズ/同日他馬を改変しても新群不変)。
- **SC-003**: 事前登録 feature-eval が mean win LogLoss 改善 かつ mean ECE 非悪化 かつ過半 fold 勝ち
  (spike: −0.00118 / +0.00421 AUC / 19-19 を実 DB で再現)。
- **SC-004**: 本番 pl_topk 候補モデルの win LogLoss が lgbm-056 (0.21615) を**下回る**、かつ
  top2/top3 非悪化 (overlap リスクの実証的クリア)。
- **SC-005**: 全パッケージのテストが緑 (features / training / serving / eval)・ruff クリーン・
  drift-check 緑・migration head 不変。

## Assumptions

- 対象 as-of 能力列はすべて features-013 で既に build 済み (新規 as-of 計算は追加しない)。
- 相対化は **build 段の within-race 後処理** (031 pace_scenario と同じ層) に置き、per-race 決定的で
  pool-end 非依存 → materialization-safe。
- 本番採用の最終判断は、機械ゲート + pl_topk 再学習結果を見てユーザーが行う (023/039/056 前例)。
- lgbm-057 (本 feature 採用時) の学習は高速化済み経路 (vectorized pl_topk + bulk eval loader) で
  ~20 分/回を前提。
- 入力の低カバレッジ列 (`venue_win_rate` 11%) は除外し、最終列集合 = 13 列 (11 deviation + 2 rank)
  を research D1 / data-model.md で確定済み (FR-010)。
