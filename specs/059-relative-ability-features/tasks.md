# Tasks: Within-race relative-ability features (059)

**Feature dir**: `specs/059-relative-ability-features/` | **Branch/worktree**: `059-relative-ability-features`

**Design refs**: [plan.md](plan.md) · [research.md](research.md)（D1 最終13列・D2 LOO・D7 self-review）·
[data-model.md](data-model.md) · [quickstart.md](quickstart.md)

**Tests**: 含む（憲法 III/品質ゲート: leakage / bit-parity / OOS 評価は必須）。

**テンプレ元**: Feature 031 pace_scenario（`relative_ability_features.py` ≈ `pace_scenario_features.py`、
`_loo_mean` 流用、単一結線 `build_asof_features`）。

**不変（全タスク共通の禁止事項）**: migration なし・API/OpenAPI/スキーマ不変・009 導出/postprocess 不変・
`STATIC_COLUMNS` 不変・`source_fingerprint`/`_HORSE_FP_COLS` 不変・新ソース生列を読まない・0 埋め禁止・
float64 固定。

---

## Phase 1: Setup

- [X] T001 worktree `059-relative-ability-features` が features-013 ベース（`grep FEATURE_VERSION features/src/horseracing_features/registry.py` == features-013、vectorized pl_topk 存在）であることを確認

## Phase 2: Foundational

（新規の共有基盤なし。既存 `build_asof_features` 単一 as-of 源・031 `_loo_mean` を再利用するため
Foundational フェーズは空。US1 から着手する。）

---

## Phase 3: User Story 1 — 相対能力群を build に組み込む (P1)

**Goal**: `relative_ability` 13列を registry/build/materialize に結線し、bit-parity と leak-guard を満たす。

**Independent Test**: features スイート緑（unit + bit-parity + leak-guard）、`build_feature_matrix` が
新群込みでリーク安全に生成、materialize==in-memory bit 一致。

### 実装

- [X] T002 [US1] `features/src/horseracing_features/relative_ability_features.py` を新規作成:
  `RELATIVE_ABILITY_COLUMNS`（data-model.md の13列）、`_DEV_INPUTS`（11 能力列）、`_RANK_INPUTS`
  （win_rate, rel_time_avg）を定義。`build_relative_ability_features(frames, *, ability_frame)` が
  (race_id, horse_id) キーで 13列を返す。`ability_frame`（＝merged `out`）は non-started 馬も含むため、
  冒頭で `frames.race_horses[entry_status]` を merge し `is_started`（`_loo_mean` が要求）を付与する。
  deviation は 031 `_loo_mean`（started & 非NaN・self 除外・他馬非NaN数0→NaN）を各 `_DEV_INPUTS` 列に
  適用し `<col>_vs_field` を生成。rank は **started 母集団のみ**で算出する: rank 前に
  `col.where(is_started == 1)` で non-started を NaN マスクし、`groupby("race_id")[col].rank(pct=True)`
  （NaN はランク付けされず NaN のまま）で `<col>_field_rank`（`groupby("race_id").rank(pct=True)` を
  full frame にそのまま適用すると non-started も母集団に入り research D3 の「started 母集団」に反する）。
  全列 float64・NaN 維持。docstring にリーク境界（strictly-before as-of 列の within-race 後処理のみ・
  生列非参照）を明記。
- [X] T003 [US1] `features/src/horseracing_features/registry.py`: `REGISTRY` に13列を
  `FeatureMeta("relative", _T.PRE_ENTRY, _M.NULL)` で追加、`FEATURE_GROUPS` に13列→`"relative_ability"`、
  `FEATURE_VERSION = "features-014"`。`STATIC_COLUMNS` は変更しない（materialized_columns() が自動包含）。
