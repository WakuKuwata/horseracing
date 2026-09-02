# Research: 騎手の時変切片 confirmatory (107)

## D1: 評価窓 — 2022-01-01..2024-12-31(採点年 2022/2023/2024・3 outer fold)

**Decision**: `first_valid_year=2022`・`--to 2024-12-31` の walk-forward。screening が使った
2025-01-01 以降は採点に含めず、driver が `eval_window.to < 2025-01-01` を構造 assert する。

**Rationale**: 選択リークの排除が唯一の必須条件。年単位 outer fold は spike と同じ
`predict_over_folds` の自然な形で、約 10,300 レース / 約 321 開催日(JRA は年 106〜109
開催日)= spike(5,680 レース)より検出力が高い。窓の選定にラベル・効果数値は使っていない(「2025 未満で最も新しい 3 年」
という日付だけの規則)。

**Alternatives considered**: (a) 2019-2024 の 6 年 — 検出力は上がるが実行時間 2 倍・
古い時代への希釈。(b) 097 型の疑似 cutoff 3 本 — 本 feature は供給シミュレーションでは
ないので不要な複雑さ。(c) 2025-26 を guard として併用 — 選択済み窓の再利用になるので不可。

**限界(明記)**: 2022-24 は nk: ID 時代の前(nk: started 行 2024=0%)。serving 時代
(2026・nk: 混在)への可搬性はこの測定では検証できない — ADOPT でも昇格は標準昇格ゲート
(直近窓非劣化)を別途要求する(FR-012)。

## D2: 判定機構 — 公式 `paired_eval` に spike の factory を直接渡す

**Decision**: `horseracing_eval.paired.paired_eval(candidate, active, ...)` は
`PredictorFactory` を直接受ける。confirmatory driver は
(a) `assert_confirmatory(cfg, expected_hash, eval_window)`(hash・窓・契約版・seed_noise の
fail-closed)→ (b) `paired_eval(候補 factory, arm E factory, gate_config=凍結 cfg)` →
(c) `final_decision` の三値 → (d) 証拠 artifact 保存、の薄い結線のみ。ゲート・CI・
seed 膨張・証拠は一切再実装しない。

**Rationale**: 088/100 の教訓 =「同じ契約の二重実装」が最大の欠陥源(106 でも再演)。
公式機構は v4 の総 CI(`inflate_for_seed_noise`)・証拠(per-race 生値からのビット一致
再計算)・窓照合を既に持つ。

**Alternatives considered**: spike スクリプトに判定を足す独自 driver — 二重実装の禁止形。
ModelRecipe 化して既存 CLI に載せる — 候補は recipe で表現できない(カスタム predictor)
ため ADOPT 後の本実装でのみ検討。

## D3: アーム定義 — A=arm E(CalibSplitFactory)/ 候補=730 日窓 b

**Decision**: 候補 factory は spike の `JockeyPooledPredictor`(window_days=730)を
`scripts/` から**輸入せず**、driver スクリプト内に同一定義で持つ(spike と定数・手続きが
一致することをテストで固定)。凍結定数: W=730 / MIN_RIDES=30 / λ クランプ [10,500] /
経験ベイズ λ=1/τ̂² / 露出加重中心化 / isotonic は調整後スコアで再 fit(校正母集団は
全 OOF 行で両アーム同一)。アーム B(静的 b)は confirmatory では走らせない(判定に
使わない対照を落とし実行時間を 2/3 に)。

**Rationale**: 候補手続きは screening で凍結済み(FR-001)。B は機構診断であって
confirmatory の estimand ではない。

## D4: gate-config — v4 契約・δ は 100 の導出を参照

**Decision**: `evaluation_contract_version: "v4"`・`min_effect_delta: 0.0035188338580500285`
(`delta_derivation_ref` → specs/100-eval-contract-v5/delta-derivation.json・
`assert_delta_provenance` を driver で実行)・`seed_noise.sd_fold: 0.001816`
(scripts/seed_variance_probe.py 2026-08-18 の実測を再掲・k_seeds=1)・
bootstrap b=2000 / seed=20260902 / alpha=0.05・`eval_window {from: 2022-01-01,
to: 2024-12-31, min_eval_days: 300}`(開催日は年 106〜109・3 年で約 321 日 — 当初の 900 は
誤算で analyze C1 が検出・097 C1 と同型)・`subgroup_guard.critical_subgroups: []`
(トップレベルに置くと誰にも読まれない死にキーになる — analyze H2)。
**verdict の正本 = `final_decision` の三値**(独自式のオーバーレイなし)。

