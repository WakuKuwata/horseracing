# Feature Specification: OOF-faithful Calibration Evidence

**Feature Branch**: `074-oof-faithful-calibration`

**Created**: 2026-07-16

**Status**: Draft

**Input**: 073 の codex レビューで判明した「校正器を immutable artifact 化するだけでは校正リークは直らない」の**是正の第一段**。two-gamma(048)/ stage discount(049)校正 sample が、そのレース結果まで見た full-history モデル由来の persisted prediction を latest-run で拾っており OOS でない。074 はこれを **recipe-faithful walk-forward OOF prediction** から作り直し、OOF 上で校正を再検証する。**production 結線はしない**(activation=076、global registry=077 に分離)。

**codex 設計レビュー反映済み**(`docs/plan/codex-074-review.md`): schema-zero は可能だが **persisted-run 再利用は不可**(過去 prediction は full-history=非OOS)。074 は「OOF bundle + 最小 content-addressed manifest + OOF 校正再検証 + calibrated-stage ECE」に限定し、製品 activation とリポジトリ全体の artifact registry 化を後続へ分ける。

---

## 概要 (Why)

校正(two-gamma / stage discount)は確率誤差を Kelly/EV で増幅する前に確率を整える工程だが、その**校正 sample 自体がリークしている**:

1. `probability/model_calibration.py:232` `_latest_run_predictions(session, race_id)` は対象レースの**最新 PredictionRun を base_model_version で絞らず**取得する。
2. さらに致命的に、過去レース用の persisted prediction は「そのレース結果まで含めて学習した full-history モデル」由来 → artifact を凍結しても **OOS にならない**。
3. `logic_version` は γ/λ を小数5桁に丸めており、それだけでは byte 再現できない。

したがって 074 は、**fold ごとに strict-past 再学習した recipe-faithful モデルの OOF prediction** を content-addressed disk artifact(prediction_runs ではない)として生成し、その OOF 上で two-gamma / stage discount を prequential fit・strictly-later OOF fold で評価し、calibrated-stage ECE を出す。これが 073 の FR-007(two-gamma 後 / stage discount 後 ECE)を参照で満たす。

**production は一切変えない**(serving/betting/API は既存のまま)。074 は「正しい校正の証拠(evidence)と immutable な OOF/calibration artifact」を作るだけ。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - OOF-faithful な校正 sample を作る (Priority: P1)

研究者が校正を検証するとき、校正 sample が latest-run でなく、**fold ごとに strict-past 再学習した recipe-faithful モデルの OOF prediction** から作られ、対象レース結果を一切見ていない。

**Why this priority**: これがリーク是正の本体。ここが無いと two-gamma/stage discount の採否も ECE も信頼できない。単独で「正しい校正証拠」という価値を成す。

**Independent Test**: 生成した OOF bundle の全レースで、booster/internal calibrator/TE/HPO の `max(train_date) < race_date` を満たし、対象レース結果を変えても OOF prediction がバイト不変(result artifact hash だけ変化)、他モデルの latest run を足しても OOF bundle digest 不変、を検証できる。

**Acceptance Scenarios**:

1. **Given** 校正 sample が latest-run 由来(非OOS)の状態、**When** OOF bundle を生成する、**Then** 各 OOF prediction は fold ごとに strict-past 再学習したモデルから作られ、全 OOF race で booster/TE/校正の `max(train_date) < race_date`。
2. **Given** 同日レース、**When** OOF fit に使う母集団を決める、**Then** 同日レースは downstream fit から全除外される(race_id 順でなく `race_date < target_date` に統一、codex リスク)。
3. **Given** 対象レースの結果、**When** その結果を変更する、**Then** その race の OOF prediction はバイト不変(result artifact hash だけが変化)。
4. **Given** 別モデル / full-history の latest run が DB に増える、**When** OOF bundle digest を再計算する、**Then** digest は不変(persisted-run に依存しない)。
5. **Given** OOF 生成を 2 回実行、**When** 出力を比較する、**Then** byte 決定論で一致。

---

### User Story 2 - legacy recipe を完全 attestation する (Priority: P1)

研究者が lgbm-063 を OOF 再現するとき、現 `ModelRecipe` に無い解決済みパラメータ一式が明示 attestation として固定されている。

