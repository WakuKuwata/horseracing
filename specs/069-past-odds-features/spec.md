# Feature Specification: 過去オッズ量特徴(F02)+ subgroup ゲート拡張

**Feature Branch**: `069-past-odds-features`

**Created**: 2026-07-13

**Status**: Draft

**Input**: [モデル予測精度向上 提案書](../../docs/plan/model-accuracy-improvement-proposal.md) Phase 3 + [モデル特徴量 再制定書](../../docs/plan/model-feature-redesign.md) F02 pm_core_strength

## 背景・目的

068 で「新特徴を増やす前に評価契約を直す」を実施済み(paired winner NLL・block bootstrap CI・採用ゲート)。本 feature はその物差しの上で、Phase 3 の最初の新情報量 = **過去レースの確定オッズ"量"** を特徴化する。

既存 past_market bundle(058-origin・現 features-017)は過去の**人気順位(rank)**のみを使う(`asof_mkt_rank_*` 4列)。同じ1番人気でも市場確率25%と60%を区別できない。F02 は**オッズ量から市場share q を復元し `s=log(q×N)` として集約**する — q=(1/O)/Σ(1/O)、s=0 が一様支持、正が一様以上の支持。対象レース自身のオッズは使わず、過去レースの確定オッズだけを strictly-before・同日除外で集約する(憲法II 境界)。

**codex 設計レビューで判明した前提整備(全採用)**: 068 の直近ガードは 3年/5年 winner NLL の**点推定**のみで、2026単年・nk: subgroup・coverage・subgroup CI を見ない。過去市場履歴は 2026 で nk: ID断層(started 行の 36.8%)の影響を受けるため、F02 は「歴史 fold(例 2021–2025)で効くが 2026 serving で死ぬ」036/061 の逆リスクを持つ(2021–2025 は例示・**実際の凍結 OOS 窓は 2019–2026、gate-config.eval_window**、analyze I1)。したがって **F02 の採否判定の前に、068 paired-eval を subgroup(2026-only / nk:-source / coverage 帯)CI ゲートへ拡張する**(本 feature 内、US1)。その上で F02 を新 bundle として **accuracy-first candidate モデル**(default 意思決定支援モデルには入れない)で評価する(US2)。

**スコープ外**: F03 pm_rank_robust / F04 pm_expectation_residual / F05 pm_conditioned(F02 採用後に別 spec で段階評価)、default モデルへの market-history 組込(p⊥q 境界・provenance 監査が前提)、odds provenance 列の追加(別 spec)、067 の追加 ID 解決。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - subgroup ゲート拡張(Priority: P1)

モデル研究者として、068 の paired-eval で「全体は改善だが 2026 や nk: 馬では死んでいる」を見逃さないようにしたい。そのために **grain 別**に subgroup を追加報告する: **race-level(winner NLL 差)** = 2026単年・フィールドの nk: 有無、**horse-level(started-all per-horse logloss 差)** = ID source(canonical / nk:)・過去市場 coverage 帯。各 subgroup の CI と subgroup ガードを採用ゲートに足す。

**Why this priority**: これが無ければ F02 の採否が信用できない。過去市場履歴は 2026 で ID断層の影響を最も受けるため、subgroup で見る物差しが F02 評価の前提。068 の評価契約の自然な拡張であり、単独で価値がある(既存モデル比較にも使える)。

**Independent Test**: 既存2モデル(または 058 込み/なしの2 recipe)を paired 評価し、全体に加えて **race-level(2026_only / 2026_field_has_nk)の winner NLL 差**と **horse-level(canonical / nk: / 2026_nk)の started-all per-horse logloss 差**、各 block bootstrap CI が1レポートに出ることで検証できる。**critical 3 subgroup(2026_only/nk/2026_nk)は F02 非依存**(race_date・horse_id prefix のみ)なので US1 単独で動く。**coverage 帯(0走/1–2走/3+走)は F02 の `asof_pm_obs_count` が前提**のため US2 で追加される(診断・非 critical)。

**Acceptance Scenarios**:

