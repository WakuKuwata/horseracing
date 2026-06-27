# Tasks: ライブ serving（未開催レースの予測・推奨生成）

**Input**: Design documents from `specs/019-live-serving/`
**Prerequisites**: plan.md, spec.md, research.md (R1–R7), data-model.md, contracts/live_serve.md, quickstart.md

**Tests**: 含む（憲法 II リーク / III 評価先行 / IV 整合 / V 監査は必須。pytest + testcontainers + 合成データ）

**Organization**: User story 単位（P1 US1 live 予測 → P1 US2 live 推奨(pre-race odds) → P2 US3 リーク無し検証 + prospective）。MVP=US1。

## パス規約

新規結線パッケージ `live/`（`horseracing-live`）。既存 scrape(008)/serving(006 run_serving)/betting(011,016)/db を
**再利用のみ・無改変**。スキーマ変更なし（head 0006）。確認済み: run_serving は as-of leak-safe（結果非参照・
同日除外・result-pending future race 安全）。scrape は urls+fetcher 前提のため orchestrator の scrape は任意
（無指定時は既存 DB 状態で動作、ガードが完全性を検証）。

---

## Phase 1: Setup（live パッケージ雛形）

- [X] T001 `live/pyproject.toml` を作成（name=horseracing-live、deps: horseracing-db/serving/betting/scrape、dev: pytest/testcontainers/ruff、[tool.uv.sources] で各 path editable）。`live/src/horseracing_live/__init__.py`（LIVE_LOGIC_VERSION 定数）
- [X] T002 [P] `live/tests/conftest.py`（betting/tests/conftest.py を踏襲: migrated PostgreSQL testcontainer @ head、session/engine fixture、truncate-between-tests）と `live/tests/_synth.py`（result-pending race + entries + pre-race odds + active model + 予測を投入するヘルパ。betting/probability の _synth を参考）

**Checkpoint**: live パッケージのビルド/テスト基盤が起動。

---

## Phase 2: Foundational（fail-closed ガード — 全 US 前提）

**⚠️ ガード（result-pending/valid id/完全性/odds）を確定。US1/US2/US3 全てが依存。**

- [X] T003 `live/src/horseracing_live/guards.py` を作成: `valid_race_id`（`^[0-9]{12}$`）、`is_result_pending`（race_results に当該 race 行が無い）、`entries_complete`（started≥1・horse_number 揃い・重複/頭数整合）、`odds_present`（対象出走集合に pre-race win オッズ）。各々 (ok: bool, reason: str) を返す（R2, FR-001/005/009）
- [X] T004 [P] `live/tests/unit/test_guards.py` を作成: 不正 id 拒否、結果あり→result-pending=false、started 欠落/重複→entries_complete=false、odds 欠損→odds_present=false を検証（SC-001）

**Checkpoint**: ガードが単体検証済み。

---

## Phase 3: User Story 1 - 未開催レースの live 予測（Priority: P1）🎯 MVP

**Goal**: result-pending race を guard→（任意 scrape）→run_serving で予測・永続化。走行済み/不完全は fail-closed。

**Independent Test**: 合成の result-pending race で live_serve（予測まで）が prediction_run を生成、結果あり/不正 id/部分取得は拒否、結果変更で予測不変（リーク境界）。

### 実装

- [X] T005 [US1] `live/src/horseracing_live/orchestrate.py` に `live_serve(session, *, race_id, model_version=None, scrape_entries_url=None, scrape_odds_url=None, recommend=True, ...)` の**予測部**を実装: guard（valid_race_id/result_pending）→ **URL 指定時のみ** 008 `scrape_entries`/`scrape_odds`（PoliteFetcher）、無指定は既存 DB 状態で続行（race_id→URL 自動逆引きはしない、deferred）→ guard（entries_complete）→ `run_serving(race_id, model_version)` で予測・永続化。違反は書込なしで拒否し理由を `LiveServeReport` に格納（R1/R3/R4, FR-001/002/005/006）
- [X] T006 [US1] `live/src/horseracing_live/cli.py` に `live-serve <race_id> [--model-version --no-recommend --scrape-entries-url --scrape-odds-url ...]` と `list-pending --date <d>`（result-pending かつ valid race_id を列挙）を実装（contracts/live_serve.md, FR-015）

### US1 テスト

- [X] T007 [P] [US1] `live/tests/integration/test_live_predict.py` を作成（合成データ）: result-pending race で予測生成・prediction_run 永続化、結果あり/不正 id/started 欠落で fail-closed（書込なし）、新馬/unmapped が出走頭数に含まれ Σ 整合（009 経由）、`race_results` 変更で予測不変（リーク境界）を検証（SC-001/SC-005/SC-006）

**Checkpoint**: US1 単独で動作・テスト緑（MVP）。

---

## Phase 4: User Story 2 - 未開催レースの live 推奨（pre-race odds）（Priority: P1）

**Goal**: live 予測に続き pre-race odds → 010 推定 → 011/016 推奨を生成、使用オッズ値 + computed_at を保存、Kelly は shadow。

**Independent Test**: live 予測済み race で推奨が estimated（double-pseudo）で使用オッズ値付き保存、odds 欠損で推奨 0 件（予測は保持）、shadow 明示。

### 実装