**Why this priority**: 現 `ModelRecipe` は完全な再現 recipe でない(resolved booster params・ordered feature cols・HPO 条件・calib_frac・legacy split の完全記録が不足)。OOF を recipe-faithful に作るにはこの attestation が前提。US1 と並ぶ P1。

**Independent Test**: lgbm-063 の legacy attestation が resolved LightGBM params・objective/postprocess・ordered feature columns+feature_version・TE 列/smoothing・internal calibration method/fraction/split unit・seed/threads・drop list・source/materialized snapshot hash・code SHA を含み、これから再構築した fold モデルが attestation と整合することを検証できる。

**Acceptance Scenarios**:

1. **Given** 現 `ModelRecipe` が不完全な状態、**When** lgbm-063 の legacy attestation を作る、**Then** metadata.json + 073 freeze を起点に、上記の完全な解決済み recipe が content-addressed に固定される。
2. **Given** attestation の任意フィールド欠落/差異、**When** OOF 再構築を試みる、**Then** fail-closed(または新 digest)になる。

---

### User Story 3 - OOF 上で two-gamma / stage discount を再検証する (Priority: P2)

研究者が校正方式の採否を見るとき、two-gamma(048)と display-stage λ(049)が **prior OOF fold だけで prequential fit**・**strictly-later OOF fold で評価**され、calibrated-stage ECE が出る。

**Why this priority**: US1/US2 の OOF bundle の上でのみ正しい再検証ができる。048 の採用根拠(persisted sample の OOS provenance 未証明)を OOF で測り直す。

**Independent Test**: two-gamma / stage λ が OOF prior fold で fit・strictly-later OOF block で ECE 評価され、verdict が ADOPT/REJECT/NO_DECISION のいずれか(点推定でなく CI ベース)を出し、OOF→full-history の分布 transfer check と NO_DECISION/fallback を持つことを検証できる。

**Acceptance Scenarios**:

1. **Given** OOF bundle、**When** two-gamma / stage λ を fit する、**Then** 各 fold で prior OOF のみを使い(prequential)、fit に使った fold を評価 CI に含めない。
2. **Given** 校正後の評価、**When** ECE を測る、**Then** transform の fit sample ではなく **strictly-later OOF block** で測る。calibrated-stage ECE(two-gamma 後 win / stage discount 後 top2/top3)を出す。
3. **Given** 048 の再検証、**When** verdict を出す、**Then** ADOPT/REJECT/NO_DECISION のいずれも許容し、点推定で採否しない。OOF→full-history 分布ミスマッチ時は NO_DECISION/fallback。
4. **Given** 検証成果、**When** artifact を出す、**Then** `evaluation_contract_version=v2` の **append-only** evaluation artifact として出し、073 の過去 verdict/result を上書き・再分類しない。073 FR-007 はこの artifact への参照で fulfilled。

---

### User Story 4 - 最小 content-addressed manifest を作る (Priority: P2)

研究者/オペレータが OOF/校正 artifact を参照するとき、byte 再現に必要な完全情報が content-addressed manifest に固定され、create-only・fail-closed で守られる。

**Why this priority**: OOF provenance はリーク是正そのものなので 074 に含む(codex は最小 manifest を 074 から外すことに反対)。ただし save_model_version の上書き廃止など全体 registry 化は 077 へ。

