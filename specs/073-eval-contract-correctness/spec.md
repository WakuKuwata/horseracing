# Feature Specification: Evaluation Contract v2 & Historical Freeze

**Feature Branch**: `073-eval-contract-correctness`

**Created**: 2026-07-15

**Status**: Draft

**Input**: 提案書 `docs/plan/model-accuracy-roi-redesign-proposal.md` (rev1) §9 基盤 feature "Evaluation and Prospective ROI Contract" のうち、発走前オッズの継続供給がないため ROI 台帳・prospective 実計測は deferred し、**新データ一切なしで今すぐ安全に着手できる「評価契約の正しさ修正」と「探索期間の凍結」だけ**に絞った版(option a)。

**codex 設計レビュー反映済み**: 当初の単一スコープ案(校正器の immutable 化・realized 改名を同梱)は、(1)校正リーク是正が単なる artifact 凍結でなく OOF-faithful な作り直しという大仕事であること、(2)realized 改名が公開 API の破壊的変更であること、から**3 feature に分割**(073 契約修正+凍結 / 074 校正 artifact 是正 / 075 API 命名 migration)。本 spec は 073 のみを扱い、074/075 は「後続 feature」として末尾に予約する。

---

## 概要 (Why)

これまでの feature 開発は、評価という物差しの一部が壊れたまま winner NLL を数 bp 改善しては採用/不採用を判定してきた。破損点:

1. **spec 完了マークと本番経路の不一致**: 068 は日単位 split を完了扱いにしているが、本番学習経路は distinct race 数で分割する関数を呼んでいる。split の意味論が recipe 上で明示されておらず、暗黙既定に依存している。
2. **採用ゲートが operator 依存**: main gate と subgroup guard が別々に返り、最終判定が手作業に依存。`eval_window`・`no_decision_min_days=10` が実判定に十分結線されておらず、空 window・標本不足の critical subgroup が黙って通る経路がある。
3. **bootstrap の誤称**: `moving_block_bootstrap_ci` は実体が block 長 1 日の cluster bootstrap で、開催を跨ぐ系列相関を保存しない。
4. **started-all 未統合**: harness 本体は finished-only のままで、started-all は paired 側に限定されている(学習は started 全馬なのに評価は finished のみ=母集団不一致)。
5. **探索期間の多重比較**: 2008–2026 を数十回参照して特徴/閾値/bundle を選んできた。070 F03–F05 の status が正確に凍結されていない。

この feature は**精度も ROI も上げない**。偽の改善を弾き、採用判定を再現可能・機械的にし、過去の探索を正しく凍結するための評価契約の是正であり、以降のモデル品質改善(校正リーク是正=074、Day-split 再学習、stable model、strict-past TE、market-offset residual、speed v2)の前提となる。

**最重要不変条件**: この feature は評価・採用・監査・凍結の契約のみを是正し、既存 active モデルの重み・確率値・serving 予測をバイト単位で変えてはならない。split を本番学習経路に切り替えて再学習することは本 feature のスコープ外(別 feature へ分離)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 採用判定を単一の三値・決定論・監査つき機械判定にする (Priority: P1)

研究者/オペレータが候補×active を評価するとき、採用可否が operator の手作業を挟まず単一の三値(ADOPT / REJECT / NO_DECISION)として得られ、同一 seed で再現し、判定根拠が監査 artifact に残る。

**Why this priority**: 評価契約の心臓部。ここが直らない限りどのモデル比較も信頼できない。単独で完成すれば「正しい物差し」という MVP を成す。既存 artifact に対して**再学習なしで**テストできる。

**Independent Test**: 既知の候補×active ペア(既存 artifact)を実 DB paired-eval にかけ、(a) 単一 enum が返る、(b) 期間/開催日/subgroup 標本の不足で NO_DECISION になる、(c) 同一 seed・単一 thread で指標差が許容誤差内に再現、(d) 監査 artifact に契約 version と各種 hash が残る、を機械的に検証できる。

**Acceptance Scenarios**:

