# Feature Specification: 予測 serving(推論専用パイプライン)

**Feature Branch**: `006-prediction-serving`

**Created**: 2026-06-23

**Status**: Draft

**Input**: User description: "予測 serving (推論専用パイプライン)。採用済み(active)の win モデルと校正器を読み込み、対象レース(結果未確定の未来レース含む)の leak-safe 特徴量を as-of で構築し、校正済み win/top2/top3 を算出して prediction_runs/race_predictions/feature_snapshots に永続化する。推奨・賭けは含めず次フィーチャーへ。"

## 概要

採用済み(`model_versions.adoption_status='active'`)の win モデルと校正器を成果物から読み込み、
指定レース(出走馬確定済み・結果未確定の未来レースを含む)について、Feature 004 と同一の leak-safe
特徴量を as-of(`race_date < R`、同日除外)で構築し、Feature 005 と同一の推論順序
(raw win → 校正 → clip → レース内正規化(Σ=1) → Harville で top2/top3)で校正済み確率を算出する。
結果は `prediction_runs` / `race_predictions` / `feature_snapshots` に永続化する。**推論専用** ——
推奨・賭け・期待値/ROI は本フィーチャーに含めず次フィーチャー(007)へ。

「利用者」は人間ではなく、推論を実行するオペレーターと、保存された予測を消費する将来の推奨ロジック
(Feature 007)。スキーマ変更なし(Feature 001 の既存テーブルを使用)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 指定レースを推論して永続化できる (Priority: P1) 🎯 MVP

オペレーターがレース ID を指定すると、active モデルで当該レースの出走全頭の校正済み
win/top2/top3 が算出され、`prediction_runs` / `race_predictions` / `feature_snapshots` に保存される。

**Why this priority**: 本フィーチャーの中核。これ単体で「採用モデルを使って未来レースの確率予測を出し記録する」
という価値が完結し、Feature 007(推奨)の入力になる。

**Independent Test**: 取込済み DB と保存済み active モデルに対し、ある race_id を推論実行し、
3 テーブルに行が作られ、各馬の確率が整合性(`0<=win<=top2<=top3<=1`、レース内合計が許容内)を満たすことを確認。

**Acceptance Scenarios**:

1. **Given** active モデルと出走情報のあるレース, **When** その race_id を推論, **Then** 出走全頭の
   win/top2/top3 が `race_predictions` に保存され、`prediction_runs` に 1 行、`feature_snapshots` に
   各馬の特徴が保存される。
2. **Given** 推論結果, **When** 確率整合性を検査, **Then** 全馬 `0<=win<=top2<=top3<=1`、レース内合計が
   許容内(`PROB_MONOTONIC` 制約を満たす)。
3. **Given** 出走頭数 N のレース, **When** 推論, **Then** `race_predictions` の行数は N(出走全頭、欠落なし)。

---

### User Story 2 - リーク無し・決定論・学習特徴と一致が保証される (Priority: P1)

推論が「結果情報を一切使わず」「同一入力で同一出力」で、かつ「学習時の特徴スキーマと一致」した状態でしか
実行されないことを、オペレーターが保証された形で運用できる。

**Why this priority**: 憲法 II(リーク防止)・IV(確率整合性)・V(再現性)の遵守は NON-NEGOTIABLE。
serving は本番に最も近く、ここでリークや非決定性が混入すると全予測が無効になる。

**Independent Test**: (a) 同一(race, model, logic_version)で 2 回推論し `race_predictions` が完全一致、
(b) 当該レースの結果確定オッズ/人気(ResultMarket)や着順(race_results)を変えても予測が不変、
(c) 学習時の feature_version / feature ハッシュと推論時のスキーマが一致しなければ推論が fail-fast。

**Acceptance Scenarios**:

1. **Given** 同一レース・同一 active モデル・同一 logic_version, **When** 2 回推論, **Then** 各馬の
   win/top2/top3 が完全一致(決定論)。
2. **Given** あるレース, **When** 結果確定オッズ/人気/着順を変更して推論, **Then** 予測は変化しない
   (モデルが結果由来情報を参照していない)。
3. **Given** 学習成果物の feature ハッシュと現行特徴スキーマが不一致, **When** 推論, **Then** 明確な
   エラーで停止し、誤った予測を保存しない。
4. **Given** 結果未確定の未来レース(race_results 無し), **When** 推論, **Then** 結果データに依存せず
   出走情報のみで推論が完了する。

