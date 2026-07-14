# Feature Specification: 過去市場 rank/residual/conditioned bundle(F03/F04/F05)

**Feature Branch**: `070-past-market-bundles`

**Created**: 2026-07-14

**Status**: Draft

**Input**: [モデル特徴量 再制定書](../../docs/plan/model-feature-redesign.md) F03 pm_rank_robust / F04 pm_expectation_residual / F05 pm_conditioned(Phase 3 続き)

## 背景・目的

069 で過去オッズ"量"F02 pm_core_strength(`s=log(q×N)`)を実装・採用済み(**lgbm-064-f02acc = features-018 accuracy-first candidate**、winner NLL −0.0057・2026/nk: subgroup 全 PASS)。068 評価契約 + 069 subgroup ゲート(2026/nk: 三値 intersection-union)という物差しが揃った今、Phase 3 の残る過去市場 bundle を段階評価する:

- **F03 pm_rank_robust**: 人気順位の percentile `u=1-(rank-1)/(N-1)`(odds provenance に鈍感)。058 の生 rank(`asof_mkt_rank_*`)を **accuracy-first candidate の recipe で置換**(058 rank と F03 を同時採用しない=帰属分離、再制定書)。
- **F04 pm_expectation_residual**: 過去の市場期待を超えた実績。`finish_residual = v - u`(着順強度 − 人気強度)、`win_residual = I(win) - q`(勝ち − 市場確率)。additive。
- **F05 pm_conditioned**: 条件別(surface/distance/venue)の過去市場評価。F02/F04 採用後・**列別依存**。

全 bundle は **accuracy-first candidate 限定**(default 意思決定支援モデルは market-history を全 drop で p⊥q 維持、058/069 前例)。FEATURE_VERSION features-018→**019 純加算**(F03 も列は追加=058 rank の物理削除はしない、置換は recipe drop で表現 → 018 hash pin で lgbm-064-f02acc/lgbm-063 serving 不変)。採否は 069 の subgroup 付き paired-eval で 1 bundle ずつ。

**スコープ外**: default モデルへの market-history 組込(p⊥q・provenance 前提)、odds provenance 列追加、058 rank の物理削除、二重条件(surface×distance)の F05、実際の production candidate 昇格(verdict 後の別ステップ)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - F03 pm_rank_robust(Priority: P1)

モデル研究者として、過去人気を **rank percentile `u`**(頭数正規化・odds 量の source 差に鈍感)として持ち、058 の生 rank より頑健な市場評価にしたい。058 rank と同時採用せず、accuracy-first candidate で置換評価する。

**Why this priority**: F04/F05 の residual は `u` を土台にする(`finish_residual = v - u`)ため、rank percentile を先に確定する。odds provenance が混在する 2025–2026 で量(F02)より rank の方が安定しうる robust fallback。

**Independent Test**: features-019 を build し、accuracy-first candidate(lgbm-064-f02acc 系 recipe から 058 rank を drop し F03 を加えたもの)を、baseline(058 rank・F03 なし)に対し 069 subgroup 付き paired-eval して winner NLL・2026/nk: subgroup・CI を出す。

**Acceptance Scenarios**:

1. **Given** 過去レース k で馬 i の人気 rank と started 頭数 `N_started`、**When** rank percentile 算出、**Then** `u_ik=1-(rank_ik-1)/(N_started-1)`(u=1 最上位人気、u=0 最下位)。取消等で raw popularity が started 頭数を超える場合は valid popularity を持つ started 馬内で再 rank する(F03 は popularity-only complete-field=odds provenance に鈍感な robust fallback が目的、odds 完備は要求しない)。
2. **Given** 各馬の過去 u、**When** as-of 集約、**Then** strictly-before + 同日除外(058 idiom)で recent-N 集約: `asof_pm_rankpct_last` / `asof_pm_rankpct_mean5` / `asof_pm_favorite_rate5`(直近5走の1番人気率)/ `asof_pm_top3fav_rate5`(直近5走の3番人気内率)(+ 信頼度列 `asof_pm_rank_obs_count`=**計5列で凍結**、gate-config `f03_formula.columns`)。
3. **Given** F03 列、**When** registry 登録、**Then** 独立 group `pm_rank_robust`、FEATURE_VERSION 018→019(純加算・058 rank 列は削除しない)。
4. **Given** F03 の accuracy-first candidate、**When** 置換評価、**Then** candidate は 058 rank(`past_market` 群)を drop し F03 を含む、baseline は 058 rank を含み F03 なし(同時採用しない=帰属分離)。