1. **Given** main gate と subgroup guard が別々に返る現状、**When** 採用判定を実行する、**Then** 結果が単一 enum で返る: `ADOPT`=main PASS かつ全 critical subgroup PASS / `REJECT`=主指標または十分な標本を持つ critical subgroup が FAIL / `NO_DECISION`=評価期間・開催日数・critical subgroup 標本の不足または必須データ欠損。
2. **Given** `eval_window`・`no_decision_min_days=10` が実判定に未結線の状態、**When** 期間や開催日数が不足するデータで判定する、**Then** 空 window や標本不足の subgroup が黙って PASS せず NO_DECISION になる。
3. **Given** confirmatory mode での判定、**When** 未知/欠落 config・評価期間不一致・gate-config hash 不一致が起きる、**Then** 即時に型付きエラーになる(fail-closed)。
4. **Given** harness 本体が finished-only の状態、**When** started-all を評価に使う、**Then** DNF/失格を含む started 全馬(win=0)で評価され、paired 側限定が解消される。
5. **Given** 同一入力・同一 seed、**When** 評価を単一 thread で 2 回実行する、**Then** winner NLL・paired 差・CI の絶対差が事前登録した許容誤差(例 `< 1e-9`)内で一致する。
6. **Given** 採用判定の実行、**When** 監査 artifact を出力する、**Then** `evaluation_contract_version`・canonical gate-config hash・source/result/race-set hash・candidate/base recipe hash・artifact checksum・started-all 集合と除外理由・決定論 rerun 証跡 が残る。
7. **Given** ECE 評価、**When** 校正を測る、**Then** 全体に加え確率帯・odds 帯・p 帯・q 帯・**事前登録した共通 tail mask**(または active/base 由来の result-blind mask)の各サブセットで測られ、arm 固有 tail は diagnostic に降格される。各帯は固定境界・欠損 bucket・最低件数/最低開催日数・NO_DECISION 規則を持つ。

---

### User Story 2 - split を recipe の明示意味論にし、既存 active を凍結する (Priority: P1)

研究者/オペレータがモデルを学習・参照するとき、calibration の split 単位が recipe の明示フィールドになっており、既存 active モデルが legacy split として digest ごと凍結され、split を変えれば recipe_hash と model_version が必ず変わる。

**Why this priority**: 068 の「日単位 split 完了扱いだが本番は distinct-race 分割」という不一致の根治。かつ最重要不変条件(既存 active の serving 予測バイト不変)を実装レベルで保証する土台。US1 と並ぶ P1。

**Independent Test**: split 戦略を変えると recipe_hash が必ず変わること、既存 active が `race_count_v1` として凍結され serving 予測がバイト不変であること、同一 model_version で split を変えた再学習が拒否されることを検証できる。

**Acceptance Scenarios**:

1. **Given** split の意味論が暗黙既定に依存している状態、**When** recipe を確認する、**Then** calibration の split 単位が明示フィールド `calibration_split_unit ∈ {race_count_v1, race_day_v1}` として recipe に含まれる。
2. **Given** 既存 active モデル(feature 開始時に DB で確定した version)、**When** それを参照する、**Then** `race_count_v1` として artifact digest ごと凍結され、この feature の前後で serving 予測がバイト不変(16 頭サンプル mismatch 0)である。
3. **Given** split 戦略の変更、**When** recipe を再計算する、**Then** recipe_hash と model_version が必ず変わる(同一 model_version で split を変えた再学習は破壊的として拒否される)。
4. **Given** この feature のスコープ、**When** 実行内容を確認する、**Then** 再学習・昇格・active artifact 書換を一切行わず、`race_day_v1` での学習・候補評価は別 feature(Day-split Retraining & Promotion)に分離されている。

---

### User Story 3 - bootstrap を実体と一致させ、過去 verdict を凍結する (Priority: P2)

研究者が CI を読むとき、bootstrap の名称が実体(開催日クラスタ)と一致し、block 幅感度が併記され、過去の採用判定が不変履歴として保存される。

**Why this priority**: 誤称の是正と感度の可視化は契約の信頼性に効くが、US1/US2 の後でよい。過去 verdict の凍結は多重比較是正の一部。

**Independent Test**: 改名後も数値が完全一致すること、v2 感度が複数 block 幅で出ること、068/069/070 の verdict が上書きされず contract_version=v1 として残ることを検証できる。

**Acceptance Scenarios**:

1. **Given** `moving_block_bootstrap_ci` が実体は block 長 1 日の cluster bootstrap である状態、**When** CI を計算する、**Then** 関数/レポートが `race_day_cluster_bootstrap_ci_v1` に改名され、数値は完全に維持される。
2. **Given** 改名、**When** 感度分析を実行する、**Then** 2/3/4 開催日・開催週・開催単位 block の v2 感度が併記され、block 重複/端点/休催日/複数場同時開催の定義が事前固定され、全感度を gate の AND 条件にはせず primary estimator を 1 つだけ事前登録する。
3. **Given** 068/069/070 の既存 verdict、**When** 契約を v2 化する、**Then** それらは `evaluation_contract_version=v1` の不変履歴として保持され、v2 再計算は参考再生に限定され、過去 verdict を上書き・再分類しない。

