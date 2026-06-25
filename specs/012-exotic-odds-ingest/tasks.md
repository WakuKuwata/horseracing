# Tasks: 実 exotic オッズ取込と疑似→実 ROI 化

**Input**: Design documents from `specs/012-exotic-odds-ingest/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/exotic_odds_ingest.md, contracts/real_roi_wiring.md, quickstart.md

**Tests**: 含む(憲法 III 評価先行 + II リーク + V 監査。パーサ/取込/突合/実 ROI/乖離はテスト必須)

**Organization**: User story 単位(P1 US1 取込 → P1 US2 配線 → P1 US3 乖離 → P2 US4 CLI)。MVP=US1。

## パス規約

`db`(スキーマ)→ `scrape`(取込、008 再利用)→ `betting`(配線/評価、011 拡張)の 3 層。全パスはリポジトリルート相対。

---

## Phase 1: Setup(スキーマ基盤・新テーブル)

- [x] T001 [P] `db/src/horseracing_db/enums.py` に `CoverageScope`(FULL/PARTIAL/ALL)を追加し、`db/src/horseracing_db/constraints.py` に `EXOTIC_BET_TYPE`(win 除外 6 券種)/`COVERAGE_SCOPE` CHECK 文字列を追加する
- [x] T002 `db/src/horseracing_db/models/market.py` に `ExoticOdds` モデル(TimestampMixin)を定義する: race_id(FK)/bet_type/selection(JSONB)/odds(Numeric)/coverage_scope/source、`UniqueConstraint(race_id, bet_type, selection)` + bet_type/coverage_scope CHECK(data-model.md §1)。`db/src/horseracing_db/models/__init__.py` に登録・export
- [x] T003 `db/migrations/versions/0005_exotic_odds.py` を作成する: `exotic_odds` テーブル + `UNIQUE(race_id, bet_type, selection)` B-tree + CHECK、down_revision=0004。憲法 VI の正当化コメントを記載(0001–0004 以降で初の新テーブル、006–011 はスキーマ変更なし)
- [x] T004 `db/tests/integration/test_exotic_odds_schema.py` を作成: migration upgrade/downgrade、`UNIQUE(race_id,bet_type,selection)`、`ON CONFLICT DO UPDATE` で最新値上書き(履歴なし・updated_at 更新)、bet_type/coverage_scope CHECK を検証(SC-001/SC-003)

**Checkpoint**: `exotic_odds` テーブルが head に存在し、上書き規律・一意制約が検証済み。

---

## Phase 2: Foundational(selection 突合の単一正準化・全 US の前提)

**⚠️ US1(格納)と US2(突合)が同一 selection 正準化を共有する。先に確定させること。**

- [x] T005 `db/src/horseracing_db/selection.py` に `canonical_selection(bet_type, numbers) -> list[int]`(単一の正準化: 順序券種=順序保持、無順序=horse_number 昇順整列、複勝=`[i]`)を実装する。`db` を単一情報源とし scrape/betting が共有(research.md R1)
- [x] T006 [P] `db/tests/unit/test_selection_parity.py` を作成: `db.canonical_selection` が Feature 011 の `horseracing_betting.exotic_selection.to_selection` と**全 6 券種で完全一致**(スカラ/順序/整列差を吸収)することを検証。join キー不一致の回帰防止(research.md R1 / SC-004)

**Checkpoint**: exotic_odds と recommendations/推定が同一 selection 配列で突合できることが保証される。

---

## Phase 3: User Story 1 - 実 exotic オッズを取込んで exotic_odds に格納 (Priority: P1) 🎯 MVP

**Goal**: netkeiba から 6 券種の実オッズを取得・パースし `exotic_odds` に冪等格納(最新値上書き、id_mappings 経由、監査)。

**Independent Test**: 保存 HTML fixture から 6 券種をパースし `exotic_odds` に同一 JSONB 安全配列 selection で格納、
`UNIQUE(race_id,bet_type,selection)` で冪等(再取込で重複ゼロ・最新値収束)、netkeiba ID は id_mappings 経由のみ。

### 実装

- [x] T007 [P] [US1] `scrape/src/horseracing_scrape/models.py` に `ScrapedExoticOdds`/`ScrapedExoticRow`(bet_type, number_tuple, odds, coverage_scope ヒント)を追加する
- [x] T008 [US1] `scrape/src/horseracing_scrape/parse/exotic_odds.py` に `parse_exotic_odds(html) -> ScrapedExoticOdds` を実装する: 6 券種(複勝/馬連/馬単/ワイド/三連複/三連単)を netkeiba HTML からパース、馬番の組 + odds、ネットワーク非依存、結果非参照(contracts/exotic_odds_ingest.md)
- [x] T009 [US1] `scrape/src/horseracing_scrape/upsert.py` に `upsert_exotic_odds(session, race_id, scraped) -> Counts` を実装する: netkeiba ID は **id_mappings 経由のみ**(`resolve_entity`、unmapped→`nk:` surrogate)、`db.canonical_selection` で selection 正準化、**`source='netkeiba'` を設定**、`ON CONFLICT (race_id,bet_type,selection) DO UPDATE`(最新値上書き・履歴なし)、odds<=0 スキップ、**2007 未満の race_id は skipped 監査**、future race_id は有効 12 桁のみ、coverage_scope(full/partial)記録。**結果確定後も上書き**(netkeiba 単独源、JRA-VAN 保護対象なし)(data-model.md §1 / FR-002/FR-004)
- [x] T010 [US1] `scrape/src/horseracing_scrape/pipeline.py` に `scrape_exotic_odds(session, *, race_id|date_range, fetcher, ...)` を実装する: fetch(008 polite)→ parse → upsert、`ingestion_jobs`(job_type='exotic_odds'、status、summary に券種別 期待/観測/欠損・unmapped)で監査、部分取得=partial(FR-005/contracts)

### US1 テスト

- [x] T011 [P] [US1] `scrape/tests/fixtures/exotic_odds.html` を追加(6 券種を含む netkeiba 風の最小 HTML)
- [x] T012 [P] [US1] `scrape/tests/unit/test_parse_exotic_odds.py` を作成: fixture から 6 券種がパースされ、馬番組・odds が正しく、ネットワーク非依存・結果非参照であることを検証(SC-001)
- [x] T013 [P] [US1] `scrape/tests/integration/test_exotic_odds.py` を作成: 実 DB で `upsert_exotic_odds`/`scrape_exotic_odds` が `exotic_odds` に格納、再取込で**最新値上書き・重複ゼロ**、`source='netkeiba'` セット、id_mappings 経由(unmapped→`nk:`)、`ingestion_jobs` 監査、**完全グリッドの期待件数テスト**(N 頭で exacta=N·(N−1)、trio=C(N,3) 等)で `coverage_scope=full` を判定・部分は partial、**2007 未満は skipped**、future race_id 非書込みを検証(SC-001/SC-002/SC-003)

**Checkpoint**: US1 単独で動作・テスト緑。実 exotic オッズが冪等に取込まれる(MVP)。

---

## Phase 4: User Story 2 - 実 exotic オッズで推奨/バックテストを実 ROI 化 (Priority: P1)

**Goal**: 実オッズ優先 / 推定フォールバックを行単位で配線し、011 の二重疑似を実 ROI に格上げ。

**Independent Test**: 合成 exotic_odds + 予測で推奨を生成し、実オッズある組み合わせは market_odds_used=実値/is_estimated_odds=false/
実 ROI、無い組み合わせは 011 推定にフォールバックし、selection 完全一致で行単位区別。

### 実装

- [x] T014 [P] [US2] `betting/src/horseracing_betting/exotic_market.py` に `load_real_exotic_odds(session, race_id) -> dict[tuple[str, tuple[int,...]], float]` を実装する: `exotic_odds` の最新値を 011 `to_selection` と同一正準キーで返す。結果非参照(contracts/real_roi_wiring.md)
- [x] T015 [US2] `betting/src/horseracing_betting/exotic_recommend.py` を拡張する: `canonical_field`→`exotic_ev_bets` を必ず経由し、候補 selection で実オッズを引く。ヒット=`market_odds_used=実値`/`is_estimated_odds=false`/`estimated_market_odds_used=null`/EV=P_model×実オッズ、ミス=011 推定(二重疑似)にフォールバック。**行単位で区別**、`use_real_odds=True` 既定、logic_version に方針記載(FR-007/FR-008/SC-004)
- [x] T016 [US2] `betting/src/horseracing_betting/exotic_roi.py` を拡張する: `score_exotic(..., real_odds=None)` で的中買い目の払戻=実オッズ(`pseudo=false`)or O_est(`pseudo=true`)、**推奨後取消を含む買い目は void/skip**、`aggregate_roi` は実払戻/疑似払戻を**ラベル分離**集計(FR-009/SC-005)
- [x] T017 [US2] `betting/src/horseracing_betting/exotic_backtest.py` を拡張する: 各レースで `load_real_exotic_odds` を**採点にのみ**渡し(`exotic_ev_bets` の EV 入力には渡さない=後知恵防止、選定は推定 O_est)、実 ROI / 疑似 ROI を分離レポート。買い目生成は結果非参照を維持(FR-009/SC-005/リーク・ガード)

### US2 テスト

- [x] T018 [P] [US2] `betting/tests/unit/test_exotic_real_wiring.py` を作成: 実オッズヒット行(market_odds_used=実値/is_estimated_odds=false/EV=P_model×実)とミス行(011 推定フォールバック)が selection 完全一致で行単位区別、real ROI 採点、**推奨後取消 void**、**011 の dead-heat 規律継承**(順序/集合券種の同着スキップ・複勝/ワイド圏内同着的中)を実オッズ採点でも検証(SC-004/SC-005)
- [x] T019 [P] [US2] `betting/tests/integration/test_exotic_real_recommend.py` を作成: 実 DB で exotic_odds 投入時に推奨が is_estimated_odds=false/実 ROI、欠損時は 011 推定にフォールバック、バックテストが実/疑似を分離することを検証(SC-004/SC-005)

**Checkpoint**: US2 単独で動作・テスト緑。実オッズで二重疑似が実 ROI に格上げ。

---

## Phase 5: User Story 3 - 推定 vs 実 exotic オッズの乖離を評価 (Priority: P1)

**Goal**: 推定 O_est(010/011)vs 実 exotic オッズの乖離を券種別・レース単位で計測(評価先行)。

**Independent Test**: 合成 (推定, 実) ペアで coverage_rate・`log(実/推定)` の median/MAE/P90 が券種別に算出され、推定= baseline で
ラベル分離されることを確認。

### 実装

- [x] T020 [US3] `betting/src/horseracing_betting/exotic_divergence.py` に `exotic_divergence(session, *, date_from, date_to, model_version=None, payout_rates=None) -> dict[str, DivergenceReport]` を実装する: 各レースで 011 推定 O_est と `exotic_odds` 実値を**同一 selection**で対応付け、券種別に coverage_rate/n_pairs/`log(実/推定)` median/MAE/P90、推定=baseline、二重疑似ラベル(FR-010/data-model.md §6)

### US3 テスト

- [x] T021 [P] [US3] `betting/tests/unit/test_exotic_divergence.py` を作成: log 比 median/MAE/P90 の算出、coverage_rate(部分カバー明示)、推定=baseline ラベル分離、決定論を検証(SC-006)
- [x] T022 [P] [US3] `betting/tests/integration/test_exotic_divergence.py` を作成: 実 DB で期間乖離レポートが券種別に算出され、実オッズ欠損レースで coverage_rate=0 を明示、決定論であることを検証(SC-006)

**Checkpoint**: US3 単独で動作・テスト緑。推定の妥当性が定量化される。

---

## Phase 6: User Story 4 - CLI で exotic オッズ取込と乖離レポート (Priority: P2)

**Goal**: CLI で取込(レース/期間)と乖離レポート(期間)、推奨/バックテストの実オッズ利用。

**Independent Test**: CLI で scrape-exotic-odds と exotic-divergence を実行し、取込件数(券種別・coverage・unmapped)と乖離指標が表示。

### 実装

- [x] T023 [US4] `scrape/src/horseracing_scrape/cli.py` に `scrape-exotic-odds` サブコマンド(`--race-id` / `--from --to`)を追加する: `scrape_exotic_odds` を呼び、券種別格納件数・coverage_scope・unmapped 件数を表示(FR-012)
- [x] T024 [US4] `betting/src/horseracing_betting/cli.py` に `exotic-divergence` サブコマンド(`--from --to --model-version`)を追加し、`exotic-recommend`/`exotic-backtest` に実オッズ利用フラグ(既定 on)を露出する(FR-012)

### US4 テスト

- [x] T025 [P] [US4] `scrape/tests/integration/test_exotic_cli.py` と `betting/tests/integration/test_exotic_divergence_cli.py` を作成: `scrape-exotic-odds`/`exotic-divergence` が実 DB で実行され、取込件数・coverage・乖離指標(推定=baseline 明示)が出力に含まれることを検証(FR-012)

**Checkpoint**: 全 US 完了。CLI から取込・乖離・実 ROI 推奨が操作可能。

---

## Phase 7: Polish & Cross-Cutting

- [x] T026 [P] `betting/src/horseracing_betting/__init__.py` に exotic_market/exotic_divergence の公開 API を追加 export する
- [x] T027 [P] 各パッケージで lint/format を解消する: `cd db && uv run ruff check .`、`cd scrape && uv run ruff check .`、`cd betting && uv run ruff check .`
- [x] T028 全テスト緑を確認する: `cd db && uv run pytest -m integration`、`cd scrape && uv run pytest`、`cd betting && uv run pytest`
- [x] T029 [P] [quickstart 検証] `specs/012-exotic-odds-ingest/quickstart.md` の手順を実 DB(2008 データ)で実行: 0005 マイグレーション適用 → scrape-exotic-odds → exotic-divergence で取込・実 ROI・乖離・リーク境界を確認(SC-001〜SC-007)

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001 [P]→T002→T003→T004(同一スキーマ、順次)。
- **Phase 2 (Foundational)**: Setup 後。T005→T006。**US1/US2 の selection 突合をブロック**。
- **Phase 3 (US1, P1, MVP)**: Foundational 後。T007 [P]→T008→T009→T010、テスト T011/T012/T013 [P]。
- **Phase 4 (US2, P1)**: Foundational + US1(`exotic_odds` 格納が前提、実 DB テストは US1 取込を利用)。T014 [P]→T015→T016→T017、テスト T018/T019 [P]。
- **Phase 5 (US3, P1)**: Foundational + US1(実オッズ)+ 011 推定。T020、テスト T021/T022 [P]。
- **Phase 6 (US4, P2)**: US1+US2+US3 後(CLI が各機能を呼ぶ)。T023/T024→T025。
- **Phase 7 (Polish)**: 全実装後。

### User Story 独立性

- US1(取込)は db スキーマ + Foundational のみに依存し単独で完結(MVP)。US2/US3 は US1 の `exotic_odds` を消費(実 DB テストで取込を利用)。US4 は CLI で束ねる。

## Parallel 実行例

- Setup: T001 を先行。Foundational テスト T006 は単独。
- US1: T011/T012/T013 を並走。
- US2: T014 と T018 準備を並走、テスト T018/T019 を並走。
- US3: T021/T022 を並走。
- Polish: T026/T027/T029 を並走。

## 実装戦略

1. **MVP first**: Phase 1→2→3(US1)で「実 exotic オッズの冪等取込」を最短達成し単独デモ可能。
2. **実 ROI 化**: Phase 4(US2)で 011 の二重疑似を実オッズで単一評価に格上げ。
3. **評価先行(憲法 III)**: Phase 5(US3)で推定 vs 実の乖離を計測し推定フォールバックの妥当性を裏付け。
4. **運用性**: Phase 6(US4)で CLI、Phase 7 で lint/全テスト/quickstart 実 DB 検証。
5. 各 Phase の Checkpoint で独立テストを緑にしてから次へ。憲法 II(リーク)/V(履歴なし最新値・監査)を全タスクで維持。