---

### User Story 2 - F04 pm_expectation_residual(Priority: P2)

モデル研究者として、過去に市場期待を超えた実績(人気より上に来た・勝率が市場確率を上回った)を residual として持ちたい。

**Why this priority**: 市場評価そのもの(F02/F03)とは別軸の新情報(市場の"読み違い")。F03(u)を土台にするため P2。

**Independent Test**: F04 additive candidate を baseline(F04 なし)に対し 069 subgroup 付き paired-eval。

**Acceptance Scenarios**:

1. **Given** 過去 finished レース、**When** 着順強度算出、**Then** `finish_strength v_ik=1-(finish_order_ik-1)/(N_k-1)`、`finish_residual e_ik=v_ik-u_ik`。
2. **Given** 過去 started レース、**When** 勝ち残差算出、**Then** `win_residual w_ik=I(win_ik)-q_ik`(q は F02 の市場share)。
3. **Given** 母集団分離、**When** 集約、**Then** DNF/失格は **finish residual から除外**(finished のみ)、win residual は started 非勝利=0 として含む(2母集団を混ぜない)。列: `asof_pm_finish_resid_mean5` / `asof_pm_finish_resid_career` / `asof_pm_win_resid_mean10` / `asof_pm_win_resid_career` / `asof_pm_resid_sd5` / `asof_pm_result_obs_count`。
4. **Given** F04 列、**When** registry 登録、**Then** 独立 group `pm_expectation_residual`・additive。

---

### User Story 3 - F05 pm_conditioned(Priority: P3)

モデル研究者として、条件別(surface/distance帯/venue)の過去市場評価を持ちたい。ただし列別依存(support は F02、residual は F04)。

**Why this priority**: 疎性・重複リスクが高く、F02/F04 の採否結果を見てから。

**Independent Test**: F05 additive candidate を baseline に対し paired-eval。support 列は F02 採用が前提、finish_resid 列は F04 採用が前提。

**Acceptance Scenarios**:

1. **Given** 過去レース、**When** 条件別集約、**Then** `asof_pm_support_surface` / `asof_pm_support_distband` / `asof_pm_support_venue`(F02 の s を条件別 all-prior 集約)、`asof_pm_finish_resid_surface`(F04 の finish residual を surface 別)。
2. **Given** 条件別の疎性、**When** 縮約、**Then** all-prior + `λ=5` の階層縮約 `mu_shrunk=(n_cell·mu_cell + λ·mu_parent)/(n_cell+λ)`。二重条件(surface×distance)は初版で作らない。
3. **Given** 列別依存、**When** 評価、**Then** F02 未採用なら support 列を評価しない、F04 未採用なら finish_resid 列を評価しない(F03 不採用だけで F05 全体を禁止しない)。

### Edge Cases

- rank tie(同人気)→ 生 popularity に同順があれば valid odds の started 馬内で決定的に再 rank(行順非依存)。
- N=1 レース → u=1(自明)、finish_strength v=1。
- DNF/取消 → finish residual 母集団外(finished のみ)。win residual は started で I(win)=0。取消は started でない。
- q の欠損(F02 complete-field 不成立レース)→ そのレースの win_residual を作らない(F02 と同じ complete-field 規律)。
- F05 の親 fallback: cell 件数不足時は surface→overall のように parent へ縮約(二重条件なし)。**親も空(デビュー馬=過去走ゼロで overall も観測なし)→ NaN**(0 代入しない=Unknown 規約・憲法 IV、T025 で assert)。

