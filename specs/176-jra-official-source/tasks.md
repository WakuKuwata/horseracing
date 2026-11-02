# Tasks: JRA 公式サイトを第 2 の情報源として追加する (176)

**Input**: `specs/176-jra-official-source/`(spec.md・plan.md・research.md D1〜D21・data-model.md・contracts/{cli,precedence,fetch-and-parse}.md・quickstart.md)

**Prerequisites**: plan.md(Phase 0〜4 の段階表)・spec.md(US1〜US4・FR-001〜025・SC-001〜012)

**Tests**: 含める。spec の SC-006〜012 と憲法 III が契約テストを要求し、plan の「テスト計画」が codex の 10 分類を採用している。Phase 1(共通 writer)と各 US の統合テストは**先に書いて赤を確認**してから実装する(両経路共通の規則は netkeiba 経路だけで赤にできる)。

**Organization**: コードは user story ごとに並べる(US1 出馬表・変更情報・オッズ / US2 結果・払戻 / US3 監査 / US4 馬場)。**実 DB への投入順序はコードの順序と別**で、Phase 11(rollout・T100〜T107)に plan D20 の順(共通安全性 → 収集・照合 → 限定レース → 全レース → 結果 → 馬場)で並べる。

**開催日の制約(plan D20・Phase 0)**: Phase 1〜3 と Phase 5〜6 の純関数・解析器・fixture・値オブジェクトは開催中でも進めてよい。**Phase 4(共通 writer の挙動変更)・`politeness.py`・`pipeline.py`・`ops/` の変更・migration の本番適用・policy ファイルの変更は開催終了後**。該当タスクに ⏸ を付ける。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 別ファイル・未完了タスクへの依存なし=並列可
- **[Story]**: US1 / US2 / US3 / US4(Setup・Foundational・Polish には付けない)
- ⏸: 開催終了後にのみ実施(稼働プロセスが読む共通コード・本番 DB・policy)

## Path Conventions

monorepo。`scrape/src/horseracing_scrape/`(略: `scrape/…`)、`scrape/tests/{unit,integration,fixtures}/`、`db/src/horseracing_db/`・`db/migrations/versions/`、`ops/src/horseracing_ops/`、`features/tests/`、`scripts/`、`config/`。テストは各パッケージで `uv run pytest`(統合は `-m integration`・testcontainers)。

---

## Phase 1: Setup(fixture・値オブジェクト・名前空間)— 開催中でも可

**Purpose**: 解析器と純関数が依存する証跡と型を固める。DB・共通コードに触らない。

- [x] T001 調査で保存した raw ページを `scrape/tests/fixtures/jra/` に**意図して**コピーする(scratchpad `…/scratchpad/jra/` から: `d_index.sjis`→`index_d_20261101.sjis.html`、`o_tokyo_list.sjis`→`daylist_o_tokyo_20261101.sjis.html`、`d_tokyo_list.sjis`→`daylist_d_tokyo_20261101.sjis.html`、`d_race.sjis`→`entries_tokyo_20261101.sjis.html`、`i_index.sjis`→`changes_20261101.sjis.html`、`o_tokyo11r.sjis`→`odds_tokyo11r_20261101_0822.sjis.html`、`s_index.sjis`→`index_s_20261101.sjis.html`、`s_race.sjis`→`results_tokyo_20261031.sjis.html`、`h_index.sjis`→`index_h_20261101.sjis.html`、`h_tokyo.sjis`→`payouts_tokyo_20261031.sjis.html`、`_data_cushion.html.raw`→`baba_cushion_20261101.sjis.html`、`_data_moist.html.raw`→`baba_moist_20261101.sjis.html`、`accessJ.raw`→`accessJ_20261101.json`、`oget.sjis`→`error_get_redirect.sjis.html`)。バイト不変で置き、UTF-8 変換版は置かない(contracts/fetch-and-parse.md §4)
- [x] T002 `scrape/tests/fixtures/jra/manifest.json` を書く: entry ごとに `kind` / `file` / `url` / `cname_sha256`(平文は書かない)/ `fetched_at`(JST・調査記録の時刻)/ `raw_sha256` / `charset` / `race_date` / `venue_code` / `note`。sha は `shasum -a 256` で raw バイトから計算する
- [x] T003 [P] `scrape/tests/unit/test_jra_fixture_manifest.py`: 全 entry の `raw_sha256` が一致・`manifest.json` と fixture 本文以外(テストコード・`url.txt` 形式)に cname 平文が無い・`charset` が `shift_jis` か `utf-8`(SC-007 の前提・FR-001/FR-007) 。**固定入口 cname(索引 D/O/S/H の定数)はコードに持ってよい**: 禁止は「取得した動的 cname の永続化・再利用」で、`test_jra_fixture_manifest.py` は manifest と `url.txt` 形式だけを検査し、`navigate.py` の定数は対象外(codex Q1-6)
- [x] T004 [P] `scrape/src/horseracing_scrape/jra/__init__.py` と `scrape/src/horseracing_scrape/jra/models.py`: data-model §4 の値オブジェクト(`JraPageRef` / `JraResponse` / `JraDayPage[T]` / `JraRaceBlock[T]` / `JraEntry` / `JraEntryHorse` / `JraChange` / `JraWinOdds` / `JraWinOddsRow` / `JraResultRow` / `JraRaceResult` / `JraPayoutSet` / `JraPayoutBlock` / `JraPayoutLine` / `JraTrackReading` / `ResolvedResultRow` / `CollisionReport` / `Collision`)を frozen dataclass で。例外型 `JraParameterError(Exception)` / `JraDecodeError(ParseError)` / `NotYetPublished` / `SourceIntervalUnconfigured(RuntimeError)` を `scrape/src/horseracing_scrape/jra/errors.py` に
- [x] T005 [P] `scrape/src/horseracing_scrape/jra/venues.py`: 競馬場名(札幌01 函館02 福島03 新潟04 東京05 中山06 中京07 京都08 阪神09 小倉10)→ 2 桁コード、未知名は None。既存 `scrape/src/horseracing_scrape/venues.py` の netkeiba 側コード表と値が一致することを `scrape/tests/unit/test_jra_venues.py` で固定
- [x] T006 [P] `scrape/src/horseracing_scrape/jra/identity.py`: `official_id(kind, href)`(馬 `pw01dud00(\d{10})` / 騎手 `pw04kmk0(\d{5})` / 調教師 `pw05cmk0(\d{5})`・境界つき正規表現・先頭ゼロ保持・不一致は None)と `jra_name_key(name)`(NFKC + 全空白除去 + 067 `strip_markers`)。`scrape/tests/unit/test_jra_identity.py`: 先頭ゼロ保持・11 桁/4 桁/非数字/別 prefix は None・騎手と調教師の同数値は別 kind・`△` `▲` `☆` マーカー除去・全角空白除去(FR-009/010・data-model §6)
- [x] T007 構造変更 fixture を派生で作る(`scrape/tests/fixtures/jra/derived/`・**T001/T002 の後**): 出馬表の `h_weight` 列削除版・**出馬表の見出しを別日に書換えた版**(T051 用)・結果の `race_header` 見出しを別日に書換えた版・払戻の組番重複版・単複オッズの `cell time` 削除版・HTTP 200 の「パラメータエラー」画面(調査記録の文面を再構成)。manifest に `derived_from` を記録(SC-007)

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_jra_fixture_manifest.py tests/unit/test_jra_identity.py tests/unit/test_jra_venues.py` 緑。

---

## Phase 2: Foundational A — 純関数(両経路共通の判定)— 開催中でも可

**Purpose**: contracts/precedence.md の規則を DB なしで固定する。Phase 4 の writer と US1/US2 がこれを呼ぶ。

- [x] T008 `scrape/src/horseracing_scrape/precedence.py`: `check_entry_collisions(existing, incoming) -> CollisionReport`(同馬番別 ID / 同 ID 別馬番[取消で None の馬番は除く] / 既存 started の重複馬番 / 入力の重複馬番・重複 ID / 馬番 NULL の既存 started / 入力に無い既存 started=余剰)。`merge_candidates` / `approved_mapping_pending_rekey` は呼び出し側が渡す名前 key・生年・承認済み mapping から組む純関数 `pair_merge_candidates(...)` に分ける(contracts/precedence.md §2・研究 D6)
- [x] T009 `scrape/src/horseracing_scrape/precedence.py`: `field_transition(group, stored, incoming, ctx) -> Decision`(`write` / `keep` / `reject`): `entry_status` の一方向遷移(`cancelled↔excluded` は page_kind ∈ {changes, results} のみ・`→started` は全経路 reject)・`jockey` と `body_weight` の `observed_at` 規則(体重は対)・`trainer` / `entry_static` / `race_static` / `post_time` の `stored_source == jra_official and incoming_source == netkeiba → keep(+static_mismatch)`(contracts/precedence.md §3・data-model §5.1)。**公式が既存と異なる静的値を採用した場合も `static_mismatch` を報告**(keep+mismatch と write+mismatch の両方・codex Q2-4)
- [x] T010 `scrape/src/horseracing_scrape/precedence.py`: `odds_decision(kind, stored, incoming, started, *, has_results, incoming_source, stored_source) -> OddsDecision`(`settled_fill` / `skip:partial_field` / `skip:no_official_at` / `skip:stale` / `write` / `write:conflict` / `skip:conflict`)。上から評価する表のとおり(contracts/precedence.md §4・data-model §5.2)。**`OddsDecision.adopted_source` を返す**: 同時刻・同値で stored_source=jra_official・incoming=netkeiba のときは値を write しても採用元は `jra_official` のまま(公式の優先権を同値確認で失わない=codex Q2-5)
- [x] T011 `scrape/src/horseracing_scrape/precedence.py`: `payout_block_decision(state, stored_rows, incoming_lines, stored_source, incoming_source) -> PayoutDecision`(`complete` のみ集合置換: 削除・更新・同値は source/来歴のみ / `not_published` 不変 / `not_sold` `refund_only` は件数のみ / `parse_failed` 無書込み / `stored_source == jra_official and incoming_source == netkeiba → skip`)(contracts/precedence.md §6)
- [x] T012 [P] `scrape/tests/unit/test_precedence_collisions.py`: §2 の 6 検査それぞれ 1 件で隔離・取消馬の馬番 None は `collision:id` に数えない・空の既存は入力重複だけ検査・`pair_merge_candidates` が名前 key+生年一致の対だけ返す
- [x] T013 [P] `scrape/tests/unit/test_precedence_transitions.py`: §3 の表を行ごとに(entry_status 9 遷移 × page_kind 3・jockey の時刻規則・体重の対で keep/write・static の keep+mismatch・post_time)・公式の異値採用で write+`static_mismatch`
- [x] T014 [P] `scrape/tests/unit/test_precedence_odds.py`: §4 の表 9 行+「全馬同値=write」「同時刻異値の両方 netkeiba=write」「stored None=write」「started の一部欠落=skip:partial_field」「複勝も同じ表」・**公式 → 同時刻同値 netkeiba(write だが adopted_source=jra_official)→ 同時刻異値 netkeiba(skip:conflict)** の 3 連
- [x] T015 [P] `scrape/tests/unit/test_precedence_payouts.py`: §6 の 5 状態・同着複数行・訂正で集合置換(旧行削除)・同額の公式採用は値を触らず source のみ・組番重複は呼び出し前に parse_failed

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_precedence_*.py` 緑。

