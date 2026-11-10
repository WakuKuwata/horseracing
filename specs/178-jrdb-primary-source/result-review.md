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

**利用者判断(2026-11-09)**: **外す(契約どおり)**。owner_name と prize_money は現行 DB の修復対象から外し、JRDB 正本 DB(R7)でだけ JRDB の定義で持つ。
R6 の修復対象は odds・popularity・weight・last_3f・breeder_name・race_class(+post_time の NULL 埋め・win_payouts は 2025 parity 後)。
`repair.assert_repairable` は parity 報告の rate を実行時に読むので、owner_name/prize_money は `parity_below_threshold` で拒否される(コード変更不要)。

## R5 段 2 を本番 DB 複製で再生(2026-11-09 月 11:40〜11:58 JST)

複製 = R2 のリハーサル DB(`horseracing_r2_rehearsal`・本番の 10:08 時点の dump・0025 適用済み)。対象は直近の開催日 **2026-10-31・11-01**
(11/02 は非開催日。正式版 SED は 11/05 木に公開済み)。日次ファイル 5 種別 × 2 日 = 10 要求(一時 policy・レプリカの `jrdb_files` に登録・桁ずれ 0)。
複製には netkeiba(+公式)が先に書いた行がある=**本番で R9 までのあいだ JRDB が置かれる順序そのもの**(netkeiba 先・JRDB 後)。
逆順(JRDB 先 → 公式 → netkeiba)は SC-004 の合成再生 `test_jrdb_replay_order.py` が固定している。

`jrdb-entries` → `jrdb-results` → `jrdb-payouts` を両日に流し、製品表を PK 付きの値+xmin で前後比較(`r5_snapshot.py`):

| 観点 | 結果 |
|---|---|
| 新規行 | races/race_horses/horses とも追加 0・**新規 `nk:` 0**(全 DB の nk: 馬 5,696 で不変)。alias_horses 191+162=既存 nk: 行への別名書込み(FR-011a) |
| 先着保持 | race_results 619 行は値・xmin とも不変(レース単位初回挿入)。horses 不変 |
| 静的項目 | `static_mismatch` 13+13(契約の行 28: stored=netkeiba なら write+報告)。中身は **表記差**=race_class 全角→半角 24(`２勝`→`2勝`・JRA-VAN と同じ綴り=098 の分裂を JRDB が戻す向き)・race_name 8(`紅葉S`→`紅葉ステークス`・`天皇賞(秋)`→`天皇賞（秋）`=JRA-VAN と同じ)・grade 1(L) |
| 時刻規則 | post_time 3 件(netkeiba 15:01→JRDB 15:00、16:30→16:25=BAC の発走予定) |
| 体重 | weight_diff 0→NULL 110 行=**全部デビュー戦**(JRA-VAN 期の慣行は NULL 473/478・netkeiba 期が 0 を書いていた=JRDB が慣行を戻す)。**weight NULL→0 が 4 行(取消馬)=欠陥**: JRDB の `000` は計測なしなのに 0 kg として書いた → decode で 0→None に正規化(同日修正・単体テスト追加)し、複製を戻して再実行 |
| 確定オッズ・人気 | `final_odds_updated` 3+8(人気の ±1 入れ替わり 11 行・SED 正式版を採用=契約の行 32)・`win_odds_observed_at` 6 |
| 払戻 | 値は全 48 行不変・採用元が `netkeiba_result`→`jrdb_result`(jrdb > netkeiba)。exotic_odds 483 行も同じ(値不変・source のみ) |
| 新規データ | race_laps 46 レース(netkeiba 期は 0 だった)・来歴 2 表に jrdb 行 860 |
| **2 回目・3 回目** | 製品表の値変更 **0**・不要書込み(xmin 移動)**0**。来歴 `race_field_provenance` の払戻 2 群 48 行だけ observed_at/job_id が更新(確認時刻の記録・値不変) |

