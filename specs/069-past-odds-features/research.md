# Research: 過去オッズ量特徴(F02)+ subgroup ゲート拡張

**Feature**: 069 | **Date**: 2026-07-13

spec/plan の主要設計判断を解決する。前例は memory / specs / codex(spec フェーズ)を根拠にする。

## D1: 市場 support の定義 `s = log(q × N)`

**Decision**: 過去レース k で started 全馬の有効オッズが揃う場合のみ、`q_ik=(1/O_ik)/Σ_j(1/O_jk)`(市場 vote-share、010 定義)、`s_ik=log(q_ik × N_k)`。s=0 が一様支持(q=1/N)、正が一様以上、負が一様未満。takeout(控除率)は q 正規化で分子分母に共通なので相殺 → **生オッズ由来 q のまま**で takeout 補正不要(010 と同じ)。

**Rationale**: raw オッズ平均は頭数・控除率・source 差の影響を受ける(codex)。`log(q×N)` は頭数正規化された支持度で「同じ1番人気の25% vs 60%」を区別する。058 の rank(順位)より情報量が多い。共通倍率不変(全オッズを定数倍しても q 不変)。

**Alternatives**: raw odds 平均(頭数/控除率に脆弱、却下)。q そのもの(頭数依存、log(q×N) で吸収)。

## D2: 縮約・recent-N・trend・sd の式(OOS 前固定、FR-011)

**Decision**（codex の未定義詳細を全固定）:
- **recent-K = 直近 K 有効市場観測**(直近 K 走ではない。オッズ欠損走はスキップ)。
- `asof_pm_support_last` = 直近1観測の s。
- `asof_pm_support_mean3/mean5` = 直近3/5観測の縮約平均 `Σ recent-K(s) / (n + λ)`、中立 prior=0(s の一様点)、mean3 は λ=2、mean5 は λ=2、career は λ=5(F02 内で固定)。
- `asof_pm_support_best5` = 直近5観測の max。5未満は観測分の max、0観測は NaN。
- `asof_pm_support_career` = 全観測の縮約平均(λ=5)。
- `asof_pm_support_trend` = 直近3観測を時間順(race_date)に単回帰した傾き。2観測未満は NaN。
- `asof_pm_support_sd5` = 直近5観測の標本標準偏差(ddof=1)。2観測未満は NaN。
- `asof_pm_obs_count` = 有効市場観測数(log1p 変換は監査 artifact に残し、初版は生 count)。
- `asof_pm_has_obs` = 観測≥1 で 1、0観測で 0(**has_history でなく has_obs**=市場観測有無、codex)。

**Rationale**: 「回数が存在しない事実」は 0(count/has_obs)、「計測不能」は NaN(連続値)を厳格に分ける(再制定書 §2.3)。全パラメータ事前固定で III(OOS 後調整禁止)。

## D3: q complete-field 判定と欠損伝播

**Decision**: 過去レース k で **started 全馬に有効オッズ(0<O<∞、境界 sentinel 除外)がある場合のみ** q/s を作る。1頭でも無効なら race k の s を作らない(部分 field の再正規化禁止、011 canonical field 教訓)。馬 i の as-of 集約は「s を持つ過去レース」のみを observation とし、s の無いレースは observation にカウントしない → `has_obs=0` は「s を持つ過去レースが1つも無い」を意味する。

**Rationale**: 部分 field の q は市場 share を捏造する(codex FORBIDDEN)。complete-field のみで q の意味(全馬の相対支持)を保つ。欠損は has_obs で明示し「市場評価なし新馬」との誤認を防ぐ。

## D4: subgroup ゲート拡張(US1)の設計 — 最重要

**⚠️ 以下の subgroup 列挙・margin 記述は D8(codex C1/C2/C3)で更新済み — 正本は D8 と data-model §4**。grain 分離(race-level winner NLL=2026_only/2026_field_has_nk、horse-level started-all=canonical/nk/2026_nk/coverage帯)・三値判定・grain 別 margin ε>0・intersection-union・paired-eval 一本化。以下は初版の記録として残す。

