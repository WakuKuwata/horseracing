# Implementation Plan: 低コスト特徴拡充 (Low-cost Feature Expansion)

**Branch**: `030-low-cost-features` | **Date**: 2026-06-29 | **Spec**: [spec.md](spec.md)

## Summary

DB に 99〜100% あるのに未活用の安価でリーク安全な特徴を 5 group 追加（020 同型・ablation 分離）: **handicap(斤量)** / **place_rate(複勝率)** / **human_form_plus(人拡充)** / **course_aptitude(コース)** / **season(季節)**。025 materialization 基盤に as-of 群、`build_static_features` に静的群を載せる。FEATURE_VERSION features-007→008。採用は **各 group 独立の事前登録 OOS ゲート**（codex Q4）。脚質/展開(running_style=結果由来)・draw_bias(冗長/市場織り込み)・grade(スパース)は除外/deferred。

## Technical Context
**Language**: Python 3.12 (features/eval/training)。**Deps**: pandas/numpy, LightGBM(欠損 NaN), pyarrow(025)。**Storage**: PostgreSQL read-only, 既存列のみ（jockey_weight を新規ロード）。**スキーマ変更なし(head 0006)**。**Testing**: pytest(make_frames 単体 + materialize parity/leak)。**Constraints**: bit パリティ・リーク境界・float64 固定。

## Constitution Check
- [x] **I データ契約**: 既存 ID/列のみ。**PASS**
- [x] **II リーク防止(NON-NEGOTIABLE)**: 斤量/season=今走既知(静的)。place/人/course は strictly-before＋対象行/同日除外(020/human_form 同型)。**running_style(corner_orders=結果 由来)は今走利用しない**(実コード+codex Q1 確認)。odds/popularity 非特徴。leak-guard test。**PASS**
- [x] **III 評価先行(NON-NEGOTIABLE)**: 各 group 事前登録・walk-forward OOS・PRIMARY=win LogLoss 改善 AND ECE 非悪化+fold ガード。ablation 診断。**PASS**
- [x] **IV 確率整合性**: win→joint(009) 不介入・Unknown 維持。**PASS**
- [x] **V 再現性**: parquet 非コミット・決定論・manifest features-008。**PASS**
- [x] **VI 分割規律**: スキーマ変更なし・契約先行。**PASS**
- [x] **品質ゲート**: codex Q1-Q5 取得・反映(research)。**PASS**

## 設計詳細

### group と配置（静的 vs as-of）
| group | 列 | 配置 |
|---|---|---|
| handicap | carried_weight, carried_weight_ratio, carried_weight_rel | **静的**(build_static_features) |
| handicap | carried_weight_change (今走−直前 started race 斤量) | **as-of**(materialized, merge_asof) |
| season | race_month, race_season | **静的** |
| place_rate | place_rate(top2), show_rate(top3), dist_band_place_rate | as-of 自馬 |
| human_form_plus | jockey_place_rate, trainer_place_rate, jockey_recent_win_rate, jockey_surface_win_rate, jt_combo_win_rate, jockey_change | as-of 跨馬(対象行+同日除外) |
| course_aptitude | venue_win_rate, venue_place_rate | as-of 自馬 |

静的群(handicap 3列+season 2列)は `STATIC_COLUMNS` に追加（materialize しない）。as-of 群は registry に登録→`materialized_columns()` 自動収録。先例: race_condition 群が field_size(静的)+class_transition(as-of) と混在＝group 内の静的/as-of 混在は許容。