**Independent Test**: manifest が schema/version・base model version・model/calibrator/preprocessor/**metadata** checksum・完全 resolved recipe hash・feature_version/ordered-column hash/source fingerprint・fold ごと train/valid race set hash+train_through+生成 model digest・OOF race 集合+prediction checksum・確率 stage 順・**full 精度**の two-gamma/λ params+fit race hash+fallback・code SHA/seed/threads・最終出力 checksum を含み、改竄/partial publish/未知 schema/世代不一致/並行生成を拒否することを検証できる。

**Acceptance Scenarios**:

1. **Given** OOF bundle と校正結果、**When** manifest を作る、**Then** 上記の完全情報(特に γ/λ は丸めない full 精度)を含む。
2. **Given** 同一 canonical payload、**When** 2 回 publish する、**Then** 同 digest で冪等成功。
3. **Given** 同一論理 key・異なる内容、**When** publish する、**Then** fail-closed(conflict)。
4. **Given** manifest 改竄 / partial publish / 未知 schema / 世代不一致、**When** load する、**Then** load 前に拒否。
5. **Given** 生成、**When** publish する、**Then** 一時 directory へ完全生成後に atomic publish、wall-clock 時刻と自己 digest は content hash 対象外。

---

### Edge Cases

- OOF→full-history 移植: recipe が同じでも学習量が違うため確率分布 transfer check が必要(不一致は NO_DECISION/fallback)。
- 066 dispersion 用 two-gamma も同じ leaky loader を使う(`training/cli.py:1055`)→ **074 では診断併記のみに確定**(是正結線は 076、research D7)。
- 別の世代非限定 latest loader が `probability/calibration.py:82`(joint calibration)にも存在。
- feature hash は列名中心で同列・値意味論変更を守れない(loader も明記)= OOF bundle の source fingerprint で補完。
- 2008–2026 を再利用する OOF ECE は正しい retrospective evidence だが **confirmatory ではない**(development evidence、073 US4 と整合)。

## Requirements *(mandatory)*

### Functional Requirements

**OOF-faithful sample(US1)**

- **FR-001**: 校正 sample は latest-run persisted prediction ではなく、**fold ごとに strict-past 再学習した recipe-faithful モデルの OOF prediction** から作らなければならない。
- **FR-002**: 全 OOF race で booster / internal calibrator / TE / HPO の `max(train_date) < race_date` を満たさなければならない。
- **FR-003**: 同日レースを downstream fit から全除外し、`race_date < target_date` に統一しなければならない(race_id 順で同日 earlier を使わない)。
- **FR-004**: 対象レース結果の変更で当該 OOF prediction はバイト不変(result artifact hash のみ変化)でなければならない(leak-guard)。
- **FR-005**: OOF bundle digest は persisted-run に依存せず、他モデル/full-history の latest run 追加で不変でなければならない。
- **FR-006**: OOF 生成は byte 決定論(2 回実行一致)でなければならない。

**legacy attestation(US2)**

- **FR-007**: lgbm-063 の legacy recipe を、resolved LightGBM params・objective/postprocess・ordered feature columns+feature_version・TE 列/smoothing・internal calibration method/fraction/split unit・seed/threads・drop list・source/materialized snapshot hash・code SHA を含む完全 attestation として固定しなければならない。
- **FR-008**: attestation フィールド欠落/差異での OOF 再構築は fail-closed(または新 digest)でなければならない。

**OOF 校正再検証(US3)**

- **FR-009**: two-gamma / stage λ は各 fold で prior OOF のみを使い prequential fit し、fit に使った fold を評価 CI に含めてはならない。
- **FR-010**: ECE は transform の fit sample でなく **strictly-later OOF block** で測り、calibrated-stage(two-gamma 後 win / stage discount 後 top2/top3)を出さなければならない。stage discount は top2/top3 の校正であり win ECE ではない。
- **FR-011**: 048 two-gamma の採否を OOF で測り直し、verdict は ADOPT/REJECT/NO_DECISION のいずれも許容(点推定で採否しない)。OOF→full-history 分布ミスマッチ時は NO_DECISION/fallback。
- **FR-012**: 検証成果は `evaluation_contract_version=v2` の append-only evaluation artifact として出し、073 の過去 verdict/result を上書き・再分類してはならない。073 FR-007 はこの artifact への参照で満たす。

**manifest(US4)**

- **FR-013**: OOF/校正 artifact の manifest は byte 再現に必要な完全情報(§US4 の列挙、特に γ/λ は **full 精度**で丸めない)を含まなければならない。
- **FR-014**: manifest は create-only・atomic publish・冪等(同 payload=同 digest)・fail-closed(同 key 異内容=conflict、改竄/partial/未知 schema/世代不一致=load 前拒否)でなければならない。identity fallback も明示 artifact とする。

**parity / スコープ境界(全 US 共通)**

- **FR-015**: **production を結線してはならない**。serving/betting/API の挙動・既存 PredictionRun/Recommendation を変更しない(activation は 076)。
- **FR-016**: 既存 active モデル(lgbm-063)の persisted **win**(`race_predictions.win_prob` / API win)はバイト不変でなければならない(`model_internal_win_parity`)。
- **FR-017**: スキーマ変更ゼロ・migration なし。OOF bundle・manifest・attestation・evaluation artifact は content-addressed disk artifact(prediction_runs に入れない=API/serving/model-selector を汚染しない)で完結する。
- **FR-018**: OOF/校正の派生値(ECE・γ・λ・verdict)をモデルの特徴量に還流してはならない(リーク境界)。

### Key Entities

- **legacy recipe attestation**: lgbm-063 の完全 resolved recipe(FR-007 の全フィールド)。content-addressed。
- **OOF prediction bundle**: fold ごと strict-past 再学習モデルの OOF prediction 集合。content-addressed disk artifact(DB 非保存)。race 集合 hash・prediction checksum・生成 model digest を保持。
- **calibration evaluation artifact**: OOF 上の two-gamma/stage λ fit + strictly-later ECE + verdict(ADOPT/REJECT/NO_DECISION)。append-only・`evaluation_contract_version=v2`。
- **content-addressed manifest**: 上記 3 者を byte 再現可能に束ねる(FR-013 の完全情報)。create-only・fail-closed。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 全 OOF race で `max(train_date) < race_date` を満たす割合 **100%**(strict-past)。
- **SC-002**: 同日レースが downstream fit に混入する件数 **0**。
- **SC-003**: 対象レース結果変更で当該 OOF prediction が変化する件数 **0**(result hash のみ変化)。
- **SC-004**: 他モデル/full-history latest run を DB に追加しても OOF bundle digest 変化 **0**。
- **SC-005**: OOF 生成 2 回の byte 一致 **100%**(決定論)。
- **SC-006**: `model_internal_win_parity` — 074 は serving/persisted 予測を触らないため lgbm-063 の persisted win は**静的に不変**(artifact digest 不変で保証)。任意で 1 レース runtime spot-check(16 頭 mismatch 0)を併記。
- **SC-007**: calibrated-stage ECE が strictly-later OOF block で出力され、fit sample では測っていないこと(手続き検証)。
- **SC-008**: manifest の改竄/partial/未知 schema/世代不一致/並行生成が **100%** 拒否され、同 payload 再 publish は冪等成功。
- **SC-009**: 073 の既存 verdict/result 上書き **0**(074 は参照で FR-007 を満たす)。
- **SC-010**: production 経路(serving/betting/API)の挙動変更 **0**・既存 PredictionRun/Recommendation 変更 **0**。

## Assumptions

- 現 active は **lgbm-063**(features-017、073 で確定・freeze 済み)。074 の parity oracle。
- schema-zero を維持(content-addressed disk artifact + 既存 JSONB 参照で完結、migration なし)。
- 074 は **evidence + immutable artifact のみ**。production activation は 076、global content-addressed registry(save_model_version 上書き廃止・loader checksum enforcement)は 077。
- 2008–2026 を使う OOF ECE は development evidence(confirmatory ではない、073 US4 と整合)。
- OOF bundle は fold ごと再学習を要するため計算コストが高い(pl_topk フル walk-forward は十数時間級の前例)。運用は長時間 job 前提。

## 依存・後続 feature・スコープ外

**後続 feature として予約(本 spec では扱わない)**

- **075 Counterfactual Return API Terminology**(073 から予約): realized→counterfactual_snapshot_{gross,net}_return 等・API/front/admin/OpenAPI 原子 migration。
- **076 Probability Pipeline Activation & Parity**: 推薦が immutable two-gamma artifact を読む・serving が immutable display-stage artifact を読む・allowed-change matrix・new-run/backfill/idempotency・eval↔serving/API parity。**表示 top2/top3 の値が新 run で変わる**のはこの feature。
- **077 Global Content-addressed Model Registry**: `save_model_version` 上書き廃止・atomic publish・loader checksum enforcement・DB pointer/promotion lifecycle。

**スコープ外**: production 結線・既存 run/recommendation 書換・serving/betting/API 変更・save_model_version の lifecycle 変更・066 dispersion calibrator の是正(診断併記にとどめる)・ROI 台帳。

**憲法**: II(リーク境界・OOF は strict-past)/III(評価先行・OOS・事前登録)/IV(確率 Σ 整合・順位保存)/V(監査 artifact・append-only・content-addressed)/VI(契約先行・スキーマ不変)。