---

## Phase 3: Foundational B — DB 契約(0023)— モデル定義は開催中でも可・⏸ 本番適用は開催終了後

**Purpose**: `Source.JRA_OFFICIAL`・来歴 2 表。既存表の列は変えない(SC-009)。

- [x] T016 ⏸ `db/src/horseracing_db/enums.py`: `Source.JRA_OFFICIAL = "jra_official"` を追加し `Source.ALL` に含める。`db/src/horseracing_db/constraints.py`: `OFFICIAL_WIN_PAYOUT_SOURCES = ("netkeiba_result", "jra_official_result")`。`db/src/horseracing_db/models/market.py`: `OfficialWinPayout` の CHECK を定数から組む(既定値 `netkeiba_result` 不変)。**既存共有モジュールの変更なので ⏸**(ops の subprocess は稼働 checkout の `db` を毎ジョブ import する=codex Q1-1)。開催中に進めるなら**別 worktree**で
- [x] T017 `db/src/horseracing_db/models/provenance.py`(新規ファイル・開催中でも可): `RaceHorseFieldProvenance`(PK race_id, horse_id, field_group・CHECK 5 群・source CHECK `JOB_SOURCE`・observed_at NOT NULL・ingestion_job_id NULL 可 FK・TimestampMixin)と `RaceFieldProvenance`(PK race_id, field_group・CHECK 8 群・official_at NULL 可・同上)。**`db/src/horseracing_db/models/__init__.py` への export 追加は ⏸**(既存共有モジュール・T089 と同一ファイルを触るので順序づけ)(data-model §2)
- [x] T018 `db/migrations/versions/0023_jra_official_source.py`(`revision="0023_jra_official_source"`・`down_revision="0022_decision_quotes"`=実ファイルで確認済み): CHECK 置換 5 本(`ck_ingestion_jobs_source` / `ck_id_mappings_source` / `ck_exotic_odds_source` / `ck_race_laps_source` / `ck_official_win_payouts_source`)+来歴 2 表+index (race_id)。downgrade は `SELECT 1 … WHERE source IN ('jra_official','jra_official_result') LIMIT 1` が 1 行でもあれば `RuntimeError` で中断。`op.execute("SET lock_timeout = '5s'")` を up/down 冒頭に(研究 D18)
- [x] T019 [P] `db/tests/test_migration_0023.py`(既存 db テストの conftest に合わせる): upgrade→downgrade→upgrade が一時 DB で通る・`jra_official` 行を 1 行入れた状態の downgrade が中断する・**既存表の列定義(`information_schema.columns` の column_name/data_type/is_nullable/column_default)と制約(`pg_constraint` の conname/consrc)が 0022 と同一で、差分が許可した集合(CHECK 5 本の定義変更+新表 2)だけ**であること(SC-009・codex SC-009)・新表の CHECK が data-model の値集合と一致
- [x] T020 [P] migration head を直書きしている既存テスト **10 本**を 0023 に更新する(research D18 の「head を使うので不要」は誤りで、2026-11-02 の grep で検出。040/054 と同型): `features/tests/unit/test_feature020_leak_guard.py:34` / `test_feature021_leak_guard.py:29` / `test_feature023_leak_guard.py:35` / `test_feature040_leak_guard.py:31` / `test_feature066_leak_guard.py:38` / `test_feature084_leak_guard.py:49` / `test_materialize_fallback_columns.py:65`(`startswith("0022_")` → `"0023_"`)、`test_feature086_leak_guard.py:173` / `test_purchase_leak_guard.py:44`(`== "0022_decision_quotes"` → `"0023_jra_official_source"`)、`live/tests/unit/test_no_schema_change.py:15`。**0024(US4・T090)で再び `0024_` に上げる**ので、T090 の完了条件にも含める。**T018/T019 と同じ段で更新するが、本番 DB の head は Phase 11 T100 まで 0022 のまま**(テストは repo の migration ディレクトリの末尾を見るので本番と独立)

**Checkpoint**: `cd db && uv run pytest -q` 緑。**本番 DB への 0023 適用は Phase 11 T100(R1・開催終了後)**。

---

## Phase 4: Foundational C — 共通 writer の挙動変更(plan Phase 1)⏸ 開催終了後

**Purpose**: FR-016 の規則を netkeiba 経路にも課す。**JRA の書込みはまだ無い**状態で netkeiba 経路を新規則に切り替え、1 開催日の通常運用で異常がないことを確かめる(研究 D20)。既存テストの一部は契約変更として理由つきで更新する(研究 D19)。

**⚠️ 先にテストを書く**: T021〜T027 は netkeiba 経路だけで赤になる(衝突の 2 行目挿入・取消復帰・部分オッズ更新・複勝 NULL 化・来歴なし)。

### Tests(先に書く・赤を確認)

- [x] T021 ⏸ [P] `scrape/tests/integration/test_precedence_entries_matrix.py`: netkeiba `upsert_entries` を同一レースに {取消前の古い出馬表→取消後→古い出馬表} / {同じ入力 2 回} / {体重 None の再投入} / {騎手変更後に古い出馬表} で流し、`entry_status` が戻らない・体重の対が保持・騎手の `observed_at` 規則・`status_rejected` が Counts に出る(SC-010 の出走表部分)
- [x] T022 ⏸ [P] `scrape/tests/integration/test_precedence_collisions_db.py`: 既存 `race_horses` に canonical 行がある状態で netkeiba が同馬番の `nk:` 行を返す→**2 行目が挿入されず**レース隔離・`quarantined_collision` が Counts・既存行とマスタが不変・`merge_candidates` に対が出る。逆(nk: 先行→canonical)も同じ(SC-011 の netkeiba 側)
- [x] T023 ⏸ [P] `scrape/tests/integration/test_precedence_odds_matrix.py`: `update_odds` / `update_place_quote` に {started 全頭揃わない入力→skip 全体・時刻不変} / {`official_at` None で保存に時刻あり→skip} / {逆行→skip} / {同時刻同値→write・来歴更新} / {同時刻異値 netkeiba 同士→write} / {結果確定後→`settled_fill` は odds NULL 行のみ・来歴不変} / {複勝の時刻不明で保存 `official_at` を NULL にしない}(SC-004/010)・**公式 8:22 → 同時刻同値 netkeiba(値 write・来歴 source は `jra_official` のまま)→ 同時刻異値 netkeiba(skip+conflicts)**
- [x] T024 ⏸ [P] `scrape/tests/integration/test_provenance_partial_update.py`: A 馬だけ体重変更・B 馬は旧値・騎手だけ変更・NULL 保持で、`race_horse_field_provenance` の各 (horse, group) の `source` / `observed_at` / `ingestion_job_id` が真実、`race_field_provenance.win_odds.official_at == races.win_odds_official_at`(INV-P1〜P5・data-model §2)
- [x] T025 ⏸ [P] `scrape/tests/integration/test_results_modes.py`: `_insert_results(mode="per_horse_legacy")` が現行どおり(馬単位・`corner_orders` / `running_style` の NULL 補完のみ)・`mode="race_atomic"` は既存行 1 行で挿入 0+`results_existing_races`・1 行不正で挿入 0・初回挿入で来歴 `results` が書かれる(FR-014・contracts/precedence.md §5)
- [x] T026 ⏸ [P] `scrape/tests/integration/test_payout_sets.py`: `upsert_official_win_payouts` / `upsert_exotic_odds` に source 引数と `complete` 集合置換(同着 2 行・訂正で旧行削除・同額は値不変で source と来歴だけ移る・`not_published` で既存行を消さない・来歴 `jra_official` のレースに netkeiba は `skipped_official_payouts`)(FR-015)・`parse_failed` の券種だけ無書込みで他券種は書かれ job は PARTIAL
- [x] T027 ⏸ [P] `scrape/tests/integration/test_precedence_concurrency.py`: 2 セッション+`threading.Barrier` で {結果確定 vs オッズ更新} / {公式払戻 vs netkeiba 再投入} / {新規 `races` 行の作成競合=ON CONFLICT DO NOTHING} / {逆順の複数レース=race_id 昇順で deadlock なし} / {値更新後・来歴更新前に例外→両方 rollback} / **{失敗後の再実行=1 回成功した場合と業務値・来歴が同等}** / **{HTTP 待機中にレース行ロックを保持しない=fake fetcher の中で `pg_locks` を読み当該 races 行の行ロックが 0}**(contracts/precedence.md §1・SC-010・codex Q2-3/SC-010)