判定: SC-003(新規 nk: 0・保護対象不変)・SC-004(2 回目変更 0)は成立。`static_mismatch=0` は「JRDB が先」の順序でしか成立しない指標で、
netkeiba が先の複製では表記差 13 件として出る(値はどれも JRA-VAN 側の綴りに寄る)。**本番で `jrdb_enabled: true` にして前日 `jrdb-entries` を始めるのは、
R3 の人物写像(当日の騎手・調教師が全件 mapped)が済んでから**=`unresolved_person` 610/636 はまだ全件(id_mappings 0 行)。

## R3 人物写像(2026-11-09・進行中)

**名鑑の構造(公開サイト `/datafile/meikan/`・2026-11-09 調査)**: 引退騎手 `jretirement.html`(1994〜2026 年度・315 人)と引退調教師 `retirement.html`
(82 人)は静的ページで `pw04kmk0<5 桁>` / `pw05cmk0<5 桁>` の doAction リンク。現役は「あ行〜わ行」の POST 索引(`accessK.html`+`pw04knlH1<行>/<check>`・
`accessC.html`+`pw05cnlH1<行>/<check>`)。人物ページは `<h1>` が「騎手情報引退蓑島 靖典（ミノシマ ヤスノリ）」の形、生年月日は `#meikan_jockey_detail .profile` の `<dl>`。

**収集 v1(10:23〜12:24・720 要求・10 秒間隔)**: 騎手 441・調教師 258 ページ、解析失敗 0、拒否 0。**ただし name が全件「検索ウィンドウ」**
(サイト共通 `<h2>` を拾う parser の欠陥・生年月日と所属は正しい)→ parser を修正(30afb201)し **v2 を再収集中**(`jra-profiles-v2.jsonl`)。

**暫定報告(v1 の生年月日+同じ公式 ID の JRA-VAN 名・KZA/CZA 2026-11-07 版 1,285/1,348 人)**:

| 分母(KYI に現れる JRDB コード) | 騎手 | 調教師 |
|---|---|---|
| 2007〜2026 | 660 中 mapped 246・conflict 46・unmapped 368 | 767 中 mapped 240・conflict 25・unmapped 502 |
| netkeiba 期(2025-10-11〜) | 178 中 mapped 122(68.5%)・conflict 15・unmapped 41 | 225 中 mapped 172(76.4%)・conflict 14・unmapped 39 |

- conflict の中身はすべて「生年月日は一致・名前が違う」で、JRA-VAN 名の 4 文字切り(`五十嵐雄祐` vs `五十嵐雄`)=v2(公式ページの氏名)で解消する見込み。
- unmapped は **JRDB コード 3xxxx(地方騎手)と短期免許の外国人騎手・地方の調教師**で、JRA 名鑑に載らない=生年月日照合では構造的に写像できない。
  netkeiba 期の騎乗数では 98.1%(騎手)/99.9%(調教師)が名鑑のある人物。残りは 名前だけの一致 → 憲法 I どおり conflict(手動承認)で、
  候補一覧を `evidence/persons-conflicts.md` に出す予定。R9 の構成では公式出馬表が騎手/調教師 ID を直接書くので運用上は埋まる。
- SC-010(2007 年以降に出走行を持つ人の 99%)は名鑑経由だけでは届かない(名鑑に無い人が 2007+ で 3 割)。達成には手動承認分を含める必要がある。

## R6/R7 用の取得と netkeiba 期の parity(2026-11-09 月 12:00〜12:41 JST)

| 取得 | 要求 | 展開 | 行 | 桁ずれ |
|---|---|---|---|---|
| 年度パック 2025(5 種別) | 5 | 109 日 × 6 kind(SED に SRB 同梱) | 153,319 | 0 |
| 年度パック 1999〜2006(5 種別) | 40 | 842〜843 日 × 6 kind | 1,227,790 | 0(`UKC_1999.zip` の job が `partial`・errors 1・message なし・103 日分は全部 written=要再現、影響なし) |
| 2026 日次(開催日 95 日 × 5 種別) | 475 | 95 日 × 6 kind | 132,474 | 0 |

`jrdb_files` は 17,923 行(1999-01-05〜2026-11-08・全部 fetched)。展開済みテキストは `artifacts/jrdb/`。
この時点で **JRDB の 1999〜2026 全期間がローカルにある**(製品表には未書込み)。