**Decision(初版・D8 で更新)**: 068 paired-eval の per-race winner-NLL 損失差を subgroup で再グループ化し、開催日 block bootstrap CI(068 再利用)を算出。標本少で CI 未確定は `NO_DECISION`(点推定で否決/合格にしない、068 D2 同型)。

**paired 相殺問題(codex 指摘2)への対処**: paired だと candidate/active 双方が同じ ID断層の影響を受け差が出にくい。→ subgroup ガードは paired 差に加えて **各 subgroup の absolute winner NLL 水準**(candidate の 2026/nk: winner NLL が全体や uniform baseline から乖離していないか)も報告する。ガード判定は paired 差 CI(非悪化)を主とし、absolute 水準は診断併記(採否閾値にはしないが「2026 で candidate が壊れている」を可視化)。多重比較補正は subgroup を**事前登録の少数固定集合**に限定して回避(OOS 後に subgroup を増やさない、III)。

**Rationale**: 068 の recent guard は 3/5年点推定のみで 2026 単年・nk: を見逃す(codex 最重要)。subgroup CI で「全体改善だが 2026/nk: で死」を捕捉する。属性割当(race_date/prefix/厳密前観測数)は結果非参照で II 準拠。eval 内で完結(training 非依存)。

**Alternatives**: subgroup を採否の hard gate にする（2026 は開催日少で NO_DECISION 多発 → 誤否決）→ non-inferiority CI + NO_DECISION 許容に緩和。

## D5: features-018 純加算と serving 互換

**Decision**: FEATURE_VERSION features-017→018。F02 は **additive left-merge**(右キー horse_id×race_id 一意 + 列名 disjoint)で既存128列を数学的に摂動しない。`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"] = {"features-017": "300b28a9312a3fb6e171b1dfd38cc88413ccbae2a0cfa9936ed278b5d14b66ac"}`(lgbm-063 の feature_hash を pin)。共有128列の byte-parity を **構造的担保(additive-merge)+ 一度きり実測**(features-017 build == features-018 build を全128列 check_exact + check_dtype、058/061 同型)で保証 → lgbm-063 は compat-load で予測 byte 一致。

**legacy 列を物理削除しない**(codex):058 rank 4列・全128列は registry/build に残す(lgbm-063 preprocessor が要求)。F02 を含めない default モデルは recipe の drop_features で market-history 群を落とす。

**Rationale**: 058 案C' の per-model feature_hash 互換(exact/compat path)を再利用。additive なので 128列は不変、017 と違い value-changing でないため compat pin が安全。

## D6: モデル配置(accuracy-first candidate 限定)

**Decision**: F02 は 058 と同じく **accuracy-first candidate モデル(非active)** に入れる。default 意思決定支援モデルには market-history 群(058 + F02)を **drop_features で全 drop** し p⊥q(対象レース市場非入力)を維持。069 では default 化しない(provenance 監査が前提・ユーザー決定)。

**Rationale**: 過去市場込み p は「対象レース q を読まない」だけで統計的 p⊥q でない(codex)。二系統(意思決定支援 / 精度最優先)を保つ。default 化は provenance 列追加後の別判断。

## D7: odds provenance(監査のみ、列追加は別 spec）

**Decision**: race_horses に source/observed_at/finality 列は無く JRA-VAN final と netkeiba single-latest が混在(codex)。069 は **F02 を candidate 限定**とし、provenance 列追加はしない。代わりに SC-005 の coverage 監査で **年×ID source×coverage 帯の 1/3/5走 coverage** と **overround 分布・境界値率・popularity と q-rank の不一致率**を出力し、量特徴の不安定性を可視化する(復元不能な provenance を隠さない)。

**Rationale**: provenance 列は migration が要る(VI スキーマ変更)→ 069 スコープ外。candidate 限定 + 監査で当面の正直さを担保。

## D8: codex plan レビュー結果

親から `codex exec` 直叩きで取得。4 件の BLOCKER/HIGH + 論点整理。全採用し spec/plan/data-model/gate-config/contracts を修正。

### 採用(correctness-critical)

