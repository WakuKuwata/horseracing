# Feature Specification: Conditional-logit (race-softmax) 目的関数

**Feature Branch**: `039-conditional-logit-objective`

**Created**: 2026-07-01

**Status**: Draft

**Input**: User description: "win モデルの学習目的関数を binary から『1レース1勝の構造を直接最適化する条件付きロジット(レース内 softmax = Plackett-Luce top-1)』に切り替える選択肢を追加。新特徴なし・スキーマ変更なし・FEATURE_VERSION 不変(features-011)。036 と同型のモデリング変更。"

## 概要

現行 win モデルは LightGBM **binary** objective で各馬を独立に P(win) 予測し、その後 009 エンジンがレース内で Σ=1 に正規化してから Plackett-Luce/Harville に渡す。しかし実構造は「1レースに必ず1頭だけ勝つ」= フィールド上の多項カテゴリカルであり、binary は独立近似でレース内の相対競争(同一レース他馬との比較)を学習に織り込めていない。

本 feature は **conditional-logit(条件付きロジット / レース内 softmax / Plackett-Luce の top-1)** 目的関数を追加する。各馬スコア s_i に対しレース内 softmax `p_i = exp(s_i)/Σ_j exp(s_j)`、損失 `−log p_winner` を直接最適化する。1レース1勝の構造を学習に埋め込み、出力 softmax がレース内で自然に Σ=1 となる(009 の後付け正規化と構造整合的、憲法 IV)。

**新しい予測特徴は足さない。スキーマ変更なし。FEATURE_VERSION 不変(features-011、feature 列は完全に同一)。** 036(TE + isotonic)と同型の「モデリング変更」であり、変わるのは学習の勾配計算のみ。

**de-risk 済み**(spike, 2019+ 実データ 3 fold, 現行と同一特徴・同一 TE jockey/trainer smoothing50):

| 目的関数 | winner-NLL | top1 的中 | AUC |
|---|---|---|---|
| binary(現行) | 2.1160 | 0.2764 | 0.7839 |
| **conditional-logit** | **2.1066** | **0.2799** | **0.7948** |
| lambdarank(対照) | 2.1371 ❌ | 0.2736 | 0.7911 |

conditional-logit が binary を全指標・全 3 fold で上回った(winner-NLL −0.0094、3/3 fold 改善)。lambdarank は winner-NLL で binary に負け(ranking 目的は確率校正とズレる)→ 採るのは lambdarank でなく **conditional-logit**。036 以来はじめての「モデル p 自体」の構造的改善候補。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - conditional-logit 目的関数で学習・予測する (Priority: P1)

学習パイプラインが binary に加えて conditional-logit(レース内 softmax、損失 −log p_winner)を **objective 選択肢**として提供し、opt-in で切り替えられる。既定は binary(現行=後方互換)。conditional-logit 選択時、学習はレースを group として group ごとの softmax 勾配(grad = p − y, hess = max(p(1−p), eps))で木を成長させ、出力は各馬のレース内 win 確率を直接与える。

**Why this priority**: 本 feature の中核。目的関数そのものが価値であり、これが無いと何も始まらない。

**Independent Test**: 小さな合成データ(数レース、各レース1勝)で conditional-logit predictor を fit → 予測がレース内で Σ=1 の妥当な確率、winner に高い確率を付ける。同一データで binary と両方 fit でき、objective を切り替えても他経路(TE/校正/009)が壊れないことを確認。

**Acceptance Scenarios**:

1. **Given** 学習行と勝敗ラベル, **When** objective=cond_logit で predictor を fit, **Then** 各レースの予測確率が Σ=1(009 正規化後と一致)で、勝ち馬に相対的に高い確率が付く。
2. **Given** 同一の学習データ, **When** objective=binary(既定)で fit, **Then** 現行 lgbm-036 と同一の挙動(後方互換、既存テスト透過)。
3. **Given** 単一クラス(勝ち馬不在)の劣化レース, **When** cond_logit で fit, **Then** 例外でなく一様分布 fallback(binary の _constant fallback と同型)。