1. **Given** paired 評価の per-race winner NLL 損失差 と per-horse started-all 損失差、**When** subgroup 集計、**Then** race-level(2026_only / 2026_field_has_nk)は winner NLL 差、horse-level(canonical / nk: / 2026_nk / coverage 帯)は started-all per-horse logloss 差を、各 開催日 block bootstrap CI と共に報告する(winner NLL は race-level のみ=1レース1標本、codex C1)。
2. **Given** subgroup 別 CI、**When** subgroup ガード判定、**Then** 事前登録 critical subgroup(`2026_only`・`nk`・`2026_nk`)を intersection-union(全 PASS)+ 三値判定(PASS=CI 上限<margin ε / FAIL=CI 下限>ε / NO_DECISION=跨ぐ)で採用条件に足す。
3. **Given** subgroup の標本が少ない、**When** CI 算出、**Then** `NO_DECISION`(CI 未確定)として報告し、点推定だけで否決/合格にしない。
4. **Given** 既存 068 ゲート、**When** subgroup ガード追加、**Then** 既存の primary/stat/recent/top/calibration ガードは不変で、subgroup ガードが加算される(後方互換)。
5. **Given** subgroup 割当、**When** ID source 判定、**Then** 対象馬の horse_id が `nk:` prefix かで canonical/nk: を分類し、割当は結果ラベルを参照しない(属性のみ)。

---

### User Story 2 - F02 pm_core_strength bundle(Priority: P2)

モデル研究者として、過去レースのオッズ量(市場 support s=log(q×N))を strictly-before 特徴として追加し、US1 の拡張ゲートで accuracy-first candidate として評価したい。

**Why this priority**: Phase 3 の本題。US1 の subgroup ゲートが前提なので P2。

**Independent Test**: F02 列を持つ features-018 を build し、058 込みの現行相当を baseline に、F02 追加 candidate を paired 評価(US1 拡張ゲート)して winner NLL・subgroup・CI を出せることで検証する。

**Acceptance Scenarios**:

1. **Given** 過去レース k で started 全馬の有効オッズが揃う、**When** q を計算、**Then** `q_ik=(1/O_ik)/Σ(1/O_jk)`、`s_ik=log(q_ik×N_k)` を作る。started 全馬のオッズが揃わないレースはその race の s を作らず(部分 field の市場share を捏造しない)。
2. **Given** 各馬の過去 s、**When** as-of 集約、**Then** strictly-before(merge_asof backward, allow_exact_matches=False)+ 同日除外で recent-K 集約する(058 idiom)。対象レース・同日・未来のオッズは一切入らない。
3. **Given** F02 列、**When** registry 登録、**Then** 既存 past_market group と別の**独立 group `pm_core_strength`** に登録し、058 の rank 4列は削除しない(同時変更で帰属不能を避ける、codex)。
4. **Given** FEATURE_VERSION、**When** features-017→018 bump、**Then** F02 は純加算で、features-017 の hash を compat map に pin し、lgbm-063 が compat-load でき共有128列が byte-parity することを確認する(serving 不変)。
5. **Given** F02 の欠損、**When** 特徴生成、**Then** 過去市場観測が無い馬は連続値 NaN・`asof_pm_obs_count=0`・`asof_pm_has_obs=0`(「市場観測あり」の意味で has_history でなく has_obs、codex)。
6. **Given** F02 candidate、**When** モデル配置、**Then** accuracy-first candidate モデル(非active)に入れ、default 意思決定支援モデルには入れない(p⊥q 維持、058 前例)。

### Edge Cases