**C1(BLOCKER). subgroup の grain 未定義**。winner NLL は race-level(1レース1標本=勝者)だが、nk:/coverage は horse-level 属性。「nk: レース」を勝者が nk: のレースと定義すると winner-conditioned=結果依存のレース選択(リークではないが spec の『結果非参照』表現が不正確)。→ **grain を分離**: (a) **race-level winner NLL subgroup** は結果非依存の race 属性のみ = `2026_only`(race_date.year)・`2026×field_has_nk`(フィールドに nk: が居るか=結果非参照)。(b) **horse-level は started-all per-horse loss** で canonical/nk:・coverage 帯を評価(per-horse 属性は結果非参照)。ID source の死活は started-all per-horse で見るのが自然。spec の「subgroup 割当は結果非参照」を「race-level は race 属性・horse-level は per-horse 属性、いずれも結果非参照」に精緻化。

**C2(BLOCKER). margin=0 と NO_DECISION 非否決は両立しない**。margin=0 だと PASS=「subgroup で有意改善(CI 上限<0)」を要求 → 真の差0でほぼ PASS 出ない。逆に CI 未通過を全て NO_DECISION=非否決にすると gate 空洞化。→ **non-inferiority margin ε>0**(許容劣化、gate-config `non_inferior_margin`=0.005)+ **三値判定**: PASS=CI 上限<ε / FAIL=CI 下限>ε(確信をもって劣化)/ NO_DECISION=CI が ε を跨ぐ。**intersection-union gate**(全 critical subgroup の PASS を AND)= 多重比較補正不要(codex)。NO_DECISION は「否決しないが採用の十分条件にもしない」= adopted は全 critical が PASS 必須。

**C3(HIGH). 2026×nk 交互作用群の欠落**。`2026_only` と `nk` を別々に守っても `2026×nk` 劣化を相殺しうる。→ critical subgroup に **`2026_nk`(2026 かつ nk: horse、started-all per-horse)** を追加。`2026×nk×coverage=0` は audit-only。

**C4(HIGH). odds が現行 loader に無い**。features/loader.py は popularity(058)は読むが raw odds を読まない。F02 は q 計算に raw odds が必要 → **loader に `RaceHorse.odds` を追加 = 新ソース列**。→ 「新ソース列なし・source_fingerprint 不変」は**誤り**。056 前例どおり source_fingerprint を odds 込みに拡張し materialize-safe を再担保(migration 不要=odds 列は既存)。plan/spec の該当記述を訂正。

### 採用(設計明確化)

**C5. F02 採否は 068 paired_eval に一本化**。現行 `training feature-eval` は旧 020 binary LogLoss/ECE gate([cli.py:143])で winner NLL/bootstrap ではない。→ F02 は **068 paired-eval 経路**(candidate=features-018 full recipe vs active=features-018 minus-F02 recipe、両者 accuracy-first)で評価。feature-eval は使わない。contract を修正。

**C6. absolute 水準は subgroup 内 uniform 比**。paired 相殺の可視化は「subgroup NLL vs 全体 NLL」でなく **subgroup 内の candidate NLL − uniform NLL**(頭数・難度差を吸収)。診断併記。

**C7. subgroups.py の責務**。coverage 帯は horse_id/race_date だけでは出せない(厳密前観測数が要る)→ **呼び出し側が per-race/per-horse 属性を注入**、subgroups.py は band 割当・集計・gate 判定のみ。overround/odds 品質監査は subgroups.py に置かず coverage-audit(training 側)に。eval→training 境界は維持。

### Phase 0 未決(実装前固定)

odds sentinel の source 別意味(999.9/1.0 が JRA-VAN と netkeiba で同義か)は coverage-audit で確認し odds_valid_range を最終化。dead-heat/empty subgroup の扱い(winner NLL 母集団は 068 の eligible=勝者1頭に従う)。

### 必須テスト(tasks へ)

subgroup: race-level/horse-level grain 別・`2026_nk` 交互群・三値判定(PASS/FAIL/NO_DECISION)・intersection-union AND・missing annotation fail-close・dead-heat/empty 群 / odds: Decimal/string/NaN/inf/0/負/1.0/999.9・共通倍率不変・馬順不変 / compat: 旧017対新018の全128列 exact parity・wrong hash/version 拒否・lgbm-063 予測一致 / loader: odds 追加後の source_fingerprint stale 検出。
