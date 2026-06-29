# Contract: 低コスト特徴拡充 (030)

features 内部契約。025 materialization 契約を継承。

## C1: 静的群 (build_static_features 拡張)
- 追加列: carried_weight, carried_weight_ratio, carried_weight_rel, race_month, race_season。
- 今走 row のみから決定（jockey_weight/weight/race_date）。carried_weight_rel=jockey_weight − 同 race の started 平均。馬体重欠損→ratio NaN。float64。
- loader: race_horses に jockey_weight を SELECT 追加。

## C2: as-of 群 (新 lowcost_features.py、build_asof_features に結線)
```
build_lowcost_features(frames, *, min_starts=...) -> DataFrame[race_id, horse_id, <as-of 列>]
```
- carried_weight_change: merge_asof(backward, allow_exact_matches=False) で直前 started race の jockey_weight → 今走−前走。前走なし NaN。
- place_rate/show_rate/dist_band_place_rate: `_cum_before_by` で top2/top3 の strictly-before 平均(自馬・同日除外・cumsum−当日)。
- human_form_plus: human_form 機構(cumsum−当日=対象行+同日除外)を jockey/trainer 複勝・(jockey,track_type)・(jockey_id,trainer_id) コンビに拡張。jockey_recent=rolling。jockey_change=今走 jockey vs 直前 started race jockey(merge_asof)。
- course_aptitude: (horse_id, venue_code) as-of 勝率/複勝率、母数<min_starts→NaN。
- 全列 float64 固定。

## C3: registry / version
- 12 列を source/timing=pre_entry/missing=NULL 登録。FEATURE_GROUPS: handicap/season/place_rate/human_form_plus/course_aptitude。
- 静的 5 列(carried_weight,ratio,rel,race_month,race_season) を STATIC_COLUMNS へ。as-of 7 列は materialized_columns 自動収録。
- FEATURE_VERSION=features-008。features-007 リテラルの既存テストを 008 に更新。
- leak-guard: 列名に odds/payout/dividend 無し。running_style/corner/finish_order を**今走分**は使わない。

## C4: 採用評価 (eval/training)
- `feature-eval` に `--candidate-drop-groups` 追加（candidate=full − cand_drop）。既存 `--drop-groups`=baseline drop。
- per-group g: candidate-drop=(030 群 − g)、drop-groups=030 全群 → candidate=features-007+g vs baseline=features-007。事前登録ゲート通過で g 採用。
- `feature-ablation` 診断。市場 q 超過は採否外。

## C5: 不変条件(テスト)
1. correctness: 斤量(値/差/比/相対)・複勝率・人複勝/コンビ/乗り替わり・venue率・season。
2. leak: 今走 結果(着順/corner/running_style)・同日他レース・未来 を変えても 030 列不変。running_style を一切参照しない(grep)。
3. parity: materialize==in-memory bit 一致(030 as-of 列含む)。
4. columns: 静的 5 列が STATIC_COLUMNS、as-of 7 列が materialized、odds/result トークン無し。
5. dtype: 030 列 float64。
6. no-schema-change: head 0006。