### Implementation

- [x] T028 ⏸ `scrape/src/horseracing_scrape/provenance.py`: `write_horse_provenance(session, race_id, horse_id, group, *, source, observed_at, job_id)` / `write_race_provenance(session, race_id, group, *, source, official_at, observed_at, job_id)` / `read_provenance(session, race_id) -> ProvenanceSnapshot`(ロック下の再読込用)。UPSERT(ON CONFLICT DO UPDATE)・同一 savepoint 前提(INV-P1)
- [x] T029 ⏸ `scrape/src/horseracing_scrape/upsert.py`: 共通ロック helper `lock_race(session, race_id, *, race_defaults) -> RaceSnapshot`(① `INSERT races … ON CONFLICT DO NOTHING` ② `FOR UPDATE` ③ race_results の有無・started 集合・既存出走行・来歴を再読込)。**全 writer のロック取得責任を明記**: `upsert_entries` / `update_odds` / `update_place_quote` / `_insert_results` / `upsert_laps` / `upsert_official_win_payouts` / `upsert_exotic_odds` は自分で `lock_race` を呼ぶか `RaceSnapshot` を受け取る(DB 状態に依存する判定はロック下の再読込で行う)。複数レースは**外側のループで race_id 昇順**。隔離時は `lock_race` が新規作成した races 行も savepoint rollback で消える(契約 §1)。**先に既存呼出しと互換のシグネチャを固定**: 新規引数はすべて keyword-only で既定値あり(`source=Source.NETKEIBA`・`observed_at: datetime | None = None`=None なら従来どおり `now()` を使う **legacy** 経路として docstring に明記・`job_id=None`)。pipeline(T034)は実 fetch 時刻を渡し、`live/near_post.py` は無改修(codex Q1-5/Q2-3)
- [x] T030 ⏸ `scrape/src/horseracing_scrape/upsert.py` `upsert_entries(session, scraped, *, source=Source.NETKEIBA, observed_at=None, job_id=None, page_kind="entries")`: `lock_race` → `precedence.check_entry_collisions` → 隔離なら無書込み(マスタも作らない)+Counts.extra に `quarantined_collision` / `merge_candidates` → `field_transition` で **`race_static` / `post_time`(races 側・来歴 `race_static` / `post_time`)**と `entry_status` / `jockey` / `trainer` / `entry_static` / `body_weight`(対)を決定 → 値+来歴を同一 savepoint で。現行の「entry_status を scraped 値で上書き」と `fill_if_null` 個別 COALESCE を遷移表に置き換える。Counts.extra に `status_transitions` / `status_rejected` / `static_mismatch` / `post_time_changes` / `jockey_changes`(codex Q2-4/Q2-8)
- [x] T031 ⏸ `scrape/src/horseracing_scrape/upsert.py` `update_odds` / `update_place_quote`(keyword-only `source` / `observed_at=None` / `job_id=None`): ロック後に結果の有無を再読込・`odds_decision` で判定・`write` は全馬+`races.*_odds_official_at/observed_at`+来歴 `win_odds` / `place_odds`(**採用元は `OddsDecision.adopted_source`**)を同一 savepoint・`settled_fill` は netkeiba win のみ来歴不変・複勝の「時刻不明なら保存 `official_at` を NULL」を削除。Counts.extra に `odds_written_races` / `odds_partial_field` / `odds_skipped_stale` / `odds_conflicts`
- [x] T032 ⏸ `scrape/src/horseracing_scrape/upsert.py`: `backfill_results` の挿入本体を `_insert_results(session, race_id, rows: Sequence[ResolvedResultRow], *, mode, source=Source.NETKEIBA, observed_at=None, job_id=None, snapshot: RaceSnapshot | None = None)` に抽出(`per_horse_legacy` が現行挙動・`race_atomic` を新設・snapshot が無ければ自分で `lock_race`)。`backfill_results` は `per_horse_legacy` を呼び初回挿入時のみ来歴 `results`。`upsert_laps(session, race_id, scraped, *, source=Source.NETKEIBA, only_if_absent=False, observed_at=None, job_id=None)`。Counts.extra に `results_inserted_races` / `results_existing_races` / `results_mismatch` / `laps_inserted` / `laps_existing`
- [x] T033 ⏸ `scrape/src/horseracing_scrape/upsert.py` `upsert_official_win_payouts` / `upsert_exotic_odds`(keyword-only `source` / `observed_at` / `job_id` / `block_state` 引数・既定は現行挙動): `payout_block_decision` に従い `complete` のみ集合置換・同値行は `source` と来歴のみ更新(`IS DISTINCT FROM` で値は触らない)・来歴 `win_payouts` / `exotic_payouts` が `jra_official` なら netkeiba は skip(`skipped_official_payouts`)・`parse_failed` の券種だけ無書込みで他券種は継続。`apply_result_page_settlement`(pipeline.py)は結果ページ由来の `block_state` を `complete` / `not_published` に写して渡す。Counts.extra に `payout_complete` / `payout_not_published` / `payout_not_sold` / `payout_parse_failed` / `payout_rows_written` / `payout_rows_removed`
- [x] T034 ⏸ `scrape/src/horseracing_scrape/pipeline.py`: `_run_job(session, *, source: Source = Source.NETKEIBA, ...)`。job 行を最初に flush して ID を来歴 FK に渡す・レースごと `begin_nested()`・`quarantined > 0 → PARTIAL`(errors=0 でも)・summary の `Counts.extra` に contracts/cli.md の netkeiba 側 6 キー(`quarantined_collision` / `status_rejected` / `odds_partial_field` / `odds_conflicts` / `skipped_official_payouts` / `static_mismatch`)。既存呼び出し(`scrape_entries` / `scrape_odds` / `scrape_results` / `scrape_exotic_odds` / `scrape_laps`)は `observed_at=fetch 時刻` を渡す(FR-006・研究 D16)
- [x] T035 ⏸ 既存テストを契約変更として更新(各ファイル冒頭に「176 で変更した契約」と理由を 1 行): `scrape/tests/integration/test_entries_never_erase.py`(取消の一方向・体重の対)・`test_place_quote_upsert.py`(時刻不明の NULL 化廃止)・`test_win_odds_provenance.py`(部分更新 skip・来歴表)・`test_odds.py` / `test_odds_already_final.py`(settled_fill の分離)・`test_final_odds_and_win_payouts.py`(source 引数・同値採用)・`test_unmapped_debut.py` / `test_ingest_identity.py`(衝突隔離)。**変えないテストは無改修で緑**を確認
- [x] T036 ⏸ `ops/` と `live/` の呼び出しが**無改修**で通ることを受入条件にする(`cd ops && uv run pytest -q`・`cd live && uv run pytest -q`)。`live/near_post.py` の差分ゼロ(FR-018・研究 D19)。T029 の既定値設計(新規引数 keyword-only・None=legacy)がこれを保証する

**Checkpoint**: `cd scrape && uv run pytest -q`(unit+integration)・`cd ops && uv run pytest -q`・`cd live && uv run pytest -q`・`cd db && uv run pytest -q` 全緑。**本番切替は Phase 11 T100〜T101(R1〜R2)**。

---

## Phase 5: Foundational D — 取得層(JraFetcher・政治性・捕捉時間帯・policy)(plan Phase 2a)

**Purpose**: すべての公式 HTTP が共有スロット・cooldown・捕捉時間帯を通る土台。`HttpFetcher` は不変。⏸ は `politeness.py` / `ops` / policy ファイル。

### Tests(先に書く)

