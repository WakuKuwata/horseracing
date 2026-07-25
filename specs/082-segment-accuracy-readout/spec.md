# Feature Specification: セグメント別精度検証計器 (segment accuracy readout)

**Feature Branch**: `082-segment-accuracy-readout`

**Created**: 2026-07-25

**Status**: Draft

**Input**: ユーザー明示要求「提案(候補特徴)を通すための指標ではなく、精度検証としての指標を立てたい」+
codex 設計レビュー全採用([`docs/plan/codex-082-review.md`](../../docs/plan/codex-082-review.md))

## 背景・目的

081 の格言検証で「牝馬×季節に本物だが小さいモデル残差」等が見つかったが、それを通すために
採用指標を作るのは metric-shopping である(ユーザー指摘・方向転換)。本 feature は採用の物差しを
一切いじらず、**active モデルの精度・較正がどのセグメントで良く/悪いかを常時・一様に見える化する
SECONDARY 計器**を立てる。047 segment diagnostics(モデル vs 市場)の**絶対精度版**。

**計器は仮説を生むが、裁定しない。** 採否は既存 073 三値ゲートのまま不変。073 の gate-config・
閾値・decision から本計器の値を参照することを禁止する(FR-013)。

### Estimand の正名(codex P0#1)

本計器が測るのは **「active-recipe historical OOF accuracy」** = 「active と同一 recipe を各年
strict-past で再学習した歴史的 OOF の精度」である。deployed artifact そのものの運用精度ではない。
「現在デプロイ中モデルの常時精度」と呼んではならない。deployed artifact の実運用精度は
**prospective operational readout**(発走前に保存された prediction のみを将来蓄積して測る)として
**別 instrument に分離・deferred** する。過去の `race_predictions` は full-history/backfill と
区別できないため本計器の入力として**禁止**(FR-003)。

### 実証済みの動機(2026-07-25 の実例)

active は本 spec 起草中に lgbm-065 → lgbm-064-f02acc に交代した(serving 事故復旧・別セッション)。
モデル名や事前生成キャッシュに結合した計器は即座に陳腐化する — active の実行時 DB 解決と
fail-closed(FR-002)、および検証済み OOF bundle 正本(FR-003)は理論上の贅沢ではなく実需である。

## User Scenarios & Testing

### User Story 1 - 計器のデータ生成層 (Priority: P1)

運用者/研究者として、1 コマンドで「active recipe の歴史的 OOF 精度のセグメント別読み出し」を
再現可能に生成し、`diagnostic_runs` に append-only で永続化したい。

**Independent Test**: 実 DB で CLI を 1 回実行 → `diagnostic_runs(kind='segment_accuracy')` に
1 行、payload に全 family×mask の指標 + structured provenance が入り、同一入力で再実行すると
指標が決定論的に一致する。

**Acceptance Scenarios**:

1. **Given** active モデルがちょうど 1 件、**When** CLI 実行、**Then** active を DB から実行時解決し
   recipe attestation(ordered feature cols / drop list / params / calibration)を記録する。
   active が 0 件・複数件なら typed error で fail-closed(codex 見落とし制約: ACTIVE 一意制約は
   DB に無い)。