---

### User Story 4 - 探索期間を development set として凍結する (Priority: P3)

研究者が今後の採用判定をするとき、2008–2026 が development evidence と明記され、070 の正確な status が凍結され、将来の prospective holdout の事前登録の器だけが休眠状態で用意されている。

**Why this priority**: 多重比較の是正は重要だが、実 prospective 計測はオッズ供給がない現状では走らせられない。今できるのは「降格の明記」と「休眠の事前登録」だけ。

**Independent Test**: 070 の status matrix(F03/F04/F05 の rejected/unwired)が凍結され、2008–2026 が development evidence と明記され、prospective holdout が DORMANT 状態で事前登録の器だけ存在し実計測が開始されていないことを確認できる。

**Acceptance Scenarios**:

1. **Given** 070 F03–F05 の registry 実態(rejected/unwired)、**When** status を凍結する、**Then** 過去文書を書き換えず、commit/verdict artifact hash を参照する append-only の supersession 記録として正確な status matrix が固定される。
2. **Given** 2008–2026 を多数の feature 選択で参照してきた履歴、**When** 期間の位置づけを確認する、**Then** development evidence と明記されている。
3. **Given** 将来の prospective holdout、**When** 事前登録の器を確認する、**Then** 仮説・特徴式・閾値・primary metric・停止条件・time-to-signal を記録するフォーマットが存在するが状態は `DORMANT`(または `AWAITING_CAPTURE`)であり、時計は capture 稼働・immutable recipe・停止規則・最初の対象レースが揃った後に初めて開始される(この feature では開始しない)。

---

### Edge Cases

- 日単位 split で train/calib いずれかが空になる小窓は既存の identity fallback 規律を踏襲する(この feature では新規学習しないため主に recipe 意味論の定義として扱う)。
- q(市場)が欠損するレースの q 帯 ECE は別サブセットに分離し 0 補完しない。
- confirmatory mode で gate-config hash や評価期間が一致しない場合は fail-closed で即時エラー。
- bootstrap v2 感度が過去判定(068/069)の数値を動かす場合でも、過去 verdict を遡及で覆さない(参考再生のみ)。
- 現 active が 062 か 063 か DB で確定できない場合は着手をブロックする(推測固定しない)。068 文書には DB active=063 の記載があり、062/063 は SHA 同一だが version は実 DB で確定する。

## Requirements *(mandatory)*

### Functional Requirements

**採用判定・監査・決定論(US1)**

- **FR-001**: 採用判定は単一の enum `ADOPT / REJECT / NO_DECISION` を返さなければならない。`ADOPT`=main gate PASS かつ全 critical subgroup PASS。`REJECT`=主指標または十分な標本を持つ critical subgroup が FAIL。`NO_DECISION`=評価期間・開催日数・critical subgroup 標本の不足、または必須データ欠損。
- **FR-002**: `eval_window`・最低開催日数・`no_decision_min_days=10` を実判定に結線し、空 window や標本不足の subgroup が黙って PASS してはならない。confirmatory mode では未知/欠落 config・評価期間不一致・gate-config hash 不一致を即時に型付きエラーにしなければならない(fail-closed)。
- **FR-003**: started-all を harness 本体に統合し、finished-only ではなく DNF/失格を含む started 全馬(win=0)で評価しなければならない(paired 側限定を解消)。
- **FR-004**: 評価は同一 seed・単一 thread で指標差が事前登録した許容誤差(例 `< 1e-9`)内に再現しなければならない(決定論)。
- **FR-005**: 採用判定の監査 artifact は `evaluation_contract_version`・canonical gate-config hash・source/result/race-set hash・candidate/base recipe hash・artifact checksum・started-all 集合と除外理由・決定論 rerun 証跡 を保持しなければならない。
- **FR-006**: ECE は全体に加え確率帯・odds 帯・p 帯・q 帯・**事前登録した共通 tail mask**(または active/base 由来の result-blind mask)で測定しなければならない。arm 固有 tail は diagnostic に降格する。各帯は固定境界・欠損 bucket・最低件数/最低開催日数・NO_DECISION 規則を持たなければならない。
- **FR-007**: 本 feature の ECE 評価対象は **raw booster score** と **model 内部 calibration + race normalization 後の win probability** までに限定する。two-gamma 後の win probability と stage discount 後の top2/top3 probability の ECE は、校正 sample を OOF-faithful に作り直す後続 feature 074 の完成を前提とする(依存を明記)。
- **FR-008**: 068 の未完了項目(started-all 統合・実 DB paired E2E・決定論確認・必須テスト突合)を完了しなければならない。