- [X] T008 [US2] `live/src/horseracing_live/orchestrate.py` の `live_serve` に**推奨部**を追加: guard（odds_present）→ `generate_exotic_recommendations`(011) / `generate_kelly_recommendations`(016) を prediction_run に対し実行（race_horses.odds=pre-race → 010 estimated）。013/017 校正器 opt-in。Kelly は shadow（記録のみ・実資金執行なし）として report に明示。odds 欠損時は推奨スキップ（予測は保持、FR-007/008/009/016, R5）
- [X] T009 [US2] `live/src/horseracing_live/orchestrate.py` に `LiveServeReport`（race_id/mode/guards/scrape counts/prediction_run_id/recommendations 数/odds_as_of/computed_at/shadow）を実装し CLI で表示（data-model.md §3）

### US2 テスト

- [X] T010 [P] [US2] `live/tests/integration/test_live_recommend.py` を作成（合成データ）: 推奨が is_estimated_odds=true（double-pseudo）で使用オッズ値（estimated_market_odds_used）+ computed_at を保存、odds 欠損で推奨 0 件・予測は残る、Kelly stake_fraction が記録され shadow フラグが立つ、校正器 opt-in が logic_version に出る、**推奨生成後に scratch した出走を含む買い目が void/skip される（F2、011/012 規約）**ことを検証（SC-002/SC-003, FR-010）

**Checkpoint**: US2 単独で動作・テスト緑。pre-race odds 推奨が成立。

---

## Phase 5: User Story 3 - リーク無し検証と prospective ログ（Priority: P2）

**Goal**: 過去レースで live 経路と retrospective の予測 p 一致、リーク境界、後日 backtest 可能な prospective ログ。

**Independent Test**: 過去レースで live_serve 予測 == 直接 run_serving の p、結果変更で予測不変、生成物が computed_at + 使用オッズ値で残る。

### 実装

- [X] T011 [US3] `live/src/horseracing_live/orchestrate.py` に prospective ログ出力（生成した予測・推奨を computed_at + 使用オッズ値で `LiveServeReport` に集約、後日 007/011/016 backtest 投入可能な形）を確定（R6, FR-014）

### US3 テスト

- [X] T012 [P] [US3] `live/tests/integration/test_parity.py` を作成（合成・過去レース）: live_serve の予測 p が直接 `run_serving` と完全一致（live==retrospective、リーク無し）、**オッズ依存の推奨は過去パリティ対象外**（過去 pre-race odds 非保持）を明示する assertion を含む。**生成された prediction_run/recommendations が既存 backtest（007/011/016）の入力形式を満たす（prospective 投入可能、F3）**ことを軽く確認（FR-012/FR-014, SC-004/SC-008）
- [X] T013 [P] [US3] `live/tests/integration/test_leak_and_determinism.py` を作成: `race_results` を変更しても当該 race の予測が不変（リーク境界）、同一 entries・同一オッズ値・同一 model/calibrator で 2 回実行し予測・推奨が完全一致（決定論）を検証（FR-011/FR-013, SC-005/SC-007）

**Checkpoint**: 全 P1+P2 完了。リーク無し・prospective 評価可能。

---

## Phase 6: Polish & Cross-Cutting

- [X] T014 [P] `live/tests/unit/test_no_schema_change.py`（or 既存ガード拡張）で live が書く先が既存テーブル（prediction_runs/race_predictions/recommendations）に限られ新規 migration が無いことを確認（FR-015, SC-009）
- [X] T015 `specs/019-live-serving/quickstart.md` を実行: 実 DB で `list-pending` + 合成 result-pending race の `live-serve`（予測+推奨）を確認（[[local-db-setup]]、ネットワーク不可なら合成テストで代替）
- [X] T016 [P] `live/` の lint/test を通す（`uv run ruff check src tests` / `uv run pytest`）
- [X] T017 [P] `CLAUDE.md` に 019 の 1 行サマリを追記（011–018 と同形式: live 結線層・run_serving 再利用・fail-closed(result-pending)・cutoff=race_date・使用オッズ値保存・p パリティ+prospective・shadow Kelly・スキーマ変更なしを要約）

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001→T002[P]。
- **Phase 2 (Foundational)**: Setup 後。T003→T004[P]。**全 US をブロック**（ガード）。
- **Phase 3 (US1, MVP)**: Foundational 後。T005→T006、テスト T007[P]。
- **Phase 4 (US2)**: US1（orchestrate 予測部）後。T008→T009、テスト T010[P]。
- **Phase 5 (US3)**: US1/US2 後。T011、テスト T012/T013[P]。
- **Phase 6 (Polish)**: 全実装後。T014/T016/T017[P]、T015。

### User Story 独立性

- US1 は live 予測で独立（MVP）。US2 は推奨（US1 の orchestrate に推奨部を足す）。US3 は検証（live==retrospective パリティ + リーク + prospective）。

## Parallel 実行例

- Setup: T002[P]。Foundational test T004。各 US テスト T007/T010/T012/T013[P]。Polish: T014/T016/T017[P]。

## 実装戦略

1. **MVP first**: Phase 1→2→3（US1）で「result-pending race の fail-closed live 予測」を最短達成。
2. **推奨**: US2 で pre-race odds → estimated 推奨（使用オッズ値保存・shadow Kelly）。
3. **検証**: US3 で p パリティ（live==retrospective）+ リーク境界 + prospective ログ。
4. 各 Checkpoint で独立テスト緑。憲法 II（run_serving as-of・結果非参照・odds 非特徴）/ III（パリティ+リーク+prospective）/ IV（check_consistency・Σ整合）/ V（computed_at+使用オッズ値）/ VI（スキーマ変更なし・結線層分離）を維持。
