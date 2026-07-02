# Research: コーナー通過順の軌跡特徴 (041)

## R1: なぜ「位置の変化」が新情報か

**Decision**: late_gain(最終コーナー→着順の伸び)・mid_move(コーナー間押し上げ)・early_pos(初角位置)の 3 生スコアを as-of 集約。

**Rationale**: 023 は位置の**水準**(rel_corner_pos_avg=最終コーナー相対位置の平均、front_runner_rate/closer_rate=脚質率)のみ。**変化**(何頭抜いたか)は「末脚の実効性」を水準と独立に表す — 同じ平均位置でも「直線で毎回3頭抜く馬」と「位置を守るだけの馬」は別物。spike(2019+ 3fold, cond_logit 経路): winner-NLL 2.1087→2.1052・top1 +0.0020・AUC +0.0010・全 fold 改善・カバレッジ 89%。同時検証の rank/gap(レース内 percentile)は flat = GBM が既に学習済みの変換は冗長、**モデルが持たない生情報(デルタ)だけが効く**(031-033 の学びと一貫)。

**Alternatives considered**: コーナー別個別遷移(2角→3角等)は列数増の割に情報が薄い見込みで deferred。通過順系列の embedding は §4 級の工数で deferred。

## R2: as-of 機構(spike の近似を production で厳密化)

**Decision**: runs 構築は 023 `_pace_runs` 同型(finished + started、race_date 付与、field_size=started 数)。expanding 集約(cumsum/cumcount/cummax)で per-run as-of 値を作り、**merge_asof(on=race_date, by=horse_id, direction=backward, allow_exact_matches=False)** で対象行へ。

**Rationale**: spike は shift(1)(レース順)近似だったが、production は 023 と同一機構で strictly-before + **同日除外** を構造的に担保(憲法 II)。023 の `_rolling_asof` は recent-N rolling なので流用せず expanding 版を追加(全過去走: 軌跡は安定特性でウィンドウ切りの根拠なし、spike も expanding)。

**Alternatives considered**: recent-N rolling — spike で検証しておらず、事前登録の観点から spike と同じ expanding を採用(rolling 変種は deferred)。

## R3: finish_order の利用はリーク境界内か

**Decision**: 過去走の finish_order を late_gain のラベルとして使用(今走は非参照)。

**Rationale**: history(avg_finish/prev_finish)・023(finish_diff_avg/best)と完全に同じ既存境界 —「過去走の結果」は対象レース前に既知。merge_asof(allow_exact_matches=False)で今走・同日は構造的に入らない。leak-guard test で担保。

## R4: source_fingerprint / スキーマ影響なし

**Decision**: 新ソース列なし、fingerprint 無改修。

**Rationale**: corner_orders/finish_order/result_status は 023 で loader 済み+fingerprint 包含(list 列対応は 025 の test_fingerprint_handles_list_columns で実証済)。race_horses(entry_status)・races(race_date)は当初からロード。

## R5: 採否ゲートと production 構成の関係

**Decision**: 採否は従来どおり `feature-eval --drop-groups corner_trajectory`(030-033 と同一基準・同一閾値)。採用時の最終モデル lgbm-041 は cond_logit+TE+isotonic(現行 production 構成)で train-evaluate。

**Rationale**: feature-eval は predictor-agnostic の既定経路(binary)で群の限界寄与を測る確立ゲート。spike は cond_logit 経路で信号を確認済みなので両経路の整合は期待できる。ゲート機構を feature ごとに変えない(憲法 III、比較可能性維持)。

## R6: 併走 spike 結果の記録(2026-07-02 ブレストキューの決着)

- **binary×cond_logit ensemble**: full 17-fold で mix50 は LogLoss −0.0003 だが mean ECE 3 倍悪化(0.00089→0.00292)、blend 再校正は LogLoss ゲイン破壊(0.21793>cl 単独)+ECE 未回復・9/17 fold → **不採用**(校正=製品価値を LogLoss 微益で売る悪い取引)。
- **レース内 rank/gap**: flat(ΔwinnerNLL −0.0001)→ **不採用**(GBM+cond_logit が既に学習)。
- **corner 軌跡**: 正(本 feature)。
- **PL top-k(top-3, w=1/.5/.25)**: winner-NLL 2.1087→**2.0992**(−0.0095)・top1 +0.0064・AUC +0.0035・全 fold 改善 → **次 feature(042)として実装予定**(objective 拡張、039 infra 流用)。

## R7: 採用結果(2026-07-02 確定)

- **feature-eval(18-fold, 事前登録ゲート)= ADOPTED(機械的通過)**: LogLoss 0.23191→0.23185・AUC 0.75186→0.75211・**ECE 0.00883→0.00858(改善)**・13/18 fold・worst_dLL +0.00038(<5e-3)・worst_dECE +0.00089(<2e-3)。026 以来の全ガード素直クリア。
- **lgbm-041(production 構成 cond_logit+TE+isotonic+features-012)**: win LogLoss 0.21794(039 比 −0.000006)・ECE 0.00093(039: 0.00096)・Brier/top2/top3 微改善。**強いモデル上の限界寄与は僅少**(binary ゲート経路の −0.00006 より小さい)が全指標非悪化〜微改善。
- 実 DB parity bit 一致(917,554×97)・カバレッジ 89.0-89.5%・serving ロード確認(95 特徴・feature_hash=features-012)。
- lgbm-041 active 昇格・lgbm-039 retired。