**Rationale**: δ を測定ノイズから導き直さない(100 US4 の fail-closed)。critical
subgroups を空にするのは 2026/nk 軸が 2022-24 窓で構造的に測定不能なため —
空でないデフォルトに任せると `critical_subgroup_not_computed` の偽 NO_DECISION になる。
その代償(serving 時代の死角)は D1 の限界として開示し、昇格ゲートで補う。

**Alternatives considered**: 窓内で測れる subgroup(頭数帯等)を critical に置く —
騎手切片の既知の失敗モードと対応しない軸を veto にすると偽 REJECT のリスクだけ増える。
開示(非 critical)としては計算する。

## D5: 実行前監査(FR-007)

**Decision**: driver が judgment の前に (a) 各 fold の window 内 nk: 騎手 ID 件数
(b) 評価レース騎乗の b カバレッジ (c) fold 別 λ/騎手数/クランプ発動 を JSON で保存。
いずれも判定に使わない開示。

## D6: 縮退・衛生ガード

**Decision**: (a) 両アーム予測完全一致 → abort(097 型・spike に実装済みの形)
(b) `load_eval_races(start_date=FEATURE_POOL_START)` 必須(pre-2007 残存 71,549 件の
混入で OOF が落ちる — 2026-09-02 実測)(c) 窓 assert(to < 2025-01-01)。

## D7: seed — 単一 seed 42 + sd_fold 膨張(v4 標準)

**Decision**: 097/098/099 と同じ v4 標準形。複数 seed バンドルの実測は行わない。

**Rationale**: v4 契約の総 CI は sd_fold 膨張で再学習分散を織り込む設計で、これが
097-099 の判定と同じ物差し。マージンが薄い候補に seed を増やして CI を縮める操作は
「分散が減る方向の変更はバグでも良い結果に見える」(100 R9)の型であり、やるなら
US3(100)の再事前登録が筋。

## D8: ADOPT 時の本実装(条件付き・概略)

**Decision**(verdict=ADOPT のときのみ着手): training に `jockey_intercept.py`
(推定純関数+テスト)を正規モジュール化し、学習時に b/λ/window メタデータを model
artifact に凍結、serving は artifact の b を raw score へ外部加算+logic_version に
`;jti=w730` マーカー。opt-in(recipe フィールド既定 None=既存 hash 不変・099 の
`NEW_HASH_DEFAULT_OMISSIONS` 機構)。候補モデル登録→標準昇格ゲート。詳細設計は
verdict 後に tasks の ADOPT 分岐で。

## D9: REJECT/NO_DECISION 時の後始末

**Decision**: driver・監査 JSON・gate-config・verdict・証拠を specs/107 に保全。
結線差分はもともとゼロ(測定は scripts/ 完結)なので revert 対象は無し。spec に実測を
転記し、閉鎖範囲(設計族限定)を明記。memory 更新。

## D10: codex 品質ゲート — unavailable

`codex exec` が 2026-09-02 に 2 回とも `failed to initialize in-process app-server client:
Operation not permitted (os error 1)` で起動不能(read-only / workspace-write 両 sandbox)。
**codex unavailable: CLI 初期化エラー(復旧は別タスク化済み)**。代替:
(a) スパイク段階の設計レビュー(選択リーク・EB 暴走・estimand 明示・閉鎖範囲・
101 との整合)を採否つきで反映済み (b) 本 plan のセルフレビュー checklist:
- [x] 判定機構は公式 paired_eval/final_decision のみ(二重実装ゼロ)
- [x] 凍結対象(W/MIN_RIDES/クランプ/窓/δ/seed)は実行前に gate-config へ・事後変更禁止
- [x] 選択済み窓(2025+)は採点に不使用・構造 assert あり
- [x] 縮退 abort・pre-2007 混入防止・δ provenance の fail-closed
- [x] 閉鎖の主張範囲が設計族に限定されている
- [x] 差分ゼロ(測定段階): モデル/特徴/FEATURE_VERSION/スキーマ/API/買い目/009 不変