---

### User Story 2 - TE + isotonic 校正との統合 (Priority: P1)

conditional-logit を既存の OOF target encoding(jockey_id/trainer_id)および isotonic 校正と統合する。cond_logit 出力は既にレース正規化 softmax なので、raw softmax 確率を isotonic に通し 009 で再正規化する経路の整合を取る(校正が最大の設計論点)。

**Why this priority**: 036 の勝ち筋(TE + isotonic)を捨てずに objective 改善を積み上げるため必須。校正が崩れると採用ゲート(ECE)を通らない。

**Independent Test**: cond_logit + TE + isotonic で fit → held-out 校正セットで isotonic が単調写像を学習し、校正後確率のレース内 Σ=1 が保たれることを確認。

**Acceptance Scenarios**:

1. **Given** cond_logit predictor, **When** TE(jockey/trainer, OOF)を適用, **Then** TE 列は 036 と同じくリーク安全(OOF + training-only encoder)で、cond_logit 学習に float 列として入る。
2. **Given** cond_logit の raw 確率, **When** isotonic 校正を fit・適用, **Then** 校正後も各馬確率は [eps, 1−eps] にクリップされ、009 に渡すとレース内 Σ=1。

---

### User Story 3 - リーク安全・確率整合の不変保証 (Priority: P1)

目的関数の変更が勾配計算のみを変え、リーク境界(憲法 II)と確率整合(憲法 IV)を一切広げない/壊さないことを保証する。

**Why this priority**: 憲法 II/IV は非交渉。objective 変更でデータ経路やリーク面が変わっていないことを機械的に担保する release gate。

**Independent Test**: leak-guard — cond_logit predictor でも (a) odds/今走結果を特徴にしない、(b) group(レース所属)は race_id のみ依存で結果を読まない、(c) 今走結果を変えても他馬の予測が変わらない(as-of/TE は 036 と不変)。

**Acceptance Scenarios**:

1. **Given** cond_logit 学習, **When** group を race_id で構成, **Then** group 割当は結果(finish_order 等)を参照しない(勝敗ラベルは損失計算のみに使用、特徴・group には非流入)。
2. **Given** 学習済み cond_logit モデル, **When** 009 win→joint に予測を渡す, **Then** Σexacta=1・Σtrifecta=1 等の 009 不変条件が保たれる(win 確率の供給元が変わるだけで 009 は不変)。
3. **Given** cond_logit の予測出力, **When** stake_fraction/確率を確認, **Then** モデル特徴に再流入しない(leak-guard)。

---

### User Story 4 - serving 経路の対応 (Priority: P2)

serving(`serving predict_race`)を conditional-logit モデルに対応させ、単一/複数レースの予測を produce できるようにする。feature 列は features-011 で不変なので feature_hash は不変だが、objective/model_family が異なるため新 model_version `lgbm-039` として登録する。

**Why this priority**: 学習だけでは運用に載らない。ただし採用が決まってからの結線でよいので P2。

**Independent Test**: lgbm-039 を保存 → serving がロードし predict_race で cond_logit 予測を返す。feature_hash が features-011 と整合し、014 API / 021 表示が既存契約のまま lgbm-039 の予測を出せる。

**Acceptance Scenarios**:

1. **Given** 保存された lgbm-039 artifacts(model + calibrator + encoders), **When** serving がロード, **Then** predict_race が TE encoder 適用 → cond_logit 予測 → 校正 → 各馬 win 確率を返す。
2. **Given** lgbm-039(features-011), **When** feature_hash を検証, **Then** features-011 と整合(特徴列不変)。

---

### User Story 5 - 採用判定(18-fold walk-forward OOS) (Priority: P1)

事前登録した採用ゲートで conditional-logit が現行 binary(lgbm-036)を上回るときだけ採用する。

