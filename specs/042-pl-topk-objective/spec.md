# Feature Specification: Plackett-Luce top-k (listwise) 目的関数

**Feature Branch**: `042-pl-topk-objective`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "039 cond_logit(PL top-1)を top-k(k=3)listwise に拡張。2着・3着の順序も損失に使い教師信号を増やす。新特徴なし・スキーマ変更なし・FEATURE_VERSION 不変(features-012)。039 と同型のモデリング変更。"

## 概要

039 の conditional-logit は「勝ち馬 1 頭」だけを損失に使う(PL の top-1)。本 feature は **Plackett-Luce の逐次分解を top-3 まで**使う: stage 1 = 全馬 softmax で −log p(1着馬)、stage 2 = 1着馬を除いた残りの softmax で −log p(2着馬)、stage 3 = 同様に 3着馬。stage 減衰重み w=[1.0, 0.5, 0.25](奥の順序ほどノイジーなため)。**2-3着の順序情報が中位馬のスコア形成を助け、win 予測自体が改善**する。

**de-risk 済み**(spike, 2019+ 実データ 3 fold, 同一特徴・同一 TE):

| | winner-NLL | top1 | AUC |
|---|---|---|---|
| cond_logit(top-1、現行) | 2.1087 | 0.2781 | 0.7942 |
| **PL top-3(w=1/.5/.25)** | **2.0992**(−0.0095) | **0.2845**(+0.0064) | **0.7977**(+0.0035) |

**全 3 fold 改善・2026-07-02 ブレストキュー最大の信号**(039 spike と同規模)。codex の事前警告「ranking 系は win 校正とズレる」は spike の win 系指標では非発現(lambdarank と違い PL top-k は確率モデルの尤度そのものであるため)。

**新特徴なし・スキーマ変更なし・FEATURE_VERSION 不変(features-012)**。予測経路は cond_logit と完全同一(raw_score→レース内 softmax→校正→009)= 変わるのは学習の勾配計算のみ。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - PL top-k 目的関数で学習する (Priority: P1)

objective 選択肢に `pl_topk` を追加(binary/cond_logit と並列、既定は binary 不変)。学習はレース group ごとに PL 逐次 stage(remaining 集合上の softmax)で grad/hess を w_j 加重加算。

**Why this priority**: 本 feature の中核。

**Independent Test**: 合成データ(数レース、着順 1-3 判明)で pl_topk fit → 予測がレース内 Σ=1・上位馬に高確率。stage 勾配の値が手計算と一致。

**Acceptance Scenarios**:

1. **Given** 1 group(着順 1,2,3,他), **When** pl_topk の grad/hess を計算, **Then** stage1 = 全馬 softmax の p−y(w=1.0)、stage2 = 1着除き残りの softmax の p−y(w=0.5)を加算、stage3 同様(w=0.25)。
2. **Given** objective 未指定(binary)/cond_logit, **When** fit, **Then** 現行と bit 一致(後方互換)。
3. **Given** stage j の対象馬が同着等で一意でない, **When** 勾配計算, **Then** その stage 以降は break(先行 stage の勾配は保持)。stage1(winner)非一意は group 全体を中立化(039 同型)。

---

### User Story 2 - rank ラベルの供給(リーク境界不変) (Priority: P1)

学習行に確定着順 rank(1..3、その他 0)を **label として**供給する。win ラベルと同じ label 側(特徴ではない)。

**Why this priority**: pl_topk の損失計算に必須。リーク境界(憲法 II)を広げないことが release gate。

**Independent Test**: rank が feature_cols に入らない・leak-guard(今走結果変更で予測不変=label は損失のみ)・grep。

**Acceptance Scenarios**:

1. **Given** training matrix, **When** rank 列を導出, **Then** race_results の確定着順(finished のみ)から win と同一機構で導出され、model_input_features に含まれない。
2. **Given** 学習済み pl_topk モデル, **When** 今走の結果(rank 含む)を変更, **Then** 予測不変(結果は損失のみ、特徴に非流入)。

---

### User Story 3 - 予測・校正・serving は cond_logit と同一経路 (Priority: P1)

pl_topk の予測は cond_logit と完全同一(raw_score → レース内 softmax → 校正 → 009)。serving は objective∈{cond_logit, pl_topk} で同じ postprocess。

**Why this priority**: 039 の最大リスク「全経路 postprocess 一致」を維持。経路を増やさない。

**Independent Test**: pl_topk predictor の predict_race が Σ=1・009 整合。serving raw_predict が pl_topk でも race-softmax。

**Acceptance Scenarios**:

1. **Given** pl_topk predictor, **When** predict_race, **Then** softmax→calibrator→009 で Σ=1(cond_logit と同一コード経路)。
2. **Given** lgbm-042 artifacts(objective=pl_topk 記録), **When** serving ロード, **Then** raw_predict が race-softmax・feature_hash=features-012 整合。

---

### User Story 4 - 採用判定(18-fold OOS、校正 A/B) (Priority: P1)

事前登録ゲート: baseline=cond_logit+TE+isotonic(lgbm-041 相当)vs candidate=pl_topk+TE+{isotonic,none 両測}。