- 過去レースで一部馬のオッズが欠ける → その race の q/s を部分母集団で再正規化しない(s を作らない)。
- オッズ境界値: **`1.0` は有効な元返し本命値として保持**(除外すると complete-field で強本命レースを丸ごと落とす、analyze D1)。無効 sentinel は `≤0`・非有限(inf/NaN)のみ。**`999.9`(netkeiba cap)は無効扱いだが T001 の source 別確認保留**(cap_pending_confirm)。valid = `1.0 ≤ O < 999.9`(暫定)。**T001 の results-blind sentinel check が pre-OOS に either 方向で確定(999.9 が正当と判れば再包含も可)し、その後凍結=post-OOS 不変**(III=確定は結果を見る前・odds 分布のみ、analyze I1)。無効が1頭でもあればそのレースの q を作らない(complete-field)。
- 頭数 N=1 のレース → q=1、s=log(1×1)=0(自明・非情報)。**N=1 / N=0 started を div/log エラーなく扱い、s=0 の N=1 を obs_count に数えるか(既定=数える)を T004/T012 で明示**(analyze E1)。人気 tie は q 計算に影響しない(q はオッズ由来)。
- DNF/取消馬 → started の定義(entry_status)に従い、cancelled はオッズ母集団に含めない。過去結果由来の派生(residual 等)は本 bundle では作らない(F04 スコープ)。
- partial ingest(過去レースの行自体が欠ける)→ complete-field 判定で s を作らず、has_obs=0 に落とす。
- recent-K の定義は「直近 N 走」か「直近 N 有効市場観測」かを実装前に固定(codex、既定=直近 N 有効観測)。

## Requirements *(mandatory)*

### Functional Requirements

**subgroup ゲート拡張(US1)**

- **FR-001**: paired-eval は subgroup を **grain 別**に集計しなければならない(codex C1): **race-level(winner NLL 差)** = 結果非依存の race 属性 `2026_only`・`2026_field_has_nk`(winner-conditioned 選択をしない)、**horse-level(started-all per-horse loss)** = per-horse 属性 `canonical`/`nk`/`2026_nk`/coverage 帯(0/1–2/3+走)。各 subgroup の開催日 block bootstrap CI を報告する。
- **FR-002**: 採用ゲートに **subgroup ガード**を追加し、事前登録 critical subgroup(`2026_only`・`nk`・**`2026_nk`** 交互群、codex C3)を **intersection-union**(全 PASS)で守らなければならない。判定は **三値**(codex C2): non-inferiority margin ε に対し PASS=CI 上限<ε / FAIL=CI 下限>ε / NO_DECISION=跨ぐ。**ε は grain 別**(race-level winner NLL 用 / horse-level per-horse logloss 用、後者は前者より約5–10倍小さいため別値、analyze A1)。ε(grain別)・critical 集合・**OOS 評価窓**は gate-config に OOS 前固定(III、analyze C1)。
- **FR-003**: FR-002 の三値判定で `NO_DECISION`(CI が margin を跨ぐ、**または subgroup の異なる開催日数が `no_decision_min_days` 未満で CI が underpowered**、analyze A1)は**否決しないが採用の十分条件でもない**(adopted は critical 全 PASS 必須)。点推定で否決/合格にしてはならない。診断として subgroup 内 `candidate − uniform`(絶対水準)を併記する(codex C6)。
- **FR-004**: subgroup 割当は結果ラベルを参照してはならない。**race-level は race 属性(race_date の年・フィールドの nk: 有無)**、**horse-level は per-horse 属性(horse_id `nk:` prefix・厳密前市場観測数帯)**から決める(いずれも結果非参照)。呼び出し側が属性を注入し、eval/subgroups.py は band 割当・集計・gate 判定のみ(codex C7)。
- **FR-005**: 既存の 068 ゲート条件(primary / stat_guard / recent_guard / top_noninferior / calibration)は不変で、subgroup ガードは加算でなければならない(後方互換)。

**F02 pm_core_strength(US2)**