### 実装機構（既存流用）
- **loader**: `race_horses` の SELECT に `jockey_weight` 追加（既存 race_horses テーブルの未ロード列）。fingerprint は race_horses の全ロード列をハッシュするので自動的に含まれる（新ソース無し、FR-008）。
- **静的(static_features.py)**: carried_weight=jockey_weight、ratio=jockey_weight/weight(馬体重欠損→NaN伝播)、rel=jockey_weight − レース内平均、race_month=race_date.month、race_season=月→季節区分。
- **as-of(新 `lowcost_features.py`)**:
  - place_rate/show_rate: `_cum_before_by`(extra_features)で top2/top3 フラグの strictly-before 平均(自馬・同日除外)。dist_band_place_rate は (horse_id,dist_band) 条件付き。
  - carried_weight_change: `merge_asof(backward, allow_exact_matches=False)` で直前 started race の jockey_weight → 差分。
  - human_form_plus: human_form の (cumsum−当日) 機構を jockey/trainer の複勝・直近・(jockey,track_type)・(jockey_id,trainer_id) コンビに拡張。jockey_change=今走 jockey_id ≠ 直前 started race jockey_id。
  - course_aptitude: (horse_id, venue_code) の as-of 勝率/複勝率。母数<min_starts→NaN。
- 全 030 列 float64 固定（パリティ）。
- **materialize.build_asof_features** に lowcost as-of ブロック追加（単一源）。

### registry / version
- 7 as-of 列 + 5 静的列 を REGISTRY 登録、FEATURE_GROUPS に group 付与、静的 5 列を STATIC_COLUMNS へ。FEATURE_VERSION=`features-008`。**版 bump 波及**: `test_materialize_core.py`/`test_feature023_leak_guard.py` の features-007 リテラルを 008 に。

### 採用プロトコル（事前登録・per-group, codex Q4）
- `eval/feature_eval.evaluate_feature_adoption` は candidate/baseline の2 predictor を取る（既存）。CLI を拡張: `feature-eval --drop-groups <baseline> --candidate-drop-groups <cand>`（候補側 drop を追加, 既定 none）。
- 各 group g: baseline=features-007(=all-030 を drop)、candidate=features-007+g(=all-030 except g を drop)。g 単独が同一ゲートを通れば採用。出荷 features-008 = features-007 + 通過群の和集合。**group/列/fold/baseline/閾値を eval 前に凍結**（OOS を見て取捨しない）。`feature-ablation` は診断のみ。
- 採用後 serving 再学習(lgbm-030)。

## Project Structure
```text
features/src/horseracing_features/
├── loader.py            # MOD: race_horses に jockey_weight
├── static_features.py   # MOD: carried_weight/ratio/rel + race_month/season
├── lowcost_features.py  # NEW: place_rate/carried_weight_change/human_form_plus/course_aptitude (as-of)
├── registry.py          # MOD: 12 列 + 5 group + STATIC_COLUMNS + FEATURE_VERSION=008
└── materialize.py       # MOD: build_asof_features に lowcost ブロック

features/tests/unit/
├── test_lowcost_features.py   # NEW: 各群の正しさ(斤量/複勝/人/コース/season)
├── test_lowcost_leak.py       # NEW: 今走結果/同日/未来 不変・running_style 非使用
├── test_materialize_core.py / test_feature023_leak_guard.py  # MOD: features-008 リテラル

eval/src/horseracing_eval/feature_eval.py   # (必要なら) candidate drop 対応は CLI 側で吸収
training/src/horseracing_training/cli.py     # MOD: feature-eval に --candidate-drop-groups、既定 drop=030 群
```

**Structure Decision**: 静的群は static_features、as-of 群は新 lowcost_features＋materialize 結線。loader に jockey_weight 1 列追加。eval コアは不変（CLI で candidate drop を構成）。

## Complexity Tracking
| Violation | Why | Rejected alternative |
|---|---|---|
| feature-eval に candidate-drop 追加 | per-group 独立評価(codex Q4)に candidate=features-007+g が必要 | bundle 一括のみ(027 で希釈の前例)→ 何が効いたか不明 |
| handicap 群が静的+as-of 混在 | carried_weight_change は前走依存(as-of)、他は今走(静的) | 全部 as-of 化は静的値の二重計算で無駄。race_condition 群の前例に倣う |