**Why this priority**: 憲法 III(評価先行)非交渉。数値を見てから閾値を動かさない。

**Independent Test**: `train-evaluate --objective cond_logit` で 18-fold expanding-yearly OOS を実行し AdoptionReport を得る。基準を機械適用して採否を決める。

**Acceptance Scenarios**:

1. **Given** 18-fold walk-forward OOS, **When** cond_logit と binary(baseline)を同一特徴・同一 TE・同一 fold で比較, **Then** PRIMARY(win LogLoss 改善 かつ ECE 非悪化)+ fold ガード(strict majority・worst-fold ECE tol・worst-fold dLogLoss tol)で採否が決まる。
2. **Given** 採用(primary_pass=True), **When** lgbm-039 を最終学習・登録, **Then** active 昇格・lgbm-036 retired・serving が自動ロード。
3. **Given** 不採用, **When** ゲート未達, **Then** main は lgbm-036/features-011 のまま、ブランチ保全(027/037/038 前例)。

---

### Edge Cases

- **1頭立てレース**: softmax が自明に p=1。損失 = −log 1 = 0(勾配 0)。学習に寄与しないが例外にしない。
- **勝ち馬不在レース(全頭 DNF/取消後に result 無し)**: 損失計算から除外 or 一様勾配。学習を止めない。
- **同着(dead heat)**: 勝ち馬が2頭以上(y の和 > 1)。損失定義が top-1 前提のため、学習では稀ケースとして扱い(和で正規化 or 除外)、評価の winner-NLL は 036/spike 同様「勝ち馬ちょうど1頭」のレースのみで測る。
- **巨大フィールド**: softmax は数値安定化(max 減算)必須。
- **既定 binary の後方互換**: objective 未指定時は現行と bit 一致(既存モデル・テストが透過)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは win モデルの学習 objective として `binary`(既定)と `cond_logit` を選択できる MUST。既定 binary は現行と後方互換(挙動不変)。
- **FR-002**: `cond_logit` はレースを group とし、group ごとの softmax(数値安定化込み)で勾配 `grad = p − y`・ヘシアン `hess = max(p(1−p), eps)` を計算する custom objective として実装する MUST。
- **FR-003**: `cond_logit` 学習は学習行をレース連続に整列し group sizes を渡す MUST(chronological fold 構造・OOF TE のリーク安全性は 036 と不変)。
- **FR-004**: `cond_logit` は既存 OOF target encoding(jockey_id/trainer_id)と統合する MUST(TE 列は OOF + training-only encoder でリーク安全、float 列として学習に入る)。
- **FR-005**: `cond_logit` の raw 確率(レース内 softmax)に isotonic 校正を適用し、校正後も 009 に渡してレース内 Σ=1 を保つ MUST。
- **FR-006**: `cond_logit` の出力は各馬の win 確率そのもので、009 win→joint(Plackett-Luce/Harville)にそのまま渡せる MUST(009・7馬券種派生・Unknown 維持は不変)。
- **FR-007**: 目的関数の変更はデータ経路(as-of 特徴・TE の OOF・fold・training-only encoder)を変えず、odds/今走結果を特徴にしない MUST(leak-guard 不変)。group は race_id のみ依存で結果を読まない MUST。
- **FR-008**: serving(predict_race)は `cond_logit` モデルをロード・予測できる MUST。feature 列は features-011 不変 → feature_hash 不変。objective/model_family を artifacts/metrics_summary に記録する MUST。
- **FR-009**: 採用判定は事前登録・18-fold walk-forward expanding-yearly OOS で行う MUST。PRIMARY = win LogLoss(009 後のレース正規化確率で評価)改善 かつ ECE 非悪化 + fold ガード(strict majority・worst-fold ECE tol・worst-fold dLogLoss tol)。SECONDARY(診断のみ) = winner-NLL・top1 的中・AUC。
- **FR-010**: DB スキーマを変更しない MUST(migration head 不変)。FEATURE_VERSION を変更しない MUST(features-011、feature 列同一)。
- **FR-011**: 採用時は lgbm-039 を active 昇格・lgbm-036 retired・serving 自動ロード。不採用時は main を lgbm-036/features-011 のまま維持しブランチ保全する MUST。
- **FR-012**: eval モジュールは predictor-agnostic を維持する MUST(winner-NLL/top1 の診断追加は eval を training に依存させない)。

