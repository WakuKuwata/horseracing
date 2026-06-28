# Contract: 血統適性特徴 (026)

features 内部契約（外部 API なし）。025 の materialization 契約を継承し血統ブロックを追加する。

## C1: builder 関数
```
build_pedigree_features(frames: Frames, *, min_starts: int = 10) -> DataFrame
# columns: [race_id, horse_id, sire_win_rate, sire_avg_finish, sire_starts,
#           sire_dist_band_win_rate, sire_surface_win_rate,
#           damsire_win_rate, damsire_avg_finish]
```
- per-(race_id, horse_id) 1 行。`_KEYS=[race_id,horse_id]` で他ブロックと merge 可能。
- frames.horses 欠損/空 → 全血統列 NaN（後方互換）。
- 決定論（入力同一→出力同一・dtype 固定）。
- `min_starts` は**特徴定義の一部の固定モジュール定数（既定 10）**。`build_asof_features`/`build_feature_matrix` の runtime 引数として上位へ通さない → materialize と in-memory が必ず同一値を使い bit パリティを壊さない。閾値変更は再 materialize（特徴再生成）を要する。

## C2: 集計契約（リーク・最重要）
- **strictly-before**: 対象レース日 D より前の finished のみ（当日 cumsum 除外）。
- **自馬除外**: `他産駒 = sire累積 − 自馬累積`（wins/cnt/finsum 各々）。
- **同日除外**: 同日他産駒の結果は当日除外で自動的に入らない。
- **未来非依存**: D 以降のレースは一切寄与しない。
- 条件付き率（dist_band/surface）: 他産駒 finished cnt < min_starts → NaN。
- 全体率: 他産駒 cnt 0 → NaN。`sire_starts` = 他産駒 finished cnt（0 可、ZERO_OK）。

## C3: registry / group
- REGISTRY に C1 の 7 列を source=`pedigree`, timing=`PRE_ENTRY`, missing=表(data-model) で登録。
- FEATURE_GROUPS: sire 5 列→`sire_aptitude`、damsire 2 列→`damsire_aptitude`。
- STATIC_COLUMNS に追加しない（materialized_columns 自動収録）。
- FEATURE_VERSION = `features-007`。
- leak-guard: 列名に odds/payout/dividend を含まない（test_materialize_columns）。

## C4: loader
- `Frames` に optional `horses: DataFrame`（default 空）。
- `load_frames` が horses（horse_id, sire_name, dam_name, damsire_name, sire_id, dam_id, damsire_id）を SELECT して同梱。

## C5: materialize 連携（025 契約継承）
- `build_asof_features` が history/extra/human_form/pace に加え pedigree を同一経路で merge。
- `source_fingerprint(frames, through=...)` が horses 血統列を含む。horses は through までの kept-race 出走馬に restrict。
- パリティ: `assemble_feature_matrix(use_materialized=True) == (=False)` を血統列含め `assert_frame_equal(check_exact=True, check_dtype=True)`。
- staleness: 血統列変更で fail-closed（MaterializationError）。
- serving 未来レース: has_future_rows → 単一レース fallback も build_asof_features 同一実装。

## C6: 採用評価（既存 eval/training 流用）
- `training feature-eval --drop-groups sire_aptitude,damsire_aptitude` で baseline=features-006、候補=features-007。
- AdoptionReport: PRIMARY=平均 win LogLoss 改善 AND ECE 非悪化 + strict majority + worst_fold_ece_tol(2e-3) + worst_fold_dll_tol(5e-3)。
- SECONDARY 診断: market_edge、prior_starts バンド別 OOS（採否に使わない）。

## C7: 不変条件（テストで保証）
1. leak: 自馬の過去/今走結果・同日他産駒・未来結果を変えても当該 target の血統特徴不変。
2. parity: materialize==in-memory bit 一致（血統列含む）。
3. staleness: 血統列後埋めで fail-closed。
4. columns: 血統列が materialized & static でない & odds/result トークン無し。
5. no-schema-change: migration head=0006、features に `__tablename__` 追加なし。
6. debut: 自馬実績ゼロでも sire_name があれば sire 特徴に値（他産駒由来）。