- [x] T037 [P] `scrape/tests/unit/test_jra_politeness.py`: `throttle_key("https://www.jra.go.jp/JRADB/accessD.html") == "jra.go.jp"`・`notjra.go.jp` は別鍵・`source_interval_s("jra.go.jp")` が env 未設定/非数値/NaN/inf/9.9 で `SourceIntervalUnconfigured`、`10` で 10.0、`60` で 60.0。netkeiba・その他ホストの既存テスト(`test_politeness_contract.py`)は無改修で緑(SC-006)
- [x] T038 [P] `scrape/tests/unit/test_jra_fetch_classification.py`(httpx `MockTransport`): contracts/fetch-and-parse.md §1 の応答表(200 正常 / 200 パラメータエラー画面 / 3xx→error013 / 3xx その他 / 400 / 403 / 429 / 503+Retry-After 秒と HTTP-date / 5xx / 接続エラー / cp932 復号失敗)が指定の例外と cooldown の有無(`RequestPoliteness.record_refusal` 呼び出し)を満たす・POST は再送しない・索引 GET は 1 回だけ再試行・**3xx は追跡しない(3xx 後の追加送信 0)**・`requests_sent` が robots と再試行を含めて数える・**robots キャッシュ: 24h 内はヒットで送信 0、期限切れで 1 件再取得、robots の 403/429 で `FetchRefused`+停止**(codex Q1-6/Q2-7)
- [x] T039 [P] `scrape/tests/unit/test_jra_fetch_archive.py`: kind=`odds_win` / `odds_exotic` は `archive_dir` があっても書かれない・`entries` / `results` は書かれ `url.txt` に cname 平文が無く sha だけ・鍵が `sha256(url + "\n" + cname)`(憲法 V・FR-001)
- [x] T040 [P] `scrape/tests/unit/test_jra_fetch_decode.py`: header charset 優先→meta・`shift_jis` / `sjis` / `x-sjis` / `windows-31j` / `ms932` を cp932・cp932 拡張文字(`①` `髙`)は復号成功・壊れたバイトは `JraDecodeError`・accessJ の UTF-8 はそのまま
- [x] T041 ⏸ [P] `scrape/tests/integration/test_jra_send_control.py`(SC-012・HTTP 単位): 索引 GET・POST・再試行・robots・**馬場断片 GET・accessJ POST** のそれぞれが `pre_request` を通る(呼び出し回数=送信数)・**別プロセス**(`subprocess` で起動した第 2 プロセス)と競合しても送信間隔 ≥ 設定値(プロセス内同期では合格させない)・DB 障害(`NoSharedLimiter`)で 0 件・`HORSERACING_SHARED_LIMITER=0` で 0 件・policy 欠損/不正値で 0 件・**スロット待ち中に捕捉時間帯が開くと次の送信で `CaptureWindowRefused`**・cooldown 中は待たずに拒否・3xx 後の追加送信 0(codex SC-012)
- [x] T042 [P] `scrape/tests/unit/test_jra_navigate.py`(fixture 入力): `index(kind)` の固定 cname(D `pw01dli00/F3` O `pw15oli00/6D` S `pw01sli00/AF` H `pw01hli00/03` I=accessI GET・**コード定数として持つことは許可**)・`day_links` が「4回東京11日」+日付で `--date` と照合し一致リンクだけ返す・無ければ `unsupported_date`・`race_links` がオッズ開催日ページから 12 件・**取得した動的 cname は `JraPageRef` 以外に出ない**(`repr` に出さない・pickle 不可)・`walk` が `JraParameterError` で 1 回だけ辿り直し `rewalks=1` を返し 2 回目で失敗

### Implementation

