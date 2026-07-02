# Feature Specification: 検証済み p 校正の推奨経路組込 (Calibrated Recommendations)

**Feature Branch**: `046-calibrated-recommendation` / **Created**: 2026-07-02 / **Status**: Draft
**Input**: 017(モデル p 校正 + edge haircut)は eval で両ゲート通過・「有効化推奨」(γ は MLE 任せ)だが、**製品の推奨経路(recommend-serve/backfill→ops/front)には未結線**で、CLI の opt-in フラグ(--p-gamma 等)でしか使えない。検証済みの校正を製品の買い目生成に既定で組み込む。

## 背景と目的
Kelly は確率誤差を増幅する(f*=edge/(O−1))。017 はモデル p の系統的過信/過小信を race-normalized power(p'∝p^γ、γ=winner-NLL MLE)で補正し、eval 両ゲート(校正改善+Kelly リスク非悪化)を通過済み(メモリ [p-calibration-017-enabled]: 2024Q4 で γ=1.30>1=シャープ化が正解、方向を決め打ちせず MLE 任せが正しい)。しかし 043/045 の製品経路は素の p のまま。**新しい校正ロジックは作らない** — 017 の既存部品(load_p_samples/fit_p_calibrator/apply_p_calibrator、min_races=50/min_wins=30 未満は identity fallback)を製品経路に結線するだけ。

## User Stories
### US1 - 製品の推奨生成が walk-forward p 校正を既定適用 (P1)
recommend-serve/recommend-backfill が、対象レースより**厳密に前**の永続化済み予測×結果から γ を都度フィット(不足時 identity)し、exotic+Kelly(016)と win(007/045)の両方に適用して生成する。適用有無と γ は logic_version に記録され、監査から再現できる。
**AC**: (1) 校正付き生成で logic_version に pcal 記録 (2) 学習サンプルは対象レースより厳密前のみ(リーク境界テスト) (3) サンプル不足時は identity で従来値と一致 (4) backfill は日単位で1回フィット(同日除外=日厳密前)し全レースに適用

### US2 - win 経路の校正対応 (P1)
007 `generate_recommendations` が p_calibrator を受け、started 再正規化後の p ベクトルに race-normalized 校正を適用してから EV 選定・Kelly sizing する(kelly_recommend と同一規律)。
**AC**: (1) calibrator 有で EV/stake が校正 p 由来 (2) None で従来とバイト同等(後方互換) (3) lv に pcal 追記

### Edge Cases
- サンプル不足(min_races/min_wins 未満)→ identity(sufficient=False、lv に記録)= 安全側で従来動作。
- 予測が無い期間 → load 範囲を「予測が存在する最古日」から bound(全期間スキャン回避)。
- dead heat は fit から除外(017 既存)。q/オッズ側は触らない(p≠q、013 は scope 外)。
- haircut は既定 none のまま(017 の役割分離、有効化推奨はされていない)。

## Requirements
- **FR-001**: 製品の推奨生成(serve/backfill)は、対象レース(または対象日)より厳密に前の永続化済み予測×結果のみから p 校正器をフィットし(選定リークなし)、不足時は identity に fallback しなければならない。
- **FR-002**: 校正は exotic+Kelly(016)と win(007)の両生成に同一の校正器で適用され、方式・γ・fallback 有無が logic_version に記録される(再現性 V)。
- **FR-003**: 007 は p_calibrator を opt-in で受け、None のとき従来挙動とバイト同等でなければならない(045 と同じ後方互換規律)。
- **FR-004**: 校正は race-normalized ベクトルに適用する(017 canonical、marginal 単体に γ を掛けない)。q・オッズ経路には適用しない(p≠q)。
- **FR-005**: 新しい校正ロジック・スキーマ変更・API 変更を追加しない。read-only 014 不変・ops 経路不変(subprocess の中身が変わるだけ)。

## Success Criteria
- **SC-001**: 実 DB で recommend-serve/backfill 生成行の logic_version に pcal(γ or identity)が記録される。
- **SC-002**: リーク境界テスト: フィットサンプルに対象レース(日)以降が混入しない(split_before/日厳密前)。
- **SC-003**: identity fallback 時、生成される推奨は校正なしと同値。calibrator=None の 007 は従来テスト緑のまま。
- **SC-004**: betting/api 全スイート緑・migration head 不変。

## Assumptions
- 017 の fit は「レース単位の最新 run の p×勝者」を使う(モデル混在は 017 CLI と同一の既存挙動、base_model_version を監査記録)。
- 製品既定=校正 ON(identity fallback があるため安全)。無効化ノブは deferred。
## Deferred
013 q 側 FL 補正の製品組込 / haircut の既定有効化 / γ の条件別・オンライン更新 / 無効化フラグ
