# Phase 0 Research: 070 過去市場 F03/F04/F05 bundle

069 の F02 + subgroup ゲート基盤を前提に、F03/F04/F05 の未定義詳細と設計判断を OOS 前に固定する。全て「結果を見る前」に確定(憲法 III)。

---

## D1. F03 rank percentile の定義(tie / gap / complete-field / 境界 / obs count)

**Decision**:
- **percentile 式**: `u = 1 - (rank - 1) / (N - 1)`(1着=1.0、最下位=0.0、N=1 は u=1.0 と定義=単走)。rank は過去レースの popularity(市場人気)順位。
- **tie 処理**: **competition rank(標準競技順位=1,2,2,4)**。同人気(同オッズ)は同 rank を与え次を飛ばす。**horse_id や行順で人為的に破らない**(codex D1・捏造禁止)。N は complete-field の頭数。
- **complete-field(popularity-only・codex 見落とし)**: 過去レースで **started 全馬に valid popularity** があるレースのみ rank を算出(odds 完備は要求しない=odds provenance に鈍感な robust fallback が F03 の目的)。部分 field 再正規化禁止(憲法 II)。欠損 field はその過去走を rank 集約から除外。
- **favorite / top3 境界**: 生 rank で `I(rank==1)` `I(rank<=3)` を過去走ごとに立て as-of で率化(`asof_pm_favorite_rate5` / `asof_pm_top3fav_rate5`)。境界は rank 整数比較で曖昧さなし。
- **F03 専用 rank obs count**: `asof_pm_rank_obs_count` = complete-field かつ popularity 有効な過去走数。**F02 の obs count とは別カウント**(codex #6・別列で信頼度)。
- **少数標本**: rank 集約は obs_count < min_obs(=3、F02 と同値)で **NaN**(縮約せず=木に「観測不足」を渡す)。has_obs は列で表現。

**Rationale**: percentile は N 非依存の相対位置=頭数横断で比較可能(058 生 rank は「16頭中5番人気」と「8頭中5番人気」を混同)。competition rank は tie に対し決定論的かつ順序保存。popularity-only complete-field で odds provenance から切り離す。

**Alternatives rejected**: (a) dense rank= tie 後 gap を潰す。(b) 平均順位= 非整数で境界曖昧。(c) odds+popularity 完備要求 → odds gate と結合し robust fallback の目的を弱める(codex 却下)。

**列(F03, 5列・spec 正本)**: `asof_pm_rankpct_last` / `asof_pm_rankpct_mean5` / `asof_pm_favorite_rate5` / `asof_pm_top3fav_rate5` / `asof_pm_rank_obs_count`。recent-K=直近K有効観測(069 同定義)。**F03 の u primitive は F04 と共有**(D6)。

---

## D2. F04 residual の 2母集団分離(finish vs win)

**Decision**:
- **finish_residual = v - u**(過去走ごと): v = 実着順 percentile `v = 1 - (finish_order - 1)/(N_started - 1)`(**分母 N_started・u と尺度統一**・codex 見落とし・N_fin ではない)。finished 馬のみで集約(DNF/失格/取消は NaN=母集団外)。正=市場想定より着順が良かった。
- **win_residual = I(win) - q**(過去走ごと): q = F02 の complete-field market share。**started 全馬**(非勝利=0)で集約=068 started-all 母集団と整合。**直近10走(mean10)** で集約(spec 正本・finish は mean5)。
- **2母集団を混ぜない**(codex D2): finish=finished 集約、win=started 集約。**単一 `asof_pm_result_obs_count`(started 結果走数)+ `asof_pm_resid_sd5`(残差ばらつき ddof=1)**(spec 正本)。
- **q/u の共有**: win_residual の q・finish_residual の u は F02/F03 の公開 primitive を**再利用**(D6)=二重計算しない。
- **リーク**: 両 residual とも過去レースの結果 × 過去レースの市場(strictly-before)=対象レース非参照(codex #2)。

**Rationale**: 「市場がどれだけ間違えたか」の符号付き情報は rank/support 水準と直交しうる新軸。finish=着順精度、win=勝敗精度で作用面が違うため分離。N_started 分母で u と v の尺度統一。

**Alternatives rejected**: (a) v の分母 N_fin → u と尺度不一致(codex 却下)。(b) residual を1母集団統合 → 作用面が潰れる。(c) 別 count 2つ → spec は単一 result_obs_count + resid_sd5(残差信頼度は sd で補足)。

**列(F04, 6列・spec 正本)**: `asof_pm_finish_resid_mean5` / `asof_pm_finish_resid_career` / `asof_pm_win_resid_mean10` / `asof_pm_win_resid_career` / `asof_pm_resid_sd5` / `asof_pm_result_obs_count`。

---

## D3. F05 conditioned の階層縮約 + 条件別 valid count

**Decision**:
- **条件軸**: surface(芝/ダ)・distband(既存 020/023 bins ≤1400/1800/2200)・venue。各条件セルで馬の過去走を all-prior 集約。
- **階層縮約(shrinkage)**: セル別平均を親(全体 all-prior 平均)へ **λ=5 の経験ベイズ縮約** `shrunk = (n_cell·cell_mean + λ·parent_mean)/(n_cell + λ)`。n_cell=0 のセルは **親 fallback**(=parent_mean)。
- **2 registry 群に分割(codex B3)**: recipe drop は group→列展開のみ → support と residual を別 drop するため **`pm_conditioned_support`(3 support 列 + 軸別 count)と `pm_conditioned_residual`(finish_resid_surface + count)を別群**。F05 は論理 umbrella。
  - **support 系**(F02 s の軸別)は **F02 採用時のみ有効**(未採用は recipe drop=`NOT_RUN`)。
  - **residual 系**(F04 finish_resid の surface 別)は **F04 ADOPT 時のみ**評価。
- **as-of 縮約手順(codex 論点1)**: 縮約済み値を持ち越すと親が陳腐化する → target 時点で「最新 cell の累積 sum/count」と「target 直前の overall parent sum/count」を**別々に as-of 取得してから縮約** `mu_shrunk=(n_cell·mu_cell + λ·mu_parent)/(n_cell+λ)`、λ=5。n_cell=0 は親 fallback。親は cumsum−当日(同日除外)=pool-end 非依存。
- **軸別 valid count(codex B5)**: 単一 count では 3 軸 × support/finish の異なる母集団を表現不能 → **各出力に対応する count**(`asof_pm_support_cond_count_{surface,distband,venue}` / `asof_pm_finish_resid_surface_count`)。**実セル観測のみ**(親 fallback で埋めた分は数えない)。

**Rationale**: 条件別の市場評価/残差は「この馬はダート短距離だと市場に過小評価される」等の交互作用を捕捉。λ=5 縮約は 049/013 の縮約規律と整合。軸別 count で親 fallback 多用セルを木が割り引ける。

**Alternatives rejected**: (a) 縮約なし生セル平均 → スパースセルで過学習。(b) λ フィット → OOS 前固定違反(III)。(c) 単一 group → support/residual を別 drop 不能(codex B3 却下)。(d) 単一 valid count → 3 軸 × 母集団を表現不能(codex B5 却下)。

**列(F05・spec 正本)**: support 群 `asof_pm_support_surface` / `asof_pm_support_distband` / `asof_pm_support_venue` + 軸別 count、residual 群 `asof_pm_finish_resid_surface` + count。二重条件(surface×dist)は初版で作らない。

---

## D4. features-018→019 純加算 + compat 018/017 両 pin(推移しない)

**Decision**:
- **物理加算・論理置換**(codex #4): schema は純加算(058 rank 4列も pm_core_strength 9列も**物理的に残す**)。F03 の「058 置換」は candidate recipe の **drop で論理的に**行う(058 列を物理削除しない=lgbm-063 preprocessor が要求)。
- **FEATURE_VERSION**: features-018 → **features-019**。
- **compat map は推移しない**: `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-019"]` に **features-018(lgbm-064-f02acc)と features-017(lgbm-063 active)を両方 直接 pin**。「019→018→017」の推移解決に依存しない(codex 論点4・非推移 registry.py:415)。**pin は短縮値でなく metadata から実測した完全 hash**(codex 論点4)。既存 059/061/069 履歴 entry は不変で 019 追加。
- **byte-parity 検証**: 共有列一致は **model-input ≈137列**(materialized 112 ではない)で確認。additive-merge は右キー一意 + 列名 disjoint で既存列を数学的に不変(058/069 と同じ `test_*_is_purely_additive`)+ 一度きり features-018 build vs features-019 build の共有137列 check_exact + check_dtype。

**Rationale**: default serving(lgbm-063=features-017・lgbm-064=features-018)を byte-parity で守りつつ新群を追加。両 pin は compat の推移的 fragility を排除(将来 018 を退役させても 019→017 が生存)。

**Alternatives rejected**: (a) 058 物理削除 → lgbm-063 の feature_hash 破壊・serving fail-closed(058 と F03 同時変更=帰属不能・III 違反)。(b) 019→018 のみ pin(017 は 018 経由) → 推移依存で fragile(codex #4)。

---

## D5. 段階評価 = per-arm keep/drop matrix(1 spec で回す)

**Decision**(FR-006a):
1. **F03 置換評価**: candidate = `past_market`(058)drop + F03。baseline = 058・F03 なし。**両 arm から F04/F05 群を drop**(F03 の帰属を分離・codex #5)。069 subgroup 付き paired-eval で採否。
2. **F03 verdict 固定**後、**F04 追加評価**(F03 が不採用でも独立に): candidate = 現行 baseline + F04。両 arm から F05 群 drop。
3. **F05 support 評価**: candidate = 現行 baseline + F05 support(F02 の条件別)。
4. **F05 residual 評価**: **F04 が ADOPT された時のみ**実施(F04 未採用なら residual 系は NOT_RUN)。
5. **stack-safety-check**(旧 confirmatory・codex 論点3 で改称): 採用された bundle 合成 vs lgbm-064-f02acc 系。**同一 2019–2026 OOS 上の段階選択後なので独立 confirmatory ではない**(stacking の安全確認に留まる)。真の確認には未使用の time holdout が要る=deferred。
- **verdict = `gate.adopted AND subgroup_guard`**(codex B2): `paired_eval` は gate と subgroups を別々に返し AND しない → **driver/operator が両 boolean を読んで AND**(069 と同じ read-time 適用)。`eval_window` は gate-config でなく CLI `--from/--to` で渡す(gate-config は事前登録記録)。coverage 帯 subgroup は obs_count 未配線で live 生成されない=critical に含まないので採否不変(diagnostic)。live 帯が要れば training/cli.py に obs_count 配線=任意小改修。

- **per-arm keep/drop matrix**: gate-config.json に各段を **OOS 前に明記**(記録用)。**gate-config `staged_evaluation` は現行 CLI 非消費**(codex 論点3)→ contracts/cli.md に**各段の完全 candidate/active recipe を明示列挙し operator が順に実行**。training は 069 の `_expand_group_drops` で群→列展開し両 arm 対称 drop。
- **baseline 前進**: 各段の verdict(ADOPT/REJECT)を固定してから次段の baseline を決める。1 spec 内で順に実行。

**Rationale**: 帰属分離(058↔F03、各 bundle 独立)を保ちつつ 1 spec で全 bundle を評価。keep/drop matrix を事前固定=OOS 後の列選別を構造的に排除(III)。

**Alternatives rejected**: (a) 070=F03/F04・071=F05 分割 → ユーザー決定で1本化。(b) 全 bundle 同時投入 → 帰属不能。

**serving(codex 論点6 で拡張)**: **同一版で列 subset を 1 列でも drop した全 artifact が `NOT_SERVABLE_PENDING_PROFILE`**(F03 置換だけでなく未採用 F04/F05 を drop した最終 candidate も)。loader は same-version で global hash 完全一致のみ exact=recipe-drop subset を fail-closed 拒否(model_loader.py:194)。**paired-eval は ModelRecipe から各 fold 再fit=評価に serving 不要**。production 昇格スコープ外(将来 `(feature_version, profile id, ordered hash)` allowlist)。

---

## D6. q/s primitive の共有 vs 再計算(materialize-safe)

**Decision**:
- **公開 primitive を新設(codex 論点2)**: 現 `_race_support` は q を内部計算後に捨て s のみ返す → **q/s/N を返す公開 primitive** を作り F02/F04/F05 で共有。F03 の u primitive も同様に公開し F04 と共有。**F04 の finish_residual も per-race primitive として公開し F05 residual(T021)が import**(全て adoption≠import)。per-race の q/u ベクトルは過去レース単位で一意=`build_asof_features` 内で1回計算し全 bundle が参照(二重計算しない)。**F04 が依存するのは「F02 の採否」でなく「F02 と同じ q 定義」**。
- **materialize 結線**: F03/F04/F05 は 069 F02 と同型で `build_asof_features` に **additive left-merge**(右キー=(horse_id, race_id)一意・列名 disjoint)。**新ソース生列を読まない**(popularity/finish_order/odds は既に loader・fingerprint 内)→ **source_fingerprint 不変**(069 は odds 追加で fingerprint 拡張したが 070 は不要=059/061 型の materialize-safe)。
- **F05 pool-end 非依存**: 親の all-prior 平均は cumsum−当日で per-row 決定的=materialize 経路と in-memory 経路で bit 一致(025 の parity 基準)。

**Rationale**: q の単一計算源=F02 と F04/F05 で値がドリフトしない。新ソース列なし=materialize の再生成不要(069 の parquet=features-018 の上に列追加は build 側で吸収、ただし features-019 では一度 re-materialize が必要=fingerprint algo 不変だが列集合が増える)。

**Alternatives rejected**: (a) F04/F05 が q を独自再計算 → F02 と丸め/欠損処理がドリフトするリスク。(b) F05 を全体プールで集約 → pool-end 依存で materialize parity 破壊(025 の restrict-from-superset 禁止と同型)。

**注意(re-materialize)**: features-019 は列集合が増えるため、materialized parquet を **一度 features-019 で再生成**が必要(fingerprint algo=fp-v2 不変・069→070 で列追加のみ)。069 と同じ運用(`features materialize` 1回)。

---

## D7. codex plan レビュー採否(記録)

plan フェーズで codex に再レビュー(`codex exec --sandbox read-only`、codex-cli 0.144.1 / gpt-5.6-sol・全 070 成果物 + 実コード読解)。**verdict = REQUEST CHANGES**(materialize/compat 方針は妥当だが列契約・subgroup 採否・F05 列別 drop がブロッカー)。**全ブロッカーを本 plan に反映済み**:

- **採用(B1・列契約不一致)**: data-model/research/gate-config が spec.md の正本列名から乖離していた → **spec.md を正本に全 Phase 0/1 文書を再整合**。F03=`asof_pm_rankpct_last/mean5/favorite_rate5/top3fav_rate5` + `asof_pm_rank_obs_count`(5)、F04=`asof_pm_finish_resid_mean5/finish_resid_career/win_resid_mean10/win_resid_career/resid_sd5/result_obs_count`(6・win は mean10・sd5 あり)、F05=`asof_pm_support_surface/distband/venue` + `asof_pm_finish_resid_surface`(spec 正本)。data-model の独自命名(mean5/best5/fav_rate 等)は破棄。
- **採用(B2・eval 変更なしは不成立)**: `paired_eval` は `gate` と `subgroups` を**別々に**返し `gate.adopted` に `subgroup_guard` を AND しない([paired.py](../../eval/src/horseracing_eval/paired.py))→ **最終 verdict = `gate.adopted AND subgroups.subgroup_guard`(driver 適用・069 と同じ read-time AND)**。plan の「eval 変更なし」を是正。`eval_window` は gate-config でなく CLI `--from/--to` で渡す(gate-config は事前登録記録)。coverage 帯 subgroup は `obs_count` 未配線で live 生成されない(critical=[2026_only,nk,2026_nk] は帯を含まないので採否は不変=069 同型・帯は diagnostic)。live 帯が要るなら training/cli.py に obs_count 配線の小改修=**任意**。
- **採用(B3・F05 group 分割)**: recipe drop は group→列展開のみ → support(F02 依存)と residual(F04 依存)を別 drop するため **registry を 2 群 `pm_conditioned_support` / `pm_conditioned_residual` に分割**(F05 は論理 umbrella)。
- **採用(B4・CLI 契約)**: `paired-eval` は `--active`(既修正)、`train-evaluate` register は **`--model-version`(not --model-label)**、**`coverage-audit --group` は未実装**(--from/--to/--json のみ)、candidate 登録例は paired 評価と同じ **target-encoding 指定が必要**(評価 recipe 再現)。contracts/quickstart を修正。
- **採用(論点2・共有 primitive)**: `_race_support` は q を捨て s のみ返す → **q/s/N を返す公開 primitive** を作り F02/F04/F05 で共有、F03 の u primitive も F04 と共有。
- **採用(論点3・段階評価)**: gate-config `staged_evaluation` は CLI 非消費 → **各段の完全 candidate/active recipe を contracts に明示列挙(operator 駆動)**。同一 2019–2026 OOS 上の段階選択後 confirmatory は独立でない → **「stack-safety-check」に改称**(真の確認には未使用 time holdout=deferred)。
- **採用(論点4・compat)**: 019→018/017 直接 pin は正・非推移([registry.py:415])。既存 059/061/069 履歴 map は entry 不変で 019 追加なら壊れない。**pin は短縮でなく metadata の完全 hash**。018 hard-code の ~7 テストは意図的更新。
- **採用(論点5/B5・F05 count)**: 単一 valid_count では 3 軸 × support/finish 母集団を表現不能 → **各出力に対応する count**(軸別・母集団別)。
- **採用(見落とし)**: F03 は odds+popularity 完備要求で「odds provenance 鈍感」目的を弱める → **popularity-only complete-field + started 内 competition re-rank**(D1 反映)。F04 v の分母は **N_started**(u と尺度統一・N_fin でなく=D2 反映)。**same-version で 1 列でも drop した artifact は全て NOT_SERVABLE**(F03 置換だけでなく未採用 F04/F05 を drop した最終 candidate も=D5 反映)。
- **不採用**: なし(全指摘採用)。
- **保留**: 実装時 self-review checklist で点検 + tasks フェーズで codex 再レビュー。

**analyze 反映(medium 以上解消)**:
- **I1(HIGH)**: SC-006 の「default が全 market-history 排除=p⊥q」は stale(lgbm-063 は 058 を含む)→ **「新 F03/F04/F05 群が default candidate に入らない」に是正**(FR-008 と整合)。
- **G1(MEDIUM)**: SC-006 の残りは schema/API 不変 + candidate 限定 = **T030(compat/NOT_SERVABLE)+ T031(materialize 不変)+ T033(--drop-groups で default 不変)で担保**=新規テスト不要(strict market-history-free default はスコープ外)。
- **C1(MEDIUM・III テンション残リスク)**: 段階選択(F03 verdict→F04 baseline→F05)は**同一 2019–2026 OOS 上の post-hoc 選択**。緩和=各 bundle は個別に事前登録(keep/drop matrix)・stack-safety-check は独立 confirmatory でないと明示・gate-config `stack_safety_check._note` に開示済み。**production 昇格(スコープ外)前に未使用 time holdout を確保する**(本 070 では確保しない=残リスクとして記録)。
- **I2(MEDIUM)**: spec Key Entities F03 に `asof_pm_rank_obs_count` を追記=5列で全文書一致。
- **LOW(U1/B1/B2/B3/I3)**: u/q primitive の import は F03 群採用と独立(FR-004)・resid_sd5 は obs<2/他 F04 は min_obs=3(gate-config)・top3 tie は competition rank で rank≤3 全馬(gate-config)・137 は exact・US1 scenario 2 は非網羅と明記。

**tasks フェーズ検証(Q1)**: tasks 生成後に `speckit-analyze` を **24 回反復**(medium 以上を毎回解消→再analyze)で cross-artifact 整合を検証し**収束**。**CRITICAL は全 pass で 0**。実質的な gap を 2 件捕捉=**(HIGH) F03 verdict の keep/drop 反転**(ADOPT で敗者 058 を drop すべきを勝者を drop と誤記→修正・T006a に inversion 回帰ガード)+**(HIGH) 段階評価 matrix の F04→F05 base 累積漏れ**(f03_verdict_resolution はあるが f04_verdict_resolution がなく F05 support が F04 の分散を過大計上しうる=III 帰属リスク→`f04_verdict_resolution` bookkeeping + F05 stages に `_base` 累積注記 + T006a に F04 verdict 分岐 downstream base の機械照合)。他=列契約/窓/フラグ/2母集団 count/TE 列集合(jockey_id,trainer_id=036 lineage・venue_code 除外)/式パラメータ束縛の締め。**最終 pass=0 CRITICAL・0 HIGH・数値は registry.py で code-validated(137=materialized 112+static 25・新19→156/131)・残 MEDIUM は stack-safety-check 非独立(同一OOS・production 昇格スコープ外で disclosed-accepted)のみ**。**2 件の HIGH attribution 修正は III に触れるため T036 codex 実装レビューで再確認**。その他は spec↔data-model↔gate-config↔tasks の列名/count/窓/フラグ整合と 2 母集団 count ゲート(finish=内部 finished 数)・matrix↔recipe 同期・eval_window 同期の締め。tasks フェーズの codex 再レビューは **実装差分に対して T036 で実施**(tasks は plan の機械分解のため)。

**T036 codex 実装レビュー(REQUEST CHANGES・2 HIGH + 2 MEDIUM)**:
- **採用(#1 HIGH・実バグ修正)**: F05 の parent が overall all-prior でなかった=`_conditioned_shrunk` が cell-null 過去走を parent 集約前に落としていた(track_type/distance/venue_code は nullable)→ **parent は全 prior 行(cell 無関係)、cell は cell-present 行のみ**に是正。null-cell target は parent fallback。回帰テスト 2 件追加(parent が cell-null 走を含む・null-cell target→parent)。**再 materialize 済(fingerprint 不変・F05 列値のみ更新)**。
- **採用(#2 HIGH・attribution 機械検証不足)**: T006a が群展開と JSON/文字列検査のみだった → **F03×F04×support 全 verdict 分岐で candidate/active の drop 群を再構築し、両 arm 差分=対象 bundle のみ・downstream 対称・勝者累積(F03 winner/F04 winner)を machine-assert**する `test_full_verdict_branch_attribution` + matrix 照合を追加。
- **採用(#4 MEDIUM・共有137列 byte-parity 未回帰化)**: 列数/disjoint のみだった → **各 070 block を assembled shared 列に merge し shared 列が check_exact+check_dtype で byte 一致**する empirical test を追加。
- **保留(#3 MEDIUM・primitive は定義共有で計算共有でない)**: F04/F05 は同じ公開関数を呼ぶため数値は完全一致(drift なし=correctness OK)。単一計算 frame の pass-through は materialize の perf 最適化として deferred(build_asof_features の signature 変更を要すため。値の正しさ=重要property は満たす)。
- **codex 確認済み(問題なし)**: 全 as-of の strictly-before+同日除外、F03 popularity-only/competition rank/N=1、F04 N_started 分母/started win 母集団/mean10/sd5/finished 内部ゲート、F02 新旧経路の byte 一致(実データ 952,197 行で確認)、compat 完全 hash、materialize 957,355 行 131 列 fingerprint 不変。**独立 second opinion と結論差なし**。

**self-review checklist(実装時)**:
1. F03: competition rank tie 決定論(行順シャッフル不変)・**popularity-only** complete-field・rank_obs_count が F02 obs と独立列。
2. F04: **N_started 分母**・win mean10・resid_sd5・q は F02 共有・win_resid の started 母集団が 068 win_realized 行一致。
3. F05: **軸別 count**(実セルのみ・親fallback除外)・親平均 cumsum−当日で pool-end 非依存・**2 registry 群**。
4. registry: features-019 compat が 018/017 両直接 pin(**完全 hash**)・既存 059/061/069 compat テスト非破壊・additive parity。
5. materialize: source_fingerprint 不変(新ソース列なし)・in-memory bit 一致・018 parquet は 019 で再 materialize。
6. serving: **同一版で列 subset を drop した全 artifact** の NOT_SERVABLE 挙動を型付きテストで固定。
7. eval verdict: `gate.adopted AND subgroup_guard` の tri-state を driver で適用(operator 手順に明記)。

---

## 未解決(NEEDS CLARIFICATION)

なし。全ての未定義詳細(F03 tie/gap/境界/obs、F04 2母集団、F05 縮約/valid、compat 両 pin、段階評価 matrix、q 共有、NOT_SERVABLE)を上記で OOS 前固定。