- [x] T043 ⏸ `scrape/src/horseracing_scrape/politeness.py`: `_JRA_KEY = "jra.go.jp"`・`throttle_key` のホスト判定(`jra.go.jp` / `.jra.go.jp` 境界つき)・`source_interval_s` の JRA 鍵限定の必須化(env `HORSERACING_JRA_MIN_INTERVAL_S`・NaN/inf/<10 拒否・`SourceIntervalUnconfigured`)・`RequestPoliteness(wait_out_cooldown: bool = True)` を足し False なら cooldown 中は `FetchRefused` を即送出。netkeiba 鍵・その他(0.0)の経路はバイト不変(研究 D3)
- [x] T044 ⏸ `scrape/src/horseracing_scrape/capture_window.py`: `ops/src/horseracing_ops/capture_window.py` から純粋計算(`CaptureWindow` / `LEAD_S` / `JST` / `window_from_post_times`)と読込(`capture_dates(path)` / `window_for_day(session, day)` / `active_capture_window(session, now, path)`)を移設し、`CaptureWindowRefused(PolitenessRefused)` も scrape に定義。`ops/src/horseracing_ops/capture_window.py` は同一オブジェクトの再 export+`refusal_detail`+ops 既定 policy パスだけ残す。`ops/tests/integration/test_capture_window.py` と `test_boundary.py`(ops は scrape を import してよい・scrape は ops を import しない)が無改修で緑(研究 D4)
- [x] T045 `scrape/src/horseracing_scrape/jra/fetch.py`: `JraFetcher(policy: RequestPoliteness, capture_guard, archive_dir, client=httpx.Client(follow_redirects=False, timeout=20.0))`。`get(url, *, kind)` / `post(url, cname, *, kind)`・送信前 4 手順(guard→pre_request→guard→送信)・応答分類表・cp932 strict 復号・archive 許可制(kind 別)・`requests_sent`。共有は `RequestPoliteness` / `robots_cache.shared_cache()` / archive 書込み関数のみ(`HttpFetcher` 不変・研究 D2)。**fake policy での先行開発は開催中でも可、実 `RequestPoliteness`(`wait_out_cooldown=False`)との統合と checkpoint は T043/T044 の後**(codex Q1-4)
- [x] T046 `scrape/src/horseracing_scrape/jra/navigate.py`: `index(kind)` / `day_links(index_page, date)` / `race_links(day_page)` と `walk(fetcher, kind, date, *, rewalks_left=1)`(`JraParameterError` で索引から 1 回だけ辿り直し・2 回目で FAILED)。cname は `JraPageRef` にだけ持つ(FR-001)。**`rewalks` と `stopped_for_capture_window` を `WalkReport` で返し、CLI が summary に載せる**(codex Q2-8)
- [x] T047 `scrape/src/horseracing_scrape/jra/policy.py`: 起動時検証 `validate_startup(policy_path, env, database_url) -> StartupContext`: (1) policy の `jra_official_interval_seconds` が数値・有限・≥10 (2) env が policy 値以上(無ければ写す・短ければ `would_shorten_source_interval`) (3) `HORSERACING_SHARED_LIMITER` 無効化拒否・`background_policy` が None を返す経路は拒否 (4) DB 到達。失敗は exit 2 で HTTP 0 件(contracts/cli.md「共通の起動時検証」)。`scrape/tests/unit/test_jra_policy_startup.py` で 4 失敗条件+旧 policy(キー無し)で CLI だけ失敗し他経路は影響なし
- [x] T047a `scrape/src/horseracing_scrape/jra/cli.py` の**共通骨格**(writer 非依存・開催中でも可): `register_jra_subcommands(sub)`・共通オプション(`--date` / `--dry-run` / `--json` / `--database-url`)・`policy.validate_startup` → `JraFetcher`(capture_guard=`active_capture_window`)→ `navigate.walk` の流れ・summary JSON の整形・exit code(0/1/2)・`unsupported_date` の SKIPPED 記録。**dry-run は `_run_job` の job 作成と業務 writer に入る前で分岐**(取得と解析まで・throttle 行のみ=研究 D17・codex Q2-2)。各 US はここに subcommand を足す。**`scrape/src/horseracing_scrape/cli.py` への登録 1 行は ⏸**(登録後は稼働 CLI が読む=codex Q1-3)
- [x] T048 ⏸ `scripts/source_operations_172.py`: `load_policy` が `jra_official_interval_seconds` を**任意キー**として受理(欠落時は None・既存の未知キー拒否は維持)、`apply_policy` が存在時だけ `HORSERACING_JRA_MIN_INTERVAL_S` に写す(継承値がより長ければ採る=172 と同じ短縮禁止)。`scripts/tests/test_source_operations_172.py`(既存があれば追記): 旧 policy は通る・新 policy+旧 reader の失敗条件を記録・`schema_version` は `172-v1` 据え置き(研究 D3 配布順序 ①②)
- [x] T049 [P] `scripts/jra_ops.sh entries|odds|results|payouts|baba|fetch|audit <date> [extra args]`: `near_post_ops.sh` と同型で policy → env → `uv run --project scrape python -m horseracing_scrape jra-<cmd> --date <date>`。下限 10 の自己検証・`set -euo pipefail`
- [ ] T050 ⏸ `config/source-operations.json` に `"jra_official_interval_seconds": 10` を追加(**T048 の reader が main に入り、稼働中 wrapper が再起動された後**=研究 D3 ③)。追加前後で `scripts/source_operations_172.py` の status 出力が変わらないことを確認

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_jra_*.py tests/integration/test_jra_send_control.py` 緑・`cd ops && uv run pytest -q` 緑。

---

## Phase 6: User Story 1 — 出馬表・変更情報・単複オッズ(P1)🎯 MVP

**Goal**: `jra-entries` / `jra-odds` が公式ページから既存契約に書き、`nk:` を 1 行も作らず、衝突はレース単位で隔離し、オッズは `official_at` / `observed_at` を分けて残す。

**Independent Test**: fixture(2026-11-01 東京・出馬表・変更情報・11R 単複 8:22)を一時 PostgreSQL に投入し、parity fixture(netkeiba 側)と出走馬集合・馬番・騎手・調教師・斤量が比較可能レースで 100% 一致、取消馬は状態まで一致、`nk:` 新規 0、`official_at == 08:22 JST`(SC-001/002/004/011)。

### Tests(解析器は開催中でも可・writer 統合は Phase 4 後)

- [x] T051 [P] [US1] `scrape/tests/unit/test_jra_parse_entries.py`: fixture `entries_tokyo_20261101` から `JraDayPage[JraEntry]`(12 ブロック・見出し「2026年11月1日（日曜）4回東京11日」→ race_id `2026050411NN`・枠/馬番/馬名+10 桁 ID/性齢/負担重量/騎手+5 桁/調教師+5 桁/馬体重と増減/発走時刻/距離・コース)。取消行(`<td class="num"><strong class="red">取消</strong></td>`)は `horse_number=None`・`entry_status=cancelled`。単勝オッズ列は読まない。派生 fixture(`h_weight` 列削除・**出馬表の別日見出し版**=T007)は `parse_failed` / `ParseError` で部分値なし、**1 ブロックの破損が他ブロックを巻き込まない**(SC-007)。`JraDayPage` が `races_published` / `races_not_yet` / `races_cancelled` / `races_parse_failed` の件数を返す
- [x] T052 [P] [US1] `scrape/tests/unit/test_jra_parse_changes.py`: fixture `changes_20261101` から場・R ごとの `JraChange`(`cancel` / `exclude` / `post_time` / `jockey`)・馬番と馬名・新発走時刻・新騎手名。該当なしの日は空 tuple(ParseError にしない)
- [x] T053 [P] [US1] `scrape/tests/unit/test_jra_parse_odds.py`: fixture `odds_tokyo11r_20261101_0822` から `JraWinOdds(kind=current, official_at=2026-11-01 08:22 JST)`・`odds_tan` / `odds_fuku`(min-max)/ `h_weight`・全角数字 NFKC。「前日最終オッズ」見出しは `kind=prev_day_final`。`cell time` 削除版は `ParseError`
- [x] T054 ⏸ [P] [US1] `scrape/tests/integration/test_jra_entries_upsert.py`: fixture 投入で races / race_horses / horses(新規馬に `birth_year` / `sex` / `data_source='jra_official'`)/ jockeys / trainers が書かれ、既存同 ID 別名のマスタは不変で **馬・騎手・調教師それぞれの `master_name_mismatch` が全件**報告される・未知 ID で `quarantined_unknown_id`・`race_date < 2025-10-11` で `skipped_pre_netkeiba_era`・id_mappings に行が増えない・`nk:` 新規 0(SC-002)・来歴 5 群が `source=jra_official`・**構造破損ブロックは業務書込み 0 で正常ブロックは書かれる**(SC-007)
- [x] T055 ⏸ [P] [US1] `scrape/tests/integration/test_jra_collisions_both_ways.py`(SC-011): {netkeiba `nk:` 先行→公式} / {公式先行→netkeiba 未解決 mapping} / {同馬番別 ID} / {同 ID 別馬番} / {取消馬番未解決} で二重行 0・隔離レースの既存行が 1 行も変わらない(全列スナップショット比較)・`merge_candidates` に (nk: id, 公式 id) 対・承認済み mapping は `approved_mapping_pending_rekey` に分かれ**どちらも書かれない**
- [x] T056 ⏸ [P] [US1] `scrape/tests/integration/test_jra_entries_matrix.py`: {netkeiba→公式→netkeiba} / {公式→netkeiba→公式} / {同一入力 2 回} / {古い公式出馬表で取消解除を試みる→reject} / {静的項目が違う netkeiba 再投入→keep+`static_mismatch`} / {変更情報の騎手変更後に古い出馬表→reject} / {変更情報の発走時刻変更後の netkeiba→keep}(SC-010 出走表部分)
- [x] T057 ⏸ [P] [US1] `scrape/tests/integration/test_jra_odds_upsert.py`(SC-004): 8:22 投入→値と `races.win_odds_official_at == 08:22`・`observed_at` は fetch 時刻・来歴 `win_odds` / `place_odds`。同 fixture 再投入で値と `official_at` 変化 0・`observed_at` と job だけ進む。netkeiba 8:22 異値→skip+`odds_conflicts`。netkeiba 8:30→write。`prev_day_final` は `odds_prev_day_final_ignored`。結果確定後は skip。started の一部欠落は `odds_partial_field`
- [x] T058 ⏸ [P] [US1] `scrape/tests/integration/test_jra_cli_entries_odds.py`: `jra-entries --date 2026-11-01 --dry-run` が業務表・ingestion_jobs・来歴の行数を変えず、**SQLAlchemy の `before_cursor_execute` で捕捉した DML が業務表・job・来歴に 1 文も出ない**(throttle 行のみ)・`--json` の形が contracts/cli.md の例と一致・exit 0/1/2・`--race-id` 指定でそのブロックだけ書く・`unsupported_date` は SKIPPED で exit 0・隔離 1 件で PARTIAL exit 1・`rewalks` / `stopped_for_capture_window` / `races_*` が summary に出る。fetcher は fixture を返す fake(ネットワーク遮断)(codex Q2-2)

### Implementation

- [x] T059 [US1] `scrape/src/horseracing_scrape/jra/parse/__init__.py` / `_common.py`(見出し正規表現・NFKC 数値・`race_header` 分割・`build_race_id` 呼び出し・`JraRaceBlock` 判定ヘルパ・`JraDayPage.state_counts()` で `races_published` / `races_not_yet` / `races_cancelled` / `races_parse_failed`)。**T060〜T062 はこの後**
- [x] T060 [P] [US1](T059 後) `scrape/src/horseracing_scrape/jra/parse/entries.py` `parse_entries_day(html, *, expected_date) -> JraDayPage[JraEntry]`(contracts/fetch-and-parse.md §3)
- [x] T061 [P] [US1](T059 後) `scrape/src/horseracing_scrape/jra/parse/changes.py` `parse_changes(html, *, expected_date) -> tuple[JraChange, ...]`
- [x] T062 [P] [US1](T059 後) `scrape/src/horseracing_scrape/jra/parse/odds.py` `parse_win_odds(html, *, expected_date) -> JraWinOdds`
- [x] T063 ⏸ [US1] `scrape/src/horseracing_scrape/jra/upsert_jra.py` `upsert_jra_entries(session, day_page, changes, *, observed_at, job_id, race_ids=None) -> Counts`: レースごと(race_id 昇順)に era check → id check(**公式 ID が抜けない行が 1 つでもあればレース隔離 `quarantined_unknown_id`**)→ `lock_race` → **ロック下で**取消馬の馬番復元(変更情報の「N番 馬名」+既存行の公式 ID+名前 key・不能なら `quarantined_cancel_unresolved`)→ `check_entry_collisions` → マスタ作成(**公式 ID 抽出済みかつ DB に未登録の ID のみ**・既存は名前照合で `master_name_mismatch`)→ `upsert_entries(source=JRA_OFFICIAL, page_kind="entries")` を呼ぶ(共通 writer)。`apply_jra_changes(session, changes, *, observed_at, job_id)`: era check → `lock_race` → `post_time`(来歴 `post_time`)・`jockey`(名前→既存出走行の騎手 ID 解決・不能なら報告)・`cancel` / `exclude`(page_kind=changes)。summary に `status_transitions` / `post_time_changes` / `jockey_changes`(codex Q2-3/Q2-4)
- [x] T064 ⏸ [US1] `scrape/src/horseracing_scrape/jra/upsert_jra.py` `upsert_jra_odds(session, odds: JraWinOdds, *, observed_at, job_id) -> Counts`: `prev_day_final` は `odds_prev_day_final_ignored`・era check(`skipped_pre_netkeiba_era`)・`lock_race` → **ロック下で**馬番→horse_id を既存 started 行から解決(馬番重複など**曖昧な解決は `quarantined_collision` で拒否**し、単なる不足=`odds_partial_field` と区別)・`update_odds` / `update_place_quote`(source=JRA_OFFICIAL, snapshot 渡し)を呼ぶ・体重は `body_weight` 群で `upsert_entries` 経路の対規則。summary に `odds_written_races`(codex Q2-4)
- [x] T065 ⏸ [US1] `scrape/src/horseracing_scrape/jra/cli.py`: T047a の骨格に `jra-entries --date [--race-id ...]`(job `jra_entries` + `jra_changes`・同じ取得ページを共有・**`requests_sent` は `jra_entries` 側に計上し `jra_changes` は `requests_sent=0` + `shared_fetch_with="jra_entries"`**)と `jra-odds --date [--race-id ...]`(job `jra_odds`・`--race-id` 無しは全レース)を足す。各 job は自分の ID を writer に渡す(codex Q2-6)
- [x] T066 [P] [US1] `scripts/export_parity_fixture.py --day --venue --source-archive artifacts/scrape_archive/<day> --out`: **稼働 DB は読まない**。netkeiba の取得 archive(`artifacts/scrape_archive/<day>`・ops/174 が保存している raw HTML)から該当日の entries / odds / results ページを選び、**既存の netkeiba 解析器**で解析した結果を「取得段階(結果前/結果後)・取得時刻・ID 解決状態(`id_mappings` の当時の mapped/unmapped は archive に無いので `nk:` 判定は解析時の mapping を使い `resolved_at` を記録)」つき JSON に書き出す。後日の DB export では発走前状態を復元できない(結果反映後に上書き済み)ので archive を正本にする(codex Q1-7)。**archive に該当日の netkeiba ページが無ければその SC は「未検証」と記録し次の開催日で取り直す**
- [x] T067 ⏸ [US1] `scrape/tests/fixtures/jra/parity/netkeiba_20261101_tokyo_pre_result.json` を T066 で**一度だけ**書き出し(archive 由来)、`scrape/tests/integration/test_jra_parity_entries.py`: 一時 DB に netkeiba fixture を投入→公式 fixture を `upsert_jra_entries` で投入→**比較可能レース**(隔離されなかった)で出走馬集合・馬番・騎手・調教師・斤量 100% 一致・取消馬は状態まで・隔離レースは「比較不能」として分母から分けて件数(SC-001)。**合格条件を固定**: 対象 12 レース中、比較可能 ≥ 1 かつ全件隔離では不合格、隔離数と理由を出力。比較可能率と一致率を別々に assert(codex SC-001)

**Checkpoint**: US1 の unit+integration 緑。実 DB への投入は Phase 11 T102〜T104(R3〜R5)。

---

## Phase 7: User Story 2 — レース結果と払戻金(P1)

**Goal**: `jra-results`(payouts 同梱)/ `jra-payouts` が結果をレース単位の初回挿入で、払戻を 7 券種の集合として書く。人気は保存しない(利用者決定 2026-11-02)。

**Independent Test**: fixture(2026-10-31 東京・結果・払戻)を一時 PostgreSQL に投入し、parity fixture と着順・タイム・馬体重・単勝払戻額 100% 一致、同着レースは勝ち馬の数だけ行(SC-003)。

### Tests

- [x] T068 [P] [US2] `scrape/tests/unit/test_jra_parse_results.py`: fixture `results_tokyo_20261031` から `JraDayPage[JraRaceResult]`(着順 数字/中止/失格/取消/除外・枠・馬番・馬名+ID・タイム・着差・コーナー通過順・推定上り・馬体重と増減・人気・ラップ・上り 4F/3F・天候・馬場・`refund_area` → `JraPayoutSet`)。別日見出し派生は `parse_failed`。レース 1 件の失敗が他ブロックを巻き込まない
- [x] T069 [P] [US2] `scrape/tests/unit/test_jra_parse_payouts.py`: fixture `payouts_tokyo_20261031` から券種ブロック(`refund_unit` 内 `dl/dt/dd`・`div.line` 複数=同着・「発売なし」→`not_sold`・返還→`refund_only`・見出しなし→`not_published`・組番重複派生→`parse_failed`)・円/100 → 倍率・枠連は `bracket_quinella` で解析のみ・7 券種の写像(`BetType` 値)
- [x] T070 ⏸ [P] [US2] `scrape/tests/integration/test_jra_results_upsert.py`: 結果未保存レースに `race_atomic` で全行挿入+来歴 `results`・既存 1 行で挿入 0+`results_mismatch`(内容に馬番・列・両値)・体重は `body_weight` 群・**`race_horses.popularity` が不変**(公式は書かない)・既存 odds 不変・`apply_final_odds` 非呼出・ラップは行が無いときだけ+`laps_existing`・`race_date < 2025-10-11` 無書込み(**entries / odds / results / payouts 単独 / changes の全公式経路で**)・結果経路でも衝突検査で隔離(FR-014/019・SC-010・codex Q2-4/SC-010)
- [x] T071 ⏸ [P] [US2] `scrape/tests/integration/test_jra_payouts_upsert.py`: 単勝同着 2 行・6 券種が `exotic_odds` に `source=jra_official` `coverage_scope=partial`・枠連は `not_mapped_bet_types` のみ・訂正で勝ち馬集合が変わると旧行削除・同額の netkeiba 既存行は値不変で `source=jra_official_result` と来歴だけ移る・`not_published` で既存行不変・返還は `refunds` 件数のみ・その後の netkeiba 払戻は `skipped_official_payouts`(FR-015)
- [x] T072 ⏸ [P] [US2] `scrape/tests/integration/test_jra_results_matrix.py`: {netkeiba 結果→公式} / {公式→netkeiba 結果(馬単位追加・NULL 補完は現行どおり)} / {公式払戻→netkeiba 結果ページ精算} / {並行: 結果確定 vs 公式オッズ}(SC-010)
- [x] T073 ⏸ [P] [US2] `scrape/tests/integration/test_jra_cli_results.py`: `jra-results --date 2026-10-31` が job `jra_results` + `jra_payouts` を分けて作り、同じ取得ページを共有(**robots キャッシュ有効なら `requests_sent` は 1 場 2 件=索引+開催日・キャッシュ無効なら +1**・`jra_payouts` 側は `requests_sent=0` + `shared_fetch_with`)・払戻 writer は `jra_payouts` の job_id で 1 回だけ呼ばれる(来歴 FK が `jra_payouts` を指す)・`jra-payouts` 単独・dry-run(DML 0)・PARTIAL 判定・払戻 `parse_failed` 券種の分離継続(codex Q1-6/Q2-6)

### Implementation

- [x] T074 [US2](T075 後) `scrape/src/horseracing_scrape/jra/parse/results.py` `parse_results_day(html, *, expected_date) -> JraDayPage[JraRaceResult]`(`refund_area` の払戻解析は T075 の `parse_refund_block` を呼ぶ)
- [x] T075 [P] [US2] `scrape/src/horseracing_scrape/jra/parse/payouts.py` `parse_payouts_day(html, *, expected_date) -> JraDayPage[JraPayoutSet]` と `parse_refund_block(node) -> JraPayoutSet`(状態判定 5 値・券種写像・円/100)
- [x] T076 ⏸ [US2] `scrape/src/horseracing_scrape/jra/upsert_jra.py` `upsert_jra_results(session, day_page, *, observed_at, job_id, race_ids=None) -> Counts`: era check → id check → `lock_race` → **ロック下で衝突検査**(結果経路も §2)→ 全行検証 → `_insert_results(mode="race_atomic", source=JRA_OFFICIAL, snapshot=...)` → 体重(`body_weight` 群・page_kind=results)→ 状態(取消/除外は page_kind=results)→ `upsert_laps(source=JRA_OFFICIAL, only_if_absent=True)` → 人気は保存値との不一致を `results_mismatch` に積むだけ。**払戻は書かない**(T077 を CLI が `jra_payouts` の job_id で別に呼ぶ=codex Q2-6)。summary に `results_inserted_races` / `laps_inserted` / `status_transitions`
- [x] T077 ⏸ [US2](T076 と独立・先に実装可) `scrape/src/horseracing_scrape/jra/upsert_jra.py` `upsert_jra_payouts(session, race_id, payout_set, *, observed_at, job_id) -> Counts`: era check(`skipped_pre_netkeiba_era`)→ `lock_race` → 券種ごとに `payout_block_decision` → 単勝は `upsert_official_win_payouts(source="jra_official_result", block_state=...)`、他 6 券種は `upsert_exotic_odds(source=JRA_OFFICIAL, block_state=...)`、枠連は `not_mapped_bet_types`、返還は `refunds`。summary に `payout_*` 6 キー(codex Q2-4/Q2-8)
- [x] T078 ⏸ [US2] `scrape/src/horseracing_scrape/jra/cli.py`: `jra-results --date [--race-id ...]`(索引 S・job `jra_results` + `jra_payouts`・**同じ解析結果を 2 つの writer に各 job の ID で 1 回ずつ渡す**・`requests_sent` は `jra_results` 側に計上)・`jra-payouts --date`(索引 H・job `jra_payouts`)
- [x] T079 ⏸ [US2] `scrape/tests/fixtures/jra/parity/netkeiba_20261031_tokyo_post_result.json` を T066 で archive から書き出し(**公式投入前の netkeiba 単独の値**であることを manifest に記録)、`scrape/tests/integration/test_jra_parity_results.py`: 着順・タイム・馬体重・単勝払戻額 100% 一致・同着レースの行数・比較可能率と一致率の分離・T067 と同じ合格条件(SC-003・codex SC-003)

**Checkpoint**: US2 の unit+integration 緑。実 DB 投入は Phase 11 T105(R6)。

---

## Phase 8: User Story 3 — 二重情報源の監査(P2)

**Goal**: `jra-fetch`(収集・業務表不変)と `jra-audit`(照合・READ ONLY)。実 DB では **US1/US2 の書込みを有効にする前に**この 2 つで公式取得と照合を実測する(plan 2b)。

**Independent Test**: fixture ディレクトリを入力に `jra-audit` を実行し、フィールド別の比較可能件数・一致率・不一致例・`stored_source`(来歴なしは `null`)・`merge_candidates` が出て DB の行数と `updated_at` が変わらない(FR-023・US3 シナリオ 1/2)。

### Tests

- [x] T080 [P] [US3] `scrape/tests/unit/test_jra_audit_compare.py`: 純関数 `compare_entries(official, stored, provenance)` / `compare_results(...)` / `compare_payouts(...)` が「同じレース・同じ公開段階・解決済み ID」の行だけ比較し、来歴不明・未解決 ID(`nk:`)・公開段階違い・時刻差・公式に無い確定オッズを**別々の分母外カウント**に積む・人気の不一致は `popularity_mismatch`(保存しない前提)・`stored_source` は来歴が無ければ None
- [x] T081 ⏸ [P] [US3] `scrape/tests/integration/test_jra_audit_readonly.py`: 一時 DB に netkeiba parity fixture を投入→`jra-audit --from-dir fixtures` 実行→全業務表+ingestion_jobs+来歴の行数と `max(updated_at)` が前後一致・`SET TRANSACTION READ ONLY` 下で書込み試行が例外になる・JSON 出力に `comparable` / `matched` / `mismatched` / `excluded_by_reason` が分かれる・`nk:` 同名同生年の対が `merge_candidates` に出て書かれない(US3 シナリオ 2)
- [x] T082 [P] [US3] `scrape/tests/unit/test_jra_fetch_cli.py`: `jra-fetch --date --save-dir --kinds entries,results` が DIR に raw+manifest(`raw_sha256` / `cname_sha256` / `fetched_at` / `kind`)を書き、業務表・job に触らない(fake fetcher・一時 DB の行数不変)。**`--kinds` に `odds` を含めても単複オッズページの raw は保存しない**(archive と同じ kind 許可制・契約 §1。時刻つき raw の蓄積はオッズ時系列=憲法 V)。`not_for_archive` のような例外は作らない(codex Q2-1)

### Implementation

- [x] T083 [P] [US3] `scrape/src/horseracing_scrape/jra/audit.py`: `load_saved_pages(dir) -> list[JraResponse]`(manifest 検証つき)・比較純関数 3 本・`AuditReport`(フィールド別 `comparable` / `matched` / `mismatched` / `examples[:20]` / `excluded_by_reason` / `merge_candidates` / `approved_mapping_pending_rekey`)・`render_json(report)`
- [x] T084 [US3](T047a 後・登録 1 行は ⏸) `scrape/src/horseracing_scrape/jra/cli.py`: `jra-fetch --date D --save-dir DIR [--kinds ...]`(起動時検証・`JraFetcher`・**kind 許可制で** raw と manifest 保存・job なし・throttle 行のみ)と `jra-audit --date D --from-dir DIR [--json]`(共通の起動時検証は同じ・`session.execute(text("SET TRANSACTION READ ONLY"))` → parse → compare → stdout)。**exit code: 照合完了は不一致があっても 0、起動時検証失敗は 2**(codex Q2-7)。単複オッズは raw が無いので audit の対象外(entries / results / payouts のみ・注記)

**Checkpoint**: US3 の unit+integration 緑。実 DB では Phase 11 T102(R3)で US1/US2 の書込みより先に使う。

---

## Phase 9: User Story 4 — 馬場情報(P3)(plan Phase 3)

**Goal**: `jra_track_conditions`(0024)+`jra-baba`。保存専用・特徴量不接続(FR-020/021)。

**Independent Test**: 断片 fixture から (venue_code, measured_at, item) ごとに 1 行・再取込で行数不変・値変更で `value_changed`・未来時刻は `rejected_future`・features が静的にも動的にも読まない(SC-008・US4 シナリオ 1/2)。

### Tests

- [x] T085 [P] [US4] `scrape/tests/unit/test_jra_parse_baba.py`: `baba_cushion_20261101` / `baba_moist_20261101` から `JraTrackReading`(`div#rc? title=東京` → 05・`div.time` 「10月31日（土曜）7時00分」+年補正(取得 2026-11-01 → 2026-10-31 07:00 JST)・`span.mg/.m4c` の数値・`data-condition` を `value_text`)。`accessJ_20261101.json` から `going_turf` / `going_dirt` / `weather`(`time_basis=fetched`・クッションの時刻を流用しない)。月が取得月より大きい断片は前年
- [x] T086 ⏸ [P] [US4] `scrape/tests/integration/test_jra_baba_upsert.py`(INV-B1/B2): 同じ断片 2 回で行数不変・`fetched_at` 不変・値を変えた派生で上書き+`baba_value_changed`+`fetched_at` 前進・`measured_at > fetched_at` は `rejected_future` で無書込み・PK 衝突なし(同時刻 4 含水率が別 item)
- [x] T087 [P] [US4] `features/tests/unit/test_jra_leak_guard.py`(静的): `features/src/` 全ソースに `jra_track_conditions` / `JraTrackCondition` / `cushion` / `moisture` / `race_field_provenance` / `RaceFieldProvenance` / `race_horse_field_provenance` / `RaceHorseFieldProvenance` の文字列が無く、`registry` の列名にも含まれない。`features/tests/unit/test_decision_quotes_leak_guard.py` と同型(INV-B3・INV-P4)
- [x] T088 ⏸ [P] [US4] `features/tests/integration/test_forbidden_tables_do_not_move_features.py`(動的・SC-008)を **2 本に分割**: (a) 来歴 2 表版=Phase 4 の完了条件(0023 後・一時 DB に標準 seed → `features materialize` → 来歴 2 表だけに行を入れる → 再 materialize → parquet バイト一致・`source_fingerprint` 不変・active 相当モデルの予測バイト一致)、(b) 馬場表版=0024 後に同じ手順。**導入前後比較は別**: T100(R1)で稼働 DB の active 予測(固定レース集合・同一 artifact)と parquet fingerprint を baseline として記録し、T106(R7)後に同一入力で再計算してバイト一致(研究 D19・codex Q1-4/SC-008)

