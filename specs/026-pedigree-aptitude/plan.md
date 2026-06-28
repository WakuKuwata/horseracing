# Implementation Plan: 血統適性 as-of 特徴 (Pedigree-Aptitude Features)

**Branch**: `026-pedigree-aptitude` | **Date**: 2026-06-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/026-pedigree-aptitude/spec.md`

## Summary

種牡馬（sire）を主軸に、「対象レース日より前・対象馬自身を除いた“他の産駒”の走力」を as-of 集計したリーク安全な血統適性特徴を追加する。020/023 の自馬・人（騎手/調教師）由来の公開情報特徴では届かない「市場が情報を持ちにくいデビュー馬・少数出走馬」に効かせるのが狙い。025 の feature materialization 基盤（`build_asof_features` 単一 as-of 源・parquet/manifest・fail-closed staleness・use_materialized opt-in）の上に新ブロックとして載せる。

**技術アプローチ（実 DB 実態に基づく）**: 集計キーは **`sire_name`/`damsire_name`（名前、~100% populate）**。`sire_id` は実 DB で 0% のため使わない（ID 版 deferred）。リークの核は「**sire 累積（cumsum−当日）− 対象馬自身の累積**」で “他産駒のみ・strictly-before” を O(n) で得る方式（per-pair 展開なし）。距離帯/芝ダート別も同じく sire 条件付き累積 − 自馬条件付き累積。Unknown=NaN 維持、条件付き率は `min_starts` 未満で NaN。FEATURE_VERSION を features-006 → **features-007** に bump。採用は 020/023 同型の walk-forward OOS ゲート。

## Technical Context

**Language/Version**: Python 3.12（features パッケージ、uv 管理）

**Primary Dependencies**: pandas / numpy（既存 as-of 機構の流用）、pyarrow（025 parquet）、LightGBM（欠損 NaN を扱える＝Unknown 維持の前提）、eval の AdoptionReport（採用ゲート）

**Storage**: PostgreSQL 16（read-only、horses の既存 sire_name 列）。parquet は artifacts 配下・非コミット（025）。**スキーマ変更なし（migration head=0006 不変）**

**Testing**: pytest（features 単体・DB-free な make_frames、integration は testcontainers）。leak-guard / parity / staleness は必須

**Project Type**: 単一 Python パッケージ拡張（`features/`）。training/eval/serving は `build_feature_matrix` 経由で透過

**Performance Goals**: materialize 生成は 025 の ~30s 規模に血統 cross 集計の許容増分（目安: 全体生成 ≤ ~90s、追加メモリ ≤ 数百 MB）。read 経路（学習/評価/serving）は parquet read

**Constraints**: bit パリティ（materialize == in-memory, `assert_frame_equal(check_exact=True, check_dtype=True)`）、fail-closed staleness、リーク境界不変（憲法 II）

**Scale/Scope**: 実 DB 920k race_horses 行、種牡馬 1,721、産駒 finished-starts 分布 p25=10/p50=37/p75=182/p90=1279

## Constitution Check

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: raceId 12 桁・2007+ は既存 loader を踏襲。血統は horses の既存名前列を消費、id_mappings は名前キーには無関係（ID 版 deferred）。ラベル不変。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 全血統特徴に source=`pedigree`・timing=`PRE_ENTRY`・missing=`NULL`/`ZERO_OK` を registry 宣言。as-of は strictly-before（当日 cumsum 除外）＋**対象馬自身除外**（自己強化防止）。オッズ・今走結果は特徴にしない。leak-guard テスト（自馬過去/今走・同日他産駒・未来 の不変）必須。walk-forward 境界は eval が適用。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 候補は事前固定（OOS で特徴選択しない）。walk-forward OOS、PRIMARY=平均 win LogLoss 改善 AND ECE 非悪化 + fold ガード。評価ハーネス（eval/feature_eval.py）は既存を再利用＝学習より先に存在。**PASS**
- [x] **IV. 確率整合性**: win→joint(009) に介入しない。LightGBM/binary・Unknown 維持。血統は win 入力特徴のみ。**PASS**
- [x] **V. 再現性・監査**: parquet 非コミット・DB から決定論再生成。manifest に血統込み source_fingerprint・FEATURE_VERSION=features-007・範囲・行数。**PASS**
- [x] **VI. feature 分割規律**: UI 無し（features 内部のみ）。スキーマ変更なし。API/DB 契約に影響なし。**PASS**
- [~] **品質ゲート**: codex second opinion を 2 回起動したが、当環境で codex-rescue の background 機構が結果を返さず取得不能（launch 直後にタスク消失）。設計判断は実 DB 実態確認＋020/023/human_form の既存実装パターン照合で自己検証。plan フェーズでも再試行し、届けば reconcile して plan/tasks に差分記録。**PARTIAL（codex 不達を明記）**

**Gate result**: PASS（品質ゲートのみ codex 不達のため PARTIAL、理由明記済み）。

## 設計詳細

### データソース（実 DB 確認済み）
- `horses.sire_name`/`dam_name`/`damsire_name` = ~100%（94,223/94,231）。`*_id` = 0%（2 行）→ 名前キーを採用、ID 版 deferred。
- 集計母集団: race_results（finished）× horses（horse_id→sire_name）。

### リーク安全な集計アルゴリズム（核心）
1. `runs` に horses を join し各出走に sire_name/damsire_name を付与（020/human_form の `_runs` 拡張）。
2. **sire 全体**: `_cum_before_by(runs, ["sire_name"])`（cumsum−当日）で (sire_name, date) の strictly-before 累積 wins/cnt/finsum。
3. **自馬全体**: `_cum_before_by(runs, ["horse_id"])` で (horse_id, date) の strictly-before 累積。
4. **他産駒のみ** = sire 累積 − 自馬累積（wins/cnt/finsum をそれぞれ差し引き）。`sire_win_rate = (w_sire−w_self)/(c_sire−c_self)`、分母 0 → NaN（自馬が唯一の産駒なら NaN＝正しい）。`sire_avg_finish = (finsum_sire−finsum_self)/(c_sire−c_self)`。`sire_starts = c_sire−c_self`（信頼度、ZERO_OK）。
5. **距離帯別 / 芝ダート別**: 同方式を (sire_name, dist_band) と (horse_id, dist_band)、(sire_name, track_type) と (horse_id, track_type) で行い差し引く。対象レースの dist_band/track_type で条件付け。`min_starts` 未満（他産駒の finished cnt）→ NaN。
6. **damsire**（任意 group）: sire と同型で damsire_name を全体のみ（dist/surface は薄いので全体 win_rate/avg_finish に絞る、ablation で寄与確認）。

> dist_band（`_DIST_BINS`）・track_type は 020 extra_features の既存定義を再利用（新区分は作らない）。

### 列とグループ（registry 追記）
- `sire_aptitude`（必須）: `sire_win_rate`(NULL) / `sire_avg_finish`(NULL) / `sire_starts`(ZERO_OK) / `sire_dist_band_win_rate`(NULL) / `sire_surface_win_rate`(NULL)
- `damsire_aptitude`（任意・ablation-gated）: `damsire_win_rate`(NULL) / `damsire_avg_finish`(NULL)
- 全列 source=`pedigree`, timing=`PRE_ENTRY`。STATIC_COLUMNS に含めない → `materialized_columns()` が自動収録。odds/payout/dividend/今走結果 トークン無し（leak-guard）。

### 025 連携
- **loader**: `Frames` に **optional** `horses` フィールド追加（default 空 DF＝既存 `Frames(...)` 呼び出し・make_frames が壊れない）。`load_frames` で horses（horse_id, sire_name, dam_name, damsire_name, sire_id, dam_id, damsire_id）を SELECT。
- **materialize.build_asof_features**: sire(+damsire) ブロックを既存 history/extra/human_form/pace と同じ単一経路で merge（FR-007）。
- **source_fingerprint**: horses の血統列（sire_name/dam_name/damsire_name + *_id）を含める拡張（FR-010）。`_restrict` は horses を「kept races の出走馬 horse_id」に絞って通す（未来馬で fingerprint が誤発火しないように）。
- **make_frames**（テスト）: specs の horse dict に任意 `sire_name`/`damsire_name` を受け、horses フレームを合成（既定 None＝既存テストは血統 NaN）。

### FEATURE_VERSION
- features-006 → **features-007**（新シグナル＝出力変化）。025 のパリティは「同一 FEATURE_VERSION 内で materialize==in-memory」であり、026 は両経路とも features-007 を出すのでパリティは維持（version 据え置きではなく bump、と 025 の infra-only とは区別）。

### 採用ゲート / 診断
- `training feature-eval --drop-groups sire_aptitude,damsire_aptitude` で baseline=features-006、候補=features-007。AdoptionReport（既存）: PRIMARY=平均 win LogLoss 改善 AND ECE 非悪化、fold=strict majority(n_win*2>n_folds)+worst_fold_ece_tol(2e-3)+worst_fold_dll_tol(5e-3)。
- SECONDARY 診断（採否バーでない）: market_edge（市場 q 超過）、prior_starts バンド別 OOS（021 の few/some/many と整合、血統が効く層の可視化）。

## Project Structure

### Documentation (this feature)
```text
specs/026-pedigree-aptitude/
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── pedigree-features.md   # 列契約・集計契約・registry/group・fingerprint 拡張
└── tasks.md             # /speckit-tasks
```

### Source Code
```text
features/src/horseracing_features/
├── pedigree_features.py     # NEW: build_pedigree_features(frames) — sire/damsire as-of (self-excluded)
├── loader.py                # MOD: Frames に optional horses + load_frames で horses SELECT
├── registry.py              # MOD: sire_aptitude/damsire_aptitude group + 列 + FEATURE_VERSION=features-007
└── materialize.py           # MOD: build_asof_features に pedigree ブロック + source_fingerprint 拡張(horses)