### Key Entities *(include if feature involves data)*

- **Objective(目的関数)**: 学習の損失/勾配定義。`binary`(現行、独立 P(win))または `cond_logit`(レース内 softmax、−log p_winner)。データではなく学習設定。
- **Race group(レース group)**: 同一 race_id に属する学習行の集合。cond_logit の softmax 正規化単位。race_id のみに依存(結果非参照)。
- **model_version lgbm-039**: cond_logit で学習した新モデル版。features-011・model_family/objective を記録。artifacts = model + calibrator + encoders。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 18-fold walk-forward OOS で conditional-logit の win LogLoss が現行 binary(lgbm-036 相当)を改善し、ECE を悪化させない(採用ゲート PRIMARY 通過)。
- **SC-002**: fold ガード通過 — 勝ち fold が strict majority(n_win*2 > n_folds)、worst-fold ECE 悪化が tol 内、worst-fold dLogLoss が tol 内。
- **SC-003**: 診断 SECONDARY で winner-NLL・top1 的中・AUC が binary を上回る(spike の傾向が 18-fold でも再現)。
- **SC-004**: 既定 binary 経路は現行と後方互換(既存 training/serving テストが透過で緑、lgbm-036 の予測不変)。
- **SC-005**: leak-guard 全通過(odds/今走結果非特徴・group が結果非参照・今走結果変更で他馬予測不変)+ 009 不変条件維持。
- **SC-006**: スキーマ不変(migration head 不変)・FEATURE_VERSION 不変(features-011)・feature_hash 整合。

## Assumptions

- LightGBM 4.x の custom objective(`params["objective"]=callable`、`(grad, hess)` を返す)を使用する。旧 `fobj` 引数は使わない。
- 採用ゲートの閾値・fold 数(18-fold expanding-yearly)・fold ガード tol は 020/023/036 と同一(事前登録、数値を見てから動かさない)。
- 校正は 036 と同じ isotonic を既定とする。cond_logit の raw 確率はレース内 softmax で、isotonic はその周辺(per-horse)確率に対して fit する(009 で再正規化)。
- 同着(dead heat)は稀ケースとして学習で軽微扱い、評価 winner-NLL は「勝ち馬ちょうど1頭」レースに限定(spike と同基準)。
- cond_logit 専用のハイパラ再探索はしない(まず既定 params で binary と同条件比較、HPO は deferred)。
- 市場非超過(現行 0.218 vs 市場 0.202)は本 feature でも維持される公算が高い(リーク無しの傍証)。市場超えは目標ではない(製品目的=意思決定支援)。

## Dependencies

- 既存 training パッケージ(win_model / predictor / artifacts / cli の train-evaluate)。
- 既存 OOF target encoding infra(036、jockey/trainer)・isotonic 校正。
- 025 feature materialization(features-011、feature 列不変=materialize パリティ非干渉)。
- 009 probability engine(win→joint、変更なし)。
- serving predict_race(TE encoder 適用経路、cond_logit 対応を追加)。

## Out of Scope (Deferred)

- 完全 Plackett-Luce(top-1 でなく top-k 順序全体)listwise 目的関数。
- 条件別(芝ダ/距離帯)objective。
- objective と recency 重み付けの併用。
- cond_logit 専用のハイパラ再探索。
- 目的関数のさらなる変種(rank_xendcg 等、lambdarank は spike で棄却済)。