**split の recipe 化・legacy 凍結(US2)**

- **FR-009**: calibration の split 単位を recipe の明示フィールド `calibration_split_unit ∈ {race_count_v1, race_day_v1}` にしなければならない。068 が完了扱いにした日単位 split と本番経路(distinct-race 分割呼び出し)の不一致を、暗黙既定でなく recipe 明示で解消する。
- **FR-010**: 既存 active モデル(feature 開始時に DB で確定した version)を `race_count_v1` として artifact digest ごと凍結しなければならない。split 戦略が変われば recipe_hash と model_version は必ず変わらなければならず、同一 model_version で split を変えた再学習は破壊的として拒否しなければならない。
- **FR-011**: 本 feature では再学習・昇格・active artifact 書換を行ってはならない。`race_day_v1` での学習と候補評価は別 feature(Day-split Retraining & Promotion)に分離しなければならない。
- **FR-012**: 既存 active モデルの serving 予測は本 feature 前後でバイト不変(16 頭サンプル mismatch 0)でなければならない。

**bootstrap 是正・verdict 凍結(US3)**

- **FR-013**: 開催日クラスタ再標本化 CI を実体と一致する名称 `race_day_cluster_bootstrap_ci_v1` に改名し、数値を完全に維持しなければならない。
- **FR-014**: v2 感度として 2/3/4 開催日・開催週・開催単位 block を追加し、block 重複/端点/休催日/複数場同時開催の定義を事前固定しなければならない。全感度を gate の AND 条件にしてはならず、primary estimator を 1 つだけ事前登録し残りは diagnostic とする。
- **FR-015**: 068/069/070 の既存 verdict を `evaluation_contract_version=v1` の不変履歴として保持しなければならない。v2 再計算は参考再生に限定し、過去 verdict を上書き・再分類してはならない。

**探索期間の凍結(US4)**

- **FR-016**: 070 の正確な status matrix(F03/F04/F05 の rejected/unwired 状態、registry 実態)を、過去文書を書き換えず commit/verdict artifact hash を参照する append-only の supersession 記録として凍結しなければならない。
- **FR-017**: 2008–2026 を development evidence として文書に明記しなければならない。
- **FR-018**: prospective holdout の事前登録フォーマット(仮説・特徴式・閾値・primary metric・停止条件・time-to-signal)を用意しなければならない。状態は `DORMANT`(または `AWAITING_CAPTURE`)とし、時計はオッズ capture 稼働・immutable recipe・停止規則・最初の対象レースが揃った後に初めて開始する。この feature では実計測を開始してはならない。

**parity / 不変条件(全 US 共通)**

- **FR-019**: 評価・採用・監査・凍結で扱う派生値(NLL・ECE・CI・gate 判定)は、モデルの特徴量に還流してはならない(リーク境界)。
- **FR-020**: この feature はスキーマ変更ゼロ・migration なしを維持しなければならない。監査 artifact・split 明示・legacy 凍結・supersession 記録は disk artifact + manifest + 既存 JSONB(metrics_summary 等)で完結する。
- **FR-021**: この feature は評価・採用・監査・凍結の契約のみを是正し、モデルの重み・確率値を変更してはならない。

### Key Entities

- **採用判定 artifact**: ある候補×active ペアの評価結果。単一 enum 判定(ADOPT/REJECT/NO_DECISION)・main gate 判定・critical subgroup 判定・各種指標(winner NLL 差・帯別 ECE・CI と block 幅感度)・evaluation_contract_version・gate-config hash・recipe hash・race set hash・source/result hash・started-all 集合・決定論証跡 を保持する append-only レコード。
- **ModelRecipe(拡張)**: calibration の split 単位を明示する `calibration_split_unit` フィールドを持つ。split 戦略が recipe_hash に反映される。
- **legacy 凍結レコード**: 既存 active モデルを `race_count_v1` として artifact digest ごと固定した記録。
- **070 supersession 記録**: F03/F04/F05 の rejected/unwired status を commit/verdict hash 参照で固定した append-only 記録。
- **prospective holdout 事前登録レコード(DORMANT)**: 将来の prospective 判定のための仮説・式・閾値・primary metric・停止条件・time-to-signal。実計測は未開始。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 任意の候補×active ペアで、採用可否が operator の手作業判断を **0 回**挟んで単一 enum(ADOPT/REJECT/NO_DECISION)として得られる。
- **SC-002**: 期間・開催日数・subgroup 標本が不足する入力で、黙って PASS する経路が **0**(すべて NO_DECISION になる)。
- **SC-003**: 同一 seed・単一 thread で評価を 2 回実行したとき、全 primary 指標の差が事前登録した許容誤差内に **100%** 収まる。
- **SC-004**: 監査 artifact が必須 8 項目(contract version・gate-config hash・source/result/race-set hash・recipe hash・checksum・started-all 集合・決定論証跡)を **100%** 含む。
- **SC-005**: 既存 active モデルの serving 予測が本 feature の前後で **16 頭サンプルの mismatch 0**(バイト不変)である。
- **SC-006**: split 戦略を変えたとき recipe_hash が **100%** 変化し、同一 model_version での split 変更再学習が **100%** 拒否される。
- **SC-007**: bootstrap レポートが実体(cluster bootstrap)と一致する名称を持ち、**2 種類以上**の block 幅感度が併記され、改名前後の数値が完全一致する。
- **SC-008**: ECE が全体を含め **5 種類以上**のサブセット(確率帯/odds帯/p帯/q帯/共通tail)で出力される。
- **SC-009**: 070 F03–F05 が正確な status matrix として凍結され、本番昇格経路から参照される件数が **0** である。
- **SC-010**: 068/069/070 の既存 verdict が上書き・再分類された件数が **0**(v2 再計算は参考再生のみ)。