**parity 2025 / 2026(netkeiba 期を含む・`artifacts/jrdb/parity/netkeiba-era/`・修復ゲートの読み先 2007〜2024 とは別に置く)**:
- 2025: 結合 100%(entries 47,884 vs DB 47,886・両方 45,286=JRA-VAN→netkeiba 切替後の `nk:` 行は結合しない)。odds 100%・popularity 99.86%・weight 100%・last_3f 100%・breeder 99.75%・race_class 100%・post_time 92.6%(739 セル・netkeiba 期のみ)・prize_money 99.44%。
- 2026: entries 41,164 のうち結合 23,787(残りは `nk:` 行=writer は別名で書くが parity は結合しない)。**odds 93.3%・popularity 94.9%・post_time 93.3%・finish_time_diff 96.5% が不一致=netkeiba 期の DB 側の値が確定値とずれている列**(R6 の修復対象の根拠)。
  weight/weight_diff/last_3f/race_class/grade/going/weather 100%、**win_payout 840 セル 100%**(`win_payouts` 修復のゲートはこの報告だけを置いた `parity/win-payout-gate/` で通す)、first_3f は 1200m の 4,535 セルで 100%。
- 血統名・馬主は 2007〜2024 と同じ表記体系差(書かない)。

次: R6 dry-run(odds・popularity・weight・last_3f・breeder_name・race_class・post_time・win_payouts/owner_name・prize_money は拒否の確認)と
R7(`horseracing_jrdb` を head で作成 → `jrdb-rebuild 1999-01-01..2026-11-08`)を背景で実行中。

## R6 段 3a 修復の dry-run(2026-11-09 月 12:42〜12:48 JST・本番 DB・書込みは巻き戻し)

対象 2025-01-01〜2026-11-30・`--parity-dir artifacts/jrdb/parity`(2007〜2024 の 18 本)・`win_payouts` だけ `parity/win-payout-gate/`(2026 の 840 セル 100%)。
各列 20〜70 秒。owner_name と prize_money は `parity_below_threshold` で拒否(利用者決定どおり)。

| 列 | 値が変わる行 | 不変 | 内訳 | 充足(2025 → / 2026 →) |
|---|---|---|---|---|
| odds | 3,555 | 48,511 | netkeiba 期の確定値ずれの訂正(JRA-VAN 期 2,663 レースは skip・別名が両方いるレース 2) | 99.6% / 99.7%(充足は不変=値の訂正) |
| popularity | 3,555 | 48,511 | 同上 | 同上 |
| weight | 5,715 | 46,466 | 計測値の訂正+デビュー戦の weight_diff 0→NULL | 99.8→99.9% / 99.7→99.9% |
| last_3f | 202 | 85,619 | NULL 埋め(結果行なし 14) | 99.6% / 99.1→99.5% |
| breeder_name | 668 | 76,621 | NULL 埋め。netkeiba の綴りと違う 1,288 は keep・JRA-VAN/公式所有 9,833 は keep | 100% / 100% |
| race_class | 1,871 | 4,291 | netkeiba 期の全角→半角など(static_mismatch 1,871)・JRA-VAN 期 234 は keep | 100% / 100% |
| post_time | 2,918 | 3,478 | JRA-VAN 期 2,663 レースの NULL 埋め+netkeiba 期 255 の時刻訂正 | **22.9→100%** / 100% |
| win_payouts | 2,949 | 0 | 公式払戻のない 2,949 レースに HJC から追加(既存 840 は一致確認のみ) | 0→22.9%(netkeiba 期のみ) / 28.0→100% |

本適用の前後で `scripts/jrdb_repair_impact.py`(T058・SC-006 の波及)を取る。本適用は利用者の承認後。

**収集 v2(parser 修正後・12:31〜14:32・720 要求)**: 騎手 441・調教師 258・名前の欠陥 0。**写像報告 v2**(KZA/CZA 2026-11-07・名前+生年月日):

| 分母 | 騎手 | 調教師 |
|---|---|---|
| 2007〜2026 | 660 中 mapped 277(42.0%)・conflict 15・unmapped 368 | 767 中 mapped 258(33.6%)・conflict 7・unmapped 502 |
| netkeiba 期 | 178 中 **mapped 136(76.4%)**・conflict 1・unmapped 41 | 225 中 **mapped 185(82.2%)**・conflict 1・unmapped 39 |