### Implementation

- [x] T089 [P] [US4] `db/src/horseracing_db/models/track_conditions.py`(新規ファイル) `JraTrackCondition`(data-model §3: PK venue_code/measured_at/item・CHECK 3 本・time_basis・value_num/value_text・published_at・fetched_at・raw_sha256・source 既定 `jra_official`)。**`models/__init__.py` への export は ⏸・T017 の export と同じファイルなので順序づけ**
- [x] T090 [P] [US4] `db/migrations/versions/0024_jra_track_conditions.py`(down_revision=0023): 表作成・downgrade は行があれば中断。`db/tests/test_migration_0024.py`: up/down/up・行あり downgrade 中断。T020 の head 直書き 10 本を `0024_jra_track_conditions` に上げる
- [x] T091 [P] [US4] `scrape/src/horseracing_scrape/jra/parse/baba.py`: `parse_cushion(html, *, fetched_at)` / `parse_moisture(html, *, fetched_at)` / `parse_kaisai_info(json_text, *, fetched_at)`・年補正・`rejected_future` 判定は writer 側
- [x] T092 ⏸ [US4] `scrape/src/horseracing_scrape/jra/upsert_jra.py` `upsert_jra_baba(session, readings, *, job_id) -> Counts`: `measured_at > fetched_at` → `baba_rejected_future`・UPSERT(値不変なら `fetched_at` も触らない・値変更で `baba_value_changed`)・`raw_sha256` 記録
- [x] T093 ⏸ [US4] `scrape/src/horseracing_scrape/jra/cli.py`: `jra-baba --date D`(`/keiba/baba/_data_cushion.html`・`_data_moist.html` GET+`POST accessJ`・job `jra_baba`・0024 未適用なら起動時検証で exit 2 `table_missing`・summary に `baba_rows_written` / `baba_value_changed` / `baba_rejected_future`)

