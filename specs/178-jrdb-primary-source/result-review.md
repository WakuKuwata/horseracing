# 178 実装の結果記録(2026-11-09〜)

実装は `.claude/worktrees/178-jrdb-primary-source`(起点 main 86eeeafb)で行い、2026-11-09(月)に main へマージ(f1eebe8a・--no-ff・実装 5e541e9a)。
テスト・codex 採否・性能見積りは `research.md` D20〜D26 と `tasks.md`。以下は rollout(Phase 11)の記録。

## R2 本番反映(2026-11-09 月 10:08〜10:12 JST・非開催日)

| 手順 | 結果 |
|---|---|
| (1) 複製でリハーサル | `pg_dump -Fc`(25 秒・365 MB)→ `horseracing_r2_rehearsal` に restore(24 秒)→ `alembic upgrade 0025_jrdb_source` 1 秒で成功。`jrdb_files` 0 行・7 表の CHECK が `jrdb`/`jrdb_result` を含む名前で置換 |
| (2) 停止 | 実行中ジョブ 0(直近 24h: succeeded 972 / partial 151 / skipped 157)。`stack.sh stop` の後も **別セッションが 11/07 に起動した ops-api/worker(pidfile `logs/forecast-auto-*.pid`・launchd 配下に孤児化)が残っていた**ので SIGTERM で停止。subprocess・DB 接続とも 0 を確認 |
| (3) バックアップ→0025 | `artifacts/db_backups/horseracing-pre0025-final-20261109-101030.dump`(365 MB・gitignore)→ 本番に `alembic upgrade 0025_jrdb_source`(lock_timeout 5s・即時成功)→ `alembic_version = 0025_jrdb_source` |
| (4) コード切替 | main マージ済み(f1eebe8a→657fa8a3→e919e6ed)。policy は全フラグ既定(`jrdb_enabled false` / `netkeiba_enabled true` / `jra_emergency_entries false` / `near_post_source netkeiba`)=JRDB・緊急・停止の機能は登録だけで動かない |
| (5) 起動 | `stack.sh start api / ops-api / worker` → `GET /api/v1/health` が `alembic_current = alembic_head = 0025_jrdb_source`・`schema_in_sync true`、ops status `writes_accepted true`・`policy_mismatch false`、worker `io=2 cpu=3` で起動 |
| (6) netkeiba 経路の 1 開催日 | **未**(次の開催日 2026-11-15 で確認) |

復旧: JRDB 初回書込み前なのでコード巻き戻し+`alembic downgrade 0024_jra_track_conditions` が使える(`jrdb_files` 0 行・`jrdb` 行 0 のあいだ)。

## 認証情報と到達確認(2026-11-09)

`config/.env.jrdb`(600・gitignore)に会員 ID/パスワード。HEAD 2 件(BAC/KYI 261108)が HTTP 200・前日 15:40 JST 公開(research D16)。ダウンロードは未実施(R0a の規約確認の後に R1)。

## R2 後に見つけた欠陥 2 件(2026-11-09・同日修正)

- `scripts/jrdb_ops.sh` が呼ぶ `python -m horseracing_scrape.jrdb.cli` に `__main__` の入口が無く、**wrapper 経由では何も実行されず exit 0 で終わっていた**(統合テストは関数を直接呼ぶので緑のまま)。入口を追加し、本番 DB への `jrdb_ops.sh audit 2026-11-08` が 36 レース(全行 netkeiba・結果/確定オッズ/払戻 36/36・T−3 記録なし)を返すことを確認。
- tasks T037/R3 の `persons-collect` が wrapper にも CLI にも無かった(`jra/person_profile.collect_profiles` は関数だけ)。`jrdb-persons-collect --kind --index-url --index-cname [--max-pages]` を追加(176 の `validate_startup`+`JraFetcher` をそのまま使う・DB の 5 桁 ID だけを対象・jsonl は append-only・拒否で exit 1)。一覧ページの URL/cname は R3 当日に公開サイトの遷移から運用者が取る。

## R0a・R1 年度パックの版確認(2026-11-09 月 10:24〜10:27 JST)

R0a: 利用者が規約を確認し「確認済み」(spec FR-018 に転記)。

R1: 一時 policy(`jrdb_enabled: true` だけ変えた複製・本番 config は false のまま)で `jrdb-fetch --archive --from 2008 --to 2008 --kinds SED KYI`。
要求 2 件・`requests_sent=2`。

| zip | 展開 | 行 | 桁ずれ | 行長 |
|---|---|---|---|---|
| `SED_2008.zip` | SED 108 日 + SRB 108 日(非 txt 58 件は skip) | 53,902 | 0 | SED 376・SRB 852 |
| `KYI_2008.zip` | KYI 108 日 | 50,450 | 0 | KYI 1024 |

→ **年度パックは最新形式(KYI 第 11 版・SED 第 4 版・SRB)で再生成されており、旧版 decoder(T006a)は不要**(research D2/D16 を確定)。
`jrdb_files` に 324 行(variant archive・status fetched)。

`jrdb-parity --year 2008`(DB 読み取り専用・5 秒・`artifacts/jrdb/parity/2008.json`):

