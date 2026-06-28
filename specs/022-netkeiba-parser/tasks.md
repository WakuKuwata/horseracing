---
description: "Task list for 022 実 netkeiba パーサ"
---

# Tasks: 実 netkeiba パーサ (022)

**Input**: Design documents from `specs/022-netkeiba-parser/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/parser-contract.md, quickstart.md

**Tests**: 本 feature はテスト必須（spec FR-010 / SC-006、憲法 品質ゲート＝leak-guard / fail-close / idmap / 確率整合）。各パーサは保存実フィクスチャでネットワーク非依存に検証する。

**Organization**: ユーザーストーリー（P1 出走表 / P2 結果 / P3 単勝オッズ）ごとに独立実装・独立テスト可能な単位で分割。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並行可能（別ファイル・依存なし）
- **[Story]**: US1=出走表 / US2=結果 / US3=単勝オッズ / US4=開催日一覧（追補）/ US5=馬プロフィール補完（追補）

## Path Conventions

- パッケージ: `scrape/`（`scrape/src/horseracing_scrape/`、`scrape/tests/`）。スキーマ変更なし。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: フィクスチャ基盤・取得経路・ネットワーク非依存テスト土台。

- [x] T001 [P] `scrape/tests/fixtures/real/` を作成し `manifest.json` の雛形（各エントリ: page_kind / url / fetched_at / race_id / sha256 / trim_note）を置く
- [x] T002 `scrape/src/horseracing_scrape/cli.py` に `capture-fixture` サブコマンド追加（1回限り polite 取得＝robots/1秒/UA、entries/results=HTML・odds=JSON、odds は no-cache、保存先 `fixtures/real/`、manifest 追記）
- [x] T003 [P] `scrape/tests/conftest.py` にテスト中の外部 HTTP 禁止を追加（`httpx.MockTransport` ないし block-network fixture）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 全パーサが依存する共通解析ヘルパ・URL 構築・robustness 土台・実フィクスチャ取得。

**⚠️ CRITICAL**: 本フェーズ完了まで US1–US3 は着手不可。

- [x] T004 [P] `scrape/src/horseracing_scrape/urls.py` を新規作成（race_id→netkeiba URL: `race/shutuba.html`・`race/result.html`・単勝 odds JSON。race_id=JRA-VAN 恒等を利用）
- [x] T005 `scrape/src/horseracing_scrape/parse/_common.py` を実 markup ヘルパへ改修（合成 data-* 撤去、必須フィールドは strict parse＝不正値を None に潰さず `ParseError`、HTML 本文からの race_id 抽出＋URL race_id 照合ヘルパ）
- [x] T006 [P] `scrape/src/horseracing_scrape/parse/_profile.py` に `ParserProfile(version, required_selectors, invariants)` を追加（不変条件: 馬番一意 / horse_id 取得率 / entry_status 閉世界 / race_id 一致）。`parser_version` を監査へ
- [x] T007 `capture-fixture` で実フィクスチャを 1 回取得（entries＋単勝odds=結果未確定の対象レース、results=過去レース、最小件数）→ `fixtures/real/` 保存＋`manifest.json` 記入＋無関係要素 trim（依存: T001, T002）

**Checkpoint**: 共通土台と実フィクスチャが揃い、各ストーリーを並行開始可能。

---

## Phase 3: User Story 1 - 出走表 (Priority: P1) 🎯 MVP

**Goal**: 実 netkeiba 出走表ページを解析し、未来 race と出走馬を既存テーブルへ取り込む。

**Independent Test**: 保存した実出走表フィクスチャを取り込み、未来 race と全出走馬が正しいフィールドで `races`/`race_horses` に入り、馬/騎手/調教師がマッピング済み=canonical、未マップ=surrogate `nk:`＋UNMAPPED で記録される。

### Tests for User Story 1 ⚠️（先に書いて FAIL を確認）

- [x] T008 [P] [US1] `scrape/tests/unit/test_parse_entries.py` を実フィクスチャベースへ更新（枠/馬番/horse_id/騎手/調教師/性齢/斤量/entry_status の抽出、本文 race_id 照合、必須欠損→`ParseError`、URL/本文 race_id 不一致→`ParseError`）
- [x] T009 [P] [US1] `scrape/tests/integration/test_entries.py` を更新（races/race_horses 取り込み、horse＋**騎手＋調教師** idmap の canonical/surrogate＋UNMAPPED キュー、無効 race_id は skip、ingestion_jobs summary）

### Implementation for User Story 1

- [x] T010 [US1] `scrape/src/horseracing_scrape/parse/entries.py` を実 netkeiba 出走表 HTML 解析へ置換（`Shutuba_Table`/`HorseList`/`/horse/{id}`/`/jockey/{id}`/`/trainer/{id}`、性齢正規化、entry_status 判定、本文 race_id 照合、必須 strict）→ `ScrapedEntry`（依存: T005, T006）
- [x] T011 [US1] `scrape/src/horseracing_scrape/pipeline.py` の `scrape_entries` を `urls.py` 経由の取得に接続し、`ingestion_jobs.summary` に **parser_version**（T006）を記録（依存: T004, T010）

**Checkpoint**: 実出走表で取り込みが成立。これ単独で「未来レースの予測母集団」が用意でき、serving に流せる（MVP）。

---

## Phase 4: User Story 2 - 結果 (Priority: P2)

**Goal**: 実 netkeiba 結果ページを解析し `race_results` へ INSERT-only 取り込み（finish_time 含む）。

**Independent Test**: 保存した実結果フィクスチャを取り込み、着順・状態・**finish_time** が `race_results` に入り、既存結果がある race は上書きされない。

### Tests for User Story 2 ⚠️

- [x] T012 [P] [US2] `scrape/tests/unit/test_parse_odds_results.py`（results 部）を実フィクスチャへ更新（finish_order/result_status/finish_time 文字列、必須欠損→`ParseError`）
- [x] T013 [P] [US2] `scrape/tests/integration/test_results.py` を更新（`race_results` に finish_order＋status＋**finish_time** が入る、既存行は INSERT-only で保護）

### Implementation for User Story 2

- [x] T014 [US2] `scrape/src/horseracing_scrape/parse/results.py` を実 netkeiba 結果 HTML 解析へ置換（着順テーブル、除外/中止/失格→result_status、タイム文字列、**本文 race_id 照合＝URL race_id と不一致なら ParseError**）→ `ScrapedResult`（依存: T005, T006）
- [x] T015 [US2] `scrape/src/horseracing_scrape/upsert.py` の `backfill_results` を小改修：`finish_time`(str 例 "1:34.5" → `Interval`/timedelta) を永続化（`ingest._parse_finish_time` 相当、既存カラム、スキーマ変更なし）

**Checkpoint**: 実結果で取り込みが成立し、バックテスト/評価の答え合わせに使える。

---

## Phase 5: User Story 3 - 単勝オッズ (Priority: P3)

**Goal**: 実 netkeiba の単勝オッズ JSON を解析し、result-pending race の odds・popularity を最新値で更新。

**Independent Test**: 保存した実 odds JSON フィクスチャを取り込み、result-pending race の `race_horses.odds`＋**popularity** が更新され、結果のある race は更新されない。cache を経由しない。

### Tests for User Story 3 ⚠️

- [x] T016 [P] [US3] `scrape/tests/unit/test_parse_odds_results.py`（odds 部）を実 JSON フィクスチャへ更新（odds＋popularity 抽出、JSON 必須キー欠損→`ParseError`）
- [x] T017 [P] [US3] `scrape/tests/integration/test_odds.py` を更新（result-pending の odds＋**popularity** 更新、結果あり race は skip、odds は **no-cache** 取得）

### Implementation for User Story 3

- [x] T018 [US3] `scrape/src/horseracing_scrape/odds_adapter.py` を新規作成（odds JSON を **no-cache** 取得＋必須キー検査＋欠損 fail-close）（依存: T004）
- [x] T019 [US3] `scrape/src/horseracing_scrape/parse/odds.py` を JSON 解析へ置換（入力 HTML→JSON、`ScrapedOdds`/`ScrapedOddsRow` 充填）。**odds JSON の突合キーを確定**＝horse_id を含めばそのまま、馬番のみなら `ScrapedOddsRow` に馬番を持たせる（依存: T005）
- [x] T020 [US3] `scrape/src/horseracing_scrape/upsert.py` の `update_odds` を小改修：`popularity` を永続化（既存カラム、スキーマ変更なし）。**馬番突合対応**＝odds JSON が馬番のみの場合 `race_horses.(race_id, horse_number)→horse_id` で解決（horse_id があれば従来どおり resolve_entity）
- [x] T021 [US3] `scrape/src/horseracing_scrape/pipeline.py` の `scrape_odds` を `odds_adapter`（no-cache）経由へ接続し、`ingestion_jobs.summary` に **parser_version** を記録（依存: T018, T019, T020）

**Checkpoint**: P1–P3 すべて独立に機能。推奨(EV/Kelly)に実 odds を供給可能。

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T022 [P] 旧合成フィクスチャ（`scrape/tests/fixtures/entries.html`/`odds.html`/`results.html`）を撤去し参照を実フィクスチャへ。`exotic_odds.html` と `scrape-exotic-odds` は **合成のまま残置**（022 対象外）をコード/READMEに明記
- [x] T023 [P] leak-guard E2E テスト追加：scrape 由来の odds・結果が `features.registry` の model input に現れない（憲法 II）
- [x] T024 Run quickstart.md 検証（unit=ネットワーク非依存、integration=testcontainers、実データ e2e predict＝SC-007）
- [x] T025 [P] `scrape` パッケージの lint/format（ruff）と `parser_version` 監査記録の確認

---

## Phase 7: User Story 4 - 開催日レース一覧取得 (Priority: P4) 〔追補 2026-06-28〕

**Goal**: 開催日 (YYYYMMDD) からその日の全 race_id を列挙し、各レースの取り込み URL を生成できる。

**Independent Test**: 保存したレース一覧フラグメント HTML フィクスチャを解析し、race_id が重複なく出現順で列挙され、12桁として無効な ID が除外される（ネットワーク非依存）。

### Tests for User Story 4 ⚠️

- [x] T026 [P] [US4] `scrape/tests/unit/test_parse_race_list.py`：フラグメントから race_id 重複排除・出現順抽出、空ペイロード→`ParseError`、JRA 開催無し日→空リスト、`discover_races` が無効 ID を除外

### Implementation for User Story 4

- [x] T027 [US4] `scrape/src/horseracing_scrape/urls.py` に `race_list_url(date)`（`top/race_list_sub.html?kaisai_date=`、YYYYMMDD/`YYYY-MM-DD` 正規化）追加
- [x] T028 [US4] `scrape/src/horseracing_scrape/parse/race_list.py` を新規作成（`race_id=` 正規抽出・重複排除・出現順、空ペイロード fail-close）→ `ScrapedRaceList`（`models.py` に追加）
- [x] T029 [US4] `scrape/src/horseracing_scrape/pipeline.py` に `discover_races`（読み取り専用・12桁無効除外）追加、`cli.py` に `list-races --date [--urls]` 追加（依存: T027, T028）

**Checkpoint**: 日付指定で race_id 一覧が取れ、US1–US3 へ URL を供給できる（手動 URL 入力不要）。

---

## Phase 8: User Story 5 - 馬プロフィール識別・血統補完 (Priority: P5) 〔追補 2026-06-28〕

**Goal**: surrogate (`nk:`) 馬の識別・血統属性を db.netkeiba.com から補完する（opt-in、NULL 列のみ、成績は読まない）。

**Independent Test**: surrogate 馬 1 頭を DB に置き、保存プロフィール HTML から 性別/生年/血統が NULL 列のみに補完され、既存非 NULL 値は保持され、解析結果型に成績フィールドが存在しないことを検証する。

### Tests for User Story 5 ⚠️

- [x] T030 [P] [US5] `scrape/tests/unit/test_parse_horse_profile.py`：識別・血統抽出、名前欠損→`ParseError`、母父欠損→None、**`ScrapedHorseProfile` が成績フィールドを持たない（leak-guard 構造検証）**
- [x] T031 [P] [US5] `scrape/tests/integration/test_profiles.py`：NULL 列のみ補完、既存非 NULL 値保持、DB 未登録馬は skip、job_type='horse_profile' 監査

### Implementation for User Story 5

- [x] T032 [US5] `scrape/src/horseracing_scrape/urls.py` に `horse_profile_url(id)`、`models.py` に `ScrapedHorseProfile`（識別・血統のみ）追加
- [x] T033 [US5] `scrape/src/horseracing_scrape/parse/_profile.py` に `parse_horse_profile`（性別/生年/父・母・母父、`db_prof_table`/`blood_table`、成績は読まない、`HORSE_PROFILE_PROFILE`）追加
- [x] T034 [US5] `scrape/src/horseracing_scrape/upsert.py` に `complete_horse_profile`（fill-NULL-only、血統 id は `resolve_entity` 経由、JRA-VAN 不上書き）追加
- [x] T035 [US5] `scrape/src/horseracing_scrape/pipeline.py` に `complete_profiles`（opt-in post-pass、surrogate 対象 or 明示 id、job 監査）追加、`cli.py` に `complete-profiles` ＋ `capture-fixture` の `race_list`/`horse_profile` kind 追加（依存: T032, T033, T034）

### Selector 検証・実 markup 反映 (US4/US5, 2026-06-28)

- [x] T036 実 netkeiba を polite fetcher で最小取得し US4/US5 パーサを実 markup 検証。フィクスチャ `fixtures/real/{race_list_20241228,horse_profile_2022103995,pedigree_2022103995}.html` 保存＋ネットワーク非依存テスト追加（`test_real_*`）
- [x] T037 取得層エンコーディング修正（FR-017）：`fetch._resolve_text` で charset ヘッダ無し時に `<meta charset>` を見て EUC-JP デコード（db.netkeiba.com 文字化け解消、UTF-8 ページは無影響）。回帰テスト追加
- [x] T038 血統取得を 2 ページ方式へ修正：本体ページ血統は JS 描画のため `urls.horse_pedigree_url`＋`parse._profile.parse_horse_pedigree`（`blood_table`、父系 `b_ml`/母系 `b_fml`、母父=母セル後の最初の `b_ml`）を追加、`complete_profiles` で識別＋血統をマージ、`capture-fixture` に `pedigree` kind 追加

### データ網羅性: entries/results パーサ完全化 (2026-06-28, FR-018/019)

- [x] T039 entries 拡張：斤量 (jockey_weight, 性齢の次セル)・馬体重増減 (weight_diff)・レース名・グレード (`.RaceName` 内限定)・発走時刻 (JST) を抽出。`ScrapedEntryHorse`/`ScrapedRace` 拡張＋`upsert_entries` 永続化
- [x] T040 results 拡張：上がり3F (last_3f)・通過順 (corner_orders) 抽出、着差 (finish_time_diff) を絶対タイム差で算出、脚質 (running_style) を通過順初角位置から JRA 語彙へ導出 (fill-if-null)。`ScrapedResultRow` 拡張＋`backfill_results`
- [x] T041 odds ルール改訂 (FR-007)：結果ありレースは全スキップ→ odds NULL の馬のみ補完 (既存=JRA-VAN 値は保護)。確定済み/未来 両レースでオッズ取得。`update_odds` ＋テスト更新
- [x] T042 実レース 202505040301 で end-to-end 検証：全列 (斤量/増減/単勝/人気/着差/上がり/通過/脚質/レース名/grade/発走) が正しく埋まることを確認。grade 誤検出 (未勝利→G3) を `.RaceName` スコープ化で修正＋回帰テスト

**Checkpoint**: surrogate 馬の血統/識別が leak-safe に補完され、036 系特徴の入力が揃う。entries/results パーサは netkeiba の利用可能列を網羅 (real exotic odds=D のみ別段 deferred)。実 markup で全パーサ検証済み（55 tests green）。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: 依存なし。
- **Foundational (P2)**: Setup 後。**全 US をブロック**（特に T005 `_common`、T004 `urls`、T007 実フィクスチャ）。
- **US1/US2/US3 (P3–5)**: Foundational 後。相互独立（各 integration test は自前で前提レースを seed）→ 並行可。
- **Polish (P6)**: 対象 US 完了後。
- **US4/US5 (P7–8, 追補)**: 当初スコープ完了後の後追い。US4 は `urls`/`parse`/`pipeline`/`cli` の追加のみ（既存無改修）。US5 は US1（surrogate 馬の取り込み）後に意味を持つが、実装は独立（明示 id 指定でも動く）。両者とも実 markup は本番前に `capture-fixture --kind race_list/horse_profile` で検証要。

### Key task dependencies

- T005（_common 改修）→ T010 / T014 / T019（各 parser）
- T004（urls）→ T011 / T018 / T021（pipeline/adapter）
- T001+T002 → T007（実フィクスチャ取得）→ 各 unit/integration テスト
- T018（odds_adapter）→ T021

### Parallel Opportunities

- Setup: T001 / T003 並行。
- Foundational: T004 / T006 並行（T005・T007 は他が依存）。
- 各 US の unit/integration テスト（[P]）は並行記述可。Foundational 完了後は US1/US2/US3 を別担当で並行可。

---

## Parallel Example: User Story 1

```bash
# US1 のテストを先に並行作成（FAIL 確認）:
Task: "T008 unit test parse_entries on real fixture in scrape/tests/unit/test_parse_entries.py"
Task: "T009 integration test entries ingest in scrape/tests/integration/test_entries.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 のみ)

