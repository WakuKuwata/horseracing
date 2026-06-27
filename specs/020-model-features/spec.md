# Feature Specification: モデル改善 — リーク安全な特徴量拡張と walk-forward 採用ゲート

**Feature Branch**: `020-model-features`

**Created**: 2026-06-27

**Status**: Draft

**Input**: User description: "予測 edge 向上のためのリーク安全な特徴量拡張。016/017 で『市場に勝てていない』と判明。新規 as-of/out-of-fold 特徴量を追加し walk-forward OOS で現行モデルを上回る場合のみ採用。"

## 概要

Feature 016/017 の実データ評価で「モデルは exotic 推定オッズ上で市場に勝てていない」と判明した。本 feature は
予測精度（win 確率の識別力・校正）の底上げを目的に、Feature 004 の特徴量セットに**新規のリーク安全特徴量**を
追加し、**walk-forward out-of-sample で現行モデル（baseline）を上回る場合のみ採用**する。スキーマ変更なし。

035/036（pedigree embedding）の校正ミス前例（[[pedigree-embedding-036-result]]）= 片側 fold 評価 + 校正未確認に
よる false positive を踏まえ、採用ゲートに **fold 別差分（勝ち fold 数・最悪 fold・ECE 差分）** と **group
ablation** を組み込む。**成功基準は「win の OOS 品質改善」**に置く（公開情報特徴は市場に織り込まれている可能性が
高く、絶対校正の改善＝市場超過ではない。市場超過 edge は努力目標・diagnostic）。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - リーク安全な新規特徴量の実装と cutoff 検証（Priority: P1）🎯 MVP

開発者は、各新規特徴量を「対象レースより前のデータのみ・同日除外・out-of-fold」で計算し、その境界をテストで
保証したい。特に騎手・調教師フォーム等の跨馬統計が対象行・同日結果を取り込まない（ターゲットエンコーディング
的リーク回避）ことを担保したい。

**Why this priority**: リーク安全性が全ての前提。リークした特徴は OOS を過大評価し採用判断を誤らせる（035/036
前例）。特徴量の正しさが採用評価の土台。

**Independent Test**: 各特徴について feature spec（source / 利用可能タイミング / cutoff 規則 / 欠損処理）を定義し、
cutoff テスト（対象レース当日・以降のデータを変更しても特徴量が不変）と target-row 除外テスト（対象行の結果を
変更しても跨馬統計が不変）が通る。

**Acceptance Scenarios**:

1. **Given** 新規特徴量（近走フォーム / 休養明け日数 / クラス遷移 / 距離・馬場適性 / 枠順・頭数 / 斤量変化 /
   騎手・調教師フォーム）, **When** 各特徴を計算, **Then** **feature spec table**（source・availability_time・
   cutoff_rule・missing_policy）が定義され、各特徴に cutoff テストがある。
2. **Given** 騎手・調教師フォーム（跨馬統計）, **When** 対象行の着順 / 同日他レース結果を変更, **Then** その
   統計値は不変（対象行除外・同日除外・out-of-fold）。
3. **Given** 新馬 / 過去成績なし, **When** 特徴を計算, **Then** Unknown（欠損）として渡し、存在しない過去成績に
   0 を代入しない。
4. **Given** 馬体重変化・斤量変化等の発表タイミング依存特徴, **When** 計算, **Then** 評価時点で利用可能な情報
   のみを使う（購入時点より後の情報を使わない）。

---

### User Story 2 - walk-forward 採用ゲート（fold 内選択・LogLoss 改善・ECE 非悪化）（Priority: P1）

開発者は、新特徴量を加えたモデルが現行モデルを out-of-sample で本当に上回るかを、選択リークなく評価し、改善
時のみ採用したい。

**Why this priority**: 憲法 III の採用ゲート。選択リーク・過学習・偶然 fold を排除した正当な採用判断が本 feature
の価値。

**Independent Test**: walk-forward で各 fold の学習窓内に inner train/validation を切り、特徴量選択・ハイパラ
選択・early stopping を完結させる。OOS で新モデル vs 現行 baseline の LogLoss/Brier/AUC/ECE と fold 別差分を
算出し、採用条件（LogLoss 改善 かつ ECE 非悪化）と勝ち fold 数・最悪 fold を判定。

**Acceptance Scenarios**:

1. **Given** 新特徴量, **When** walk-forward 評価, **Then** **候補特徴集合は事前固定**（既存+新規9）で OOS を
   見て特徴を選ばず（選択リーク無し）、fold 内はハイパラ・early stopping のみを学習窓内で完結する。OOS は
   「固定候補集合 vs baseline」を評価し、採用時はその固定集合を全体再学習する（評価＝デプロイ一致）。
2. **Given** OOS 評価, **When** 新モデルと現行 baseline を比較, **Then** **PRIMARY = LogLoss 改善 かつ ECE
   非悪化**（Brier 非悪化が望ましい、AUC は順位性能の説明に限定）で採用判定する。