conflict は全部「生年月日だけ一致・名前が違う」(地方・外国コード 3xxxx/7xxxx の別人、JRDB 略記 `Ｔ．マーカン`)=正しく止めている。
unmapped 82 件(netkeiba 期)の内訳を `artifacts/jrdb/persons/persons-conflicts.md`(JRDB 由来の名前を含むので repo に置かない)に出した:
名前だけ一致の候補がちょうど 1 件 **51**(騎手 27・調教師 24・netkeiba 期の騎乗 957 件/出走 41 件を占める)・候補なし 27・複数候補 4。
候補なしの中に **美浦/栗東の新規調教師 9 人(JRDB 10522〜10532)**がいる=DB には `nk:` 行しかなく、DB 由来の wanted から漏れて名鑑の
ページを取っていなかった → `persons-collect --all-linked`(索引に載る全員)を追加して追加収集中。承認用の候補 JSON は
`artifacts/jrdb/persons/manual-proposal.json`(利用者が見て承認したものだけ `jrdb-persons --manual` で書く)。

**追加収集(`--all-linked`・14:36〜14:42・要求 34)**: DB が知らなかった 12 人(騎手 1・調教師 11=2026 年新規開業の美浦/栗東調教師ほか)を取得。
**写像報告 v3(netkeiba 期)**: 騎手 136/178(76.4%・conflict 2・unmapped 40)、調教師 **194/225(86.2%**・conflict 1・unmapped 30)。
2007〜2026: 騎手 277/660・調教師 267/767。残りの unmapped は地方(3xxxx)と短期免許の外国人(7xxxx)で、名前だけの一致候補(`artifacts/jrdb/persons/manual-proposal.json`)を
利用者が承認すれば `jrdb-persons --manual` で書ける。承認前は `id_mappings` に 0 行(本番は未書込み)。

## R7 段 3b 別 DB 再構築(2026-11-09 月 12:42〜15:00 JST・2 時間 18 分)

`horseracing_jrdb` を `alembic upgrade head`(0025)で作り、`jrdb-rebuild --from 1999-01-01 --to 2026-11-08`(profile `jrdb_canonical`・参照=現行 DB・読取のみ)。
日 × 4 段 = 8,962 job・エラー 0・桁ずれ 0。**実測 0.09 秒/レース**(D17 の見積り 0.1 秒/レース・2.6 時間とほぼ同じ)。

| 表 | 行 |
|---|---|
| races | 96,135(1999-01-05〜2026-11-08) |
| race_horses | 1,353,256(`nk:` 0) |
| horses | 132,912 |
| race_results | 1,347,938(first_3f 96.4%・corner_orders 99.9%=JRDB 定義で保持) |
| race_laps | 92,533 |
| official_win_payouts | 96,252 / exotic_odds 897,816 |
| DB サイズ | 2,009 MB |

年次比較(`artifacts/jrdb/rebuild/compare-<year>.json`): 1999〜2024・2026 は races/entries とも現行 DB と同数(結果行は取消・除外ぶん JRDB が多い。
現行 DB の 1999〜2006 残存レースとも一致)。**2025 だけ blocking**: 12/13・12/14 の 53 レースが無い。原因は年度パック内の **BAC が 2 日分とも 9〜10 レース分しか
持たない**(KYI/SED は 36 レース分ある)=`skipped_no_program` 27+26。**日次単体 BAC(2 要求)は 36 レース分で完全** → 正本 DB で欠けた archive 版を
`superseded` にして 2 日分を再投入し、**2025 の blocking は解消**(races 3,455/3,455・entries 47,884 vs 47,886・結果 47,715 vs 47,654)。
ここで欠陥 3 件を修正(コミット参照): (1) `find_versions` が variant で絞っていて年度パックと日次が比較されず「日次→バックナンバー」規則が一度も働かなかった
→ daily/archive を同じ版族に(TYB 場別は別族)、(2) rebuild/repair の loader が別版名 `BAC251213.<sha12>.txt` を無言で飛ばしていた → 平文名で解釈、
(3) rebuild が superseded/held/rejected の版も読んでいた → writer と同じ規則で除外。年度パックの BAC 欠落は JRDB 側の事象なので
`result-review` に残し、他の年に同様の欠落が無いことは compare-<year> の races 一致(全年)で確認済み。