- [X] T004 [US1] `features/src/horseracing_features/materialize.py` `build_asof_features`: 全ブロック
  merge 済みの `out` に対し `build_relative_ability_features(frames, ability_frame=out)` を呼び、結果を
  `out` に `merge(on=_KEYS, how="left")`。既存の `cols = [*_KEYS, *materialized_columns()]` 選択で新群を拾う
  （選択行の変更不要）。`build_pace_scenario_features` 直後あたりに配置。

### テスト

- [X] T005 [P] [US1] `features/tests/unit/test_relative_ability_features.py` 新規: 小さな合成
  ability_frame で (a) `win_rate_vs_field` == 手計算の self−LOOmean、(b) 単騎/全馬NaN→NaN、
  (c) NaN 馬混在時の分母（非NaN他馬数）、(d) `win_rate_field_rank` の percentile 値・同値平均順位、
  (e) 13列すべて float64・出力キー一致、(f) **non-started（取消）馬を含むフィールドで rank が started
  母集団のみで計算され（non-started は分母に入らず自身は NaN）、deviation の LOO も started のみを見る**
  ことを検証。
- [X] T006 [P] [US1] `features/tests/unit/test_relative_ability_leak.py` 新規（`test_pace_scenario_leak.py`
  をテンプレ）: 対象レースの (a) 全馬 finish_order/result_status、(b) odds、(c) 同日別レース結果、
  (d) **未来レース行の追加**（as-of 性=pool-end 非依存）を改変/追加しても、対象レースの13列が
  bit 不変であることを assert。
- [X] T007 [US1] `features/tests/unit/test_registry.py` の期待列/群/FEATURE_VERSION を features-014・
  relative_ability 13列に更新（registry 整合テスト）。
- [X] T008 [US1] materialize bit-parity: `test_materialize_core.py` / `test_asof_real_db.py`（integration,
  実 DB）が新群込みで `assert_frame_equal(check_exact=True, check_dtype=True)` 緑を確認。必要なら
  期待列数・parquet 再生成を反映（`features materialize` → features-014）。

### 検証（US1 完了判定）

- [X] T009 [US1] `uv run --project features pytest -q` 緑（unit+leak+parity）、`ruff check` クリーン。
  実 DB で `features materialize --out ../artifacts/features.parquet`（features-014）成功、
  旧 parquet は fail-closed で検知されることを確認。

---

## Phase 4: User Story 2 — 採用ゲート + pl_topk overlap 検証で採否確定 (P1)

**Goal**: 事前登録ゲートを実 DB で再現し、本番 pl_topk で lgbm-056（0.21615）超えを確認、採用時 lgbm-057。

**Independent Test**: `feature-eval --drop-groups relative_ability` が primary_pass、`model-eval`
（pl_topk）候補 win LogLoss < 0.21615。

### 実装（結線）

- [X] T010 [US2] `training/src/horseracing_training/cli.py` の feature-eval 既定 drop-group
  （`_DEF_056 = "pace_first3f,owner_breeder,race_level,sire_line"`）は**変更しない**。T003 で
  `FEATURE_GROUPS` に `relative_ability` を登録すれば、`--drop-groups relative_ability` を明示指定した
  ときに `gcols` 経由で新群 13 列だけが drop され baseline=features-013 相当・candidate=features-014 と
  なる（T011 の呼び出しがそのまま成立）。既定を書き換えると bare `feature-eval` の baseline 意味論が
  変わるため触らない。**このタスクは「T003 で群が登録済みなら CLI 側は変更不要」の確認のみ**
  （`--drop-groups relative_ability` が `gcols.get("relative_ability")` で 13 列に解決されることを確認）。

### 検証（ゲート）

- [X] T011 [US2] 事前登録 feature-eval を実 DB 実行:
  `uv run --project training python -m horseracing_training feature-eval --drop-groups relative_ability`。
  結果（LogLoss/AUC/ECE/fold）を tasks.md 追記。spike 13列版の目安（LogLoss≈−0.0011・AUC≈+0.004・
  19/19）を再現し `primary_pass=True` を確認。