3. **Given** fold 別差分, **When** 採用判定, **Then** 平均だけでなく **勝ち fold 数・最悪 fold・fold 別 ECE
   差分**を確認し、一部 fold の偶然改善を全体採用に混ぜない。
4. **Given** 過学習リスク, **When** 学習, **Then** 特徴数上限・正則化（min_data_in_leaf / lambda / feature_
   fraction / num_leaves 等）レンジを事前固定し、fold 間で feature 寄与（gain/SHAP/ablation の符号・順位）が
   安定しない特徴は除外候補とする（feature importance のみで採否を決めない）。
5. **Given** group ablation, **When** 寄与分析, **Then** 特徴を group（近走フォーム / 適性 / 人的フォーム /
   レース条件）単位で ablation し、horse フォームと jockey フォームのように履歴を共有する group の寄与を分離
   して判断する。

---

### User Story 3 - 下流 diagnostic と市場超過の現実的評価（Priority: P2）

開発者は、win 品質改善が下流（pseudo-ROI / Kelly）と市場超過 edge にどう波及するかを diagnostic として把握し、
過剰期待を避けたい。

**Why this priority**: 016/017 の「市場に勝てない」を踏まえ、win 改善が必ずしも収益化しないことを明示し、採用
判断を主ゲート（win 品質）に正しく置く。

**Independent Test**: 採用候補モデルで 011/016 の pseudo-ROI / Kelly bankroll backtest（SECONDARY、高分散）と、
市場 q に対する edge（p−q 校正・edge bucket 別実現勝率・q 条件付き LogLoss）を測り、主採用判断は win 品質で行う。

**Acceptance Scenarios**:

1. **Given** 採用候補モデル, **When** pseudo-ROI / Kelly backtest, **Then** SECONDARY diagnostic として算出される
   が、高分散のため主採用ゲートにしない（偶然勝ち fold を採用基準に使わない）。
2. **Given** 市場 q, **When** edge 評価, **Then** p−q calibration・edge bucket 別実現勝率・q 条件付き LogLoss を
   測り、「絶対校正の改善＝市場超過ではない」ことを明示する。成功基準は OOS win 改善（市場超過は努力目標）。

### Edge Cases

- **同日他レース / 直前レース結果**: 跨馬統計に混入させない（同日全除外、対象レースより前のみ）。
- **対象行リーク**: jockey/trainer フォームに対象行（当該馬の当該レース結果）を含めない（out-of-fold）。
- **発表タイミング**: 馬体重・斤量等は評価時点で利用可能な値のみ（後出し情報を使わない）。
- **新馬 / 過去不在**: Unknown（欠損）。0 代入禁止。
- **選択リーク**: 全期間 OOS を見て効いた特徴だけ残す＝検証ラベルで選択 → 禁止（候補特徴は事前固定、OOS で
  特徴を選ばない。fold 内はハイパラ/early-stopping のみ）。
- **偶然 fold**: 平均改善でも最悪 fold 悪化・勝ち fold 少数 → 採用しない。
- **高次元過学習**: 多窓展開で実質高次元 → 特徴数上限・正則化・安定性検査。
- **市場効率**: win 改善が edge に繋がらない場合あり → 成功は win 品質で判定。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 各新規特徴量に **feature spec**（source / availability_time / cutoff_rule / missing_policy）を定義
  MUST。Unknown=欠損で渡し、存在しない過去成績に 0 を代入してはならない。
- **FR-002**: 全累積 / 適性 / フォーム特徴は対象レースより前のデータのみ、または out-of-fold で計算し、
  walk-forward 分割境界日を特徴量計算に適用 MUST。同日他レース・将来情報・結果を使ってはならない。
- **FR-003**: 騎手・調教師フォーム等の跨馬統計は **対象行の結果を含めず・同日除外・out-of-fold** で計算 MUST
  （ターゲットエンコーディング的リーク回避）。各特徴に cutoff テストと target-row 除外テストを持つ。
- **FR-004**: 市場オッズを予測モデルの特徴量にしてはならない MUST（既存規約）。
- **FR-005**: **候補特徴集合は事前固定**（既存特徴 + 新規9特徴）とし、**OOS（検証 fold）を見て特徴を選択しない**
  MUST（評価モデル＝デプロイモデルを一致させ、選択リークを原理的に排除）。fold 内で行うのは**ハイパラ選択・
  early stopping のみ**（各 walk-forward fold の学習窓内 inner train/validation で完結、検証 fold ラベル不使用）。
  group ablation（FR-008）は寄与把握の diagnostic であり、採用特徴の選別には使わない。
- **FR-006**: 評価は walk-forward OOS で新モデルと現行 baseline を比較し、**LogLoss 改善 かつ ECE 非悪化**を
  PRIMARY 採用条件とする MUST（Brier 非悪化が望ましい、AUC は順位性能の説明に限定）。
