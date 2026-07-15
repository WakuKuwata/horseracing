# Research: Evaluation Contract v2 & Historical Freeze

**Feature**: 073 | **Date**: 2026-07-15 | codex レビュー: `docs/plan/codex-073-review.md`

各決定は「Decision / Rationale / Alternatives」で記す。実コード根拠は `file:line` で示す。

## D1. split の recipe 意味論化と既存 active の hash 互換

**Decision**: `ModelRecipe`([recipe.py:31](../../training/src/horseracing_training/recipe.py)) に `calibration_split_unit: str = "race_count_v1"` を追加。`predictor.py:165` の `split_train_by_time` 呼び出しを recipe 由来の分岐にし、`race_count_v1`→既存 `split_train_by_time`(distinct race, [calibration.py:27](../../training/src/horseracing_training/calibration.py))、`race_day_v1`→既存 `split_train_by_day`([calibration.py:54](../../training/src/horseracing_training/calibration.py))。`recipe_hash` は **back-compat canonicalization**: 値が legacy 既定 `race_count_v1` のときは `meta()` に含めず(既存 recipe_hash 文字列と byte 一致)、`race_day_v1` のときのみ含める(hash と model_version が必ず変わる)。

**Rationale**: 068 の「日単位 split 完了扱いだが本番は race-count」不一致は、暗黙既定でなく recipe 明示で根治するのが最小・最堅(codex #1)。両 split 関数は既に存在するため新ロジックはゼロ。既存 active は `race_count_v1` として recipe_hash 不変=058 の hash pinning と同型。**serving 予測バイト(SC-005)は保存済み booster/calibrator artifact 由来で recipe_hash に非依存**なので、field 追加は予測を変えない(FR-012 は独立に成立)。

**Alternatives**: (a) 本番 predictor を即 day-split に切替 → 校正母集団が変わり再学習必須=精度変化と契約変化が交絡(codex が明示排除)。(b) field を常に hash に含める → 既存 active の recipe_hash が変わり compat/fail-closed 参照が壊れうる。→ back-compat canonicalization を採用。

## D2. 採用ゲートを単一三値 enum に統合

**Decision**: `paired.py` の `GateResult`([paired.py:54](../../eval/src/horseracing_eval/paired.py))を `adopted: bool` から単一 enum `decision ∈ {ADOPT, REJECT, NO_DECISION}` に拡張(後方互換: `ADOPT` ⇔ 旧 `adopted=True`)。`_build_gate`([paired.py:150](../../eval/src/horseracing_eval/paired.py))が main gate と `_compute_subgroups`([paired.py:199](../../eval/src/horseracing_eval/paired.py))の subgroup 三値(既存 `subgroups.three_way`)を統合:
- `ADOPT` = main PASS かつ全 critical subgroup PASS
- `REJECT` = 主指標 FAIL または十分標本の critical subgroup が FAIL
- `NO_DECISION` = 評価期間不足 / 開催日不足 / critical subgroup 標本不足 / 必須データ欠損

`eval_window`(069 gate-config の top-level に既存)・`no_decision_min_days`(069 gate-config の **`subgroup_guard` 配下**に既存、値 10)を実判定に結線し、空 window・underpowered subgroup を黙って PASS させない。confirmatory mode では未知/欠落 config・評価期間不一致・gate-config hash 不一致を型付きエラー(fail-closed)。

**Rationale**: 069 は既に subgroup 側で三値 intersection-union を導入済み([paired.py:263](../../eval/src/horseracing_eval/paired.py) `subgroup_guard`)。それを主判定に昇格し boolean AND(`primary and stat_guard and recent_guard and top_ni and calibration`, [paired.py:165](../../eval/src/horseracing_eval/paired.py))を単一 enum に置換すれば operator 判断を排除できる(codex #4)。

**Alternatives**: main gate と subgroup を別出力のまま CLI で合成 → operator 依存が残る(現状の問題そのもの)。→ 単一 enum に統合。

## D3. started-all を harness 本体へ統合

**Decision**: `harness.py:99` `_score_race`(現状 "finished horses only" [harness.py:101](../../eval/src/horseracing_eval/harness.py))に started-all 経路を統合。paired.py の `_started_all_arrays`([paired.py:101](../../eval/src/horseracing_eval/paired.py))と同一意味論(DNF/失格=win0、started 全馬)を harness 本体でも選べるようにし、監査 artifact に started-all 集合と除外理由を記録。

**Rationale**: 学習は started 全馬なのに評価が finished のみ=母集団不一致(068 の未完了項目、codex #5)。paired 側限定を解消し harness 本体でも整合させる。

**Alternatives**: paired 側限定のまま → 068 の母集団不一致が残る。→ 本体統合。

## D4. bootstrap 改名 + v2 感度 + 過去 verdict 凍結

**Decision**: `moving_block_bootstrap_ci`([bootstrap.py:32](../../eval/src/horseracing_eval/bootstrap.py))を `race_day_cluster_bootstrap_ci_v1` に改名し**数値を完全維持**(golden test で byte 一致)。旧名は deprecation alias を残さず呼び出し元を全置換(内部 API のため)。v2 感度として 2/3/4 開催日・開催週・開催単位 block を追加(block 重複/端点/休催日/複数場同時開催の定義を事前固定)。全感度を gate の AND にせず primary estimator を 1 つ事前登録・残りは diagnostic。068/069/070 の既存 verdict は `evaluation_contract_version=v1` の不変履歴として保持し、v2 再計算は参考再生のみ。

**Rationale**: 実体は block 長 1 日の cluster bootstrap で moving block ではない(codex)。改名で誤称を正し、感度で block 幅依存を可視化。過去 verdict の遡及変更は III 違反(codex #6)。

**Alternatives**: moving block を真に実装 → 数値が変わり過去 verdict と非互換=遡及変更。→ 改名(数値維持)+ v2 感度を diagnostic に。

## D5. ECE のサブセット分割と tail mask(074 依存部の切り分け)

**Decision**: ECE を全体 + 確率帯 + odds 帯 + p 帯 + q 帯 + **事前登録共通 tail mask**(または active/base 由来 result-blind mask)で測定。各帯は固定境界・欠損 bucket・最低件数/最低開催日数・NO_DECISION 規則を持つ。arm 固有 tail(candidate 自身の bet 対象)は評価集合が arm ごとに動くため **diagnostic に降格**。本 feature の評価段は **raw booster score** と **model 内部 calibration + race normalization 後の win probability** までに限定。two-gamma 後 win prob と stage discount 後 top2/top3 prob の ECE は **074**(OOF-faithful 校正)完成後。

**Rationale**: stage discount は win でなく top2/top3 の校正、arm 固有 tail は confirmatory 比較を壊す(codex #7)。two-gamma/stage discount 後の ECE は現状 latest-run 世代非限定([model_calibration.py:232](../../probability/src/horseracing_probability/model_calibration.py))で非OOS のため、単に測っても信頼できない → 074 に依存させる。

**Alternatives**: 全段の ECE を本 feature で測る → 074 前提の校正リークで数値が非OOS=偽の校正評価。→ 段を限定。

## D6. 070 status 凍結と prospective holdout DORMANT

**Decision**: 070 の正確な status matrix(F03/F04/F05 = rejected/unwired、`registry.py:207` 実態)を、過去文書を書き換えず commit/verdict artifact hash を参照する **append-only supersession 記録**として固定。2008–2026 を development evidence と明記。prospective holdout は仮説/式/閾値/primary metric/停止条件/time-to-signal のフォーマットを用意するが状態は **DORMANT**(または AWAITING_CAPTURE)。時計は capture 稼働・immutable recipe・停止規則・最初の対象レースが揃って初めて開始(本 feature では開始しない)。

**Rationale**: 過去文書の書き換えは監査性を損なう。holdout を STARTED にするとデータ 0 で偽の「進行中」になる(codex #9)。

**Alternatives**: 070 の過去 verdict を書き換えて統一 → 監査性喪失。holdout を即 STARTED → 空計器の偽進行。→ append-only + DORMANT。

## D7. 将来 ROI 台帳は憲法 V 改定が前提

**Decision**: market_snapshot / decision_attempt / decision_bet / settlement を伴う ROI 台帳 feature は、**憲法 V(オッズはスナップショット履歴を保存せず最新値で上書き)の改定**を前提とする旨を spec 依存節に記録。本 feature では扱わない。

**Rationale**: 台帳は判断時オッズ snapshot を履歴保存する=現憲法 V と衝突([constitution.md:77](../../.specify/memory/constitution.md))。憲法改定なしに台帳 feature を起こせない(codex #10)。

## D8. active model version を DB で確定

**Decision**: FR-010/SC-005 の「既存 active」は着手時に**実 DB で確定**する(068 文書は DB active=063、リポジトリ上 062/063 は model/calibrator/preprocessor の SHA-256 同一だが version を推測固定しない)。確定できない場合は着手をブロック。

**Rationale**: parity oracle を間違った version に固定すると SC-005 が無意味(codex)。

## 未解決(plan では NEEDS CLARIFICATION なし)

なし。全論点を D1–D8 で確定。tasks 段で back-compat canonicalization の実装形と三値 enum の後方互換マッピングを詳細化する。