- **FR-006**: 過去レース k の **started 全馬の有効オッズが揃う場合のみ** `q_ik=(1/O_ik)/Σ(1/O_jk)`、`s_ik=log(q_ik×N_k)` を作らなければならない。1頭でも無効オッズがあれば race 全体の q/s を作らない(部分 field の市場share を捏造しない)。
- **FR-007**: F02 は各馬の過去 s を **strictly-before(merge_asof backward, allow_exact_matches=False)+ 同日除外**で recent-K 集約しなければならない。対象レース・同日・未来のオッズは特徴に入ってはならない(leak-guard test)。
- **FR-008**: F02 列は既存 past_market group と別の独立 group `pm_core_strength` に登録し、058 の rank 4列は削除してはならない(帰属分離、codex)。
- **FR-009**: FEATURE_VERSION を features-017→features-018 に bump し、F02 を純加算とし、features-017 の feature_hash を compat map に pin して lgbm-063 が compat-load でき共有128列が byte-parity することを確認しなければならない(serving 不変)。**legacy 列を物理削除しない**(lgbm-063 preprocessor が要求、codex)。
- **FR-010**: 過去市場観測が無い馬は連続 F02 値 NaN・`asof_pm_obs_count=0`・`asof_pm_has_obs=0` としなければならない(has_obs=市場観測有無、has_history と区別、codex)。
- **FR-011**: F02 の縮約平均・recent-K・trend・sd の式(recent-K=直近 K 有効観測、trend=直近3観測の時間順単回帰傾き・2未満 NaN、sd=直近5の標本標準偏差 ddof=1・2未満 NaN、best5、shrinkage prior=0/λ 固定)を OOS 前に固定しなければならない(III)。
- **FR-012**: F02 は accuracy-first candidate モデル(非active)で評価し、default 意思決定支援モデルには入れてはならない(p⊥q 維持、058 前例)。**「market-history」= {group `past_market`(058 rank), group `pm_core_strength`(F02)}**(data-model §2、analyze M2)。default から drop するのは両群の**展開列**(`FEATURE_GROUPS` 反転、drop_features は列名で group 名では効かない、analyze F1)。

**採用判定(US2、US1 の物差しを使用)**

- **FR-013**: F02 は 1つの事前登録 bundle として **068 `paired-eval` 経路**で評価し(旧 `feature-eval` の binary LogLoss/ECE gate は使わない、codex C5)、OOS 結果を見て列を後から選別してはならない(III)。candidate=features-018 全群 vs active=F02 群のみ drop(058 rank は両者に残す)。採用は winner NLL 改善 + subgroup intersection-union ガード(2026/nk:/2026_nk 全 PASS)+ top2/top3 non-inferiority + 校正非劣化。

**境界(全体)**

- **FR-014**: オッズ/人気/q/s は対象レース自身では特徴に入らない。列名は leak-guard 禁止トークン(odds/popularity)を避ける(058 の `asof_mkt_` / 041 の命名回避 idiom)。behavioral leak-guard(今走・同日・未来のオッズ変更で不変、過去変更で変化)を必須とする。
- **FR-015**: F02 の q 計算には raw odds が必要で、現行 features loader は popularity のみ読み **odds を読まない**(codex C4)。loader に `RaceHorse.odds` を追加し、**source_fingerprint を odds 込みに拡張**(056 前例、migration 不要=odds 列は既存)、materialized/in-memory の bit-parity と stale fail-closed を再担保しなければならない(025 同型)。
- **FR-016**: DBスキーマ・migration・API・OpenAPI は変更しない。
- **FR-017**: 評価派生値(subgroup CI・coverage-audit 出力・paired 差)はモデル特徴に一切戻してはならない(FR-014 の評価側 mirror、憲法II、analyze C1)。import-graph + behavioral leak-guard(T023a)で担保。

### Key Entities *(include if feature involves data)*