- **FR-007**: 採用判定は平均だけでなく **fold 別差分（勝ち fold 数・最悪 fold・fold 別 ECE 差分）** を含める
  MUST。一部 fold の偶然改善で全体採用しない。
- **FR-008**: **group ablation**（近走フォーム / 適性 / 人的フォーム / レース条件）を行い、履歴を共有する group
  の寄与を分離して判断 MUST。feature importance のみで採否を決めない。
- **FR-009**: 過学習対策として特徴数上限・正則化レンジを事前固定し、fold 間で feature 寄与（gain/SHAP/ablation
  の符号・順位）が安定しない特徴を除外候補とする MUST。
- **FR-010**: SECONDARY diagnostic として 011/016 の pseudo-ROI / Kelly bankroll backtest を算出 MUST（高分散の
  ため主採用ゲートにしない）。
- **FR-011**: 市場 q に対する edge（p−q calibration・edge bucket 別実現勝率・q 条件付き LogLoss）を測り、
  「絶対校正の改善＝市場超過ではない」ことを明示 MUST。成功基準は OOS win 改善（市場超過は努力目標）。
- **FR-012**: LightGBM / binary objective を維持し、新特徴量で再学習・評価 MUST。Feature 009 の win→joint 派生は
  確率整合性（IV）のため維持する。
- **FR-013**: スキーマ変更を行ってはならない MUST（特徴量は計算、model_versions に新 feature_version を記録、
  既存 prediction/eval テーブルを使用）。
- **FR-014**: CLI で新特徴量込みの再学習・評価を提供 MUST。日本語規約維持。決定論（同一データ・同一 seed で
  同一評価結果）。

### Key Entities *(include if feature involves data)*

- **feature spec**: 特徴名・source・availability_time・cutoff_rule・missing_policy・group。
- **新規特徴量群**: 近走フォーム / 休養明け日数 / クラス遷移 / 距離・馬場適性 / 枠順・頭数 / 斤量変化 /
  騎手・調教師フォーム。各々リーク安全・Unknown 欠損。
- **採用評価レポート**: fold 別 + 平均の LogLoss/Brier/AUC/ECE（新 vs baseline）、勝ち fold 数・最悪 fold・
  ECE 差分、group ablation 寄与、採用判定（PRIMARY）、SECONDARY pseudo-ROI/Kelly・市場 q edge。
- **model_versions**: 新 feature_version + 採用メタ。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 全新規特徴量に feature spec（source/availability/cutoff/missing）が定義され、各々に cutoff テストが
  ある（100%）。
- **SC-002**: 跨馬統計（騎手・調教師フォーム）が対象行・同日結果を取り込まない（target-row 除外テスト 100% パス）。
- **SC-003**: 新馬/過去不在で 0 代入が発生しない（Unknown 欠損、検証で確認）。
- **SC-004**: 候補特徴集合が事前固定で OOS を見て特徴選択しない（選択リーク 0 件）。fold 内はハイパラ・early
  stopping のみが学習窓内で完結し、検証 fold ラベルを使わない。
- **SC-005**: OOS で新モデル vs baseline の LogLoss/Brier/AUC/ECE が fold 別 + 平均で算出される。
- **SC-006**: 採用判定が **LogLoss 改善 かつ ECE 非悪化**（PRIMARY）+ fold 別差分（勝ち fold 数・最悪 fold・ECE
  差分）で行われ、偶然 fold 改善が全体採用に混ざらない。
- **SC-007**: group ablation で近走フォーム/適性/人的フォーム/レース条件の寄与が分離して報告される。
- **SC-008**: SECONDARY（pseudo-ROI/Kelly・市場 q edge）が diagnostic として算出され、主採用ゲートにされない。
- **SC-009**: スキーマ変更ゼロ。決定論（同一データ・同一 seed で評価再現）。
- **SC-010**: 改善が無い場合は採用しない（baseline 未超過なら不採用、false positive を出さない）。

## Assumptions

- **成功基準**: OOS win 品質改善（LogLoss 主・ECE 非悪化）。市場超過 edge は努力目標であり主基準にしない
  （公開情報特徴は市場に織り込み済みの可能性が高い）。
- **モデル**: LightGBM / binary 維持。ranking/monotonic/model family/multi-output/pedigree 見直しは deferred。
- **派生**: win→joint（009）は IV 整合のため維持。多出力直接学習は deferred。
- **データ**: 既存取得データ（JRA-VAN 2007+）の範囲。sectional/ラップタイム等の未取得データは deferred。
- **スキーマ**: 変更なし（feature_version で版管理、既存テーブル）。
- **依存**: features / training / eval を拡張。011/016（diagnostic）・013/017（校正）・009 に依存。
- **deferred**: ranking objective、monotonic 制約、model family 変更、multi-output（win+place 直接）、pedigree
  embedding 見直し、sectional/ラップタイム等の未取得データ、特徴量ストア化。
