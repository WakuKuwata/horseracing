# Tasks: JRDB を主情報源にし、netkeiba を止められる状態にする (178)

**Input**: `specs/178-jrdb-primary-source/`(spec.md・plan.md・research.md D1〜D16・data-model.md・contracts/{files-and-ids,precedence-jrdb,parity,cli}.md・quickstart.md)

**Prerequisites**: plan.md(段 0〜4 の順序・構造)・spec.md(US0/US1/US2/US3/US3.5/US4・FR-001〜FR-022・SC-001〜SC-010)

**Tests**: 含める。SC-003/004/008/009 と憲法 III・V が契約テストを要求し、176 と同じく「両経路共通の規則」は writer を書く前に赤で固定する。固定長 decode は公開サンプル(ログイン不要で配布される `/program/<種別>/<種別>yymmdd.txt`)の既知の行を期待値にする。**会員配信ファイル・会員 URL・認証情報は fixture・テスト・ログに置かない**(FR-022・研究 D5/D6)。

**Organization**: コードは user story ごと(US0 診断 / US1 読取専用 parity / US2 writer / US3 修復 3a+再構築 3b / US3.5 運用成立試験 / US4 netkeiba 停止)。**実 DB・実会員ページへの投入順序はコードの順序と別**で Phase 11(rollout・R0〜R9)に plan の順(診断 → 年度パック確認 → migration → 人物写像 → parity → 複製で writer → 修復 → 再構築 → 隔離試験 → 停止)で並べる。

**実装状況(2026-11-09)**: Phase 1〜10(MVP+US2+US3+US3.5+US4+Polish)は worktree `178-jrdb-primary-source` で実装済み(未コミット・Polish は research D26・性能見積りは D17)。残りは Phase 11 rollout(R0a〜R9)のみ。⏸ の登録行(T015/T016/T022/T036/T046/T050a)も worktree 内では実装済みで、**main への反映だけを R2 に残す**(稼働 checkout は worktree を import しない)。