---

### User Story 3 - 日付指定で複数レースを一括推論し、active モデルを解決できる (Priority: P2)

オペレーターが日付を指定すると当日の全対象レースを一括推論できる。active モデルの選択規則
(単一前提・複数時/不在時の扱い・明示指定)が定義されている。

**Why this priority**: 運用効率の向上。MVP(US1)が単一レースで成立した後の拡張。

**Independent Test**: ある日付を指定して当日の複数レースが推論・保存され、各レースに `prediction_runs` が
1 行ずつ作られること、および active モデルが 0/複数のとき定義通り(エラー or 明示指定要求)に動くことを確認。

**Acceptance Scenarios**:

1. **Given** 当日に複数の対象レース, **When** 日付指定で推論, **Then** 各レースが推論され保存される。
2. **Given** active モデルが厳密に 1 つ, **When** モデル未指定で推論, **Then** その active モデルが使われる。
3. **Given** active モデルが 0 個、または複数, **When** モデル未指定で推論, **Then** 明確なエラーで停止し
   明示指定(--model-version)を促す。

---

### Edge Cases

- **出走頭数が極端**(1 頭、または top3 より少ない N<3): 整合性の目標和は `min(k, N)`。1 頭でも整合的に出力。
- **結果未確定レース**: `race_results` が存在しない。出走情報(`entry_status`)のみで母集団を取得。
- **取消・除外馬**: `entry_status != 'started'` は母集団から除外(予測対象外)。
- **デビュー馬・少履歴馬**: 履歴特徴は欠損(NaN)。学習時と同じ欠損方針で扱い、0 と混同しない。
- **同一レースの再推論**: 既存予測を壊さず、新しい推論実行(run)として履歴に追加する。
- **active モデルの成果物欠落**(weights_uri/calibrator_uri のファイルが無い): 明確なエラーで停止。
- **学習に存在しなかった新カテゴリ**(新騎手等): 学習時と同じ未知カテゴリ方針で安全に処理(エラーにしない)。
- **対象レースが特徴の取込スコープ外**(2007 年より前): スコープ外として推論しない/明確に通知。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは `model_versions.adoption_status='active'` のモデルと、その推論に必要な**前処理状態を
  含む全成果物**(weights / calibrator / **特徴量列順・categorical 方針・target encoder 等の前処理器**)を
  保存済み成果物からロードして推論に用いる MUST。前処理器が成果物として保存されていない学習モデル
  (例: target encoding を使ったのに encoder 成果物が無い)は serving できず fail-fast する。
- **FR-002**: システムは対象レースの母集団を **出走情報(`entry_status='started'`)** から取得し、取消・除外を
  除外する MUST。結果(`race_results`)には依存しない(結果未確定でも母集団を確定できる)。
- **FR-003**: システムは各馬の特徴量を Feature 004 と同一の **as-of(`race_date < R`、同日除外)** で構築し、
  `model_input_features()` の列のみをモデル入力に使う MUST。
- **FR-004**: システムは推論を Feature 005 と同一順序 **raw win → 校正 → clip([eps,1-eps]) → レース内
  正規化(Σwin=1) → Harville で top2/top3** で行う MUST。
- **FR-005**: 出力は各馬 `0<=win<=top2<=top3<=1`、レース内合計が許容内(`race_predictions` の
  `PROB_MONOTONIC` 制約を満たす)MUST。違反時は保存せず停止する。
- **FR-006**: システムは推論ごとに `prediction_runs` に 1 行(race_id, model_version, logic_version,
  computed_at)を記録する MUST。
- **FR-007**: システムは出走全頭の win/top2/top3 を `race_predictions` に保存する MUST(欠落なし)。
- **FR-008**: システムは各馬の特徴を `feature_snapshots`(feature_version, features jsonb)に保存し、
  推論の監査・再現を可能にする MUST。保存対象は **前処理後の model-input ベクトル**(target encoding 適用後の
  実際にモデルへ入力した値、特徴量名でキー付け)とする。raw 値だけでは TE 依存モデルを再現できないため。
- **FR-009**: 推論は決定論的 MUST。同一(レース, モデル, logic_version)で再実行すると `race_predictions` が
  完全一致する(成果物が同一である限り)。
- **FR-010**: モデルは結果由来情報(結果確定オッズ/人気=ResultMarket、着順=`race_results`)を入力に
  使わない MUST。当該レース当日以降のデータを特徴に混ぜない(未来リーク禁止)。