**jockey_id / trainer_id は全行 NULL**(`person_map=0`・id_mappings 未書込み)。R3 の承認後に写像を書き、全期間の entries を再投入して埋める必要がある
(来歴の jockey/trainer 群は NULL → 値 なので規則上は書ける)。それまでは JRDB 正本 DB の人物特徴は使えない。

## R6 段 3a 修復の本適用(2026-11-09 月 16:12〜16:23 JST・利用者承認)

バックアップ `artifacts/db_backups/horseracing-pre-r6-20261109-161223.dump`(368 MB)→ 8 列を順に適用 → 同じ 8 列を 2 回目実行 → impact post。

| 列 | 1 回目 変更行 | 2 回目 | 充足 2025 / 2026 |
|---|---|---|---|
| odds(+popularity は同じ書込みで更新) | 3,555 | 0 | 値の訂正(充足 99.6% / 99.7% 不変) |
| popularity | 0(odds の回で書かれた) | 0 | 同上 |
| weight | 5,715 | 0 | 99.8→99.9% / 99.7→99.9% |
| last_3f | 202 | 0 | 99.6% / 99.1→99.5% |
| breeder_name | 668 | 0 | 100% / 100% |
| race_class | 1,893 | 0 | 100%(dry-run の 1,871 より 22 多い=12/13・14 の日次 BAC 別版が loader 修正で読めるようになったぶん) |
| post_time | 2,920 | 0 | **22.9→100%** / 100% |
| win_payouts | 2,949 | 0 | **0→22.9%**(netkeiba 期のみ) / **28.0→100%** |

**2 回目は全列 変更 0**(冪等)・エラー 0。owner_name・prize_money は利用者決定どおり書いていない。

**波及(`scripts/jrdb_repair_impact.py`・`artifacts/jrdb/repair/impact-2025-2026/repair-impact.json`・139 と同じ型・判断記録は書き換えない)**:
- 過去走経由だけ(対象レースのオッズ/人気を修復前に固定): 86,095 頭・6,225 レースで期待回収率の差 |Δ| の中央値 0・p90 0・最大 0.094、
  0.01 を超えて動いた馬 0.06%、**S1〜S5 の該当は 1 頭も変わらない**=FEATURE_VERSION 据え置きでよい(067/139 の前例どおり)。
- 対象レース自身の値も含めた場合(確定オッズの訂正がそのまま効く): |Δ| 中央値 0・p90 0.0022・平均 Δ −0.0027・0.01 超 4.6%。
  S1 199→191(13 外れ・5 入る)・S3 639→615・S5 1,095→1,056=**確定オッズが正しい値に直ったぶん、過去の注目条件の事後集計が少し変わる**。
  前向き記録(decision_quotes・attention_picks)は判断時点の値で残しており書き換えていない。

`artifacts/features.parquet`(10/04 作成)は既に古く、稼働中のスタックは既定で materialized を使わない。materialized を使う研究を回すときは `features materialize` を取り直す。

## R3 人物写像の書込み(2026-11-09・利用者承認)

利用者が名前だけ一致の候補を承認: 候補 1 件の 51 件+2 候補から推奨 3 件(Ｒ．キング→05643・佐藤裕太→05719・佐々木国明→05736)=**54 件**
(`artifacts/jrdb/persons/manual-approved-20261109.json`・resolution_note `manual: operator approval 2026-11-09`)。分母は 1999〜2026 の KYI に現れる全コード
(騎手 1,028・調教師 1,199)。`jrdb-persons --manual` で **本番 DB と正本 DB の両方**の `id_mappings(source=jrdb)` に 2,226 行
(mapped 682=騎手 389・調教師 293/conflict 30/unmapped 1,514=写像の空白を見える形で残す)。