- **PastMarketSupport(F02)**: 過去レースの市場 support。属性: `asof_pm_support_last` / `mean3` / `mean5` / `best5` / `career` / `trend` / `sd5` / `asof_pm_obs_count` / `asof_pm_has_obs`。grain=horse-history。source=race_horses.odds(過去 started 行)。history_boundary=strictly-before + 同日除外。missing=NaN + has_obs=0。特徴のみ、結果非参照。
- **SubgroupGateResult(US1)**: paired-eval の grain 別 subgroup 報告。属性: race-level(`2026_only` / `2026_field_has_nk`)の winner NLL 差、horse-level(`canonical` / `nk` / `2026_nk` / coverage 帯)の started-all per-horse logloss 差、各 block bootstrap CI + 三値(PASS/FAIL/NO_DECISION)、critical(`2026_only`/`nk`/`2026_nk`)の intersection-union ガード判定 + cand−uniform 診断。068 の PairedReport に加算。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: paired-eval が race-level(2026_only / 2026_field_has_nk)の winner NLL 差と horse-level(canonical/nk:/2026_nk)の started-all per-horse logloss 差、各 CI を含むレポートを再現可能に生成でき、subgroup ガード(critical 三値 intersection-union)が機械判定される。**coverage帯(0/1–2/3+走)は F02 の obs_count 依存のため US2 で populate**(US1 MVP は critical 3 subgroup で成立、analyze F1/U1)。
- **SC-002**: F02 列を持つ features-018 が build でき、共有128列が features-017 と byte-parity(check_exact/check_dtype)、lgbm-063 が compat-load で予測 byte 一致(serving 不変)。
- **SC-003**: F02 の leak-guard test が緑(今走・同日・未来のオッズ変更で不変、過去変更で変化)、materialized/in-memory bit-parity 一致。
- **SC-004**: F02 candidate を US1 拡張ゲートで paired 評価し、winner NLL・2026/nk: subgroup CI・top2/top3・校正を含む採否レポートを出力できる。採否は事前登録ゲートで機械判定。
- **SC-005**: 年×ID source×coverage 帯の 1/3/5走 coverage 監査を出力でき、2026 nk: 馬の過去市場 coverage が数値で示される(「市場評価なし新馬」との誤認を防ぐ)。
- **SC-006**: DBスキーマ・API・OpenAPI・migration 不変。default 意思決定支援モデルに F02 が入らない(p⊥q leak-guard)。

## Assumptions

- 067 の物理 repair は実 DB に実質適用済み(未マージ分裂 nk: 2件・2026 馬の 76.7% が過去走連結・id_mappings mapped 6,341)。ただし 2026 started 行の 36.8% が nk: ID のため subgroup 監査は必須。
- 評価は 068 の paired-eval / calib-split-eval 基盤を拡張して行う(eval predictor-agnostic 維持・CLI が predictor 注入)。
- odds provenance(source / observed_at / finality)列は現状 race_horses に無く、JRA-VAN final と netkeiba single-latest が混在する。**069 は F02 を accuracy-first candidate 限定**とし、provenance 監査(量特徴の安定性確認)は SC-005 の coverage 監査に含めるが、provenance 列追加は別 spec。
- 現行 active は **lgbm-063**(features-017, pl_topk+isotonic、lgbm-062 と recipe 共有の絶対パス再学習版、[weights-uri-relative-path-ops-bug])。提案書の lgbm-062 表記は stale。
- codex second-opinion 取得済み(親から `codex exec` 直叩き)。plan フェーズで再確認する。

## 憲法チェック

- **II(リーク境界)**: 対象レース自身のオッズ/人気/q/s は特徴に入らない・strictly-before + 同日除外・部分 field の再正規化禁止・列名で odds/popularity トークン回避 + behavioral leak-guard・subgroup 割当は属性のみ・F02 を default モデルに入れない(p⊥q)。
- **III(事前登録ゲート)**: F02 は1 bundle・列を OOS 後に選別しない・subgroup 閾値/式/recent-K を OOS 前固定・058 と同時変更しない(帰属)。
- **IV(確率整合)**: F02 は win 特徴で 009 の Σ=1・順位保存に影響しない(068 と同経路)。
- **V(監査)**: coverage 監査(年×source×帯)・subgroup CI・bundle 事前登録記録。
- **VI(契約)**: スキーマ・API・OpenAPI・migration 不変。FEATURE_VERSION bump は serving compat(hash pin + byte-parity)で正当化。

## 関連

- [モデル予測精度向上 提案書](../../docs/plan/model-accuracy-improvement-proposal.md) Phase 3
- [モデル特徴量 再制定書](../../docs/plan/model-feature-redesign.md) F02–F05・§8 過去オッズ契約
- [068 評価契約](../068-evaluation-contract-calibration/spec.md)(subgroup ゲート拡張の基盤)
- [features past_market_features.py](../../features/src/horseracing_features/past_market_features.py)(058 rank bundle、拡張元)
- 前例: 058(過去市場 rank・accuracy-first candidate・serving compat)、061/036(歴史 fold で効くが serving で死ぬリスクの逆=absolute軸の生存)