2. **Given** 074 型 content-addressed OOF bundle(prediction checksum・race-set hash・attestation
   digest 検証済み)、**When** 計器実行、**Then** bundle を正本として再利用する。bundle 不在・
   attestation/window/race-set 不一致なら**再生成**する。081 の生 parquet・historical
   `race_predictions` は公式 run の入力として**拒否**する(codex P0#2)。
3. **Given** OOF 予測、**When** race-level mask の指標算出、**Then** PRIMARY 表示は
   **`excess_nll_uniform = mean[-log(p_winner) - log(field_size)]`**(加法・頭数正規化済み)。
   raw `winner_nll`・`uniform_nll`・`market_nll`・`excess_nll_market` は併記(codex P0#3)。
   market 系は market-complete subset に限定し、model/uniform の母集団と分離する(047 の
   odds 欠損再正規化を踏襲しない)。
4. **Given** horse-level mask、**When** 指標算出、**Then** started-all per-horse logloss の
   **`p=1/field_size` 同一行 baseline に対する paired excess** を主表示とし、reliability
   (固定 probability bins)+ calibration-in-the-large(`mean(p) − realized_rate`)+ 固定定義 ECE を
   出す。horse-level mask から race-level winner NLL は算出**不能**とする(winner-conditioned
   selection 回避、codex P0#4)。
5. **Given** race-level mask の較正、**When** ECE 算出、**Then** 二段 grain を明記する:
   「race 属性でレースを選び、**選ばれたレースの started 全馬**で calibration/ECE を計算」
   (`winner_nll.grain=race` / `calibration.grain=started_horse_within_selected_races`)。
6. **Given** 全 CI、**When** 算出、**Then** ECE/NLL/excess の CI は race-day cluster bootstrap
   (seed 固定)。reliability bin の Wilson は補助表示と明記。pointwise CI には
   「多重比較未調整」ラベルを付す(codex P0#5)。
7. **Given** 除外レース(finished label ゼロ・dead heat・partial ingest・all-DNF)、**When** 実行、
   **Then** exclusion ledger(理由別件数)を payload に記録する(黙って落とさない)。

### User Story 2 - 仮説漁りを構造的に防ぐ出力契約 (Priority: P1)

研究者として、計器が「毎回最悪セグメントに飛びつかせる」garden of forking paths を注意書きでなく
**出力契約**で防ぎたい(codex P1#7 全採用)。

**Acceptance Scenarios**:

1. payload は**固定順**(mask library 定義順)。score/gap/ECE によるソート済み出力を持たない。
2. payload に `worst_segment`・rank・色・PASS/FAIL・verdict 相当の field を**持たない**
   (機械検証: 禁止 key テスト)。
3. 安定性表示は重複 run 間ではなく**非重複の年別値**(fold 別)で持つ。
4. 計器で見つけた仮説の検証は `discovery_run_id` を持つ**新規事前登録**を要求する(payload に
   この規約を明記)。
5. 073 gate-config・閾値・decision は本計器の値を参照しない(既存 gate-config 不変で自動成立、
   import/参照 leak-guard テストで固定)。

### User Story 3 - マスクライブラリ v1 の凍結 (Priority: P2)

**マスクライブラリ v1**(codex 推奨表を採用):

| family | grain | v1 マスク |
|---|---|---|
| temporal | race | eval year/fold(必須) |
| race_core | race | surface / distance band / race_class / field_size band |
| course | race | `venue_code × track_type` |
| horse_core | horse | sex / debut・history-depth 帯 |
| data_quality | horse | canonical/nk / past-market coverage 帯(0/1–2/3+) |
| market_context | horse | q band + `q_missing`(closing-market-conditioned と明記) |
| **post_081_exploratory** | horse | sex×season / current・prior rotation 帯 / previous finish 帯 / draw×venue / body-mass×going / weight gain |

**凍結・拡張ポリシー**(codex P1#6 + library 節):

- **`post_081_exploratory` family は core と分離**し `origin=post_081_exploratory` を記録する。
  これらの軸は 081 の結果を見た後に選ばれており、**081 の独立確認には使えない**(明記)。
- 連続値マスクは軸名でなく **missing bucket・境界・端点・availability timing・交差セルまで固定**
  (14/70 日・440kg・+11kg 等は 081 由来である旨を definition に保持)。
- mask ID と definition hash は不変。v2 は v1 の superset(追加のみ)。定義変更は**新 ID**。
- run 間比較は `mask_definition_hash × metric_contract_version × population_hash` 一致行のみ。
- 過去 payload は書き換えない。旧 source を新 library で再計算するのは「新 run」。

### Edge Cases

- p bins(reliability)は mask library でなく **metric contract** に置く(codex)。
- equal-width(021)と equal-mass(073)の ECE は別の量 — 本計器は**固定 probability bins
  (equal-width)** を `metric_contract_version=sa-v1` で凍結。
- probability stage は **model-internal calibrated win prob(two-gamma 前)** に固定(混在禁止)。
- yearly expanding fold では「model age within year」が季節診断に交絡する(年初は前年末モデル、
  年末も同じモデル)— 既知の交絡として payload note に常設明記。
- q は closing 系(発走前 snapshot でない)— market_context family に常設明記。

## Requirements

- **FR-001**: 計器は SECONDARY(採否・閾値調整・active 昇格判断に使用しない)。この規律を
  payload 冒頭の `instrument_contract` に機械可読で埋め込む。
- **FR-002**: active モデルは実行時に DB から解決し、ちょうど 1 件でなければ typed error。
  recipe attestation(ordered cols/drop list/params/calibration/checksum)を payload に記録。
- **FR-003**: OOF 正本は 074 型検証済み bundle(再利用 or 再生成)。081 parquet・historical
  `race_predictions` は公式 run 入力として拒否(typed error)。
- **FR-004**: race-level PRIMARY 表示は `excess_nll_uniform`。horse-level は 1/N-baseline paired
  excess。raw 値・market 参照は併記、market は market-complete subset に限定。
- **FR-005**: ECE の二段 grain(US1-5)・固定 bins・calibration-in-the-large・cluster bootstrap CI・
  Wilson=補助、を `metric_contract_version=sa-v1` で凍結。
- **FR-006**: マスクライブラリ v1(US3 表)を definition hash 込みで凍結。拡張は superset+新 ID。
- **FR-007**: 仮説漁りガード(US2 の 5 項目)を出力契約として実装・テスト固定。
- **FR-008**: 永続化は `diagnostic_runs(kind='segment_accuracy')` への転記のみ(054 規律=
  後段で独自指標を作らない)。`segment_edge` とは別 kind、payload 統合禁止(codex)。
- **FR-009**: structured provenance を payload に保存: base model version・artifact/attestation
  digest・OOF bundle digest・prediction checksum・race/horse set hash・train floor・eval window・
  fold boundaries・probability stage・code SHA・feature/source fingerprint・label snapshot hash・
  mask-assignment hash・metric contract version・mask library version/hash。`logic_version`
  文字列だけに押し込めない。
- **FR-010**: exclusion ledger(除外理由別件数)を payload に記録。
- **FR-011**: スキーマ変更ゼロ(migration 不要=`diagnostic_runs` 汎用)・API/front/admin 不変・
  FEATURE_VERSION/モデル/採用ゲート不変。
- **FR-012**: 計器出力はモデル特徴に還流しない(憲法 II、leak-guard テスト)。マスク割当は
  result-blind 属性のみ(結果変更で割当不変テスト)。
- **FR-013**: 073 gate-config・decision 経路から本計器の値・kind への参照ゼロ(機械固定)。

## Success Criteria

- **SC-001**: 実 DB で CLI 1 回実行 → `segment_accuracy` run が永続化され、同一入力の再実行で
  指標が決定論一致(seed 固定)。
- **SC-002**: uniform 予測を入力した golden test で全セグメントの `excess_nll_uniform=0`
  (可変頭数でも)。horse-level uniform excess も同様に 0。
- **SC-003**: 禁止 field(worst/rank/verdict/色)が payload に存在しない・固定順・年別安定性
  形式、の出力契約テスト緑。
- **SC-004**: bundle 不整合(attestation/window/race-set)で fail-closed、081 parquet・historical
  race_predictions の入力拒否テスト緑。
- **SC-005**: 073 gate-config/decision の不変(diff ゼロ)+ 参照ゼロの leak-guard 緑。
- **SC-006**: 除外 ledger の Σ reconciliation(全レース = 採点 + 理由別除外)。

## スコープ外 (deferred)

- **viewer / 定型 read CLI / 更新運用**(常設計器の「常設」完了条件だが、本 feature は
  **データ生成層まで**と明記 — codex 結論を採用)。054 viewer 拡張は別 feature。
- **prospective operational readout**(deployed artifact の発走前 prediction 蓄積による実運用
  精度・drift 計測)。`computed_at < post_time` + prospective provenance の将来行のみが対象。
- anomaly/alert(付けるなら軸 family 内 simultaneous CI が前提 — codex)。
- opportunity-set **採用ゲート**(v3 contract)— 本計器とは別の議論。計器の実測が
  その事前登録の設計材料になるのは可(値の流用でなく設計判断として)。

## 憲法チェック

- **II(リーク境界)**: 計器出力・マスク・除外 ledger をモデル特徴に還流しない(FR-012)。
  マスクは result-blind 属性のみ。市場 q は market_context family 内で表示専用。
- **III(評価先行・事前登録)**: 採用ゲート不変(FR-013)。計器は SECONDARY。マスク/指標契約は
  実行前凍結、結果を見た後の変更は新 ID/新 run(US3)。081 由来軸の post-selection を
  origin ラベルで明示(独立確認に使えない)。
- **IV(確率整合)**: 確率値に一切触れない(読み出しのみ)。
- **V(再現性・監査)**: structured provenance(FR-009)・append-only・seed 固定・exclusion ledger。
- **VI(契約先行)**: スキーマ/API/migration 不変(FR-011)。kind 追加のみ。

## codex 設計レビュー記録 (2026-07-25・全採用)

`codex exec --sandbox read-only` 直叩き。全文 [`docs/plan/codex-082-review.md`](../../docs/plan/codex-082-review.md)。
結論「方向性は正しいが現案のままでは進めない」→ 6 条件を全て本 spec に反映:

1. 074 型検証済み OOF bundle を正本に(FR-003) — 081 cache 正本案は**却下された**(reuse-cache
   無検証・is_winner の dead-heat 0 化はラベル流用不可)
2. `excess_nll_uniform` を主表示に(FR-004) — raw NLL+参照線併記だけでは誤読を防げない
3. race-level ECE の二段 grain 明記(FR-005/US1-5)
4. 081 軸を post-081 exploratory family にラベル(US3) — 凍結は post-selection を消さない
5. 固定順・rank/worst 不在の構造保証(US2/FR-007)
6. structured provenance(FR-009) — logic_version 文字列に押し込めない

追加採用: estimand 正名(historical OOF accuracy / prospective は別 instrument)・二層分離案・
OOF source 優先順位・mask 比較キー 3 点セット・market population 分離・exclusion ledger・
active fail-closed・probability stage 固定・model-age-within-year 交絡の明記。
**codex 指摘で採用しなかったものは無し。**

## 関連

- [047 segment diagnostics](../047-segment-diagnostics/spec.md)(SECONDARY 規律の前例・市場版)
- [054 diagnostics viewer](../054-diagnostics-viewer/spec.md)(`diagnostic_runs` 汎用テーブル)
- [074 OOF bundle](../074-oof-faithful-calibration/spec.md)(content-addressed OOF 正本)
- [081 folklore probe](../081-folklore-residual-probe/)(exploratory 軸の由来・独立確認不可)
- [073 evaluation contract](../073-eval-contract-correctness/)(不変の採用ゲート)
- [`docs/plan/codex-eval-metric-review-lens{A,B,C}.md`](../../docs/plan/codex-eval-metric-review-lensA.md)(本 feature に至るマルチ codex レビュー)