- netkeiba 期では騎手 164/178・調教師 220/225 が mapped(名前+生年月日 136/194 に承認 28/26 を足した数)。
- 正本 DB の騎手/調教師 ID: 1 日(2026-11-01)の試行で 0/318 → 騎手 313・調教師 318。全期間は KYI の適用記録を戻して再投入中。
- 写像の無い人は主に 1999〜2010 年代の地方・海外の騎手/調教師(JRA 名鑑・DB とも正規 ID が無い)で、その出走行の ID は NULL のまま。
- R5 のゲート「当日の騎手・調教師が全員 mapped」は、候補なし 18 人(DB にも正規 ID が無い地方・外国人)が騎乗する日は満たせない。
  切替時に「DB に 5 桁 ID がある人は全員 mapped」と読み替えるかは利用者確認事項。

### R5 人物ゲートの実装と直近 2 開催日の判定(2026-11-09)

`jrdb-audit` の `stage.persons`(`persons_readiness`): その日の KYI の騎手/調教師コードを `id_mappings(source=jrdb)` と突き合わせ、
`mapped` / `reviewed_gap`(unmapped・conflict の行がある=写像が無いと確認済み)/ `unreviewed`(行が無い新顔)に分ける。**`unreviewed` 0 で ready**。

| 日 | 騎手 | 調教師 | ready |
|---|---|---|---|
| 2026-11-08 | 111 中 mapped 109・gap 2(30620・70370) | 176 中 mapped 175・gap 1(30716) | true |
| 2026-11-01 | 100 中 mapped 99・gap 1(70370) | 153 中 153 | true |

gap のうち 2 件は承認時の一覧(netkeiba 期の分母を 11/08 の日次取得より前に作った)に入っていなかった:
**30620(川崎の騎手)は DB に名前だけ一致する公式 ID が 1 つある**=承認候補(未承認のため unmapped のまま)。30716(川崎の調教師)は DB に `nk:` 行しか無く写像不能。
70370 は生年月日だけ一致・名前が違う外国人騎手で、正しく止めている。

**30620 の追加承認(2026-11-09 夜・利用者承認)**: 30620 → 05630(候補 1 件・名前一致)を承認ファイルに足し
(`artifacts/jrdb/persons/manual-approved-20261109b.json`=前回 54 件+1)、`jrdb-persons --manual` を本番 DB と正本 DB に再実行。
dry-run で変わる code は 30620 だけ(unmapped→mapped)を確認してから書いた。両 DB とも `id_mappings(source=jrdb)` は
mapped 683(騎手 390・調教師 293)/conflict 30/unmapped 1,513。正本 DB は 30620 が騎乗した 3 日(2022-12-17 2 騎乗・
2023-12-16 2 騎乗・2026-11-08 7 騎乗)の KYI だけ適用記録を戻して `jrdb-rebuild` を日ごとに再実行(rc 0)し、
11 騎乗すべての騎手 ID が 05630 に入った(NULL がちょうど 2/2/7 減少)。本番 DB は netkeiba が既に 05630 を書いており変化なし。
11/08 の人物ゲートは騎手 111 中 mapped 110・gap 1(70370)・調教師は不変で ready=true。


## 正本 DB の人物 ID 補完と特徴量構築の確認(2026-11-09 夕)

**人物 ID の全期間補完**(KYI の適用記録を戻して `jrdb-rebuild` 1999-01-01〜2026-11-08 を再実行・16:59〜18:36・rc 0)。出走行のうち ID が入った割合:

| 期間 | 騎手 | 調教師 |
|---|---|---|
| 1999〜2006 | 94〜97% | 36〜61% |
| 2007〜2013 | 95〜97% | 66〜90% |
| 2014〜2026 | 95〜99.6% | 95〜99.9% |

調教師は `id_mappings(source=jrdb)` で mapped 293・unmapped 896。古い年が埋まらないのは、R3 で集めた JRA 名鑑に古い引退調教師が載っていないため
(1999 年は 44,568 行中 16,202 行・使われた調教師は 90 人)。名前と生年月日以外の照合経路は使っていない。

**特徴量構築の確認**(`build_feature_matrix(end_date=2026-11-08)` を現行 DB と正本 DB で 1 回ずつ・どちらも READ ONLY・各 53 秒)。
モデル入力 138 列はどちらも全部そろう(欠落 0)・レース 68,620 で一致。2007〜2015・2016〜2024・2025〜2026 の 3 期間で非 NULL 率を比べ、5pt 以上の差は 31 件:

- **馬場状態と天候が正本 DB で全期間 0%**。それに依存する `going_change` と、セルに馬場を含むスピード指数 4 列(`asof_spdfig_*`)も 0%。
  原因は writer の欠陥: BAC に馬場・天候は無く SED が持つが、成績の書込み(`upsert_jrdb_results`)がそれを races に書いていなかった。
  → 修正: SED の行からレースの馬場・天候を多数決で決め、**NULL の列だけ埋める**(現行 DB の netkeiba/公式の値は書き換えない=going は R4 基準未達で修復対象外のまま。
  race_static の来歴は書かない=同じ群の他列の出所を偽らない)。テスト追加(単体 3・統合 1)。
  正本 DB へは SED の適用記録を戻して再投入(18:48〜19:16・rc 0)→ **馬場・天候は 1999〜2026 の全レースで埋まった**。
  再比較で馬場・天候は全期間 100%(現行 DB は 2025〜2026 が 98.2%)、`going_change` とスピード指数は現行 DB と 0.4pt 以内で一致し、5pt 以上の差は 31 件 → 10 件。
  **表記の追補(2026-11-09 夜)**: 上の修正は JRDB の区分を「稍重・不良」と書いていたが、`races.going` は JRA-VAN・netkeiba とも
  「良・稍・重・不」。現行 DB で NULL のレースを埋めると 1 列に 2 表記が混ざる(現行 DB はまだ書いておらず 0 件)。
  `going_from_code` を短い表記に、`going_norm` も短い表記に寄せ、正本 DB の 18,330 レースを置換(稍重 14,359→稍・不良 3,971→不)。
  特徴量側(`condition_change_features._GOING_ORD`)は両表記を受けるので値は不変。
- **調教師の 3 列と騎手×調教師**(2007〜2015 で 99.8% → 82.8%・コンビ 77% → 63%)= 上の調教師写像の空白。
- 正本 DB が多い列: 2025〜2026 の系統 2 列(94% → 100%)とテン3F 系 3 列(71% → 89%)。テン3F は JRDB の定義(1200m 以外は別定義)で、現行 DB には書かない列。

**出走頭数の差**: 正本 DB の started が 1,461 行多い(1,325 レース・毎年 50〜110 行)。現行 DB で「取消」の馬が正本 DB では「中止」(1,428)か「失格」(35)。
現行 DB の JRA-VAN 取込みは異常区分を読まず「着順 0 で通過順も走破時計も無い=取消」と推定していた(`ingest/mapping.py::normalize_status`)ため、
スタート直後の落馬などが取消になっていた。JRDB は異常区分(0〜6)を明示するので正本 DB の方が正しい。除外も JRDB だけが区別できる(1,808 行)。

残る 10 件の差は、調教師の写像の空白 5 件(2007〜2015)と、正本 DB の方が多い 5 件(系統・テン3F)。
**結論: 正本 DB は既存の特徴量パイプラインでそのまま学習用の表を作れる**。2007〜2015 の調教師系の特徴が 17pt 欠けるのが学習上の主な弱点で、
名鑑以外の照合経路(例: 2007〜2024 の同じ出走行で JRA-VAN の調教師 ID と JRDB コードを対にする)を使うかは別途判断する。再学習と切替は別 spec。

## 人物写像の追加: 同じ出走での対(2026-11-10・利用者決定「対にして埋める」)

名鑑(生年月日)で結べない引退者・地方の人のため、1999〜2025 年の同じ出走 (レース, 馬番) で JRDB コードと現行 DB の JRA-VAN ID を対にした
(SED=実際の騎乗者を優先・KYI で補う・1,312,827 頭・読めないファイル 0・現行 DB に無い行 0)。
- 条件: コードの対の 99% 以上が 1 つの ID、その ID の対の 99% 以上が同じコード。**全コードが両方向とも 100%**(例外 0)。
  生年月日で結んだ既存 674 件(騎手 390・調教師 284)は全件同じ ID になった。