1. Phase 1 Setup → Phase 2 Foundational（T004–T007）
2. Phase 3 US1（T008–T011）
3. **STOP & VALIDATE**: 実出走表で取り込み→serving 予測が通ることを確認（SC-001 / SC-007 の出走表部）
4. ここまでで「実 netkeiba の未来レースを予測できる」最小価値が成立

### Incremental Delivery

1. Setup + Foundational → 土台
2. US1（出走表）→ 独立検証 → MVP
3. US2（結果）→ 独立検証（finish_time 含む）
4. US3（単勝オッズ）→ 独立検証（popularity / no-cache）
5. Polish（合成撤去・leak-guard・quickstart）

---

## Notes

- 既存の取得層（`HttpFetcher`）・ID 解決（`idmap`）・race_id 構築（`venues`）・書き込み保護ルール（INSERT-only / result-pending）は無改修で再利用。変更は parse 層＋ URL/odds_adapter＋ upsert 小改修（popularity/finish_time）＋実フィクスチャに限定。
- **upsert 小改修（T015/T020）は parse に閉じない変更**＝Codex 指摘の核心。これが無いと正しく解析しても popularity/finish_time が DB に落ちない。
- odds は **no-cache**（古い odds を書かない＝憲法 V）。necessary な必須フィールドは strict parse で fail-close。
- スキーマ変更なし。exotic odds・RaceFront write は別 feature。
- コミットは各タスク/論理単位で。各 Checkpoint でストーリー独立性を検証。
- **追補 (T026–T035, US4/US5)**: 開催日一覧 (US4) と馬プロフィール識別・血統補完 (US5)。US5 はリーク境界が核心＝**競走成績を読まず識別・血統のみ**を NULL 列にのみ補完（`ScrapedHorseProfile` に成績フィールドを置かない構造担保＋leak-guard）。Obsidian doc の Playwright 記述は古く、本 feature の静的取得方針 (FR-013) と実装が正。実 markup は本番前に実ページ検証が必要。