features/tests/
├── _frames.py               # MOD: horses 合成(sire_name/damsire_name, 既定 None)
├── unit/test_pedigree_features.py   # NEW: 集計正しさ(他産駒・距離/馬場・min_starts・debut)
├── unit/test_pedigree_leak.py       # NEW: 自馬過去/今走・同日他産駒・未来 の不変(US3)
├── unit/test_materialize_columns.py # MOD/NEW: 血統列が materialized & leak-token 無し
├── unit/test_materialize_core.py    # MOD: parity に血統列含む / fingerprint が horses 反映
└── integration/...          # parity / staleness(血統 backfill) 実データ寄り

eval/ training/              # 変更なし（既存 feature-eval を --drop-groups で流用、CLI は既存）
```

**Structure Decision**: 単一パッケージ拡張。新規ファイルは `pedigree_features.py` と血統テスト 2 本のみ。残りは既存ファイルへの追記（loader/registry/materialize/_frames）。eval/training は既存 CLI（feature-eval --drop-groups）で透過。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 集計キーに名前(sire_name)を使用 | 実 DB で sire_id 0%・sire_name 100% | ID キーは理想だが実データが無く評価不能。ID 版は scrape の血統 ID 解決後に移行(deferred) |
| Frames に optional horses 追加 | 血統は races/race_horses/race_results に無い第 4 ソース | 既存 3 フレームに血統列を相乗りさせると horse 粒度↔race_horse 粒度が崩れる。optional default で後方互換維持 |