**Checkpoint**: US4 緑。実 DB 投入は Phase 11 T106(R7)。

---

## Phase 10: Polish・回帰・文書

- [x] T094 [P] 全パッケージ回帰: `cd scrape && uv run pytest -q` / `cd db && uv run pytest -q` / `cd ops && uv run pytest -q` / `cd live && uv run pytest -q` / `cd features && uv run pytest -q` / `cd scripts && uv run --project training pytest -q tests`。`ruff check` を scrape / db / ops / features / scripts で緑(quickstart §7)
- [x] T095 [P] OpenAPI 不変: `cd api && uv run pytest -q tests/test_openapi_drift.py`・`cd front && pnpm test -- openapi`・`cd admin && pnpm test -- openapi`・ops の `/status` schema 不変(`ops/tests/integration/test_refresh_day_contract.py` 等緑)(SC-009)
- [x] T096 [P] 境界: `ops/tests/integration/test_boundary.py` に「scrape が ops を import しない」を追加(capture_window 移設の逆依存防止)。既存「ops は live/features を import しない」は不変
- [x] T096a [P] `scrape/tests/integration/test_jra_request_budget.py`(SC-005): fake fetcher で**開催 2 場 × 12R の 1 日分**を `jra-entries` → `jra-odds`(全 24 レース)→ `jra-results` → `jra-baba` の順に流し、全 job の `requests_sent` 合計が robots 1 件を含めて ≤20(オッズ除く)・≤50(オッズ込み)。辿り直し 1 回を注入した場合も上限内(codex SC-005)
- [x] T097 [P] `specs/176-jra-official-source/manual.md`: 運用手順(開催前日 `jra_ops.sh entries` と netkeiba `entries` の順・当日朝 `jra-fetch`→`jra-audit` → `jra-odds --race-id` → 夜 `jra-results` → `jra-baba`・捕捉時間帯の停止と再実行・拒否時の cooldown・`unsupported_date`・dry-run の定義・0023/0024 の適用手順と lock_timeout・ロールバック=JRA 取得停止でスキーマ維持)。174 の `specs/172-source-interval/manual.md` の様式に合わせる
- [x] T098 [P] `scrape/src/horseracing_scrape/jra/README.md`(短く): モジュール配置・contracts へのポインタ・「`HttpFetcher` を触らない」「`precedence.py` は両経路の正本」「fixture は raw・cname 平文を書かない」の 3 禁止事項
- [x] T099 quickstart.md の §1〜§3・§5〜§8 を実行し結果(件数・一致率・バイト一致)を `specs/176-jra-official-source/result-review.md` に転記(実 DB 手順 §4 は Phase 11 で)

---

## Phase 11: Rollout(実 DB・plan D20 の順・各段で停止判断)

**Purpose**: コードは story 順に揃うが、本番は共通安全性 → 読み取り専用の実測 → 限定書込み → 全レース → 結果 → 馬場の順にしか進めない。各 R は前の R の完了条件を満たしてから。**⏸=本番を変える段(migration・稼働 checkout への反映・policy・JRA 書込み)は開催終了後。R2 のような「既存運用の読み取り観察」は開催日に行う**(codex Q1-8)。