- [X] T012 [US2] 本番 pl_topk overlap 検証（feature の pl_topk 下での価値を直接測定 + lgbm-057 生成）:
  **⚠ `model-eval --objective pl_topk` は使わない**: candidate=pl_topk・baseline=**binary**
  （`cli.py:279` は objective 無指定）を比較するため objective 差を測る用途で、**feature の価値を
  測れない**（candidate/baseline とも全特徴で drop なし）。代わりに候補を本番構成で walk-forward 学習し、
  **lgbm-056 と同一プロトコル**（pl_topk+isotonic+OOF-TE jockey/trainer・同 fold/seed、差は
  features-013→014 のみ）の OOS を出す:
  `uv run --project training python -m horseracing_training train-evaluate --objective pl_topk
  --calibration isotonic --target-encode jockey_id,trainer_id --model-version lgbm-057`
  （nohup+監視、~20 分）。得た OOS **win LogLoss / top2 / top3 / ECE** を **lgbm-056 の記録値**
  （win **0.21615**・top2 **0.34003**・top3 **0.43037**）と直接比較（同一プロトコル=特徴のみ差、有効）。
  **結果をユーザーに提示**（win<0.21615 かつ top2/top3 非悪化なら採用推奨、縮めば 023/039/056 型判断）。
  併せて（任意・軽量）binary の label 別確認が要れば `evaluate_feature_adoption(..., label="top2"/"top3")`
  で top2/top3 A/B も取得可（既定 label="win"、`feature_eval.py:50`）。

### 採用（ユーザー承認後）

- [X] T013 [US2] ユーザー承認後、T012 で学習済みの **lgbm-057 を昇格**: `model_versions` で lgbm-057
  active / lgbm-056 retired（DB 更新）。**追加学習は不要**（T012 の train-evaluate が artifact
  `artifacts/model_versions/lgbm-057/` を生成済み）。採用ゲート PASS を artifact metadata で確認。
- [ ] T014 [US2] serving 疎通: `serving predict --race-id <rid>` が `feature_version: features-014`・
  feature 列 +13 をロードすることを確認。

---

## Phase 5: Polish & Cross-Cutting

- [X] T015 [P] 回帰 + 全 ruff: `uv run --project features/training/serving/eval pytest -q` 緑
  （列追加でのゲート/経路回帰なし）、各パッケージ `ruff check` クリーン（SC-005 の全パッケージ
  スイープ=features だけでなく training/serving/eval も）。
- [X] T016 [P] drift-check / migration head 不変を確認（API/OpenAPI 不変・head assert 変更なし）。
- [X] T017 spec/plan の Status 更新（実装完了サマリ）、CLAUDE.md の 059 ポインタを「実装完了」に更新、
  memory（feature-059 結果 + MEMORY.md index）を記録。採否・実測値・overlap 検証結果を明記。
- [ ] T018 worktree を main へマージ（fast-forward）、worktree 撤去。

---

## Dependencies & Order

- US1（T002–T009）→ US2（T010–T014）。US1 が build を通さないとゲートを回せない。
- T005/T006/T007 は [P]（別ファイル）。T002→T003→T004 は同一群の逐次依存。
- T011→T012→（承認）→T013→T014 は逐次（ゲート結果でユーザー判断が入る）。
- Polish（T015–T018）は US2 完了後。

## MVP scope

US1（T002–T009）= 特徴が build/materialize にリーク安全に載る最小価値。US2 で採否確定・本番化。

## 品質ゲート対応（憲法）

- II リーク: T006 leak-guard。III 評価先行: T011/T012 OOS ゲート + bit-parity T008。IV: 009 不変
  （特徴追加のみ・T012 top2/top3 非悪化）。V: T013 FEATURE_VERSION 記録。VI: migration/API 不変。
- codex: unavailable（plan D7 self-review 済み）。実装中に復帰すれば T012 の overlap 判断で second opinion。
