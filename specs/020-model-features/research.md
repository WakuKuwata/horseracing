# Research: モデル改善 — リーク安全な特徴量拡張 (020)

Phase 0。**核心発見**: features には既に (a) `registry.FeatureMeta`（source/timing/missing = codex の言う feature
spec table）、(b) `history._cumulative_before`（daily `cumsum − 当日分` = 厳密に前 + **同日除外**が組込み）、
(c) `merge_asof(direction=backward, allow_exact_matches=False)`（同日除外の as-of）がある。020 はこの実証済み
リーク安全機構を**転用**して新特徴を足す（新規リーク機構を作らない）。codex top-3（跨馬統計の対象行/同日/out-of-fold・
fold 内選択・LogLoss+ECE ゲート）を機構化。

---

## R1: feature spec / 版管理（registry 転用）

**Decision**: 新特徴は `registry.REGISTRY` に `FeatureMeta(source, timing, missing_policy)` で登録＝codex の
feature spec table。`model_input_features()` が post_result を機械除外、`validate_columns` が未登録を fail。
各特徴に **group ラベル**（recent_form / aptitude / human_form / race_condition）を付与（ablation 用、registry
拡張 or 別マップ）。feature_version を bump（features-005）し model_versions に記録。

**Rationale**: 既存の登録機構がそのまま「feature spec table」。Unknown 欠損（MissingPolicy.NULL）も既定。

---

## R2: as-of / out-of-fold / 同日除外（既存パターン転用、codex #1/#A）

**Decision**: 馬の履歴系は `_cumulative_before` 同様 `daily cumsum − 当日分`（厳密前 + 同日除外）。前走系は
`merge_asof(backward, allow_exact_matches=False)`。**騎手・調教師フォーム（跨馬統計）も同型**: jockey_id/
trainer_id でグルーピングし daily `cumsum − 当日分` → 当日の全レース（対象レース＝対象行を含む）を除外＝
**target-row 除外 + 同日除外**が同時に成立。out-of-fold は walk-forward 境界（対象レースより前のみ）で担保。

**Rationale**: codex #1（騎手/調教師の target-encoding リーク回避）を**既存の同日除外機構**で機械的に満たす。
当日除外 = 対象行除外（対象レースは対象日にある）。新規ロジックを作らずリーク面を増やさない。

**Alternatives**: per-row leave-one-out 集計 → 複雑・誤りやすい、却下（daily-minus-current で十分）。

---

## R3: 新特徴 group（codex group ablation）

**Decision**: 4 group で追加（履歴共有 group を分離評価）:
- **recent_form**: 直近 N 走（既存 prev_finish に加え）avg_last3_finish・recent_win_rate（as-of、馬）。
- **aptitude**: 距離帯別・芝ダ別の as-of win_rate/avg_finish（馬）。
- **race_condition**: field_size（出走頭数）・class_transition（前走 race_class との昇/同/降）。
- **human_form**: jockey_form・trainer_form（as-of win_rate、当日/対象行除外、跨馬）。
全て MissingPolicy.NULL（過去不在は Unknown、0 代入しない）。

**Rationale**: codex #G — horse form と human form は履歴を共有するので group 単位 ablation が必須。

**Alternatives**: 一括追加 → どの group が効いたか判別不能、却下。

---

## R4: 候補固定 + fold 内ハイパラのみ（codex #2/#B、analyze F1 解消）

**Decision**: **候補特徴集合を事前固定**（既存特徴 + 新規9特徴）し、**OOS（検証 fold）を見て特徴を選択しない**。
walk-forward 各 fold で学習窓を inner train/validation に分割し、**ハイパラ選択・early stopping のみ**を inner で
完結。OOS（fold test）で「固定候補集合 vs baseline」を比較。採用時はその**同一固定集合**を全体再学習＝
**評価モデル＝デプロイモデルが一致**。group ablation（R3）は寄与把握の diagnostic で、採用特徴の選別には使わない。

**Rationale**: codex #2 + analyze F1。fold ごとに違う特徴を選ぶと「報告 OOS（fold 内選択）」と「単一デプロイ
モデル（固定集合）」が乖離する。候補固定なら (a) その乖離が無く、(b) OOS を見て特徴を選ばないので selection
leak が原理的に発生しない。035/036（片側 fold + 校正未確認）の false positive も fold 別差分（R5）で回避。

**Alternatives**: fold 内 feature selection（per-fold で特徴を選ぶ）→ 評価とデプロイの不一致 + nested CV の複雑性、
却下。全期間で一度特徴選択 → 選択リーク、却下。

---

## R5: 採用ゲート（codex #3/#C/#E/#F）

**Decision**: PRIMARY = baseline 比 **LogLoss 改善 かつ ECE 非悪化**（Brier 非悪化が望ましい、AUC は順位説明
限定）。**fold 別差分**（勝ち fold 数・最悪 fold・fold 別 ECE 差分）を必須化し、平均改善でも最悪 fold 悪化/
勝ち fold 少数なら不採用。**過学習対策**: 特徴数上限・正則化レンジ（min_data_in_leaf/lambda/feature_fraction/
num_leaves）を事前固定、fold 間で gain/SHAP/ablation の符号・順位が不安定な特徴を除外候補（importance のみで
決めない）。group ablation で group 寄与を分離。

**Rationale**: codex #3/#F。AUC 改善で校正悪化、平均改善で偶然 fold、を捕捉。035/036 = 片側 fold + 校正未確認。

---

## R6: SECONDARY diagnostic（codex #C/#D）

**Decision**: 採用候補で 011/016 pseudo-ROI/Kelly backtest（高分散、主ゲートにしない）+ 市場 q edge
（p−q calibration・edge bucket 別実現勝率・q 条件付き LogLoss）を diagnostic 出力。成功基準は **OOS win 改善**、
市場超過は努力目標（公開情報特徴は織り込み済みの可能性が高い）。

**Rationale**: codex #D。win 改善≠市場超過。期待値の現実的設定。

---

## R7: スキーマ・再現性

**Decision**: スキーマ変更なし。feature_version=features-005 を bump、model_versions に記録。決定論（同一
データ・同一 seed で評価再現）。市場オッズ/結果は特徴にしない（leak-guard）。

**Rationale**: 憲法 V/VI。既存 eval/training/prediction テーブルで完結。

---

## 設計判断サマリ（codex 反映）

| 論点 | 採用 | codex |
|---|---|---|
| 跨馬リーク | jockey/trainer も daily cumsum−当日（対象行+同日除外）+ walk-forward 前のみ | #1 → R2 |
| 選択リーク | 候補特徴を事前固定（OOS で特徴選択しない）、fold 内はハイパラ/early-stopping のみ、評価＝デプロイ一致 | #2/F1 → R4 |
| 採用ゲート | LogLoss 改善 かつ ECE 非悪化 + fold 別差分（勝ち/最悪/ECE 差） | #3 → R5 |
| group ablation | recent_form/aptitude/race_condition/human_form を分離 | #G → R3/R5 |
| 過学習 | 特徴数上限・正則化レンジ固定・fold 安定性、importance 単独不可 | #E → R5 |
| 効率市場 | 成功=OOS win 改善、市場超過は努力目標・diagnostic | #D → R6 |
| feature spec | registry.FeatureMeta（既存）+ group + cutoff test | #A → R1 |