## Requirements *(mandatory)*

### Functional Requirements

**F03 pm_rank_robust(US1)**

- **FR-001**: `u_ik=1-(rank_ik-1)/(N_started-1)`(`N_started`=当該過去レースの started 頭数=以降 `N_k≡N_started`)を strictly-before + 同日除外で recent-N 集約(`asof_pm_rankpct_last`/`mean5`/`favorite_rate5`/`top3fav_rate5`+ **F02 obs count と別の `asof_pm_rank_obs_count`**=rank coverage は odds coverage と異なる、codex #6)。**OOS 前固定の未定義詳細**: complete-field=started 全馬に valid popularity があるレースのみ / 取消の rank gap は started 内の popularity 順で詰める / tie は **competition rank(同順位、horse_id・行順で順位を捏造しない)** / `favorite_rate5`=直近5 valid rank obs の `rank==1` 率・`top3fav_rate5`=`rank<=3` 率(**competition rank 下では rank≤3 の全馬を数える=tie で3頭超もあり得る・決定論**、gate-config) / N=1 は u=1(観測に数える) / rate は直近5 valid rank observations・**少数標本(obs<min_obs=3)は NaN(`asof_pm_rankpct_last` 単値列も含む全 F03 列)**=生率/1-obs 高分散値でなく木に観測不足を渡す(gate-config `f03_formula.min_obs`)。
- **FR-002**: F03 は独立 group `pm_rank_robust`。058 rank(`past_market`)と **同時採用しない**(candidate は 058 rank drop + F03、baseline は 058 rank・F03 なし=帰属分離、III)。058 rank 列は物理削除しない(serving compat)。

**F04 pm_expectation_residual(US2)**

- **FR-003**: `finish_residual=v-u`(finished のみ)、`win_residual=I(win)-q`(started 非勝利=0)。**2母集団(finished/started)を混ぜない**。列は data-model §2。q 欠損レースは win_residual を作らない(F02 complete-field)。**2母集団の NaN ゲート(OOS 前固定・analyze U1)**: `finish_resid_*` は **finished 観測数 < min_obs=3 で NaN**(started 数でなく=多数出走・少数完走の 1-obs 高分散残差を出さない)、`win_resid_*` と surfaced `asof_pm_result_obs_count` は started 数。**surfaced count は started の1本のみ**(finished ゲートは内部・6列契約維持)。
- **FR-004**: F04 は独立 group `pm_expectation_residual`・additive。過去結果由来(finish/win)は strictly-before の確定済み過去結果のみ(対象レース結果非参照)。**u/q primitive の共有は「計算の共有」であって「群の採用」とは独立**(F03 群が REJECT でも F04 は u primitive を import して列を build できる=adoption ≠ import、codex U1)。

**F05 pm_conditioned(US3)**

- **FR-005**: 条件別(surface/distband=既存 `≤1400/≤1800/≤2200/>2200` 再利用/venue)の all-prior + λ=5 階層縮約 `mu_shrunk=(n_cell·mu_cell+λ·mu_parent)/(n_cell+λ)`。**as-of は「最新 cell の累積 sum/count」と「target 直前の overall parent sum/count」を別々に取得してから縮約**(縮約済み値を持ち越さない=親が陳腐化しない、codex 論点1)。support は F02 の s、finish_resid は F04 の residual を条件別に。**registry は 2 群に分割**=`pm_conditioned_support`(F02 依存)/ `pm_conditioned_residual`(F04 依存)を別 drop するため(codex B3)。**parent-fallback と実 cell を区別する軸別 valid count**(`asof_pm_support_cond_count_{surface,distband,venue}` / `asof_pm_finish_resid_surface_count`=単一 count では 3 軸 × support/finish 母集団を表現不能、codex B5)。二重条件(surface×dist)は作らない。
- **FR-006**: F05 は列別依存 — support 列は F02 採用が前提、finish_resid 列は F04 採用が前提。**依存不成立時は当該列を `NOT_RUN`**(F03 不採用のみで F05 全体を禁止しない、codex #5)。
- **FR-006a(段階評価・per-arm keep/drop matrix、codex 最重要)**: 1 spec で全列を features-019 に載せるため、**各 bundle の paired-eval では対象外群を両 arm から drop する exact keep/drop matrix を事前登録**する(さもないと F03 評価に F04/F05 が混入)。**gate-config の matrix は記録用で現行 CLI は非消費 → operator が各段の完全 candidate/active recipe を実行**(codex 論点3)。**最終 verdict = `gate.adopted AND subgroup_guard`**(paired_eval は両者を別フィールドで返す→driver が read-time AND、069 同型、codex B2)。順序(**paired-eval 通し番号 段1–5・間に bookkeeping**): **段1** F03 置換評価(F04/F05 を両 arm から drop)→ [bookkeeping: F03 verdict を次 baseline に固定=勝者のみ残す・番号外] → **段2** F04 追加評価(F03 不採用でも実行)→ **段3** F05 support 評価 → **段4**(**F04 ADOPT 時のみ**)F05 residual 評価 → **段5** 最終 stack を lgbm-064 系 baseline に **stack-safety-check**(同一 2019–2026 OOS 上の段階選択後なので独立 confirmatory ではない=真の確認は未使用 time holdout 要・deferred、codex 論点3)。

**共通(全 bundle)**

- **FR-007**: FEATURE_VERSION features-018→**019 純加算**(F03/F04/F05 は列追加のみ、058 rank 削除しない=schema は純加算・「F03 が 058 を置換」は candidate recipe の drop で表現する物理加算・論理置換、codex #4)。**compat map は推移しない → features-019 から features-018(lgbm-064-f02acc)と features-017(lgbm-063)を両方 直接 pin**(codex #4/論点4・非推移 registry.py:415)。**pin は短縮でなく metadata から実測した完全 hash**(codex 論点4・実装時に埋める)。既存 059/061/069 履歴 entry は不変。**byte-parity の検証対象は共有(features-018 由来)model-input 列 = 137**(features-019 全体は 137+19=156・materialized 112 ではない)。両旧モデルの予測 byte 一致で確認。
- **FR-007a**: **同一 version で列 subset を 1 列でも drop した artifact は全て serve 不能**(loader は同一 version の global hash 完全一致のみ exact とし、recipe-drop された same-version subset を意図的に拒否=fail-closed 契約、codex #5/論点6・model_loader.py:194)。→ **F03 置換 candidate だけでなく、未採用 F04/F05 を drop した最終 accuracy-first candidate も `NOT_SERVABLE_PENDING_PROFILE`**。**070 は production 昇格をスコープ外**とする。将来昇格するなら `(feature_version, profile/recipe id, ordered feature hash)` の明示 allowlist が必要。paired-eval は各 arm を再fit するので評価には serving 不要。
- **FR-008**: 全 bundle は accuracy-first candidate 限定。**default モデルの正確な表現は「対象レース市場非入力」**(codex #6): 現 active lgbm-063 は既に 058 rank 4列を含む=統計的 p⊥q ではない(提案書 §47 も過去市場を許容し「独立」を対象レース自身の市場に限定と明記)。070 の default 非組込は「新 market-history 群(F03/F04/F05)を default candidate の accuracy-first 系にのみ入れ、対象レース自身の市場は依然入れない」を意味する。**strict な market-history-free default が要るなら全市場群 drop の新 default artifact を別途作る**(070 スコープ外・repo 全体の framing 是正として proposal doc も更新)。
- **FR-009**: 採否は 069 subgroup 付き paired-eval で 1 bundle ずつ(068 gate + 2026/nk: subgroup 三値 intersection-union)。OOS 後に列選別しない(1 bundle=事前登録)。
- **FR-010**: 対象レース自身のオッズ/人気/rank/結果は特徴に入らない(strictly-before + 同日除外・部分 field 再正規化禁止・列名 odds/popularity トークン回避 + behavioral leak-guard・default 非組込)。
- **FR-011**: DBスキーマ・migration・API・OpenAPI 不変。式パラメータ(recent-N・λ・rank tie・complete-field)を gate-config に OOS 前固定。

### Key Entities *(include if feature involves data)*

- **RankRobust(F03, group pm_rank_robust, 5列)**: `asof_pm_rankpct_last`/`mean5`/`favorite_rate5`/`top3fav_rate5`・`asof_pm_rank_obs_count`(F02 obs と別)。grain=horse-history。source=race_horses.popularity(過去 started・popularity-only complete-field)。058 rank の置換候補。
- **ExpectationResidual(F04, group pm_expectation_residual)**: `asof_pm_finish_resid_mean5`/`career`・`asof_pm_win_resid_mean10`/`career`・`asof_pm_resid_sd5`・`asof_pm_result_obs_count`。source=race_results.finish_order(finished, v 分母=**N_started** で u と尺度統一)+ F02 q(started)。2母集団分離。
- **Conditioned(F05, 2 群 pm_conditioned_support / pm_conditioned_residual)**: support 群 `asof_pm_support_surface`/`distband`/`venue` + 軸別 count、residual 群 `asof_pm_finish_resid_surface` + count。all-prior λ=5 階層縮約。列別依存(support←F02 / residual←F04)を別 drop するため 2 群(codex B3)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: features-019 が build でき、**共有(features-018 由来)model-input 列 = 137**(features-019 全体 156=137+新19・**materialized: features-018 実測 112 → features-019 は +新19 で 131**[新列は全て as-of materialize 対象=exact]・**byte-parity 検証は共有 137 列で行い materialized count ではない**、codex #4)が byte-parity、features-019 が features-018(lgbm-064-f02acc)と features-017(lgbm-063)を両方 直接 pin(**完全 hash**)、lgbm-064-f02acc / lgbm-063 が compat-load で予測 byte 一致(serving 不変)。
- **SC-002**: F03 の rank percentile leak-guard 緑(今走・同日・未来の人気変更で不変、過去変更で変化)、058 rank との同時採用なし(candidate drop 検証)。
- **SC-003**: F04 の finish/win residual が 2母集団分離(DNF は finish residual 外・win residual は started 0)、q 欠損レースで win_residual 非生成。leak-guard 緑。
- **SC-004**: F05 の列別依存(F02 未採用で support 非評価・F04 未採用で resid 非評価)、階層縮約の parent fallback。
- **SC-005**: 各 bundle を 069 subgroup 付き paired-eval で採否判定(winner NLL + 2026/nk: 三値 intersection-union)、採否 verdict を artifact 記録。
- **SC-006**: DBスキーマ・API・OpenAPI・migration 不変。**新 market-history 群(F03/F04/F05)は default candidate に入らない**(accuracy-first candidate 限定=T033 の --drop-groups で登録・**default lgbm-063 の byte-parity 検証は T030[compat-load 16頭 mismatch 0]+ T029[共有137列 parity]**)。※現 active lgbm-063 は既に 058 `past_market` を含むため「default が全 market-history を排除=p⊥q」とは主張しない(FR-008/codex #6「対象レース市場非入力」)。strict な market-history-free default は別 artifact=スコープ外。

## Assumptions

- 069 の F02(features-018・lgbm-064-f02acc candidate)・subgroup ゲート・coverage-audit が main 済み。070 はこの上に構築。
- 現 active は lgbm-063(features-017、default p⊥q)。F03/F04/F05 の candidate は accuracy-first(lgbm-064-f02acc 系)で評価、default 非組込。
- odds provenance 列は無いまま(F03 rank percentile は raw rank より provenance に鈍感=robust fallback の位置づけ)。
- F04 の `win_residual=I(win)-q` は **過去レースの q**(strictly-before)なのでリークでない。対象レースの q は入らない。
- codex second-opinion を取得(親から `codex exec` 直叩き)。plan フェーズで再確認。

## 憲法チェック

- **II(リーク境界)**: 対象レース市場/結果 非入力・strictly-before + 同日除外・部分 field 再正規化禁止・列名トークン回避 + behavioral leak-guard・default 非組込(p⊥q)・過去 q/結果のみ。
- **III(事前登録ゲート)**: 1 bundle ずつ・OOS 後列選別しない・058 rank と F03 同時変更しない(帰属)・式/λ/tie を OOS 前固定・069 subgroup ゲート。
- **IV(確率整合)**: win 特徴で 009 の Σ=1・順位保存に影響しない。
- **V(監査)**: coverage・subgroup CI・bundle 事前登録・feature_hash pin。
- **VI(契約)**: スキーマ・API・migration 不変。FEATURE_VERSION 019 bump は additive + compat pin で serving 不変。

## codex 設計レビュー(spec フェーズ・全採用)

親から `codex exec` 直叩きで取得。主要指摘を反映済み:

- **#1 F03 帰属**: 058 rank と F03 を同時採用しない=candidate は 058 rank(past_market)を drop + F03、baseline は 058 rank・F03 なし(FR-002)。
- **#2 F04 リーク判断 OK**: `win_residual=I(win)-q` は過去レースの q(strictly-before)なのでリークでない。finish/win の2母集団分離(FR-003)。
- **#3 F05 階層縮約**: parent-fallback と実 cell を区別する条件別 valid count 追加・distband は既存 bins 再利用(FR-005)。
- **#4 features-019 = 物理加算・論理置換**: schema 純加算(058 残す)・F03 置換は recipe drop・**compat は推移せず 018/017 両方 直接 pin**・byte-parity 検証は model-input 137 列(FR-007/SC-001)。
- **#5 F03 置換 candidate は serve 不能**: loader が same-version subset を拒否 → `NOT_SERVABLE_PENDING_PROFILE`・production 昇格スコープ外・将来は profile allowlist(FR-007a)。per-arm keep/drop matrix が必須(FR-006a)。
- **#6 p⊥q 表現が現物と矛盾**: lgbm-063 は 058 4列を含む → 「対象レース市場非入力」に是正・strict market-free default は別 artifact(FR-008)。F03 未定義詳細(tie=competition rank・rank gap・complete-field・favorite 境界・F03 専用 rank obs count・少数標本縮約)を OOS 前固定(FR-001)。

**codex スコープ推奨と決定**: codex は 070=F03/F04・071=F05 の2 spec 分割を推奨したが、**ユーザー決定 = F03/F04/F05 を 070 に1本化**。codex の懸念(F05 の列別依存・帰属混入)は **FR-006a の per-arm keep/drop matrix**(各 bundle 評価で対象外群を両 arm から drop)+ **F05 列別依存 NOT_RUN**(FR-006)で1 spec 内に吸収する。段階評価順(F03→F04→F05 support→F04 ADOPT 時のみ F05 residual→最終 confirmatory)を守り、各 bundle は独立の事前登録 gate で採否する。

## 関連

- [モデル特徴量 再制定書](../../docs/plan/model-feature-redesign.md) F03/F04/F05
- [069 F02 + subgroup ゲート](../069-past-odds-features/spec.md)(基盤)
- [features pm_core_strength.py](../../features/src/horseracing_features/pm_core_strength.py)(F02・F05 support の土台)
- [features past_market_features.py](../../features/src/horseracing_features/past_market_features.py)(058 rank・F03 置換対象)
- 前例: 069(F02 additive・subgroup ゲート・candidate 限定)、058(rank・accuracy-first candidate)