**Why this priority**: 憲法 III。039 と同型の A/B 規律。

**Acceptance Scenarios**:

1. **Given** 18-fold walk-forward OOS, **When** 両校正経路を測る, **Then** PRIMARY(win LogLoss 改善 かつ ECE 非悪化)+ fold ガード(strict majority・worst-fold ECE 2e-3・worst-fold dLogLoss 5e-3)で採否、良い校正経路を採る。
2. **Given** 採用, **Then** lgbm-042 active・lgbm-041 retired。SECONDARY 診断: winner-NLL・top1・AUC・top2/top3 LogLoss(Harville 導出の改善が期待される)。
3. **Given** 不採用, **Then** main は lgbm-041 のまま、ブランチ保全。

---

### Edge Cases

- **同着(stage 対象非一意)**: stage1 なら group 中立化(039 同型)、stage2/3 ならその stage 以降 break。
- **少頭数(remaining<2)**: 以降の stage は自明/未定義 → break。
- **着順 2/3 欠損(DNF 等で 3着まで揃わない)**: 揃っている stage までで break。
- **1頭立て**: 全 stage 自明 → 勾配 0 相当(非例外)。
- **stage 重みは w=[1.0, 0.5, 0.25] 固定**(spike の事前選択値、OOS で調整しない)。純 PL(均等 w)は SECONDARY 診断で 1 回だけ併測可(事前登録)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: objective に `pl_topk` を追加する MUST(既定 binary・cond_logit は不変=後方互換 bit 一致)。
- **FR-002**: pl_topk は group ごとに PL 逐次 stage(k≤3、remaining 集合上の数値安定 softmax)で `grad += w_j(p−y_j)`・`hess += w_j·max(p(1−p),eps)` を計算する MUST。STAGE_WEIGHTS=[1.0,0.5,0.25] 固定。
- **FR-003**: stage 中断規則: stage 対象非一意 or remaining<2 → break(先行 stage 保持)。stage1 非一意は group 中立化 MUST。hess 下限 eps 維持 MUST。
- **FR-004**: rank ラベル(確定着順 1..3、他 0)を label 側で供給する MUST(特徴に非流入、model_input_features 外、leak-guard)。
- **FR-005**: 予測・校正・serving は cond_logit と同一経路を再利用する MUST(raw_score→race softmax→校正→009、objective∈{cond_logit,pl_topk} 同一 postprocess)。
- **FR-006**: sample_weight は cond_logit 同様 grad/hess に明示適用する MUST(weight=None は不変)。
- **FR-007**: 採用判定は 18-fold OOS・校正 A/B(isotonic vs none)・PRIMARY win LogLoss+ECE+fold ガードで行う MUST(039 同型・事前登録)。
- **FR-008**: objective を artifacts/metadata/metrics_summary に記録する MUST。FEATURE_VERSION 不変(features-012)・スキーマ変更なし MUST。
- **FR-009**: 採用時 lgbm-042 active・lgbm-041 retired。不採用時ブランチ保全 MUST。

### Key Entities

- **pl_topk objective**: PL 逐次 top-3 の custom objective(group sizes + rank 配列の closure)。
- **rank ラベル**: 学習行の確定着順(1..3/0)。label 専用(win と同じ境界)。
- **model_version lgbm-042**(採用時): features-012 + pl_topk + TE + 採用校正。

## Success Criteria *(mandatory)*

- **SC-001**: 18-fold OOS で pl_topk が cond_logit(lgbm-041 相当)の win LogLoss を改善し ECE 非悪化(PRIMARY 通過)。
- **SC-002**: fold ガード通過(strict majority・worst-fold ECE ≤2e-3・worst-fold dLogLoss ≤5e-3)。
- **SC-003**: SECONDARY: winner-NLL/top1/AUC 改善(spike 再現)+ top2/top3 LogLoss 非悪化。
- **SC-004**: 後方互換(binary/cond_logit 経路 bit 不変、既存テスト透過)。
- **SC-005**: leak-guard 全通過(rank は label のみ・今走結果変更で予測不変)。
- **SC-006**: スキーマ不変・FEATURE_VERSION 不変(features-012)・feature_hash 整合。

## Assumptions

- STAGE_WEIGHTS=[1.0,0.5,0.25] は spike の事前選択値で固定(数値を見てから動かさない)。均等 w の併測は SECONDARY 診断のみ。
- k=3 固定(JRA 複勝圏・009 の top3 と整合)。k の探索は deferred。
- 校正は 039 同様 isotonic を本命に A/B(none)。温度校正 deferred。
- codex 事前警告(校正崩し)は spike の win 指標で非発現だが、18-fold ECE ゲートが最終防衛線。

## Dependencies

- 039 cond_logit infra(cond_logit.py の race_softmax/group_sizes/winner_nll、WinModel objective 分岐、predictor group 引き回し、serving raw_predict、cli --objective)。
- dataset(rank ラベル追加)、eval(predictor-agnostic 不変)。

## Out of Scope (Deferred)

- k>3 / k の探索、stage 重みのチューニング、純 PL 均等重みの本採用判定。
- margin-aware(着差重み)objective。
- top2/top3 の直接学習・専用校正。