- [ ] T100 ⏸ (R1) 開催日の最終レース後、**別 worktree で実装・試験した Phase 4 を稼働 checkout に反映する前に** `cd db && uv run alembic upgrade 0023_jra_official_source`(**`head` は使わない**=0024 が repo にあれば馬場表まで上がる・codex Q1-2)。前後で `alembic_current`・5 表の行数・`information_schema.columns` と制約の差分が許可集合だけであることを `result-review.md` に記録。**SC-008 の baseline**: 固定レース集合(直近開催日 24 レース)の active 予測と `artifacts/features.parquet` の fingerprint を記録。その後 共通コードを稼働 checkout に反映 → api / ops-api / worker を `scripts/stack.sh` で再起動(ops の subprocess は再起動なしで新コードを読むので**順序は migration → コード反映 → 再起動**=140 の教訓)
- [ ] T101 (R2・読み取り観察=開催日に行う) Phase 4 の netkeiba 経路切替後、**1 開催日**の通常運用(ops 更新・174 捕捉・夜の `scrape-results`)で `quarantined_collision` / `status_rejected` / `odds_partial_field` / `odds_conflicts` の件数を ingestion_jobs.summary から集計し異常なしを確認。**この段は本番を変更しない観察で ⏸ の対象外**(codex Q1-8)。異常があれば Phase 4 に戻る(JRA 書込みは未有効)
- [ ] T102 ⏸ (R3) T048 の reader が稼働 wrapper に入った後に T050(`config/source-operations.json` へキー追加)。`scripts/jra_ops.sh fetch <date> --save-dir artifacts/jra_raw/<date>` → `scripts/jra_ops.sh audit <date> --from-dir artifacts/jra_raw/<date>` を 1 開催日分(2 場)実行し `requests_sent` 合計・フィールド別一致率・`stored_source=null` 件数・`merge_candidates` 件数を `result-review.md` に記録(US3)
- [ ] T103 ⏸ (R4) `jra_ops.sh entries <date> --race-id <1 レース>` で限定投入 → 翌日の netkeiba 再投入で両方向再投入(T056 相当)が実 DB でも規則どおりか確認(来歴・`static_mismatch`)。異常なければ全レース
- [ ] T104 ⏸ (R5) `jra_ops.sh odds <date> --race-id <数レース>` → 全レース(1 回)。1 日の全コマンドの `requests_sent` 合計(≤20 / オッズ込み ≤50)を記録(SC-005 の実測・正本は T096a)。捕捉時間帯での停止は T041 の一時環境で検証済みなので**実運用での観測は必須条件にしない**(観測できれば `stopped_for_capture_window=1` を記録・codex Q1-8)
- [ ] T105 ⏸ (R6) 夜 `jra_ops.sh results <date>`。`results_existing_races`(netkeiba が先に入った分)・`results_mismatch` の内容・`payout_complete` 件数・単勝払戻の `source` 分布を記録(SC-003 の実 DB 版は参考・正本は T079)
- [ ] T106 ⏸ (R7) `cd db && uv run alembic upgrade 0024_jra_track_conditions` → `jra_ops.sh baba <date>` → T088(b) を実行。**SC-008 の導入前後比較**: T100 で記録した固定レース集合を同一 artifact で再予測し予測と parquet fingerprint がバイト一致
- [ ] T107 ⏸ (R8) SC-001〜012 の実測値を spec.md「Measurable Outcomes」の各行末に転記・plan.md の Status を「実装完了」に・CLAUDE.md の SPECKIT 区間(Edit で手動・hook 禁止)と memory `feature-176-jra-official-source-plan.md` を更新

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1(Setup)**: 依存なし・開催中でも可
- **Phase 2(純関数)**: Phase 1 の T004(値オブジェクト)に依存・開催中でも可
- **Phase 3(DB 契約)**: モデル・migration ファイルは独立・**本番適用は Phase 11 R1**
- **Phase 4(共通 writer)⏸**: Phase 2・3 に依存。**US1/US2 の writer 統合テストと `upsert_jra.py` をブロック**
- **Phase 5(取得層)**: T043/T044/T048/T050 ⏸ は開催終了後、`jra/fetch.py` / `navigate.py` / `policy.py` / `jra_ops.sh` は Phase 1 後いつでも
- **US1(Phase 6)**: 解析器(T051〜T053・T059〜T062)は Phase 1 後すぐ。writer(T063〜T065)は Phase 4・5 後。parity(T067)は実 DB からの fixture 書出し(開催終了後・読み取り専用)
- **US2(Phase 7)**: 解析器は Phase 1 後すぐ。writer は Phase 4 後(+US1 の `cli.py` 骨格 T065 を共有)
- **US3(Phase 8)**: 比較純関数(T080・T083)は US1/US2 の解析器に依存。CLI は Phase 5 の取得層と **T047a の共通骨格**に依存(T065 を待たない)。**実 DB では US1/US2 の書込み有効化より先に使う(R3)**
- **US4(Phase 9)**: 解析器・モデル・migration は独立。writer と CLI は Phase 5 後。動的 leak 試験(T088)は 0023 後に来歴 2 表・0024 後に馬場表
- **Phase 10(Polish)**: 全 story 後
- **Phase 11(Rollout)⏸**: R1 → R2 → R3 → R4 → R5 → R6 → R7 → R8 の順。前段の完了条件を満たさなければ進めない

### User Story Dependencies

- **US1** は Phase 4/5 だけに依存(他 story なし)。MVP
- **US2** は US1 の `jra/cli.py` 骨格と `upsert_jra.py` のモジュールを共有するが、関数は独立。US1 と並列実装可(同一ファイルの追記は順序づけ)
- **US3** は US1/US2 の解析器を読むだけ。writer 不要=Phase 4 を待たずにほぼ完成できる
- **US4** は他 story と独立。0024 は 0023 の後

### 開催中に進められるもの(⏸ なし)

T001〜T020・T037〜T040・T042・T045〜T047・T049・T051〜T053・T059〜T062・T066・T068〜T069・T074〜T075・T080・T082〜T085・T087・T089〜T091・T094〜T098 の一部(T100〜T107 の rollout はすべて ⏸)(回帰は共通コード変更後に再実行)。

---

## Parallel Example: User Story 1(Phase 4・5 完了後)

```bash
# 解析器 3 本は別ファイル・並列
Task: "parse/entries.py parse_entries_day"           # T060
Task: "parse/changes.py parse_changes"               # T061
Task: "parse/odds.py parse_win_odds"                 # T062
# 統合テスト 5 本は別ファイル・並列(赤を確認してから T063〜T065)
Task: "test_jra_entries_upsert.py"                   # T054
Task: "test_jra_collisions_both_ways.py"             # T055
Task: "test_jra_entries_matrix.py"                   # T056
Task: "test_jra_odds_upsert.py"                      # T057
Task: "test_jra_cli_entries_odds.py"                 # T058
```

---

## Implementation Strategy

### 開催中(今日 2026-11-02 を含む)

1. Phase 1 → Phase 2 → Phase 3(ファイルのみ)→ Phase 5 の ⏸ なし → US1/US2/US4 の解析器と unit テスト → US3 の比較純関数。**共通コード・本番 DB・policy は触らない**。
2. この時点で fixture 入力の解析は全ページ通り、純関数の規則は表のとおりに固定されている。

### 開催終了後(共通安全性 → JRA)

3. Phase 4 のテストを先に書いて赤を確認 → writer 実装 → 既存テストの契約更新 → ops/live 無改修緑 → **R1(0023 適用・再起動)→ R2(1 開催日観察)**。
4. Phase 5 の ⏸(politeness・capture_window 移設・172 reader)→ R3(config キー追加・`jra-fetch` / `jra-audit` 実測)。
5. US1 writer → R4/R5(限定レース → 全レース)。US2 writer → R6。US4 → R7。
6. Phase 10 → R8(spec への転記・result-review.md)。

### MVP

US1 だけでも価値がある(出走表の正規 ID 結合・`nk:` 増加の停止・公式時刻つきオッズ)。US2 は精算正本の公式化。US3 は両方の計器。US4 は保存のみ。

---

## Notes

- 利用者決定(2026-11-02): 結果ページの単勝人気は保存せず監査にだけ使う(US2 シナリオ 1/4・研究 D9)。別表は作らない。
- ⏸ の判定基準: **稼働プロセスが import する既存モジュールへの変更と本番 DB・policy**。ops の subprocess は `uv run --project`(editable)で稼働 checkout を毎ジョブ読むので、`db/enums.py` / `db/models/__init__.py` / `scrape/upsert.py` / `pipeline.py` / `politeness.py` / `scrape/cli.py` / `ops/*` / `scripts/source_operations_172.py` / `config/source-operations.json` は開催中に触らない(codex Q1-1)。新規ファイル(`jra/*` / `precedence.py` / `provenance.py` / `models/provenance.py` / 新テスト)は import されないので安全。**推奨: 176 の実装は別 worktree(`git worktree add`)で進め、稼働 checkout への反映は開催終了後に 1 回**(反映前に T100 の migration)。
- 「先にテストを書いて赤を確認」は**新しい挙動を要求するケースに限る**(既存挙動の維持や「禁止表を読まない」試験は実装前から緑で正常)。新 API の import 失敗だけを赤と数えず、シグネチャを先に置いて期待する assertion の失敗を確認する(codex Q1-6)。
- 来歴の `observed_at` は HTTP 取得時刻(呼び出し側が渡す)。既存呼出しの既定値 None は従来の `now()` を使う legacy 経路で、pipeline は実 fetch 時刻を渡す(T029/T034)。
- codex: tasks 段階のレビュー(2026-11-02・Q1 8 件・Q2 8 件・SC 表 12 行)を全件反映。採否は `research.md` 末尾「codex tasks レビューの採否」。
- `git add -A` 禁止。コミットは利用者の指示があるときだけ・パス明示。
- raw fixture のコピー(T001)は FR-001 の例外(証跡)。cname を読んで再利用するコードを書かない。