- **FR-011**: システムは学習成果物の特徴スキーマ識別子(feature_version / feature ハッシュ)と推論時の
  特徴スキーマが一致しない場合、推論を **fail-fast** で停止する MUST(誤った予測を保存しない)。
- **FR-012**: システムは結果未確定の未来レースでも推論できる MUST(`race_results` の有無に依存しない)。
- **FR-013**: システムは CLI で **race_id 指定** または **日付指定**(当日全対象レース)で推論を実行できる MUST。
- **FR-014**: システムは推論に用いた **logic_version**(特徴ロジック + 推論ロジックのバージョン)を記録する MUST。
- **FR-015**: active モデルが厳密に 1 つなら既定でそれを使う MUST。0 個または複数の場合は明確なエラーで停止し、
  `--model-version` の明示指定を促す MUST(明示指定時はそれを使う)。

### Key Entities *(include if feature involves data)*

- **PredictionRun**(`prediction_runs`): 1 回の推論実行。race_id・model_version・logic_version・computed_at。
  同一レースの再推論は新しい run として追加(監査履歴)。
- **RacePrediction**(`race_predictions`): run × 馬ごとの win/top2/top3 確率。`PROB_MONOTONIC` 制約。
- **FeatureSnapshot**(`feature_snapshots`): run × 馬ごとの特徴値(feature_version + jsonb)。監査・再現用。
- **ServingModel**: ロードした active モデル + 校正器 + **前処理器(特徴量列順・categorical 方針・target
  encoder)** + 学習メタ(feature_version, feature ハッシュ, seed, 校正方式)。`model_versions` + 成果物から復元。
  training 側の成果物保存に前処理器を含める拡張が前提(DB スキーマ変更ではなくファイル成果物の拡張)。
- **対象レース母集団**: `entry_status='started'` の出走馬(結果非依存)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 任意の対象レースで、出走全頭の整合的な予測が 3 テーブル(`prediction_runs`/`race_predictions`/
  `feature_snapshots`)に保存される(整合性検査を通る)。
- **SC-002**: 同一(レース, モデル, logic_version)で 2 回推論すると `race_predictions` が完全一致する(決定論)。
- **SC-003**: 結果未確定の未来レース(`race_results` 無し)でも推論が完了し保存される。
- **SC-004**: 結果確定オッズ/人気/着順を変更しても予測が不変(リーク無し)。学習特徴スキーマ不一致では
  fail-fast し、予測を保存しない。
- **SC-005**: `feature_snapshots` から推論時の特徴を再現でき、予測の監査が可能。
- **SC-006**: active モデルが 0 個/複数のとき、定義どおり(エラー + 明示指定要求)に動作する。

## Assumptions

- Feature 001(prediction_runs/race_predictions/feature_snapshots/model_versions スキーマ)、004(leak-safe
  特徴量)、005(active モデル + 成果物保存)が適用済み。少なくとも 1 つの active モデルと成果物が存在する。
- 母集団は出走情報から取得し、結果未確定レースでも確定できる(出馬表が取り込まれている前提)。
  `entry_status='started'` を「確定出走馬」とみなす。取消・除外がレース前に `cancelled`/`excluded` へ
  更新される取込運用を前提とし、出走しない馬が母集団に混入しないことは ingest の責務とする(本フィーチャー外)。
- serving は前処理状態(特徴量列順・categorical 方針・target encoder)を含む成果物を必要とする。これは
  Feature 005 の成果物保存を拡張して満たす(DB スキーマ変更なし、ファイル成果物の追加)。既存の前処理器なし
  成果物は、target encoding 不使用なら feature_hash 一致を条件に列順を再構成して serving 可、TE 使用モデルは
  fail-fast。
- 単一の win モデル(`label_schema='win_top2_top3'`)を対象とする。複数 label スキーマの併存は本フィーチャー外。
- 同一レースの再推論は上書きではなく新しい run として履歴に追加する(冪等ではなく追記)。`prediction_run_id`
  は実行ごとに一意。
- 確率整合性の許容は Feature 003/005 と同一(win 0.05 / top2 0.10 / top3 0.15)。
- スキーマ変更なし。推奨・賭け・ROI・期待値・推定オッズは Feature 007 に分離。
- 実オッズの取り込み有無に関わらず、モデルはオッズ/人気を参照しない(リーク防止)。