## Assumptions

- 発走前オッズの継続供給はこの feature の時点で存在しない。ROI 台帳・multi-arm shadow・実 prospective 収集はスコープ外(オッズ供給が立ってから、かつ憲法 V 改定後に別 feature)。
- この feature はスキーマ変更ゼロを維持する。監査・凍結は disk artifact + manifest + 既存 JSONB で完結し、DB migration を伴わない。
- 現 active モデルは feature 開始時に**実 DB で確定**する(068 文書には DB active=063 の記載があり、062/063 は model/calibrator/preprocessor の SHA-256 が同一。version は推測固定しない)。その serving 予測のバイト不変が最上位の受け入れ条件。
- split は本 feature では recipe の明示意味論化と legacy 凍結にとどめ、`race_day_v1` での本番学習経路切替・再学習は別 feature に分離する。
- 068 が定義した「bit-parity 非要求(校正分割で TE encoder 母集団が変わるのは実験の意図)」は将来のモデル比較実験の文脈であり、本 feature の FR-012(既存 active の serving 不変)とは対象が異なる。
- prospective holdout の事前登録は文書/レジストリ上の器(DORMANT)であり、実データ収集を伴わない。

## 依存・後続 feature・スコープ外

**後続 feature として予約(本 spec では扱わない)**

- **074 Immutable Probability Pipeline Artifact**: base model artifact の create-only 化、**OOF-faithful な two-gamma / stage discount 校正**(現状の latest-PredictionRun を世代非限定で取得する校正リークの是正=単なる artifact 凍結では直らない)、content-addressed manifest、evaluation↔serving の最終確率 parity。**本 feature の FR-007 後半(two-gamma 後 / stage discount 後 ECE)はこの feature に依存**。
- **075 Counterfactual Return API Terminology**: 既存 shadow/backtest の `realized_return`/`realized_roi`(API schema 露出)を `counterfactual_snapshot_gross_return`/`net_return`/`recovery_rate`/`valuation_basis`/`n_scored` に意味分離して改名。favorite 側が `race_horses.odds`(decision-time snapshot 保証なし)を参照する点を `current_odds` provenance として明示。API/front/admin/OpenAPI snapshot/生成 TS/fixture を**原子的に** migration(公開契約の破壊的変更)。
- **Day-split Retraining & Promotion**: `race_day_v1` で新 model_version を学習し、精度変化と契約変化を分離して評価、採用後に新 baseline 化。
- **(将来)ROI 台帳**: market_snapshot / decision_attempt / decision_bet / settlement。**憲法 V(オッズはスナップショット履歴を保存せず最新値で上書き)の改定が前提**。発走前オッズの継続 capture パイプラインが律速。

**スコープ外(deferred)**: serving-compatible / stable / source-masked model、校正 A/B/C/D の勝者選定実験、strict-past ordered TE、pl_topk 専用 HPO、時間適応比較、新特徴(F06/F07/F09/F10)、payout drift policy、race-set / pace mixture、Kelly 再開・自動購入・exotic 拡張。

**憲法**: II(評価派生値をモデル特徴に還流しない・リーク境界)/ III(事前登録ゲート・OOS 後の閾値変更禁止・評価先行)/ IV(確率整合を評価が壊さない)/ V(監査 artifact・冪等・append-only・再現性)/ VI(契約先行)。