- 結合: レース 3,452/3,452・出走 50,450/50,450・馬 ID 50,450 全結合(unjoined 0)。結果行は JRDB 50,450 vs DB 50,110=JRDB 側だけの 340 行は取消/除外(DB は結果行を持たない・仕様どおり)。
- 一致 100%: finish_order・finish_time・last_3f・horse_number・frame・sex・odds・popularity・weight・weight_diff・jockey_weight・weather。
- horse_name 99.97%(不一致 3 件は DB=JRA-VAN が 9 文字で切っている・JRDB が正)。
- **基準未達**: `going` 99.07%(基準 99.5%・不一致 32 レース・芝に偏る)、`finish_time_diff` 94.6%(基準 99%・全区分で 93〜95%=定義差の疑い。JRDB の「1(2)着タイム差」の符号/単位を R4 で確定)。
- 別定義の確認: `first_3f` 34.8%(1200m 芝/ダは 100%・他は 7〜27%)、`corner_orders` 18.3%、`running_style` 0%=FR-021a どおり現行 DB には書かない。
- 未比較(ファイル未取得): UKC/BAC/HJC 由来の列(血統・馬主・生産者・レース静的・払戻)は R4 で。騎手 247・調教師 336 コードは未写像(R3 の収集後)。

R4 への申し送り: `going` と `finish_time_diff` は基準未達のまま allowlist(修復対象)には入れない。残り 17 年の取得は 5 種別 × 18 年=90 要求。

## R4 段 1 parity 2007〜2024(2026-11-09 月 10:41〜11:04 JST)

取得: 年度パック 88 要求(2007 年 5 種別・2008 年 BAC/UKC/HJC・2009〜2024 年 5 種別・5 秒間隔・約 11 分)。展開 11,428 ファイル・273 万行・**桁ずれ 0**。
`SED_2022.zip`・`HJC_2024.zip` の job は `partial`(errors 1・error_messages なし・行は全件読めている=zip 内の非 txt メンバー 1 件の扱い。要再現・影響なし)。
`jrdb_files` は 11,644 行(全部 fetched・variant archive)。展開済みテキストは `artifacts/jrdb/`(約 1 GB・gitignore)。

parity は 18 年とも 5〜6 秒(1 年 3,450 レース)。**結合は全年 100%**(レース・出走・馬 ID とも unjoined 0)・JRDB<DB の年なし・
`uncovered_reference_cells` 0。結果行は毎年 JRDB の方が 220〜340 多い(取消/除外)。

比較側の表現規則を 2 回直して取り直した(`contracts/parity.md`「表現規則の是正」・閾値不変。初回 `artifacts/jrdb/parity/r4-first-run/`、
2 回目 `r4-second-run/`、確定 `artifacts/jrdb/parity/<year>.json`)。

| 判定 | 列 |
|---|---|
| **合格(18 年すべて基準以上)** | finish_order・finish_time・finish_time_diff(大きさ比較)・last_3f・horse_number・frame・sex・odds・popularity・weight・weight_diff・jockey_weight・horse_name(99.97%・DB 側 9 文字切り)・birth_year・**breeder_name**(99.6〜99.9%)・race_date・race_class・**grade**(文字コード写像後 100%)・track_type・distance・weather |
| **不合格・表現体系の違い** | sire_name 90〜94%・dam_name 90〜97%・damsire_name 51〜75%: JRDB はカナ転写、DB(JRA-VAN)は外国馬を原綴り。現行 DB の血統名は特徴の集計キーなので **JRDB で書かない**(JRDB 正本 DB では JRDB 表記で一貫) |
| **不合格・時点差** | owner_name 95.4〜98.5%(年を追って改善): 残りは馬主変更(JRDB の UKC 発行時点 vs DB の最終値)と JRDB 独自の略記(`HimRockRacingH`)。**allowlist 列だが基準未達=現行 DB の修復ゲートは拒否する** |
| **不合格・値の違い** | going 97.9〜100%(7 年で未達・開催内の馬場変更が片側だけに反映)・prize_money 99.3〜99.9%(年 6 レース前後が JRDB の 0.7 倍・頭数規則ではない・原因未特定・**allowlist 列だが未達**)・race_name_short 48〜50%(JRDB の 4 文字略称 `WAJ1` は前方一致にもならない) |
| **別定義(基準なし・現行 DB に書かない=FR-021a)** | first_3f(1200m のみ 100%・他 7〜27%)・corner_orders 18%・running_style 0%・sire_line/damsire_line 0% |
| **未比較** | post_time(DB の JRA-VAN 期は NULL=jrdb_only)・win_payout(DB の公式払戻は 2025-10-11 以降のみ=2007〜2024 に重なりなし。2025 年の parity で測る)・jockey_id/trainer_id(人物写像=R3 後) |

修復 allowlist 10 列の R4 時点の可否: odds ✓ popularity ✓ weight ✓ last_3f ✓ breeder_name ✓ race_class ✓ / owner_name ✗ prize_money ✗ /
post_time(jrdb_only・NULL 埋めのみ可) / win_payouts(2025 parity 待ち)。

見習い騎手の斤量(JRDB 51.0 vs DB 54.0・サンプルで 5 行)は 18 年の jockey_weight が 100% なので再現せず(サンプル固有)。

**利用者判断(R6 の前)**: owner_name と prize_money を基準未達のまま「netkeiba 期(2025-10-11 以降)の NULL 埋めだけ」に限って書くか、
契約どおり現行 DB には書かず JRDB 正本 DB(R7)だけに持つか。