**開催日の制約(FR-016・176 と同じ)**: 新規ファイル(`scrape/jrdb/`・`jra/emergency_entries.py`・`jra/person_profile.py`・`live/source_probe.py`・`db/models/jrdb.py`・migration ファイル・fixture・テスト)は開催中でも進めてよい。**既存共通モジュール(`precedence.py`・`db/enums.py`・`constraints.py`・`models/__init__.py`・`scrape/cli.py`・`live/cli.py`・ops・`config/source-operations.json`・netkeiba fetcher の生成点)への変更と本番 DB への migration 適用は開催終了後**。該当タスクに ⏸ を付ける。推奨は別 worktree で実装し、main への反映は開催終了後に 1 回。**⏸ の判定は「新規ファイルか」ではなく「稼働プロセスの import 経路に載るか」**(codex D18 #1): 新規ファイルでも `models/__init__.py` の export・CLI の登録・既存モジュールからの再エクスポート・依存追加・import 時の副作用で到達すれば ⏸。登録 1 行を入れるまで新規ファイルは到達不能なので、登録タスクだけを ⏸ にし、反映は R2 でまとめる。

**会員ページの制約(FR-022・研究 D5)**: 開催日 08:00〜19:00 JST は昨年以前のファイル(年度パック・旧年の単体)を取りに行かない。取得は 1 ファイル 1 要求・同時 1・固定間隔。rollout の該当タスクに 🕒 を付ける。**ダウンロードは利用者の明示承認を得てから**。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 別ファイル・未完了タスクへの依存なし=並列可
- **[Story]**: US0 / US1 / US2 / US3 / US3.5 / US4(Setup・Foundational・Polish・Rollout には付けない)
- ⏸: 開催終了後にのみ実施(稼働プロセスが読む共通コード・本番 DB・policy)
- 🕒: 会員ページの過去ファイルに触る=開催日 08:00〜19:00 を避ける・利用者承認後

## Path Conventions

monorepo。`scrape/src/horseracing_scrape/`(略: `scrape/…`)、`scrape/tests/{unit,integration,fixtures}/`、`db/src/horseracing_db/`・`db/migrations/versions/`、`live/src/horseracing_live/`、`features/tests/unit/`、`scripts/`、`config/`。テストは各パッケージで `uv run pytest`(統合は `-m integration`・testcontainers)。生ファイルと報告は `artifacts/jrdb/`(gitignore 済み `artifacts/`)。

---

## Phase 1: Setup(fixture・値オブジェクト・名前空間・依存)— 開催中でも可

**Purpose**: decode と純関数が依存する証跡と型を固める。DB・共通コードに触らない。

- [x] T001 公開サンプル 9 本を `scrape/tests/fixtures/jrdb/public/` にバイト不変でコピーする(scratchpad `…/scratchpad/jrdb/samples/` から `BAC080913.txt` `KYI150712.txt` `UKC020908.txt` `KSA020907.txt` `TYB210912.txt` `HJC240929.txt` `SED080913.txt` `SRB080913.txt` `KAB080913.txt`。いずれも `https://jrdb.com/program/<種別>/` でログイン無しに配布される公開物=FR-022 の許可範囲)。cp932 のまま置き、UTF-8 変換版は置かない
- [x] T002 `scrape/tests/fixtures/jrdb/public/manifest.json`: entry ごとに `kind` / `version`(KYI 第 11 版・SED 第 4 版 等=仕様書の版)/ `file` / `public_url`(`/program/...` の公開 URL は書いてよい)/ `record_length` / `rows` / `raw_sha256` / `fetched_at`(2026-11-08)/ `note`。sha は `shasum -a 256` で raw バイトから
- [x] T003 [P] `scrape/tests/unit/test_jrdb_fixture_manifest.py`: 全 entry の `raw_sha256` 一致・各行が CRLF 区切りで `record_length` バイト(cp932)・**`scrape/` 配下の全ファイルに `jrdb.com/member/` と `JRDB_MEMBER_` の値らしき文字列が無い**(会員 URL・認証情報の混入検査=FR-022)
- [x] T004 [P] `scrape/src/horseracing_scrape/jrdb/__init__.py`・`scrape/src/horseracing_scrape/jrdb/models.py`: data-model §5 の値オブジェクト(`JrdbRaceKey` / `JrdbRaceProgram`(BAC)/ `JrdbEntry`(KYI 採用列のみ)/ `JrdbHorse`(UKC)/ `JrdbPerson`(KZA/CZA)/ `JrdbPreRace`(TYB)/ `JrdbResult`(SED)/ `JrdbRaceResult`(SRB)/ `JrdbPayouts`(HJC)/ `JrdbFileMeta`(kind, version, data_date, variant, sha256, size, record_length))を frozen dataclass で。**指数類・コメント・基準オッズ・10 時オッズの属性は持たない**(憲法 II・V)。例外型 `JrdbDecodeError` / `JrdbLayoutMismatch`(行長不一致)/ `JrdbUnsupportedVersion` / `JrdbArchiveBlackout` / `JrdbCredentialsMissing` / `JrdbHeld` を `scrape/src/horseracing_scrape/jrdb/errors.py` に
- [x] T005 [P] `scrape/src/horseracing_scrape/jrdb/layout/__init__.py`: レイアウト表の型 `Field(name, start, length, type ∈ {"9","Z","X","F"}, scale, target ∈ {"current","canonical_only","skip"})` と `Layout(kind, version, record_length, fields)`。`target="canonical_only"` が別定義 4 列(`first_3f`・`corner_orders`・`running_style`・`sire_line/damsire_line` の出所列)、`"skip"` が取り込まない列(指数・コメント・基準オッズ・10 時オッズ)。`layout_for(kind, version)` は未知の版で `JrdbUnsupportedVersion`
- [x] T006 [P] `scrape/src/horseracing_scrape/jrdb/layout/{bac,kyi,ukc,kza,cza,tyb,hjc,sed,srb}.py`: 公開仕様書(`bac_doc` / `kyi_doc` 第 11 版 1024 / `ukc_doc` 292 / `Ks_doc1`・`Cs_doc1` 272 / `tyb_doc` 128 / `hjcdata_doc` 444 / `sed_doc` 376 / `srb_doc` 852)の**転記**。各ファイル冒頭に出所 URL と版・取得日を docstring で。KZA/CZA の生年月日・所属・初免許年・名前の相対位置、SED の確定単勝オッズ/人気/前 3F/後 3F/コーナー順位/脚質/馬体重、HJC の 7 券種(枠連は `skip`)、BAC の発走時間・1〜5 着賞金・条件コード、UKC の血統登録番号/生年月日/父母母父名/馬主/生産者/系統コードを含む
- [x] T007 [P] `scrape/pyproject.toml`: zip 展開は標準 `zipfile`。LZH は**任意依存** `lhafile`(`[project.optional-dependencies] lzh`)とし、無い環境では `JrdbLzhUnsupported` を typed で返す(研究 D4: zip のバックナンバーが全期間あるので LZH は旧形式 lzh を読むときだけ)。`uv lock` 更新。`scrape/tests/unit/test_jrdb_archive_formats.py` は zip の展開(標準 zipfile で作った fixture)と、`lhafile` 不在時の typed エラーだけを固定(LZH の実展開テストは `lhafile` がある環境で `pytest.importorskip`)

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_jrdb_fixture_manifest.py tests/unit/test_jrdb_archive_formats.py` 緑。

---

## Phase 2: Foundational A — decode・ID 写像・書込み先プロファイル(純関数)— 開催中でも可

**Purpose**: contracts/files-and-ids.md §2/§3 と precedence-jrdb §3a を DB なしで固定する。US1〜US3 の全経路がこれを呼ぶ。

- [x] T008 `scrape/src/horseracing_scrape/jrdb/decode.py`: `decode_file(raw: bytes, meta: JrdbFileMeta, *, profile: Literal["current","jrdb_canonical"]) -> DecodedFile(rows, rejected_rows, record_length)`。cp932 strict・CRLF・行長 ≠ `Layout.record_length` の行は `rejected_rows` に(件数と行番号・**値は出さない**)。型規則: `9`→int、`Z`→空白 None、`X`→strip して空は None、`F`→16 進、`scale` で 10 で割る、`XZ9` は空白除去後 int、タイム `9999` は 分+0.1 秒。**`profile="current"` のとき `target="canonical_only"` の列は値オブジェクトに載せない(None)**、`"skip"` は常に decode しない(D7a 一重目のガード)
- [x] T009 [P] `scrape/src/horseracing_scrape/jrdb/ids.py`: `race_id_from_key(key8, *, file_year) -> str | None`(場 01〜10 のみ・年 2 桁→4 桁・日 16 進→10 進 2 桁・それ以外 None)、`horse_id_from_blood_no(no8, *, race_year, birth_date=None) -> str`(年 2 桁 ≤ race_year%100 なら 2000 年代・超えれば 1900 年代・UKC 生年月日があれば検証し不一致は `JrdbDecodeError`)、`venue_code_jrdb_to_db`。**騎手/調教師コードは写像しない**(id_mappings の仕事=persons.py)
- [x] T010 [P] `scrape/src/horseracing_scrape/jrdb/profile.py`: `WriteProfile(name, allowed_columns_by_table)`。`CURRENT` は `race_results.first_3f` / `race_results.corner_orders` / `race_horses.running_style` / `horses.sire_line` / `horses.damsire_line` を**含まない** allowlist、`JRDB_CANONICAL` は含む。`assert_allowed(profile, table, column)` は違反で `ProfileViolation`(upsert 段の二重目のガード=D7a)。`profile_from_arg(str)` は 2 値以外を拒否
- [x] T011 [P] `scrape/tests/unit/test_jrdb_decode_public_samples.py`: 公開サンプルの既知の行を期待値に(research D1 の実測値): SED080913 の 324 行・各行 376 バイト・1 行目の馬名/着順/タイム/確定単勝オッズ/人気/馬体重、KYI150712 の 498 行・枠番/性別/負担重量(見習い 51.0 の行を含む)、BAC080913 の発走時間と 1 着賞金、UKC020908 の血統登録番号/生年月日/父母母父名、KSA020907 の 岡部幸雄=コード 10076・生年月日 1948-10-31、HJC240929 の単勝払戻 24 レース、SRB080913 のハロンタイム、TYB210912 の馬体重増減 `- 4` → −4。**`profile="current"` では SED の前 3F/コーナー順位/脚質と UKC の系統が None、`"jrdb_canonical"` では値が入る**。行長を 1 バイト削った合成行は `rejected_rows` に数えられ本文に出ない
- [x] T012 [P] `scrape/tests/unit/test_jrdb_ids.py`: レースキー 4 日(2008-09-13・2015-07-12・2021-09-12・2024-09-29)の全レースで既知 race_id と一致・日 `A`〜`C` の 16 進・場 21〜79 は None・血統登録番号の 1999/2000 境界(`99xxxxxx` と `00xxxxxx` をレース年 2008 で)・UKC 生年月日と矛盾する番号は例外
- [x] T013 [P] `scrape/tests/unit/test_jrdb_profile.py`: CURRENT の allowlist に別定義 4 列(5 物理列)が無い・JRDB_CANONICAL にはある・`assert_allowed` の違反・未知のプロファイル名は拒否・**allowlist はテストに列名を逐語で持つ**(将来の追加で黙って広がらない)
- [x] T014 [P] `scrape/tests/unit/test_jrdb_leak_guard.py`: `jrdb/models.py` と `layout/*.py` の `target != "skip"` な列名に `idm` / `index` / `shisu` / `odds10` / `comment` / `kijun_odds` 系のトークンが無い(指数類・基準オッズ・10 時オッズを取り込まない=憲法 II/V)。`features/` が `horseracing_scrape.jrdb` を import しない(import-graph)

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_jrdb_*.py` 緑。公開サンプルだけで decode→ID 写像が通る。

---

## Phase 3: Foundational B — DB 契約(migration 0025)— モデル定義は開催中でも可・⏸ 共通モジュールと本番適用は開催終了後

**Purpose**: `Source.JRDB`・`jrdb_files`。既存表の列は変えない(SC-008)。

- [x] T015 ⏸ `db/src/horseracing_db/enums.py`: `Source.JRDB = "jrdb"` を追加し `Source.ALL` に含める。`db/src/horseracing_db/constraints.py`: `OFFICIAL_WIN_PAYOUT_SOURCES` に `jrdb_result` を加える。**既存共有モジュールなので ⏸**(ops の subprocess は稼働 checkout の `db` を毎ジョブ import する)
- [x] T016 `db/src/horseracing_db/models/jrdb.py`(新規・開催中でも可): `JrdbFile`(data-model §2: `jrdb_file_id` uuid PK・`kind` CHECK 25 種・`data_date`・`variant` CHECK 5 値・`sha256`・`size_bytes`・`record_length`・`source_url_sha256`・`published_at` NULL 可・`fetched_at`・`applied_at` NULL 可・`status` CHECK 6 値・`race_status` jsonb NOT NULL 既定 `{}`・`ingestion_job_id` FK SET NULL・TimestampMixin・UNIQUE(kind, data_date, variant, sha256))。**`models/__init__.py` への export 追加は ⏸**
- [x] T017 `db/migrations/versions/0025_jrdb_source.py`(`revision="0025_jrdb_source"`・`down_revision="0024_jra_track_conditions"`): CHECK 置換 7 本(`ck_ingestion_jobs_ck_ingestion_jobs_source` / `ck_id_mappings_ck_id_mappings_source` / `ck_exotic_odds_ck_exotic_odds_source` / `ck_race_laps_ck_race_laps_source` / `ck_official_win_payouts_source` / 来歴 2 表の source CHECK=0023 の名前を実ファイルで確認して転記)+`jrdb_files` 新表+index (kind, data_date)・(status)。downgrade は `source IN ('jrdb','jrdb_result')` 行か `jrdb_files` 行が 1 行でもあれば `RuntimeError`。up/down 冒頭 `SET lock_timeout = '5s'`(176 T018 と同型)
- [x] T018 [P] `db/tests/integration/test_migration_178.py`(`test_migration_176.py` と同型): 0024→0025→0024→0025 が通る・`jrdb` 行 1 行で downgrade 中断・**既存表の列定義と制約が 0024 と同一で、差分が許可集合(CHECK 7 本の定義変更+新表 1)だけ**・`jrdb_files` の CHECK と UNIQUE が data-model と一致。併せて **`db/tests/integration/test_migration_176.py` の `_EXISTING_TABLES_SQL` 除外に `jrdb_files` を足す**(足さないと 0025 後の head で 176 のテストが新表を差分と見なす)
- [x] T019 [P] migration head を直書きしている既存テスト **11 本**を 0025 に更新する(2026-11-08 の grep: `features/tests/unit/test_feature020_leak_guard.py` / `test_feature021_leak_guard.py` / `test_feature023_leak_guard.py` / `test_feature040_leak_guard.py` / `test_feature066_leak_guard.py` / `test_feature084_leak_guard.py` / `test_materialize_fallback_columns.py` / `test_feature086_leak_guard.py` / `test_purchase_leak_guard.py`、`live/tests/unit/test_no_schema_change.py`、`db/tests/integration/test_migration_176.py` の `0024_` 参照)。テストは repo の migration ディレクトリ末尾を見るので本番 DB の head(0024 のまま)と独立

**Checkpoint**: `cd db && uv run pytest -q -m integration tests/integration/test_migration_178.py tests/integration/test_migration_176.py` 緑・`cd features && uv run pytest -q tests/unit/test_feature020_leak_guard.py` 緑。**本番 DB への 0025 適用は Phase 11 R2(開催終了後)**。

---

## Phase 4: User Story 0 — 現構成の診断(段 0・Priority P1)

**Goal**: netkeiba を止めずに 2 開催日、(a) 公式 T−3 オッズの適時取得率、(b) netkeiba 依存の棚卸し、(c) JRDB 各版の実到着時刻を記録する(FR-001/002/019・SC-001)。合否にしない。

**Independent Test**: 1 開催日の全レースについて (a) の成否と取得時刻が 1 行ずつ `artifacts/jrdb/probe/<day>.jsonl` に残り、(b) の一覧と (c) の時刻表が出る。公式経路と DB 以外に触らない(netkeiba は呼ばない)。

- [x] T020 [P] [US0] `live/src/horseracing_live/source_probe.py`: `ProbeConfig(day, freshness, modes)` を**実行前に JSON で凍結**(`artifacts/jrdb/probe/<day>-config.json`・既存パスは拒否)。**鮮度条件は独自の秒数を持たず 174 の捕捉窓そのもの**: `near_post.schedule_day` の各レースの `window_open`〜`window_close`(定数 `TARGET_S=180`・`WINDOW_S=60`・衝突ずらし `COLLISION_STEP_S` を import して導出=実窓は発走 240〜120 秒前・近接レースは 60 秒ずつ後ろ)に `official_at` が入れば `ok`。凍結 JSON には導出した定数値とレースごとの窓を書く(analyze I2: 既定を 180/60 秒と書くと取得が構造的に「早すぎ」になる)。`probe_official_odds(session, cfg, *, fetcher, clock)`: その窓で **`jra/race_odds.refresh_race_win_odds` は呼ばない**(dry-run が無く `_run_job` が odds と `jra_odds` ジョブを接続先 DB に commit する=analyze U2)。代わりに 176 の `jra/navigate.Walker(fetcher, date).odds_pages(venue, want)` と `jra/parse/odds.parse_win_odds` を直接使い、**DB には一切書かない**(session は `races.post_time` と出走馬の読取だけ・`hr_manual_observer` の read-only role で動くことをテストで固定)。レースごとに `{race_id, post_time(変更後), fetched_at, official_at, ok, reason}` を artifact JSON に append(FR-001・受入 1/2)。`fetcher` は 176 の `JraFetcher` を `jra/policy.validate_startup` 経由で作り、鍵 `jra.go.jp` の共有スロット(`RequestPoliteness`)に入るので ops の公式取得(manual_ui の refresh)と重なっても直列化される=間隔を別に決めない。`probe_dependencies(session, day)`: 来歴 2 表・`horses.data_source`・`id_mappings`・`race_horses` の生成元から「行の生成 / マスタの生成 / 現在値 / ID 写像 / 結果 / 確定オッズ / 払戻 / T−3 取得」の 8 区分で netkeiba 依存件数を数える(FR-002・受入 3)。**連闘馬(前日に走った出走馬)の頭数**も数える(研究 D14 の限界計測)
- [x] T021 [P] [US0] `live/src/horseracing_live/source_probe.py` に `probe_arrivals(cfg, *, head)`: 会員 URL の **HEAD** を 10 分おきに投げ、各版(KYI/BAC/UKC 前日・TYB 場別・HJC・SED 速報)の `Last-Modified`/`Content-Length` の変化時刻を記録(FR-019)。**当日ファイルだけ**(昨年以前は触らない=08:00〜19:00 の制限は年度パックが対象だが、当日分以外を一切要求しない実装で安全側に)。URL は `jrdb/fetch.py` の URL ビルダを使い、記録には `source_url_sha256` だけ
- [x] T022 [US0] ⏸ `live/src/horseracing_live/cli.py`: `source-probe --date D --mode official-odds|dependencies|arrivals [--config ABS]` を登録(既存共有ファイルなので ⏸・新規コードは T020/T021 に置く)。`official-odds` は 176 の起動時検証(`jra/policy.validate_startup`)を通す
- [x] T023 [P] [US0] `live/tests/unit/test_source_probe.py`: 鮮度判定(窓内/早すぎ/遅すぎ・発走変更後基準・**窓が `near_post` の定数から導出され、定数を monkeypatch すると追随する**・衝突ずらし k=1 のレースは窓が 60 秒後ろ)・config 凍結ファイルの既存パス拒否・依存棚卸しの 8 区分が合成 DB 行で正しく数えられる・`probe_arrivals` が HEAD 以外を送らない(fake fetcher)・netkeiba のホストへ要求 0(transport mock で assert)

**Checkpoint**: `cd live && uv run pytest -q tests/unit/test_source_probe.py` 緑。実行は Phase 11 R0。

---

## Phase 5: User Story 1 — JRDB を読み取り専用で取り込み、既存データと突き合わせる(段 1・Priority P1)🎯 MVP

**Goal**: 年度パック/日次ファイルを取得・展開・`jrdb_files(status=fetched)` に記録し、重複期間の parity 報告(列ごとの意味の一致/区分別数値一致/`uncovered_reference_cells`)を書く。製品表には 1 行も書かない(FR-003/004/005/009/022・SC-002/009/010)。

**Independent Test**: 公開サンプル(SED080913 等)だけで `jrdb-parity` が動き、2008-09-13 の 324 行について D1 の実測値(結合 324/324・着順/タイム/確定オッズ 100%・前 3F は 1200m のみ 100%)が JSON に出る(ログイン前の smoke)。会員ページで 1 年分を取れば同じ CLI が年単位で動く。

### Tests(先に書く)

- [x] T024 [P] [US1] `scrape/tests/unit/test_jrdb_fetch.py`: `JrdbFetcher`(fake transport)が Basic 認証ヘッダを付ける・1 ファイル 1 要求・固定間隔(`HORSERACING_JRDB_MIN_INTERVAL_S`・既定 5 秒・下限 2 秒)・HEAD の `Last-Modified`/size が前回と同じなら GET しない・401/403 は即 `FetchRefused`(再試行なし)・**ログ・例外文字列にパスワードと会員 URL のパス部分が出ない**・年度パック URL と単体 URL と直前情報 URL の組み立て(contracts §1.1 の形)・`archive_blackout`(開催日 08:00〜19:00 JST に `--archive` は `JrdbArchiveBlackout`・`--force-blackout` は存在しない)
- [x] T025 [P] [US1] `scrape/tests/unit/test_jrdb_files_versioning.py`: `files.py` の純関数 `classify_version(existing: JrdbFileMeta|None, incoming) -> "same" | "apply" | "supersede" | "held"`: 同 sha → same、速報(`variant=daily`・SED 当日)→正式(木)→ supersede、日次→バックナンバー(`variant=archive`)→ supersede、**同 (kind, data_date, variant) で sha が違いどちらの規則にも当たらない → held**、`published_at` の新旧は判定に使わない(逆転させても結果が同じ)
- [x] T026 [P] [US1] `scrape/tests/unit/test_jrdb_persons_match.py`: `persons.match(jrdb: JrdbPerson, candidates: list[JraPersonProfile]) -> Match(status, canonical_id, note)`: 名前(NFKC・空白除去)+生年月日一致 → mapped、名前のみ一致(生年月日欠損または不一致)→ conflict、候補なし → unmapped、同一生年月日で名前違い → conflict(理由を note に)。所属・初免許年の不一致は mapped のまま note に書く
- [x] T027 [P] [US1] `scrape/tests/unit/test_jra_parse_person_profile.py`: `jra/parse/person.py` が騎手ページ(`pw04kmk0<5桁>`)・調教師ページ(`pw05cmk0<5桁>`)の HTML から 生年月日・名前・所属 を読む。fixture は **合成 HTML**(調査で保存した実ページの構造を再現・個人情報の実値は置かない)。URL から正規 ID を `jra/identity.official_id` で取る
- [x] T028 [P] [US1] `scrape/tests/unit/test_jrdb_parity_report.py`: `parity.py` の純関数を公開サンプル+合成 DB フレーム(pandas)で: `counts`(jrdb/db/both/only)・`ids`(horse joined/unjoined・jockey_codes mapped/unmapped/conflict)・`columns.<col>.denominator`(both_present/jrdb_only/db_only/unjoined)・`by_segment`(距離帯 4×芝ダ障 3・tolerance)・`examples_disagree` ≤10・**`meaning` はコードが推定せず contracts/parity.md の表(辞書定数)から転記**・`uncovered_reference_cells` の 4 区分(row_missing/id_unresolved/value_missing/definition_mismatch)が合成の欠落 1 件ずつを正しく数える・別定義 4 列は `definition_mismatch` 別掲で 0 を要求しない・既存パスへの書き込み拒否

### Implementation

- [x] T029 [US1] `scrape/src/horseracing_scrape/jrdb/fetch.py`: `JrdbFetcher(client, *, user, password, min_interval_s, archive_dir)`。`url_yearly(kind, year)` / `url_single(kind, date, fmt="zip")` / `url_person_master(kind, date)` / `url_tyokuzen(date)`(contracts §1.1)。`head(url)`・`get(url) -> bytes`。Basic 認証は httpx の `auth=`。**`HttpFetcher`/`JraFetcher` は変更しない**・共有スロットも使わない(別ホスト・別契約)。`extract(bytes, name) -> list[(member_name, bytes)]`(zip 優先・lzh は任意依存)。`sha256`・`source_url_sha256`
- [x] T030 [US1] `scrape/src/horseracing_scrape/jrdb/files.py`: `JrdbFileMeta` の検出(ファイル名→kind/version/data_date/variant・行長検出)・`classify_version`(T025)・`record_fetched(session, meta, ...)`(`jrdb_files` に status=fetched・既存 sha は行を増やさず `fetched_at` も変えない)・`mark_superseded` / `mark_held`。展開テキストは `artifacts/jrdb/<kind>/<yyyymmdd>/<name>.txt` に保存
- [x] T031 [US1] `scrape/src/horseracing_scrape/jrdb/blackout.py`: `is_archive_blackout(now_jst, race_days) -> bool`(開催日 08:00〜19:00・開催日は DB の `races.race_date` 当日か policy の指定日)。`jrdb/policy.py`: `validate_startup(policy_path, env, *, needs_credentials, is_archive)`(176 `jra/policy.py` と同型: `jrdb_enabled: true`・`JRDB_MEMBER_USER`/`JRDB_MEMBER_PASSWORD` の存在だけ検査し値はログに出さない・`HORSERACING_JRDB_MIN_INTERVAL_S` の下限・`archive_blackout`)。1 件も送る前に落ちる
- [x] T032 [P] [US1] `scrape/src/horseracing_scrape/jra/parse/person.py`・`scrape/src/horseracing_scrape/jra/person_profile.py`: 騎手/調教師ページの fetch(176 `JraFetcher`・索引 → 一覧 → 個人ページの 2〜3 ホップ・10 秒間隔)と parse。出力 `JraPersonProfile(kind, canonical_id, name, birth_date, affiliation)` を `artifacts/jrdb/persons/jra-profiles.jsonl` に append(**1 回限りの収集**・既存 ID は再取得しない)。対象 ID は DB の `jockeys`/`trainers` 全件(約 1,500)。根拠は正規 ID に直接ひも付く属性だけ(FR-003a)
- [x] T033 [US1] `scrape/src/horseracing_scrape/jrdb/persons.py`: KZA/CZA の全行 × `jra-profiles.jsonl` を `match`(T026)で突き合わせ `PersonMappingReport(mapped, conflict, unmapped, examples)` を返す純関数+`artifacts/jrdb/persons/report.json`。**id_mappings への書込みは US2(T047)**。**分母は対象 JRDB データ(2007〜現在の KYI/SED)に出現する一意の人物コード**で、騎手/調教師・年代別に unmapped/conflict を含めて集計(公式から取れた約 1,500 件や名前照合に成功した集合を分母にしない・出現回数で重み付けしない=codex D18 #10)。99% は進捗指標で、**投入する開催日は必要人物が全件 mapped** を別に判定(T051 の `jrdb-entries` は未解決人物を含むレースを `unresolved_person` として明示・`jrdb-audit` で当日の未解決 0 を R5 のゲートに)。未解決の人数は「不足」として報告に数える(FR-003a)
- [x] T034 [US1] `scrape/src/horseracing_scrape/jrdb/parity.py`: `build_parity(year, files_dir, session_ro, *, person_mapping) -> dict`(contracts/parity.md の JSON)。入力は BAC/KYI/UKC/SED/SRB/HJC(+KZA/CZA)。DB は読み取り専用セッション(`SET TRANSACTION READ ONLY`)。列ごとの正規化(race_class 対応表 05→500万 等・going 10/11→良・名前 NFKC+空白+末尾「氏」除去)は `jrdb/normalize.py` に純関数で分ける。`uncovered_reference_cells` は 44 列固定の定数リスト(別定義 4 列と `jockey_code`/`trainer_code` を除いた JRA-VAN 由来列)
- [x] T035 [US1] `scrape/src/horseracing_scrape/jrdb/cli.py`: `jrdb-fetch --date D [--kinds …] | --archive --from --to [--kinds …]`(年度パック 1 本を既定・単体は `--single`)・`jrdb-parity --year Y --files-dir ABS [--out ABS]`。起動時検証(T031)→ fetch → `jrdb_files(fetched)`。`jrdb-parity` は `--db-url` を読み取り専用で開き、製品表への書込みが無いことを `test_jrdb_parity_readonly`(T038)で固定。summary キーは contracts/cli.md の共通キー
- [x] T036 [US1] ⏸ `scrape/src/horseracing_scrape/cli.py`: `jrdb-*` の登録 1 行(176 の `jra-*` と同じ分岐=`args.command.startswith("jrdb-")` → `jrdb.cli.main`)。既存共有ファイルなので ⏸
- [x] T037 [P] [US1] `scripts/jrdb_ops.sh`: `fetch|parity|persons-collect YYYY[-MM-DD] …`。policy の `jrdb_interval_seconds`(既定 5)を `HORSERACING_JRDB_MIN_INTERVAL_S` に写す・`config/.env.jrdb`(gitignore)から `JRDB_MEMBER_USER/PASSWORD` を読む・`set -euo pipefail`・値を echo しない。`jra_ops.sh` と同型
- [x] T038 [P] [US1] `scrape/tests/integration/test_jrdb_parity_readonly.py`: 公開サンプル 2008-09-13 を seed した一時 DB に対し `jrdb-parity` を流し、(1) 製品表 9 表の `xmin`/行数が前後で不変、(2) `columns.finish_order.by_segment` 全区分 rate 1.0、(3) `columns.first_3f` は `1200/芝`・`1200/ダ` だけ 1.0 で他区分 <0.1、(4) `uncovered_reference_cells` の合成欠落が 4 区分に出る、(5) 出力 JSON が contracts/parity.md のキー集合と一致
- [x] T039 [P] [US1] `scrape/tests/integration/test_jrdb_files_table.py`: 同 sha の `record_fetched` 2 回で `jrdb_files` 1 行・異 sha(速報→正式)で旧行 superseded・held の行は `applied_at` NULL のまま・UNIQUE 違反なし

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_jrdb_*.py tests/unit/test_jra_parse_person_profile.py && uv run pytest -q -m integration tests/integration/test_jrdb_parity_readonly.py tests/integration/test_jrdb_files_table.py` 緑。公開サンプルで `jrdb-parity --year 2008` が JSON を書く。**MVP はここまで**。

---

## Phase 6: User Story 2 — JRDB を第 3 の writer にし、生成責任を移す(段 2・Priority P2)

**Goal**: KYI/UKC/BAC から出走行・マスタ行を正規 ID で作り、TYB/SED 正式版/SRB/HJC を 176 の規則で書く。`nk:` を増やさない。緊急モード(公式出馬表から行生成)と復旧経路(FR-006/007/007a/008/009/011/012・SC-003/004)。

**Independent Test**: 本番 DB 複製で 1 開催日を {JRDB→公式→netkeiba} と {公式(書込先なし)→JRDB→公式再取込} で再生し、決定論項目の最終状態が同じ・保護対象不変・同一入力 2 回目で製品値の変更 0・`static_mismatch=0`・新規 `nk:` 0。

### Tests(先に書く・赤を確認)

- [x] T040 [P] [US2] `scrape/tests/unit/test_precedence_jrdb_transitions.py`: precedence-jrdb §3 の表を行ごとに: 行生成は incoming=jrdb のみ write・stored=jra_official に対する jrdb の entry_static は keep+`static_mismatch`・stored=netkeiba に対する jrdb は write+`static_mismatch`・stored=jrdb に対する netkeiba は keep+mismatch、jra_official は write+mismatch・post_time(BAC は stored 無しで write・stored=jra_official は keep)・jockey/trainer は写像済み ID だけ write・未解決 NULL は keep+`unresolved_person`・**win_odds/place_odds は incoming=jrdb で常に skip**・確定単勝オッズは `apply_final_odds` の規則(値が変わる行のみ)・results は race_atomic で正式版のみ(`variant=daily` の SED は reject:`sokuho_not_input`)・laps は stored=netkeiba でも write・payouts は公式と同値なら採用元 official のまま
- [x] T041 [P] [US2] `scrape/tests/integration/test_jrdb_entries_upsert.py`: 公開 KYI150712+合成 UKC/BAC で (a) 全レースの `race_horses`/`horses`/`races` が正規 ID で作られ `horses.data_source='jrdb'`・`birth_year` あり、(b) 同 sha 2 回目で製品値の変更 0(主キー付き列値比較)かつ不要な書込み 0(`xmin` 不変)かつ `jrdb_files.race_status` 全 applied、(c) 1 レースを故意に失敗させる(合成の衝突)と他レースは applied・そのレースだけ failed・ファイルは partially_applied・再投入で未 applied だけ再処理(**このケースでは失敗レースの変更を許し applied 済みレースだけ変更 0 を要求**)、(d) **`profile=current` で SED を書いても別定義 4 列(5 物理列)が `IS DISTINCT FROM` で不変**(D7a・FR-021a)、(e) 既存 `nk:X` 行があるレースで `X` を書くと 2 行にならず `jra/alias.py` の別名規則で既存行へ(FR-007a)
- [x] T042 [P] [US2] `scrape/tests/integration/test_jrdb_replay_order.py`(SC-004): 本番複製の代わりに合成 1 開催日(2 レース)で {JRDB entries → JRA entries/odds/results → netkeiba entries/results} と {JRA results(書込先なし・`no_target_rows` 記録)→ JRDB entries → JRA results 再取込} を流し、**順序によらず同じになる項目**(払戻=公式・静的=先着・確定オッズ)は 2 順序の最終値を比較し、**入力履歴に応じた期待値を持つ項目**(結果の race_atomic・odds の採否と `adopted_source`・来歴の source・held/superseded の状態)は**各投入直後に規則から導いた期待値**と比較する(2 順序で同じ誤った結果になる形を排除=codex D18 #8)。追加ケース: 古い版が後から届く・同 (kind,date,variant) の異内容(held)・1 レース部分失敗→再投入。JRA-VAN 期の静的項目・確定済み結果/払戻は不変。**「製品値の変更 0」は主キー付きの列値比較で、「不要な書込み 0」は `xmin` 比較で別々に採点し、INSERT/DELETE も検出**(同値 UPDATE は後者だけに出る=codex D18 #9)。176 の `_seed176.py`/`_jra_fake.py` を再利用
- [x] T043 [P] [US2] `scrape/tests/integration/test_jra_emergency_entries.py`: policy `jra_emergency_entries: true` のとき 176 `parse_entries_day` の出力から出走行とマスタ行を正規 ID で作る・来歴 `entry_static=jra_official`・後から来た KYI は static を書き換えない(先着保持)・policy false では従来どおり行を作らない(176 FR-011a 不変)
- [x] T044 [P] [US2] `scrape/tests/integration/test_jrdb_results_payouts_upsert.py`: SED 正式版で `race_results` race_atomic・確定単勝オッズは `apply_final_odds` 経由で値が変わる行だけ・`official_win_payouts` と整合(HJC 単勝 = 確定オッズ×100)・SRB → `race_laps(source=jrdb)`・HJC 7 券種の集合置換(枠連なし・`exotic_odds.source='jrdb'`=CHECK は `Source.ALL`、`official_win_payouts.source='jrdb_result'`=ページ種別の CHECK・analyze I5)・**速報版 SED(`variant=daily`)は `fetched` で止まり製品表に 1 行も書かない**・TYB で body_weight の対と entry_status 一方向遷移
- [x] T045 [P] [US2] `scrape/tests/integration/test_jrdb_persons_idmap.py`: `jrdb-persons` が `id_mappings(source=jrdb, entity_type∈{jockey,trainer})` に mapped/conflict を書き unmapped も行として残す・既存 mapped は触らない・`--dry-run` で書込み 0・conflict の `resolution_note` に理由

### Implementation

- [x] T046 ⏸ [US2] `scrape/src/horseracing_scrape/precedence.py`: 規則表に `source="jrdb"` の列を足す(`field_transition` の entry_static/race_static/post_time/jockey/trainer・`odds_decision` は incoming=jrdb を `skip:source_not_allowed`・`payout_block_decision` の stored=jra_official 同値・新規 `results_input_allowed(source, variant)` で SED 速報版 reject)。**既存の netkeiba/jra_official の判定はバイト不変**(176 の `test_precedence_*` が全部緑のまま=回帰ガード)。共通コードなので ⏸
- [x] T047 [US2] `scrape/src/horseracing_scrape/jrdb/persons.py` に `write_id_mappings(session, report, *, dry_run)`: mapped → `status=mapped`・conflict → `conflict`・候補なし → `unmapped`、`resolution_note` に材料。`jrdb/cli.py` に `jrdb-persons --files-dir ABS --profiles ABS [--dry-run]`
- [x] T048 [US2] `scrape/src/horseracing_scrape/jrdb/upsert_jrdb.py`: `upsert_jrdb_entries(session, decoded_bac, decoded_kyi, decoded_ukc, *, profile, file_meta, job_id) -> Counts`(レースごと savepoint・`lock_race`・`check_entry_collisions`・`jra/alias.resolve_horses` 再利用・`precedence.field_transition`・来歴 `write_horse_provenance_many`/`write_race_provenance`・`race_status` 更新)。`horses` 生成は UKC・`birth_year`。未解決人物は NULL+`unresolved_person`。`profile.assert_allowed` を全列で通す
- [x] T049 [US2] `scrape/src/horseracing_scrape/jrdb/upsert_jrdb.py` に `upsert_jrdb_prerace(TYB)`(body_weight 対・取消 → entry_status 一方向)、`upsert_jrdb_results(SED 正式版+SRB)`(`_insert_results(mode="race_atomic")` 相当の共通経路・`apply_final_odds` を source=jrdb で・`upsert_laps(source=jrdb)`)、`upsert_jrdb_payouts(HJC)`(`upsert_official_win_payouts` は source=`jrdb_result`[`OFFICIAL_WIN_PAYOUT_SOURCES` に追加・ページ種別の CHECK]、`replace_exotic_dividends` の集合置換は source=`jrdb`[`exotic_odds`/`race_laps` の CHECK は `JOB_SOURCE`=`Source.ALL` なので `jrdb_result` は違反])。**win_odds/place_odds には触らない**
- [x] T050 [P] [US2] `scrape/src/horseracing_scrape/jra/emergency_entries.py`: `create_entries_from_official(session, day_page, *, policy) -> Counts`。176 `parse_entries_day` の `JraEntry` から `races`/`race_horses`/`horses`(馬 ID は `pw01dud00` の 10 桁)を作り来歴 `entry_static=jra_official`。policy `jra_emergency_entries` false なら何も作らない。純関数+新規ファイルのみ(本番経路への結線は T050a)
- [x] T050a ⏸ [US2] `scrape/src/horseracing_scrape/jra/cli.py` の `cmd_entries`(現状は `upsert_jra_entries` を直接呼ぶだけ)に結線: 起動時に読んだ policy の `jra_emergency_entries` が true なら、`upsert_jra_entries` の**前**に同じ `day_page` で `create_entries_from_official` を呼び、Counts を ingestion_jobs summary(`emergency_rows_created`)に出す。`--emergency` フラグは付けず policy だけで有効化(運用者が意識して切り替える)。false のときは関数を呼ばず既存経路バイト不変。T043 はこの `cmd_entries` 経由(本番と同じ入口)でも通す。稼働 checkout が import する共通ファイルなので ⏸・R2 の反映リストに含める(analyze G1: 結線が無いと runbook T071 の「policy 切替→`jra-entries`」が成立しない)
- [x] T051 [US2] `scrape/src/horseracing_scrape/jrdb/cli.py` に `jrdb-entries --date D [--profile current|jrdb_canonical]`・`jrdb-prerace --date D [--venue E|W|L]`・`jrdb-results --date D`・`jrdb-payouts --date D`。`--profile` は**必須相当**(既定なし・CLI は `current` を明示させる)。各コマンドは `jrdb_files` の版判定(T030)→ held なら書かず報告 → upsert → `race_status`/`status` 更新 → ingestion_jobs summary(`files`=処理したファイルの `[{jrdb_file_id, kind, data_date, variant, sha256, published_at}]`(data-model §3a の利用可能時刻の導出元) / `rows_read` / `rows_rejected` / `races_written` / `static_mismatch` / `unresolved_person` / `alias_*` / `stale_version` / `held`)
- [x] T052 [US2] `scrape/src/horseracing_scrape/jrdb/cli.py` に `jrdb-audit --date D`: 来歴 2 表・`jrdb_files`・`artifacts/jrdb/` の sha 整合(INV-F3)・`race_status` と `status` の整合(INV-F4)・新規 `nk:` 行数(SC-003)を READ ONLY で報告
- [x] T053 [P] [US2] `scripts/jrdb_ops.sh` に `entries|prerace|results|payouts|audit YYYY-MM-DD [--profile current]` を追加(前日: `fetch`→`entries`、当日: `prerace`、夜: `results`→`payouts`→`audit`)

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_precedence_*.py tests/unit/test_jrdb_*.py && uv run pytest -q -m integration tests/integration/test_jrdb_*.py tests/integration/test_jra_emergency_entries.py tests/integration/test_jra_*.py tests/integration/test_precedence_*.py tests/integration/test_netkeiba_replay_regression.py` 緑(176 の回帰を含む)。実 DB 複製での再生は Phase 11 R5。

---

## Phase 7: User Story 3 — 修復(3a・現行 DB)と再構築(3b・JRDB 正本の別 DB)(Priority P2)

**Goal**: 3a は段 1 の基準に合格した列だけ 2025〜2026 を埋め、充足率と 139 と同じ波及測定を記録(FEATURE_VERSION 不変)。3b は 1999〜現在を別 DB に source=jrdb だけで作り、年別件数と `uncovered_reference_cells` を記録(FR-005/010/013/020/021/021a・SC-005/006/009)。

**Independent Test**: 3a は `jrdb-repair --column first_3f` が allowlist で拒否され、`--column odds` が値の変わる行だけを更新し他行バイト不変。3b は別 DB の各年 races/entries/results が現行以上で、別定義 4 列が JRDB 定義で入り、現行 DB は `xmin` 不変。

- [x] T054 [P] [US3] `scrape/tests/unit/test_jrdb_repair_allowlist.py`: `repair.REPAIRABLE_COLUMNS` が parity の基準合格列(odds・popularity・owner_name・breeder_name・race_class・prize_money・post_time・weight・last_3f・win_payouts)だけで、`first_3f`/`corner_orders`/`running_style`/`sire_line`/`damsire_line` を渡すと `RepairColumnRefused`。**合格判定は parity JSON(`artifacts/jrdb/parity/<year>.json`)を読んで列ごとに `rate >= 基準` を確認し、不合格なら実行時にも拒否**(契約の基準値をコードに持つ)
- [x] T055 [P] [US3] `scrape/tests/integration/test_jrdb_repair.py`: 合成 2025 行で `--column odds` が `apply_final_odds` 経由で値が変わる行だけ更新・`official_win_payouts` と整合・`--column owner_name` が netkeiba 期 NULL/表記ゆれだけ埋め JRA-VAN 期の値は不変・`--dry-run` で書込み 0・修復対象外の行の `xmin` 不変・来歴に `source=jrdb`、**利用可能時刻は来歴の `ingestion_job_id` → `ingestion_jobs.summary.files[]` の `published_at` で引ける**(来歴表に列は足さない・data-model §3a の導出経路・FR-010)。同じレースを 2 回投入したとき来歴の job_id は現在値を採用した job を指し、`jrdb_files.ingestion_job_id` の上書きに影響されないことも検査
- [x] T056 [P] [US3] `scrape/tests/integration/test_jrdb_rebuild.py`: 2 つ目の一時 DB(testcontainers)に同じ alembic head を適用し、公開サンプル 2008-09-13 を `profile=jrdb_canonical` で `rebuild` → 別 DB に races/race_horses/race_results/horses/official_win_payouts/race_laps が入り **別定義 4 列が JRDB 定義で非 NULL**(前 3F は全距離・脚質は JRDB コード文字列・系統は JRDB 系統名)・元 DB は不変・`compare-2008.json` に年別件数と現行 DB との差
- [x] T057 [US3] `scrape/src/horseracing_scrape/jrdb/repair.py`: `repair_column(session, column, date_from, date_to, *, files_dir, parity_dir, dry_run) -> RepairReport(rows_changed, rows_unchanged, by_year_fill_before/after)`。odds は 139 `apply_final_odds` を source=jrdb で、owner/breeder/race_class は `field_transition(entry_static|race_static)` を通す(JRA-VAN 期は stored=jra_van で keep)、post_time は BAC、payouts は HJC。profile は常に `current`。充足率の年別表を `artifacts/jrdb/repair/<column>-<run>.json` に
- [x] T058 [US3] `scripts/jrdb_repair_impact.py`(139 の `evidence/repair_input_shift.json` と同じ手順): 修復前後で 2025-10〜2026-11 の `market_ev_predictions` を再計算(`training market-ev --ensemble-only` を複製 DB で)し、期待回収率の差の中央値・p90・注目条件 S1〜S5 の該当集合の差分を `specs/178-jrdb-primary-source/evidence/repair-impact.json` に書く。**`decision_quotes`・`attention_picks` は書き換えない**(FR-013)。FEATURE_VERSION 据え置きの判断材料(SC-006)
- [x] T059 [US3] `scrape/src/horseracing_scrape/jrdb/rebuild.py`: `rebuild_range(files_dir, date_from, date_to, *, db_url, reference_db_url) -> RebuildReport`。年ごとに entries→prerace→results→payouts の順で `upsert_jrdb_*` を `profile=jrdb_canonical`・別 engine で呼ぶ(同じ関数・別 DATABASE_URL)。`compare-<year>.json`: races/entries/results/payout_rows の両 DB 件数・列充足率・**JRDB < 現行の年は `blocking_years` に入れて終了コード非 0**(FR-020「理由を特定するまで進まない」)
- [x] T060 [US3] `scrape/src/horseracing_scrape/jrdb/cli.py` に `jrdb-repair --column C --from --to --files-dir ABS --parity-dir ABS [--dry-run]`・`jrdb-rebuild --from --to --files-dir ABS --db-url URL --reference-db-url URL`。`jrdb-rebuild` の `--db-url` が `--reference-db-url` や env `DATABASE_URL` と同じなら拒否(現行 DB を誤って上書きしない)
- [x] T061 [P] [US3] `scripts/jrdb_ops.sh` に `repair COLUMN FROM TO [--dry-run]`・`rebuild FROM TO --db-url URL`(`DATABASE_URL_JRDB` env を読む)

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_jrdb_repair_allowlist.py && uv run pytest -q -m integration tests/integration/test_jrdb_repair.py tests/integration/test_jrdb_rebuild.py` 緑。実 DB は Phase 11 R6/R7。

---

## Phase 8: User Story 3.5 — 移行後の構成で運用成立試験(隔離環境・Priority P2)

**Goal**: netkeiba から一切供給しない複製 DB で、JRDB ファイルを実到着時刻どおりに再生しつつ公式経路を実取得し、行生成→T−3 判断記録→結果→確定単勝オッズ→払戻がレース全件で揃うことと、JRDB 遅延時の緊急モード復旧を記録する(FR-015/015a・SC-001a)。

**Independent Test**: `artifacts/jrdb/trial/<day>.jsonl` にレースごと 1 行(各入力の供給元・到着時刻・判断締切との差・成否)が残り、不成立 0。JRDB 当日ファイルを遅らせた日も緊急モードで T−3 記録が残る。

- [x] T062 [P] [US3.5] `live/tests/unit/test_source_trial.py`: 到着時刻順の再生スケジューラ(段 0 の `arrivals` 記録または `--arrivals ABS` の表から各ファイルの投入時刻を決める・`--delay-kind KYI --by 2h` で遅延注入)・レースごとの記録行のキー(`race_id, entries_source, entries_at, master_source, quote_at, quote_ok, results_at, final_odds_at, payouts_at, deadline_delta_s, ok, reason`)・不成立の集計・**netkeiba ホストへの要求 0 を transport mock で assert**・隔離の起動検査(`isolation_violated`): 当日の `nk:` 行・`data_source='netkeiba'` 行に加え、**来歴 2 表に当日 `source=netkeiba` の行がない・`id_mappings(source=netkeiba)` を当日の人物解決に使わない・HTTP キャッシュ dir が空・許容する継承データ(過去年のレース/マスタ・R3 の人物写像)の範囲が `trial-config.json` に凍結されている**(codex D18 #6)。netkeiba ホストへの 0 要求は transport mock だけでなく、**プロセス全体の外向き接続ブロック**(`scripts/source_operations_172.py` の `no_outbound_python_connections` と同型・netkeiba ホスト限定)でも固定し、取得が空/失敗でも通る「0 要求」を合格条件にしない=**全レースの行生成・T−3 捕捉・必要値の充足**を同時に要求(codex D18 #11)
- [x] T063 [US3.5] `live/src/horseracing_live/source_trial.py`: `run_trial(day, *, db_url, files_dir, arrivals, delay, clock)`。起動時に複製 DB の `race_horses`/`horses` に `nk:` 行・`data_source='netkeiba'` の当日行が無いことを検査。到着時刻に `jrdb-entries`/`prerace`/`results`/`payouts` を関数呼びで投入、T−3 は **専用の入口** `source_trial.capture_official(race, deadline)` から JRA 公式オッズ(`race_odds.refresh_race_win_odds`)を取り、174 `near_post` の純関数(`schedule_day`・`window_state`・`quote_field`・`quote_digest`)だけを import して `decision_quotes` を書く(稼働中の `near_post.py` は変えない=codex D18 #4)。**複製 DB は当日 00:00 時点に戻す**(当日の `race_horses`/`races`/`race_results`/払戻/来歴を削除)、人物写像は R3 時点の `id_mappings` を許容する継承データとして `trial-config.json` に凍結し、各レースは**判断締切時点の充足**で採点する(codex D18 #7)。記録は append-only
- [x] T064 [US3.5] `live/src/horseracing_live/source_trial.py` に緊急モード分岐: `--delay-kind KYI` で KYI が締切までに来なければ policy `jra_emergency_entries: true` 相当で `jra/emergency_entries.create_entries_from_official` を呼び、**緊急モードの生成処理が実際に走ったこと**(作られた行数・来歴 `entry_static=jra_official`)と、後から来た KYI が static を書き換えないことを記録(US3.5 受入 2・codex D18 #7: 事前データが行を埋めていれば緊急モードは検証にならない)
- [x] T064a ⏸ [US3.5] `live/src/horseracing_live/near_post.py`: 本番の T−3 捕捉の取得元を policy `near_post_source: netkeiba|jra_official`(既定 `netkeiba`=現行バイト不変)で切り替える分岐。`jra_official` は T063 と同じ `race_odds.refresh_race_win_odds` 経路、**公式失敗時に netkeiba へ fallback しない**(netkeiba 無効時は `skipped:source_unavailable` を記録)。再試行・締切(18 秒)・欠測時の記録・来歴は現行と同じ契約。`live/tests/unit/test_near_post_unit.py` に取得元 2 値のテスト(既定で netkeiba の既存テストが不変)。R2 で反映・R8 の隔離試験は同じ経路を使う・R9 の前に `jra_official` に切替(codex D18 #4)
- [x] T065 ⏸ [US3.5] `live/src/horseracing_live/cli.py`: `source-trial --date D --db-url URL --files-dir ABS [--arrivals ABS] [--delay-kind K --by DUR]` を登録(⏸)。`live/tests/unit/test_no_schema_change.py` の head 参照は T019 で済み

**Checkpoint**: `cd live && uv run pytest -q tests/unit/test_source_trial.py` 緑。実行は Phase 11 R8(2 開催日)。

---

## Phase 9: User Story 4 — netkeiba を policy で止める(段 4・Priority P3)

**Goal**: `netkeiba_enabled: false` で netkeiba への要求が 0 件になり、停止中の状態表示と復旧手順(緊急モード/再開)が runbook にある(FR-014・SC-007・US4 受入 2)。コードは削除しない。

**Independent Test**: policy false で全 netkeiba CLI と ops job が HTTP 0 件で SKIPPED、true に戻せば従来どおり。`jrdb-audit` が「どの段が止まっているか」を出す。

- [x] T066 [P] [US4] `scrape/tests/unit/test_netkeiba_policy_gate.py`: `netkeiba_policy.assert_enabled(policy_path)` が `netkeiba_enabled: false` で `NetkeibaDisabled`(typed)、キー無しは true 扱い(既定)、bool 以外は拒否。`scrape/cli.py::_make_fetcher`・ops `runner.py` の fetcher 生成・live `chaos_politeness` の `CaptureHttpFetcher` 生成・`near_post` の fetcher 生成が false のとき **HttpFetcher を構築せず** 0 要求(transport mock)で `SKIPPED` を返す
- [x] T067 [US4] `scrape/src/horseracing_scrape/netkeiba_policy.py`(新規): `read_netkeiba_enabled(policy_path) -> bool`・`assert_enabled`。`jrdb_enabled` も同じ関数群で読む(`read_flag(policy, key, default)`)。CLI 起動時に読む(再起動不要=172 と同じ)
- [x] T068 ⏸ [US4] netkeiba fetcher の生成点 4 箇所に `assert_enabled` を差す: `scrape/src/horseracing_scrape/cli.py::_make_fetcher`・`ops/src/horseracing_ops/runner.py`(HttpFetcher 構築の直前・false なら job を SKIPPED で終える)・`live/src/horseracing_live/chaos_politeness.py`・`live/src/horseracing_live/near_post.py`。**それぞれの既存テスト(scrape 762 / ops 263 / live 188)が緑のまま**。共通コードなので ⏸
- [x] T069 ⏸ [US4] `config/source-operations.json` に任意キー `jrdb_enabled`(false)・`netkeiba_enabled`(true)・`jra_emergency_entries`(false)・`jrdb_interval_seconds`(5)・`jrdb_archive_blackout`(true)・**`near_post_source`(`netkeiba`・T064a)**の 6 キーを**既定値のまま**追加。`scripts/source_operations_172.py` は `OPTIONAL_KEYS`(現状 `CAPTURE_ONLY_KEYS | {JRA_INTERVAL_KEY}`)外のキーを `policy_keys_invalid` で**拒否する**ので、6 キーを許可リストに**必ず**足す(足さないと R2 (5) の wrapper 起動が落ちる=analyze I3)。値の検証は T066 の読み手と同じ(bool/int/列挙)。`scripts/tests` の wrapper テストに「6 キー入りの policy で起動検証が通る・未知キーは従来どおり拒否」を追加。ops `routers/status.py` の `PolicyFileStatus` に `netkeiba_enabled` を純追加するかは **FR-017(ops-openapi 不変)との兼ね合いで本 feature では足さない**=状態表示は `jrdb-audit` と runbook で
- [x] T070 [US4] `scrape/src/horseracing_scrape/jrdb/cli.py` の `jrdb-audit` に「段の状態」節: 当日の `jrdb_files`(KYI/BAC/UKC 前日・TYB・SED・HJC の有無と status)・来歴の生成元・T−3 記録件数・`netkeiba_enabled`/`jra_emergency_entries` の現在値を 1 画面に(US4 受入 2 の判断材料)
- [x] T071 [P] [US4] `specs/178-jrdb-primary-source/manual.md`(runbook・176 `manual.md` と同型): 週次(木: KZA/CZA・SED 正式・SRB・SKB は取らない)/ 前日(金・土 20:00 以降: fetch→entries)/ 当日(TYB prerace・T−3 は JRA 公式=174 near-post・夜 HJC payouts・SED 速報は fetch のみ)/ 木曜(SED 正式 results)の順序、JRDB 未着時の緊急モード手順(policy 切替→`jra-entries`)、netkeiba 再開手順、`held` の承認手順(差分確認→`--apply-held FILE_ID`)、会員ページの制限時間帯、認証情報の置き場(`config/.env.jrdb`・gitignore)

**Checkpoint**: `cd scrape && uv run pytest -q tests/unit/test_netkeiba_policy_gate.py` 緑・各パッケージ全スイート緑(SC-008)。実運用は Phase 11 R9。

---

## Phase 10: Polish & Cross-Cutting

- [x] T072 [P] `scrape/src/horseracing_scrape/jrdb/README.md`: 役割(第 3 の writer・学習データの正本)・禁止事項(別定義 4 列を current に書かない・TYB/基準オッズを現在値に書かない・会員物を repo に置かない・規則を複製しない)・ファイル配置・CLI 一覧・176 README への相互参照
- [x] T073 [P] `jrdb/cli.py` に `--apply-held FILE_ID`(運用者承認の差分適用: `held` 行の差分を表示→確認→§3 を再適用・先着保持の項目は変えない)と `jrdb-fetch --show-held`。`scrape/tests/integration/test_jrdb_held_apply.py` で held → 承認 → applied と、承認無しでは製品値 0 変更
- [x] T074 [P] 性能: 公開サンプル 1 日分の decode が 1 秒以内・parity 1 年分(約 3,400 レース・約 48,000 行)が合成で 30 分以内の見積りを `research.md` D17 に追記(実測は R4 で)
- [x] T075 [P] `ruff check` を scrape/db/live の変更ファイルで緑に。`front/openapi.json`・`admin/openapi.json`・`ops-openapi.json` が `git diff --numstat` で 0 行(SC-008・FR-017)
- [x] T076 codex に tasks レビューを依頼済み(2026-11-08・`codex exec -m gpt-6-astra`・質問 2 問)。11 件全採用・`research.md` D18 に記録・本 tasks.md に反映済み(T041/T042/T062/T063/T064/T064a/T033/R2/R3/R5/R8/R9・開催日制約の節)
- [x] T077 `CLAUDE.md` の 178 要約を「tasks 済」に更新し、メモリ `feature-178-jrdb-primary-source.md` を更新

---

## Phase 11: Rollout(実 DB・実会員ページ・順序が大事)

**Purpose**: plan の段の順で本番に当てる。各段の記録が次段の前提。開催日: 2026-11-15/16・11-22/23・11-29/30(予定)。

- [x] R0a **2026-11-09 利用者確認済み(spec FR-018 に転記)** 利用者が JRDB に規約を確認する(FR-018: 自動取得・継続取得・DB 保存・解約後の保持。研究 D5 の読みは「私的利用に収まる」だが明文は無い)。回答を `spec.md` FR-018 の NEEDS CLARIFICATION に転記。契約・初回ダウンロード(R1)の前に済ませる
- [ ] R0 [US0] 段 0 診断を **2 開催日**(例 11-15・11-16)流す: `live source-probe --date D --mode official-odds`(鮮度条件を実行前に `…-config.json` で凍結)・`dependencies`・`arrivals`(当日ファイルの HEAD のみ)。`artifacts/jrdb/probe/` に記録。結果を `spec.md` FR-019 の NEEDS CLARIFICATION に転記(SC-001)
- [x] R1 🕒 **2026-11-09 実施: 最新形式=T006a 不要(result-review)** 年度パック 1 年分で版を確認(週中・利用者承認後): `scripts/jrdb_ops.sh fetch 2008 --archive --kinds SED KYI`(`SED_2008.zip`・`KYI_2008.zip` の 2 要求)→ 行長が 376/1024 か、KYI/SED の最新版レイアウトで decode できるかを `jrdb-parity --year 2008` で確認。**旧レイアウトなら** `layout/{kyh,sec}.py` を追加するタスク(T006a)を起こしてから先へ。研究 D2/D16 を確定
- [x] R2 ⏸ **(1)〜(5) 2026-11-09 実施・(6) は 11/15 の開催日で確認=`result-review.md`** 開催終了後に main へ反映(1 回・codex D18 #2/#3/#5 の手順): (1) **先に本番 DB の 0024 複製に対し候補リリースの checkout から `alembic upgrade 0025_jrdb_source` を実際に流して成功を確認**(migration 環境が `models/__init__` を読むので `Source.JRDB`/`JrdbFile`/CHECK の対応が揃っていないと落ちる。失敗なら本番に触らない)。(2) ops の新規ジョブ投入を止め(ops-api 停止)・worker 停止・**実行中ジョブと subprocess の終了を `pgrep` で確認**。(3) `pg_dump` → 本番 DB に 0025。(4) 共通コード変更(T015/T016 export/T022/T036/T046/T050a/T064a/T065/T068/T069)をマージし稼働 checkout を切替。**全 JRDB/緊急/停止フラグは既定(`jrdb_enabled=false`・`jra_emergency_entries=false`・`netkeiba_enabled=true`・`near_post_source=netkeiba`)のまま**=機能は登録だけで動かない。(5) api/ops-api/worker 起動 → `GET /api/v1/health` が head 一致・ops status 正常。(6) netkeiba 経路の 1 開催日通常運用で異常なし。**復旧手順を 2 つに分ける**: JRDB 初回書込み前はコード巻き戻し+`alembic downgrade 0024`、書込み後は downgrade 不可なのでコード巻き戻しのみ(フラグ false で JRDB 経路は止まる)
- [ ] R3 🕒 **収集・報告 v3 済み(2026-11-09)・名前だけ一致 51 件は利用者承認待ち・id_mappings 未書込み** 人物写像: `scripts/jrdb_ops.sh persons-collect`(JRA 公式の騎手/調教師ページ 約 1,500 件・10 秒間隔・約 4〜5 時間・1 回限り・開催日の公式経路と同時に流さない)→ `jrdb-fetch --kinds KZA CZA`(直近の週次マスタ 2 本)→ `jrdb-persons --dry-run` で報告 → mapped 率(2007 年以降に出走行を持つ人)が **99% 以上**(SC-010)なら `--dry-run` 無しで `id_mappings` へ。conflict は一覧を `specs/178-jrdb-primary-source/evidence/persons-conflicts.md` に。分母は T033 の定義(対象データの一意コード)
- [x] R4 🕒 **2026-11-09 実施(result-review)・owner_name/prize_money は基準未達=利用者決定で修復対象から外す(契約どおり)** 段 1 parity 2007〜2024(週中・年度パック 8 種別×18 年=約 144 要求・1 日で終える必要はない): `jrdb-fetch --archive --from 2007-01-01 --to 2024-12-31 --kinds BAC KYI UKC SED HJC` → `jrdb-parity --year Y` を 18 年分 → `artifacts/jrdb/parity/`。**結果を見る前に contracts/parity.md の基準を commit 済みにする**。各列の合否・`uncovered_reference_cells`・JRDB<DB の年を `specs/178-jrdb-primary-source/result-review.md` に(SC-002/009 の前半)。見習い騎手の斤量(JRDB 51.0 vs DB 54.0)はここで JRA 公式の結果ページと突き合わせて決着
- [ ] R5 **複製での再生は 2026-11-09 に合格(10/31・11/01・result-review)・`jrdb_enabled: true` への切替は R3 の mapped 確認後** 段 2 を本番 DB 複製で再生(1 開催日・例 11-15): `createdb horseracing_replica`+`pg_restore` → quickstart 段 2 の 2 順序 → `static_mismatch=0`・新規 `nk:` 0・2 回目変更 0・保護対象不変(SC-003/004)→ 合格なら policy `jrdb_enabled: true` にして本番 DB で前日 `jrdb-entries` を開始(JRDB が行を作り、公式・netkeiba はその行に書く)。**当日の必要人物が全件 mapped**(`jrdb-audit` の `unresolved_person=0`)でなければその開催日は開始しない。合格しなければ T046〜T051 に戻る
- [x] R6 **2026-11-09 本適用(8 列・2 回目変更 0・過去走経由の波及は S1〜S5 該当 0 変化=result-review)** 段 3a 修復(現行 DB): R4 で基準合格の列だけ `jrdb-repair --column C --from 2025-01-01 --to 2026-11-30 --dry-run` → 報告 → 本適用。前後の年別充足率(馬主・生産者 netkeiba 期 ≥95%=SC-005)と `scripts/jrdb_repair_impact.py` の波及(SC-006)を `result-review.md` に。FEATURE_VERSION 据え置き
- [x] R7 🕒 **2026-11-09 実施(2 時間 18 分・全年 現行以上・blocking 空・jockey/trainer は R3 承認後に再投入=result-review)** 段 3b 再構築(別 DB・週中): `createdb horseracing_jrdb` → `alembic upgrade head` → `jrdb-fetch --archive --from 1999-01-01 --to 2006-12-31`(残り年度パック)→ `jrdb-rebuild --from 1999-01-01 --to 2026-11-30 --db-url … --reference-db-url …` → `compare-<year>.json` で各年 現行以上・`uncovered_reference_cells`(44 列)=0・`blocking_years` 空(SC-009)。学習・切替は別 spec
- [ ] R8 段 3.5 運用成立試験(**2 開催日**・例 11-22・11-23): 本番 DB 複製から当日の netkeiba 由来行を含まない状態を作り(`nk:` 行・`data_source='netkeiba'` の当日行が無いこと=起動検査)、`live source-trial --date D --db-url … --files-dir … --arrivals artifacts/jrdb/probe/<R0 の日>.jsonl`。2 日目は `--delay-kind KYI --by 3h` で緊急モード(生成処理の実行記録まで確認)。隔離は起動検査(T062)に加え、試験プロセス群に netkeiba ホストへの外向き接続ブロックを掛け、HTTP キャッシュ dir を空にして始める。合格条件は全レースの行生成・T−3 捕捉(鮮度条件)・結果・確定オッズ・払戻の充足(SC-001a)。不成立が 1 件でもあれば原因を `result-review.md` に書き段 4 に進まない(利用者判断 1 が「割合」に変わればその基準で)
- [ ] R9 ⏸ 段 4: 利用者承認の上で、まず `near_post_source: jra_official`(T064a)を前の開催日で 1 日運用し T−3 記録が揃うことを確認 → `config/source-operations.json` の `netkeiba_enabled: false`(policy は CLI 起動時に読む・**ops worker と ops-api は停止→起動で古い設定を持つ処理を残さない**・fetcher を保持している常駐プロセスが他に無いことを `pgrep` で確認=codex D18 #5)→ 1 開催日 → `ingestion_jobs` で netkeiba 要求 0 件・T−3 記録・結果・確定オッズ・払戻が揃う(SC-007)→ `result-review.md`。問題があれば `true` に戻す(復旧手順は manual.md)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1(Setup)** → **Phase 2(decode/ids/profile)** → すべての US。Phase 1/2 は開催中でも進められる。
- **Phase 3(DB 契約)**: モデル定義(T016/T017/T018/T019)は Phase 1 と並列可。⏸ の T015 と `models/__init__.py` export、本番適用(R2)は開催終了後。US2 以降の統合テストは一時 DB で 0025 を使うので Phase 3 のファイルが先。
- **US0(Phase 4)**: Phase 1〜3 に依存しない(公式経路と DB の読取だけ)。T022 の CLI 登録のみ ⏸。**最初に実行できる**(R0)。
- **US1(Phase 5)**: Phase 2 に依存。`jrdb_files` を書くので Phase 3 のモデルに依存(一時 DB)。parity 自体は製品表を書かない。
- **US2(Phase 6)**: US1 の fetch/files/persons と Phase 3、`precedence.py` の拡張(T046 ⏸)に依存。緊急モードの本番結線(T050a ⏸)は T050 の後・R2 で反映。
- **US3(Phase 7)**: 3a は US1 の parity 報告(R4)の合格列に依存。3b は US2 の `upsert_jrdb_*` に依存(同じ関数を別 DB で)。
- **US3.5(Phase 8)**: US2・US0 の `arrivals` 記録・`jra/emergency_entries.py` に依存。
- **US4(Phase 9)**: US3.5 の全件成立(R8)が実運用(R9)の前提。コード(T066〜T071)は US2 と並列で書ける。
- **Rollout**: R0 → R1 → R2 → R3 → R4 → R5 → R6/R7(並列可・R7 は週中)→ R8 → R9。R1 が旧レイアウトを示したら T006a を挟む。

### User Story Dependencies

- US0: 独立(診断)。US1: Phase 2/3。US2: US1(fetch・files・persons)。US3: US1(基準)+US2(upsert)。US3.5: US2+US0(到着時刻)。US4: US3.5(実運用の前提)。

### Within Each User Story

- テストを先に書いて赤を確認(US2 の規則・US3 の allowlist・US4 のゲート)
- 純関数(decode/ids/profile/precedence)→ 薄い upsert → CLI → wrapper
- `profile` は全経路の必須引数(D7a)。`current` で別定義 4 列が変わらないテスト(T041 d)は US2 の完了条件

### Parallel Opportunities

- Phase 1: T003/T004/T005/T006/T007 は並列。Phase 2: T009/T010 と T011〜T014 は並列。
- Phase 3: T016/T017 の後 T018/T019 は並列。
- US0(T020/T021/T023)は US1 と完全に並列。
- US1: テスト 5 本(T024〜T028)は並列・実装では T032(JRA 人物ページ)が T029〜T031 と並列。
- US2: テスト 6 本(T040〜T045)は並列・T050(緊急モード)は T046〜T049 と並列。
- US3: T054/T055/T056 は並列、T057/T059 は並列(repair と rebuild は別ファイル)。
- US4 のコード(T066/T067/T071)は US2 と並列。

---

## Parallel Example: User Story 1

```bash
# テストを先に(別ファイル・並列)
Task: "test_jrdb_fetch.py — Basic 認証・1 要求・HEAD skip・blackout・秘密の非漏えい"
Task: "test_jrdb_files_versioning.py — same/apply/supersede/held"
Task: "test_jrdb_persons_match.py — 名前+生年月日=mapped・名前のみ=conflict"
Task: "test_jra_parse_person_profile.py — pw04kmk0/pw05cmk0 の合成 HTML"
Task: "test_jrdb_parity_report.py — denominator/by_segment/uncovered_reference_cells"

# 実装(別ファイル・並列)
Task: "jrdb/fetch.py" / Task: "jrdb/files.py + blackout.py + policy.py" / Task: "jra/parse/person.py + jra/person_profile.py"
```

---

## Implementation Strategy

### MVP First(Phase 1〜3 + US0 + US1)

1. Phase 1〜2(開催中でも可)→ 公開サンプルで decode/ids/profile が緑
2. Phase 3 のファイル(T016〜T019)→ 一時 DB で 0025 が通る
3. US0(T020〜T023)→ R0 を直近の開催日に流す(診断・合否なし)
4. US1(T024〜T039)→ 公開サンプルで `jrdb-parity --year 2008` が JSON を書く
5. **STOP**: R1(年度パック 1 年分・週中)で版を確定 → R2(開催終了後に共通コードと migration)→ R3/R4(人物写像・parity 18 年)
6. R4 の報告を読んでから US3 の修復対象列が決まる

### Incremental Delivery

- US2(writer)→ R5 複製で再生 → 本番で前日 `jrdb-entries` 開始(netkeiba は並走のまま)
- US3(3a/3b)→ R6/R7(3b は週中・数時間)
- US3.5 → R8(2 開催日・隔離)→ 全件成立で US4 → R9(policy 1 行)

### Honest Limits(tasks 時点)

- 年度パックが本当に最新形式かは R1 まで不明(旧形式なら decoder 2 本追加=T006a)。
- 人物写像は JRA 公式ページ約 1,500 件の 1 回収集(4〜5 時間)が前提。99% 未満なら conflict を手動で解く時間が要る。
- 段 3.5 は「全件成立」が既定。T−3 の公式取得が 1 レースでも落ちると段 4 に進めない(利用者判断 1)。
- 連闘馬の前走確定オッズ・前 3F は木曜まで欠ける(研究 D14)。段 3.5 で頻度を測り限界に記録するだけで、埋めない。
- 本 feature は予測精度・回収率の改善を約束しない。

---

## Notes

- [P] = 別ファイル・依存なし。⏸ = 開催終了後。🕒 = 会員ページの過去ファイル(開催日 08:00〜19:00 禁止・承認後)。
- 会員 URL・認証情報・配信ファイル・その由来の値を repo/fixture/ログ/成果物に置かない(FR-022)。テストは公開サンプルと合成行だけ。
- 規則は `precedence.py` が正本。`jrdb/` に複製を作らない。別定義 4 列は `profile=current` で二重ガード。
- 基準(contracts/parity.md)は結果を見る前に commit。結果を見てから閾値を動かさない。
- 各 checkpoint で止まって確認してよい。コミットはタスク群ごとに。