- 新しく写像にしたのは騎手 617・調教師 899(conflict だった騎手 20・調教師 8 を含む)。両 DB の `id_mappings(source=jrdb)` に mapped で書いた
  (`resolution_note` は「178 pairing: … n=…」)。書込み前の行は `artifacts/jrdb/persons/backup/` に CSV で退避。対の生データと集計は
  `artifacts/jrdb/persons/pairs-20261110-1116.json`・`pair-analysis-20261110.json`(どちらも gitignore)。
- 残る未対応は騎手 21・調教師 6(2026 年のデータにしか出ないコード)。出走数 1〜2 の対が騎手 221・調教師 330(逆方向も 1 対 1)。
- 正本 DB は人物 ID が欠けていた 2,828 日の KYI を未適用に戻し、出走表の段だけ再構築(約 70 分・exit 0)。差分は騎手 48,186・調教師 271,001 の NULL→値だけで、
  他の表は時刻列を除いてハッシュ一致。ID の欠けは 1999〜2024 年で 0%。
- 既知の差: 正本 DB は出走表(KYI)の予定騎手を出走行に書くので、乗り替わり 3,916 頭(2007〜2025 の 0.42%)で現行 DB(実際の騎乗者)と騎手が違う。
- これで再学習した期待回収率モデルの比較は `docs/plan/jrdb-canonical-ens15-20261109.md`(v3)。

## 乗り替わりの騎手(2026-11-10 午後・31fba436)

`upsert_jrdb_results` が SED の実騎乗者を出走行の騎手に書くようにした(騎手群の時刻規則・取消/除外/未対応コードは書かない・同じ騎手でも観測時刻は進める・
成績が入ったレースでは KYI の再適用で予定騎手に戻さない)。日次取込(本番・netkeiba 期のレース)と再構築の両方に効く。codex レビュー 8 件中 2 件採用
(KYI 再適用で戻る・同値で時刻が進まない)。不採用: 騎手群が時刻だけで決まること・observed_at が処理時刻であること(どちらも既存の全経路共通の規則)、
速報版(書込み前に拒否済み)、斤量(現行 DB との差は 919,335 行中 758 行=0.08%・今回は範囲外)。
正本 DB は乗り替わりのある 1,290 日と 2026 年の成績の段を再投入(exit 0)=騎手 5,671 行だけ変化・他の表はハッシュ一致。2007〜2025 年で現行 DB と騎手が違うのは 203 頭
(194 頭は現行 DB が nk: ID を持つ 2025 年の行)。テスト: 新規 6・scrape unit+integration 1,036 緑。R5 のときに直し漏れた policy テスト 1 件(jrdb_enabled false 固定)も直した。

## 脚質と系統の語彙合わせ(v5・2026-11-10)

正本 DB の脚質(SED コード)を、2007〜2009 年の同じ出走で学習して凍結した決定木で JRA-VAN の語に写し(2010〜2024 年の一致 97.8%)、
系統(UKC コード)を馬単位の最頻値で現行の 8 系統に写した(一致 99.9%)。正本 DB には書かず、学習行を作る時点で写す。
本番と同じ作り方で 15 seed を学習した v5 は、本番の行に入れても本番 cur1108 と勝ち馬 NLL の差 +0.000002 [−0.00038, +0.00035](劣らない)で、
2025〜2026 年だけでも劣らない・Σp 1.0056・ECE 1.19 倍=事前登録の本番化条件をすべて満たした。本番の差し替えは未実施(利用者の確認待ち)。
詳細は `docs/plan/jrdb-canonical-ens15-20261109.md` の v5 の節。

## 表示する期待回収率を v5 に切替(2026-11-10)

利用者決定で `mev-ens15-v1` → `mev-ens15-jrdb-v5`(表示値の更新規則の例外・同じ精度の置き換え)。注目条件は定義を変えずに再凍結し
policy v3(11/11 開始)、市場混合は係数を fit し直し、判断時点の見込みは検証待ち、判断記録の採点は版ごと。本番 DB に v5 行を埋めて再起動済み。
詳細 `docs/plan/displayed-model-switch-jrdb-v5-20261110.md`。R9 の前に、脚質・系統を JRDB から現行 DB に書く取込みが要る(今は netkeiba が書く)。
