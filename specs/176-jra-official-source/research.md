# Research: JRA 公式サイトを第 2 の情報源として追加する (176)

**Date**: 2026-11-01 | **Spec**: [spec.md](spec.md) | **調査記録**: [docs/research/jra-official-site-survey-20261101.md](../../docs/research/jra-official-site-survey-20261101.md)

spec の FR から実装前に決めなければならない点を D1〜D21 に分けた。各項目は「決定 / 理由 / 代替案」の順。**codex の plan 段階レビュー(2026-11-01)を受けて暫定案 9 点を改めた**。差分と採否は末尾の表に記録する(暫定案は取り消し線でなく「暫定案→決定」の形で残す)。

## D1. 配置 — `scrape/` の中にサブパッケージ `jra/`、ops は互換 adapter だけ

**決定**: `scrape/src/horseracing_scrape/jra/` に navigator(索引→開催日→レースの POST cname 遷移)・`fetch.py`(JraFetcher)・`parse/`(entries / changes / odds / results / payouts / baba)・`identity.py`・`upsert_jra.py`・`audit.py`・CLI サブコマンドを置く。両経路に共通の判定は `scrape/src/horseracing_scrape/precedence.py`(純関数)と `upsert.py`(DB 書込み)に置く。**ops の業務機能は増やさないが、D4 の `capture_window` 移設で ops は互換 adapter(再 export)だけ変わる**。front / admin / api は触らない。

**理由**: 書く先と共有スロット・fetch 層・`upsert.py` の既存契約がすべて scrape にある。別パッケージにすると FR-016 の「両 writer に共通」が import 境界で保証できない。

**代替案**: 新パッケージ(却下: 上記)。`ingest/`(CSV 用・HTTP 層なし・却下)。

## D2. 取得 — JRA 専用アダプタ `JraFetcher`(既存 `HttpFetcher.get` は不変)

**暫定案→決定**: `HttpFetcher` を request callable に一般化する案は**取り下げ**(codex: 応答分類・復号・archive 方針の違いを一般化では吸収できず、GET 経路の同値性試験を別途書くコストの方が大きい)。`jra/fetch.py` に `JraFetcher` を置き、**共有するのは `RequestPoliteness`(`pre_request` / `record_refusal`)・`robots_cache.shared_cache()`・archive の書込み関数**だけで、POST・リダイレクト・応答分類・復号・archive 許可は JRA 専用に書く。

- **POST**: `post(url, cname)`。httpx のリダイレクト自動追従は無効。3xx は Location を読み `error013` なら `JraParameterError`、それ以外の 3xx も不整合として同じ型(ループしない)。
- **復号**: HTTP header charset → meta charset の順で読み、`shift_jis` / `sjis` / `x-sjis` / `windows-31j` は **cp932** で `errors="strict"` 復号する。復号失敗は `JraDecodeError(ParseError)`=その応答は使わない(置換文字で黙認しない)。
- **応答分類**(FR-005): 

| 応答 | 分類 | 共有 cooldown | 同 run の続行 |
|---|---|---|---|
| 403 / 429(robots を含む) | `FetchRefused` | あり(既存 `_COOLDOWN_S`・Retry-After 優先) | 止める(当日=この run。翌 run は cooldown 満了まで `pre_request` が拒否) |
| 503 + Retry-After | `FetchRefused(503)` | あり(Retry-After の秒/HTTP-date) | 止める |
| 5xx(Retry-After なし)・接続エラー | `FetchError` | なし | **POST は再送しない**(1 回のみ)。索引 GET のみ 1 回再試行 |
| 400 | `JraParameterError` | なし | 索引からの辿り直し 1 回(`MAX_REWALK=1`)、2 回目で FAILED |
| 200 で「パラメータエラー」画面 / 301→error013 | `JraParameterError` | なし | 同上 |
| 200 で想定の表が無い | parser が `NotYetPublished` / `ParseError` | なし | レース単位の判定(FR-008) |

  netkeiba 固有の「素の 400=ブロック」は JRA に適用しない。辿り直しは HTTP retry ループの**外**で管理する(同じ不正 POST を 3 回送ってから辿り直す形にしない)。
- **archive**: **ページ種別の許可制**。索引・開催日一覧・出馬表・変更情報・結果・払戻・馬場断片・accessJ は archive 可(結果・払戻は確定事実、出馬表の単勝列は netkeiba 出馬表 archive と同じ前例)。**単複オッズページ(`pw151ou…`)と前日最終オッズは archive 禁止**(取得時刻つき追記=オッズ時系列になる。憲法 V・`ARCHIVE_DENY_PATHS` と同じ理由)。鍵は `sha256(url + "\n" + cname)`、`url.txt` には url と cname の sha のみ書く(cname 平文は書かない=FR-001)。
- **要求計数**: `JraFetcher.requests_sent`(robots・索引・再探索・リダイレクト先を含む HTTP 送信数)を summary に出す(SC-005 の測定値)。

**理由**: 093 の「6 経路が共有スロットを迂回」事故を 7 番目にしないために政治性フックは共有し、それ以外は JRA の性質(POST・cp932・エラー画面)に合わせて独立させる。

## D3. 政治性 — JRA は独立鍵・間隔は設定必須・wrapper 迂回も fail-closed

**決定**:
- `throttle_key(url)`: ホストが `jra.go.jp` または `.jra.go.jp` で終わる場合(境界つき)に `_JRA_KEY = "jra.go.jp"`。
- `source_interval_s(key)`: **JRA 鍵に限り** env `HORSERACING_JRA_MIN_INTERVAL_S` を必須とし、未設定・非数値・NaN・無限大・下限 10 秒未満は `SourceIntervalUnconfigured(RuntimeError)`。netkeiba 鍵・その他ホスト(0.0)の挙動は不変(`test_politeness_contract.py` 緑)。
- **JRA CLI 自身が起動時に検証**: (1) `config/source-operations.json` を読み `jra_official_interval_seconds` が数値・有限・≥10 であること、(2) env の値が policy と等しいこと(wrapper を通さず起動されたら policy 値を自分で env に写すが、継承値がより長い場合はそれを採る=172 の短縮禁止と同じ)、(3) `HORSERACING_SHARED_LIMITER` が無効化されていないこと(`background_policy` が None を返す経路は JRA では拒否)、(4) DB に到達できること(`NoSharedLimiter` → 1 件も送らない)。
- `RequestPoliteness` に `wait_out_cooldown: bool` を足し、JRA CLI は **cooldown は待たずに拒否**(スロット間隔は待つ)。「当日の取込を止める」= 同 run を止め、cooldown 満了前の再起動は `pre_request` で拒否される。
- policy のキー追加は**配布順序を固定**(codex): ① `load_policy` が `jra_official_interval_seconds` を**任意キー**として受け付ける reader を先に入れる(旧 policy はそのまま通る)→ ② `apply_policy` が存在時だけ env に写す(継承値の短縮拒否も適用)→ ③ `config/source-operations.json` にキーを追加 → ④ JRA CLI の必須検証を有効化。`schema_version` は `"172-v1"` 据え置き。ロールバックは ③ を外せば旧 reader でも起動できる。
- wrapper `scripts/jra_ops.sh entries|odds|results|baba|fetch|audit <date>` は policy → env を写して CLI を呼ぶ(`near_post_ops.sh` と同型)。
- ops の `/status`(`PolicyFileStatus`)には足さない(SC-009)。

## D4. 捕捉時間帯 — 窓計算を scrape に移し、送信ごとに確認

**決定**: `ops/capture_window.py` を**純粋な窓計算**(`CaptureWindow` / `LEAD_S` / `JST` / `window_from_post_times(first, last, n)`)と**読み込み**(`capture_dates(path)` / `window_for_day(session, day)` / `active_capture_window(session, now, path)`)に分け、両方を `scrape/src/horseracing_scrape/capture_window.py` に置く。`ops/capture_window.py` は同一オブジェクトを再 export する互換 adapter(`refusal_detail` と ops 既定の policy パスは ops 側)。`JraFetcher` は **HTTP 送信の直前**(robots・POST・リダイレクト先・再試行のすべて)にスロット予約の前後 2 回 `active_capture_window` を見て、開いていれば `CaptureWindowRefused`(scrape 側に定義を移し ops が再 export)で止める。CLI はこれを `stopped_for_capture_window=1` として PARTIAL で記録する。policy 破損・発走時刻欠損で窓が計算できない場合は ops と同じ「窓なし」(fail-open)を**JRA にも許す**(窓は捕捉への礼儀で、政治性契約そのものは共有スロットが担保する)。

## D5. ID — 公式コードをそのまま canonical・id_mappings は報告用に読むだけ

**暫定案→決定**: 「id_mappings を一切読み書きしない」は**読みだけ許す**に改める。`jra/identity.py` の `official_id(kind, href)` が馬 `pw01dud00(\d{10})` / 騎手 `pw04kmk0(\d{5})` / 調教師 `pw05cmk0(\d{5})` を境界つき正規表現で抜く(前後の桁・非数字・別 prefix は None=未知 ID → レース隔離 `quarantined_unknown_id`)。既存マスタに同 ID があれば `jra_name_key(name)` = NFKC + **全空白除去** + 067 のマーカー除去で照合し、不一致は `master_name_mismatch` に ID と両名を積む(マスタ不変)。一致してもマスタは触らない。**新規馬は `birth_year`(レース年 − 馬齢)と `sex` も保存**(`data_source='jra_official'`): 後続の netkeiba 取込が 067 の `classify_identity` で同一個体に昇格できる条件が「名前+生年」なので、ID と名前だけでは分裂防止に足りない(codex)。`resolve_entity` は呼ばず、`id_mappings` には書かない。読むのは D6 の報告用(承認済み mapping の有無)だけ。

## D6. 衝突検査 — 両経路がロック下で同じ検査を通る

**暫定案→決定**: 公式側だけの検査では二重出走行を防げない(JRA が canonical 行を作った後、netkeiba が未解決 mapping から `nk:` を返して別 PK で挿入できる。`race_horses` の PK は (race_id, horse_id) で馬番の一意制約は無い)。`precedence.check_entry_collisions(existing, incoming) -> CollisionReport` を純関数で置き、**netkeiba の `upsert_entries` と公式の `upsert_jra_entries` の両方が、レース行ロック(D8)の下で呼ぶ**。検査項目: 同馬番・別 horse_id / 同 horse_id・別馬番 / 既存行内の重複馬番 / 入力内の重複馬番・重複 horse_id / 馬番 NULL の既存 started 行 / 入力に無い既存 started 行(余剰)。1 件でも該当すればそのレースは無書込み(`quarantined_collision`、マスタ行の作成より前に判定=マスタも作らない)。取消馬の馬番未解決は別理由 `quarantined_cancel_unresolved`。隔離レースでは `horses` の名前(D5 の key)+生年一致の (nk: id, 公式 id) 対を `merge_candidates` に、`id_mappings` に承認済み mapping がある対は `approved_mapping_pending_rekey` に分けて列挙する(**承認済みでも本 feature は書かない=spec FR-011 より狭い実装**。再キーは 067 系の別 spec)。

**netkeiba 経路への影響**: 現行は衝突しても黙って 2 行目を挿入できる。共通検査により netkeiba 側も隔離するようになる=挙動変更。Phase 1 で先行導入し、開催終了後に切り替える(D20)。

## D7. 来歴 — 馬単位とレース単位の 2 表、`source` は「現在値を採用した経路」

**暫定案→決定**: `race_field_provenance(race_id, field_group)` 1 表では FR-017 を満たせない(A 馬の体重だけ公式で更新すると B 馬まで公式由来に見える)。2 表に分ける:

- `race_horse_field_provenance(race_id, horse_id, field_group, source, official_at, observed_at, ingestion_job_id)` PK (race_id, horse_id, field_group)。`field_group` ∈ {`entry_static`(frame / horse_number / jockey_weight / sex / age), `jockey`(jockey_id・騎手変更は別時刻で届く), `trainer`, `body_weight`(weight + weight_diff を**対**で), `entry_status`}。
- `race_field_provenance(race_id, field_group, source, official_at, observed_at, ingestion_job_id)` PK (race_id, field_group)。`field_group` ∈ {`race_static`(distance / track_type / race_name / grade / race_class / prize_money), `post_time`, `win_odds`, `place_odds`, `results`, `laps`, `win_payouts`, `exotic_payouts`}。

意味: `source` = **その項目群の現在値を採用した経路**。同値でも優先権のある経路(公式)が確認したら採用元は公式に移る(再確認=採用)。拒否された入力(逆行・低優先・NULL 保持)は来歴を更新しない。`observed_at` は **HTTP 応答の取得時刻**(呼び出し側が渡す・DB 書込時刻ではない)。`official_at` はオッズ系だけ(140 の `races.win_odds_official_at` / 0014 の `place_odds_official_at` が正・一致をテスト・他は NULL)。`ingestion_job_id` は job 行を先に flush した ID(dry-run は書かないので問題にならない)。既存行は作らない。features / training / serving / betting / api は読まない(leak-guard)。

## D8. 優先規則(FR-016)— 項目別遷移表・共通ロック順序・全馬一体のオッズ契約

**決定**: 純関数は `scrape/precedence.py`、DB 書込みは `upsert.py` の既存関数を共通化して両経路が呼ぶ。

**ロック順序(全 writer 共通・codex BLOCKER)**: 取得・解析は**トランザクション外**で済ませ、レース単位の短いトランザクションで ① `races` 行を `INSERT … ON CONFLICT DO NOTHING` で確保 → ② `SELECT … FOR UPDATE` でロック → ③ ロック下で結果の有無・started 集合・来歴・既存出走行を**再読込** → ④ 判定(衝突・遷移・優先) → ⑤ 値と来歴を同じトランザクションで書く → commit。1 run で複数レースを扱うときは race_id 昇順に 1 レースずつ(デッドロック回避)。既存 `update_odds` は結果の有無をロック前に読んでいるので、ロック後の再読込に改める(netkeiba 側の挙動変更=Phase 1)。HTTP 待ちの間にロックを持たない(174 の T−3 捕捉を待たせない)。

**項目別遷移表**(欠落 / 明示変更 / 訂正を区別):

| 項目群 | 欠落(None) | 明示値 | 訂正・逆行 |
|---|---|---|---|
| `entry_status` | 保持 | `started→cancelled/excluded` は採用。`cancelled↔excluded` は**変更情報または結果ページ**からのみ採用(出馬表は不可) | `cancelled/excluded→started` は**どの経路からも拒否**(古い出馬表の再投入) |
| `jockey` | 保持 | 採用。ただし来歴の `observed_at` が保存より古い入力は拒否(古い公式出馬表で変更情報を巻き戻さない) | 同左 |
| `body_weight` | **対で**保持(weight と weight_diff を個別 COALESCE しない=「新体重+旧増減」を作らない) | 対で採用 | 同じ入力時刻規則 |
| `entry_static` / `trainer` / `race_static` | 保持 | 採用。来歴 `source=jra_official` のとき netkeiba は上書きせず `static_mismatch` を報告(FR-016(e)) | — |
| `post_time` | 保持 | 採用。来歴 `source=jra_official`(変更情報)のとき netkeiba は上書きしない | — |
| `win_odds` / `place_odds` | 下表 | 下表 | 下表 |
| `results` | — | **レース単位の初回挿入のみ**(D9) | 不一致は報告のみ |
| `win_payouts` / `exotic_payouts` | — | 券種ごとの完全な集合のみ採用。来歴 `source=jra_official` なら netkeiba は skip | 公式の訂正は集合置換 |

**オッズの共通契約**(単勝・複勝それぞれ・両経路):
1. 結果未確定レースのみ(ロック下で再確認)。netkeiba の「確定後 fill-if-null」は**別モード `settled_fill`** として残し、来歴を触らない(既存どおり)。公式経路は `settled_fill` を使わない。
2. **全馬一体**: started 全頭の値が入力に揃っていないときは**レース全体を skip**(`odds_partial_field`)。一部の馬だけ更新してレースの時刻を進めない(FR-013)。
3. 時刻: `incoming.official_at` が None で保存に時刻があれば skip(観測時刻だけで新しいと判定しない)。保存より古ければ skip(逆行)。新しければ採用。**同時刻**で値が同じなら採用(観測の更新)、値が違えば **公式を優先**(stored=netkeiba・incoming=公式 → 採用+`odds_conflicts`、逆 → skip+`odds_conflicts`)。
4. 複勝は現行「時刻不明なら保存 `official_at` を NULL にする」を止め、3 と同じ規則にする(netkeiba 側の挙動変更=Phase 1)。
5. 来歴(`win_odds` / `place_odds`)は採用時だけ更新し、`official_at` は 140/0014 の列と同じ値。

**理由**: FR-013 の「全馬と来歴を一体更新」「結果未確定のみ」を両経路に同じ形で課す。現行 `update_odds` の部分更新・時刻不明での上書きは FR-013 に合わないので、共通契約に合わせて直す。

## D9. 結果と払戻 — レース単位の初回挿入・7 券種・集合の完全性

**決定**:
- **結果の挿入単位**: FR-014 は「結果行が無いレースだけ」。公式経路は **race-atomic**: そのレースに `race_results` 行が 1 行でもあれば挿入しない(不一致は `results_mismatch` に件数と内容)。1 行でも検証に落ちれば(finished なのに着順なし等)レース全体を `parse_failed` にして部分挿入しない。netkeiba の現行(馬単位の追加・`corner_orders` の NULL 補完・`running_style` の NULL 補完)は**列挙された例外**として残し、`_insert_results(session, race_id, rows, *, mode)` の `mode="per_horse_legacy"` に閉じ込める(公式は `mode="race_atomic"`)。来歴 `results` は初回挿入経路。
- **結果ページの馬体重**: `race_horses.weight / weight_diff` に `body_weight` 群の規則(対で・時刻規則)で書く(発走前に公表された値と同じ事実)。
- **結果ページの単勝人気**: **`race_horses.popularity` には書かない**(odds と対で書く列で、公式は確定オッズを持たないので「T−3 のオッズ+確定人気」の不整合行になる)。監査(D13)の比較材料と `results_mismatch` の報告にだけ使う。**spec US2 シナリオ 1/4 の「人気が書かれる」を「人気は保存せず監査に使う」に改める**(plan 段階の仕様訂正。**利用者が 2026-11-02 に承認**)。別表に保存する案は契約追加になるので v1 では採らない。
- **ハロンタイム**: `upsert_laps(..., source=)` に source 引数を足し、公式経路は **行が無いときだけ**書く(既存行は不一致報告のみ)。来歴 `laps`。
- **払戻の対象券種**: `BetType` の 7 券種(単勝・複勝・馬連・馬単・ワイド・3連複・3連単)。**枠連は対象外**(憲法と `BetType` が「枠連は対象外」と定める・`EXOTIC_BET_TYPE` が許さない)。公式ページの枠連は解析して `not_mapped_bet_types` に件数を出すだけ。**spec FR-015 の「全券種」を「製品の 7 券種」と明記する**。
- **払戻の完全性**: 券種ごとに {`not_published`, `complete`, `not_sold`, `refund_only`, `parse_failed`} を返し、**`complete` の券種だけ**集合置換(`official_win_payouts` は勝ち馬集合、`exotic_odds` は (race_id, bet_type) の集合。公式集合に無い既存行は削除)。`not_published` と `not_sold` を区別する(単に空の券種で既存行を消さない)。同額の公式払戻でも**採用元が公式に移る**ように `upsert_official_win_payouts` / `upsert_exotic_odds` は値が同じでも `source` と来歴を更新する(値の行は `IS DISTINCT FROM` で触らない=既存の冪等性は維持し、来歴だけ進める)。`upsert_exotic_odds` の「不正行を飛ばす・重複組番は最後の値」は dividend 入力には使わず、公式解析器が重複・矛盾を `parse_failed` にする。`coverage_scope` は dividend 行では `partial` のまま(的中組番だけ=グリッド充足とは別概念・既存どおり)。
- **CHECK の拡張**: `Source.JRA_OFFICIAL = "jra_official"` を追加(`Source.ALL` 経由で `ID_SOURCE` も広がるが id_mappings には書かない=意図して許容)。`official_win_payouts.source` は `('netkeiba_result', 'jra_official_result')`。
- **JRA-VAN 行の保護**(SC-010): 公式経路は `race_date < NETKEIBA_FINAL_ODDS_FROM (2025-10-11)` のレースを `skipped_pre_netkeiba_era` として一切書かない(索引から到達できる日は直近だけなので構造的にも当たらないが、`horses.data_source` は出走行の由来を証明しないので日付で守る)。
- `apply_final_odds` は呼ばない(確定オッズの数値が無い)。

## D10. 馬場情報 — 項目コードに測定対象を含めた `jra_track_conditions`

**暫定案→決定**: `item=moisture` + 非キー列 surface/position では同時刻 4 値が衝突するので、**項目コードに測定対象を含める**: `item` ∈ {`moisture_turf_goal`, `moisture_turf_corner4`, `moisture_dirt_goal`, `moisture_dirt_corner4`, `cushion_turf`, `going_turf`, `going_dirt`, `weather`}。PK (venue_code, measured_at, item)。`time_basis` ∈ {`measured`(断片の「10月31日（土曜）7時00分」+年補正・JST), `fetched`(accessJ の馬場状態・天候は測定時刻を持たないので取得時刻を当て、クッションの時刻を流用しない)}。`fetched_at` は**その値を採用した最新の取得時刻**(値が変わらない再取込では動かさない=payouts と同じ)。`published_at` は断片に無いので NULL(後から補う経路は v1 に無い)。年補正: 取得日の年を当て、月が取得月より大きければ前年。`measured_at > fetched_at` は `rejected_future`。

**リーク境界**(D19 で動的試験も): features の静的 leak-guard(文字列 `jra_track_conditions` / `JraTrackCondition` / `cushion` / `moisture` / `race_field_provenance` / `RaceFieldProvenance` / `race_horse_field_provenance`)に加え、**禁止表だけに行を入れても特徴量 parquet と active モデルの予測がバイト一致**する動的試験を置く。

## D11. 解析 — 開催日ページはレースごとに判定・見出しから race_id

**決定**: 出馬表・結果の開催日ページは `race_header`(12 個)ごとに「2026年11月1日（日曜）4回東京11日」+ `race_number` を読み、競馬場名 → 2 桁コード(札幌01 函館02 福島03 新潟04 東京05 中山06 中京07 京都08 阪神09 小倉10)を `jra/venues.py` に置いて `build_race_id` に渡す。見出し不読・索引で選んだ日付/回/日次との食い違い・R 範囲外・同じ race_id の重複はそのレースを `parse_failed`。各レースの状態は {`published`, `not_yet`, `cancelled`, `parse_failed`}。取消馬(`<td class="num"><strong class="red">取消</strong></td>`)は行順から馬番を復元せず、変更情報「出走取消 N番 馬名」と出走行の公式 ID + 馬名 key で復元、不能ならレース隔離。オッズページの「8時22分現在オッズ」は official_at(JST・分精度・日付は索引で選んだ開催日)、「前日最終オッズ」は取り込まない(FR-019)。

## D12. fixture — raw Shift_JIS を証跡として置く(FR-001 の例外を明記)

**決定**: `scrape/tests/fixtures/jra/` に調査で保存した raw バイト(`.sjis.html` / `.html` / `.json`)を置き、`manifest.json` に url・kind・fetched_at(JST)・**raw bytes の sha256**・charset・`cname_sha256`(平文は書かない)を記録する。raw HTML 本文に cname が含まれるのは不可避なので、**spec FR-001 に「運用で再利用する遷移情報として永続化しない。テスト fixture と archive は証跡として raw を保持してよい(読んで再利用しない)」の例外を明記**する。ハッシュは raw bytes(既存 `page_sha256` は UTF-8 本文なので別名 `raw_sha256` で区別)。構造変更 fixture は raw をコピーして要素を削った派生で fail-closed を固定。

**parity の母集団**: 稼働中 DB との比較だけでは SC-001/003/010 を再現できない(後から更新される・`nk:` 衝突で隔離される)。netkeiba 側の対象行を**取得段階・取得時刻・ID 解決状態つきの JSON fixture** に書き出し(`scripts/export_parity_fixture.py`・読み取り専用)、テストは一時 PostgreSQL に投入して比較する。**比較可能率と一致率を分けて**出す(既知の不一致を分母から黙って除かない)。実 DB との比較は quickstart の補助手順。

## D13. 監査(US3) — 収集と照合を分け、照合は DB 無変更

**暫定案→決定**: ライブ取得は共有スロットの DB 書込みが必須なので「DB 完全無変更の監査」は fixture 入力に限る。`jra-fetch --date D --save-dir DIR`(収集工程・HTTP と throttle 行の書込みのみ・業務表不変)と `jra-audit --date D --from-dir DIR`(照合工程・`SET TRANSACTION READ ONLY`・業務表も job も不変)に分ける。照合は「公式の解析値 vs 取得時点の保存値」で、**spec FR-023 を「保存された両経路の比較」から「公式解析値と保存値の照合」に改め**、比較可能件数・来歴不明・未解決 ID・公開段階違い・時刻差を**別々に集計**する。`nk:` 統合候補は D6 と同じ key で列挙。`--dry-run`(取込コマンド)は「HTTP と throttle 行は出る・業務表と job は書かない」と定義する。

## D14. 要求数の見積もり(SC-005)— 実測は `requests_sent`

開催 2 場の 1 日: robots(1・24h キャッシュ)+ 出馬表=索引 D(1)+開催日(2)、変更情報 I(1)、結果=索引 S(1)+開催日(2)、払戻=結果ページに同梱(0)、馬場=断片 2+JSON 1 → **11 件 ≤ 20**(辿り直しが 1 回入っても 13)。単複オッズ全 24 レース=索引 O(1)+開催日(2)+24=27 → **合計 38 ≤ 50**。5 つの CLI が毎回索引から辿るので、SC-005 の実測は D2 の `requests_sent` の**合計**で測る(ページ本体だけで数えない)。

## D15. 開催日の到達判定(FR-022)

索引に出ている開催日だけ到達可能。`--date` が索引に無ければ `unsupported_date` として job を SKIPPED で記録し、エラーにしない。索引→開催日のリンク文言(「4回東京11日」+日付)を `--date` と照合し、一致するリンクだけを辿る。

## D16. ジョブ記録(FR-006)とトランザクション

`_run_job(session, *, source=Source.NETKEIBA, ...)` に `source` 引数を足し、公式経路は `Source.JRA_OFFICIAL`。job_type は `jra_entries` / `jra_changes` / `jra_odds` / `jra_results` / `jra_payouts` / `jra_baba`。複合コマンドは**取得したページを共有して job 行を分ける**(`jra-entries` → `jra_entries` + `jra_changes`、`jra-results` → `jra_results` + `jra_payouts`(+ `laps` は `jra_results` の extra))。job 行は最初に flush して ID を来歴の FK に使い、レースごとに `begin_nested()`(savepoint)で書き、失敗は savepoint だけ戻して PARTIAL。**隔離が 1 件以上あれば errors=0 でも PARTIAL**(現行 `_run_job` の判定に `quarantined>0 → PARTIAL` を足す)。例外で run が落ちた場合は現行どおり FAILED 行を別に作る。summary の名前つき件数は contracts/cli.md に列挙。

## D17. dry-run

取得と解析は実際に行い(共有スロット・捕捉時間帯の規律は同じ)、書込み直前で `rollback`。業務表・`ingestion_jobs`・来歴は書かない。`fetch_throttle_state` だけは動く(定義を summary に明記)。

## D18. マイグレーション — 2 本に分ける

**暫定案→決定**: 1 本同梱から **2 本**へ。
- `0023_jra_official_source`(Phase 1): `Source.JRA_OFFICIAL` に伴う CHECK 置換(`ck_ingestion_jobs_source` / `ck_id_mappings_source` / `ck_exotic_odds_source` / `ck_race_laps_source` / `ck_official_win_payouts_source`)+ `race_field_provenance` + `race_horse_field_provenance`。
- `0024_jra_track_conditions`(Phase 3): 馬場表。P3 の表を P1 の導入条件にしない。

適用は**開催日の最終レース後**、`lock_timeout` を短く設定し(CHECK 置換は表ロック)、適用前後に `alembic_current` と行数を確認する。downgrade は `jra_official` 行が 1 行でもあれば**中断**(旧 CHECK に戻せない)=通常の撤退は「JRA 取得を止めて拡張スキーマは維持」。既存行は 1 行も書き換えない。~~`features/tests/conftest.py` 等は `head` を使うので head 直書きの修正は不要(確認済み)~~ **訂正(2026-11-02)**: conftest は `head` だが、features の leak-guard 系 unit テスト 9 本と `live/tests/unit/test_no_schema_change.py` が `versions[-1]` に `0022_` を直書きしている(`startswith("0022_")` 7 本・`== "0022_decision_quotes"` 2 本・live 1 本)。0023 で 10 本、0024 で再び 10 本を更新する(tasks T020/T090・040/054 の前例)。

## D19. 既存経路のバイト不変と動的リーク試験

- `HttpFetcher`: 不変(JraFetcher は別クラス)。既存 fetch テスト全件緑。
- `source_interval_s`: netkeiba・その他ホスト不変、JRA 鍵のみ新規テスト。
- `upsert_entries` / `update_odds` / `update_place_quote` / `backfill_results` / `apply_result_page_settlement` / `upsert_exotic_odds` / `upsert_laps`: Phase 1 の共通修正(衝突検査・遷移表・ロック順序・オッズ全馬一体・来歴)は**挙動変更を伴う**。既存 integration テストで変わるものは契約の変更として理由つきで更新し、変わらないものは緑を維持。
- features: **動的試験**=禁止表(来歴 2 表・馬場表)だけに行を入れた前後で `features materialize` の parquet と active モデルの予測がバイト一致(SC-008 は「同じ入力データでコード変更前後」に限定。canonical ID・取消・騎手が正しく更新された後まで不変を要求しない)。
- 174: `decision_quotes` 不変に加え、**JRA CLI が HTTP 待ち中にレース行ロックを持たない**こと(D8)と捕捉時間帯で止まること(D4)を試験し、T−3 捕捉の取得挙動が変わらないことを別に確認する。
- api / front / admin: OpenAPI snapshot drift-check 緑。

## D20. 段階導入 — 開催中は共通コードに触らない

**暫定案→決定**(codex の Phase 0〜3 を採用):
- **Phase 0(開催中でも可)**: 契約・fixture・parser・純関数(`precedence.py`・`jra/identity.py`・窓計算の純粋部)・一時 PostgreSQL での検証。本番 DB・policy・稼働プロセスが読む共通コード(`upsert.py` / `pipeline.py` / `politeness.py` / `ops`)は変えない。
- **Phase 1(開催終了後・共通安全性)**: migration 0023 / enums / `_run_job(source=)` / 衝突検査の両経路導入 / 遷移表(取消復帰防止・体重の対) / ロック順序 / オッズ全馬一体 / 来歴 2 表の書込み / `_insert_results` の mode 分離 / 払戻 writer の source 引数。**JRA の書込みは無効のまま**で netkeiba 経路を切り替え、全テスト緑と実 DB の 1 開催日分の通常運用を確認(変更前の writer が残った状態で公式優先値を入れると、その writer が値を戻す)。
- **Phase 2(JRA)**: 2a `JraFetcher` + policy 任意キー reader → env → config → CLI 必須化(D3 の順序)+ `capture_window` 移設。2b `jra-fetch` / `jra-audit`(書込みなし)で公式取得と照合を実測。2c `jra-entries` / `jra-odds` を**限定レース**(`--race-id` 指定)で有効化。2d `jra-results`(payouts 含む)。各段で両方向再投入と並行試験を通す。HTTP を止めても採用済み公式値の保護(来歴ベースの skip)は残る設計。
- **Phase 3(馬場)**: migration 0024 + `jra-baba`。保存専用で、特徴量への非接続(静的+動的試験)を確認。
- **Phase 4**: SC-001/003(parity fixture)/SC-005(`requests_sent`)/SC-008(動的試験)の実測を spec に転記。

## D21. spec の訂正(plan 段階で見つかった矛盾)

codex が指摘した spec 内の矛盾・不足を plan と同時に直す(いずれも clarify 済みの決定を変えない表現の是正、または範囲の明示):
- SC-004: 「観測時刻=表示時刻」→「`official_at`=表示時刻。同じ表示時刻の再投入で値と `official_at` は不変、`observed_at` と job は進む」(FR-013 と同期)。
- SC-009: 「API / OpenAPI 不変」と「許容する DB 変更(0023/0024 の列挙=CHECK 置換と新表 3)」を分ける。
- FR-001: fixture / archive の証跡保持を例外として明記(D12)。
- FR-015: 「全券種」=製品の 7 券種(枠連は対象外)(D9)。
- FR-023: 「両経路の保存値の比較」→「公式解析値と保存値の照合」+集計の分離(D13)。
- US2 シナリオ 1/4: 単勝人気は保存せず監査に使う(D9)。受入シナリオの変更なので利用者確認に回し、**2026-11-02 に承認**。
- codex-review.md の保留欄を clarify 済みの本文と同期。

## codex plan レビューの採否(2026-11-01)

codex は提示した 14 ファイルだけを読み、実装・テスト実行なし。BLOCKER 9 / HIGH 17 / MEDIUM 3。**不採用 0・部分採用 2**。

| # | 重要度 | 指摘(要約) | 採否 | 反映 |
|---|---|---|---|---|
| 1 | BLOCKER | 来歴 PK(race_id, field_group) では馬ごとの由来が嘘になる。正規化した追加表を推奨 | 採用 | D7: 馬単位+レース単位の 2 表 |
| 2 | HIGH | 来歴の「採用」と「再確認」の区別・`observed_at` は HTTP 取得時刻・140 の時刻列を正 | 採用 | D7 |
| 3 | BLOCKER | `update_odds` 流用では不足(部分更新で時刻が進む・時刻不明で上書き・複勝が NULL 化・確定後補完) | 採用 | D8 オッズ共通契約 1〜5(`settled_fill` を別モードに) |
| 4 | BLOCKER | `races FOR UPDATE` は全 writer 同一順序でなければ無効。HTTP 待ち中のロック禁止 | 採用 | D8 ロック順序 |
| 5 | HIGH | 取消修正は先行変更に分離。欠落/明示/訂正の項目別遷移表。体重の対 | 採用 | D8 遷移表・D20 Phase 1 |
| 6 | BLOCKER | 公式側だけの衝突検査では netkeiba の `nk:` 挿入を防げない。両経路・ロック下・検査項目の列挙 | 採用 | D6 |
| 7 | HIGH | id_mappings の読みは許す・birth_year 保存・空白除去の定義 | 採用 | D5(承認済み mapping でも書かない=spec より狭い実装と明記) |
| 8 | BLOCKER | 結果 INSERT-ONLY の単位が spec(レース)と現行(馬+補完)で違う。契約で列挙 | 採用 | D9 `mode` 分離 |
| 9 | HIGH | 人気だけ書く案は非推奨。別表か spec 改訂 | 採用(spec 改訂側) | D9・D21(利用者承認 2026-11-02) |
| 10 | BLOCKER | 「全券種」は枠連が `exotic_odds` に収まらない | 採用(7 券種に限定・spec 明記) | D9・D21 |
| 11 | BLOCKER | 払戻の完全性は非空/coverage_scope では証明できない。券種ごとの状態・同額採用の来歴更新 | 採用 | D9 |
| 12 | HIGH | 最小リスクは JRA 専用取得アダプタ。cp932 は header も・置換文字で黙認しない | 採用(暫定案の一般化を取り下げ) | D2 |
| 13 | HIGH | 拒否分類の表(robots 403・503 Retry-After・400・200 エラー画面)・辿り直しは retry の外・「止める」の定義 | 採用 | D2 表・D3 cooldown 非待機 |
| 14 | BLOCKER | archive 継承でオッズ時系列ができる。ページ種別の許可制。cname 保存は FR-001 と矛盾 | 採用 | D2 archive・D12・D21 FR-001 |
| 15 | HIGH | 例外化は JRA 鍵限定・NaN/inf 拒否・CLI 自身が policy と limiter を検証(wrapper 迂回不可) | 採用 | D3 |
| 16 | HIGH | policy キー追加は旧 reader に破壊的。任意キー reader を先に配布 | 採用 | D3 配布順序 |
| 17 | HIGH | 窓チェックは各 HTTP 送信直前・純粋計算と読込の分離・fail-open の可否を決める | 採用 | D4 |
| 18 | HIGH | 馬場表の PK は項目コードに測定対象を含める・天候にクッション時刻を流用しない・fetched_at の意味 | 採用 | D10 |
| 19 | HIGH | 文字列 leak-guard では不足。禁止表だけ変えて特徴・予測不変の動的試験 | 採用 | D10・D19 |
| 20 | HIGH | 監査は「公式解析値 vs 保存値」に仕様変更を明示。DB 無変更は fixture 限定。収集と照合を分離 | 採用 | D13・D21 FR-023 |
| 21 | HIGH | 稼働中 DB との比較では SC-001/003/010 を再現できない。固定 fixture+一時 PostgreSQL。比較可能率と一致率の分離 | 採用 | D12 |
| 22 | HIGH | spec 内矛盾(SC-004 / SC-009 / codex-review 保留欄) | 採用 | D21 |
| 23 | MEDIUM | migration は三段階を推奨。lock timeout。downgrade は成立しない | 部分採用(2 本: CHECK+来歴 / 馬場。来歴は CHECK と同じ Phase で不可分) | D18 |
| 24 | HIGH | Phase 0〜3 の順序(開催中は共通コードに触らない・共通安全性を先に・JRA 書込みは限定から) | 採用 | D20 |
| 25 | HIGH | `decision_quotes` 不変と取得挙動不変は別に確認 | 採用 | D19 |
| 26 | HIGH | `horses.data_source` は出走行の由来を証明しない。JRA-VAN 保護の識別方法 | 採用 | D9(日付で守る) |
| 27 | HIGH | 結果の馬体重・人気・ラップの保存先が `_insert_results` 抽出だけでは実現しない | 採用 | D9(体重=body_weight 群・人気=保存しない・ラップ=upsert_laps source 引数) |
| 28 | HIGH | `_run_job` のトランザクション設計(job FK・部分成功・隔離のみで SUCCEEDED になる) | 採用 | D16 |
| 29 | MEDIUM | 複合コマンド内の job 分割とページ共有の契約化 | 採用 | D16・contracts/cli.md |
| 30 | MEDIUM | SC-005 は robots・再探索・リダイレクトを含めて測る | 採用 | D2 `requests_sent`・D14 |

**暫定案との差分**: D2(一般化→専用アダプタ)・D5(読み禁止→報告用に読む+birth_year)・D6(公式側のみ→両経路ロック下)・D7(1 表→2 表)・D8(流用→共通契約と遷移表)・D9(馬単位→レース単位・人気不保存・7 券種・完全性状態)・D10(PK 設計)・D13(収集と照合の分離)・D18(1 本→2 本)・D20(Phase 0〜3)。**追加するテスト**は plan.md「テスト計画」に codex の 10 分類をそのまま採用して列挙する。

## codex tasks レビューの採否(2026-11-02)

tasks.md 草案(99 タスク+rollout 8 段)・contracts/precedence.md 全文・cli / fetch-and-parse の抜粋・SC・plan の段階表・既存コードの事実を埋め込み、質問 2 問(順序・依存・⏸・テスト成立条件 / 契約との対応漏れ)で `codex exec --sandbox read-only`(gpt-6-astra 自動選択・1 回で完走)。**Q1 8 件・Q2 8 件・SC 表 12 行、不採用 0・部分採用 1**。

| # | 指摘(要約) | 採否 | 反映 |
|---|---|---|---|
| Q1-1 | `db/enums.py` / `models/__init__.py` など既存共有モジュールは稼働プロセス(ops subprocess)が毎ジョブ import する=「モデル定義だから開催中でも可」は誤り。別 checkout で実装 | 採用 | T016 ⏸・T017/T089 の export 部分を ⏸・Notes に「別 worktree で実装し反映は開催終了後」 |
| Q1-2 | `alembic upgrade head` は 0024 まで上がる(R1 で馬場表が本番に入る)。migration → コード反映 → 再起動の順序 | 採用 | T100/T106 は revision ID 指定・順序を明記。T020 の head 直書き更新は 040/054 前例どおり bump(部分採用=分離はしない。repo 側 head を見るテストで本番と独立) |
| Q1-3 | `jra-audit` が US1 の CLI 骨格(T065)に依存し先行実行できない。登録後の `jra/cli.py` は稼働 CLI が読む | 採用 | T047a(writer 非依存の共通骨格・登録 1 行は ⏸)を新設 |
| Q1-4 | 依存の明示(T007←T001/2・T060〜62←T059・T074←T075・T076↔T077・T045←T043/44・T088 の分割) | 採用 | 各タスクに前後関係を明記・T088 を (a)来歴 (b)馬場 に分割 |
| Q1-5 | 新規引数を必須にすると ops/live 無改修が成立しない。観測時刻を書込み時刻で代用しない | 採用 | T029 で keyword-only+既定値(None=legacy `now()`)を固定・T034 が fetch 時刻を渡す・T036 を受入条件に |
| Q1-6 | 出馬表の別日 fixture 欠落 / 固定索引 cname と動的 cname の区別 / robots キャッシュ有無で `requests_sent` が変わる / 3xx は追跡しない | 採用 | T007・T003/T042・T073・T038/T041 |
| Q1-7 | 後日の DB export では発走前(pre_result)状態を復元できない | 採用 | T066 を **netkeiba 取得 archive(`artifacts/scrape_archive/<day>`)から既存解析器で復元**に変更・無ければ未検証と記録 |
| Q1-8 | R3 の引数欠落・「rollout は開催終了後」と R2/R5 の開催日観察が矛盾 | 採用 | T102 に `--save-dir/--from-dir`・Phase 11 を「変更は ⏸・観察は開催日」に分離・T104 の実運用観測を必須から外す |
| Q2-1 | `jra-fetch` のオッズ raw 保存は禁止された時系列蓄積 | 採用 | T082/T084: kind 許可制を収集工程にも適用・`not_for_archive` 例外を削除・オッズは audit 対象外 |
| Q2-2 | 「書込み直前で rollback」の dry-run は job flush や writer 侵入を排除できない | 採用 | T047a: `_run_job` と writer の前で分岐・T058/T073 は DML 捕捉で 0 文を assert |
| Q2-3 | ロック契約が results/laps/payouts と ID 解決に落ちていない・隔離時の races 行 | 採用 | T029 で全 writer の責任を明記・T063/T064/T076 の解決はロック下・T027 に「HTTP 中ロック 0」「失敗後の再実行」 |
| Q2-4 | `race_static` / `post_time` の遷移・公式異値の `static_mismatch`・結果経路の衝突検査・JRA-VAN 保護を全公式経路に・マスタ作成条件の言い直し | 採用 | T030/T009/T013/T076/T064/T077/T063 |
| Q2-5 | 同時刻同値の netkeiba が来歴の採用元を公式から戻す | 採用 | T010 `OddsDecision.adopted_source`・T014/T023 に 3 連の試験 |
| Q2-6 | results/payouts の job 分離が曖昧(来歴 FK・二重実行・`requests_sent` 二重計上) | 採用 | T076 は払戻を書かない・T065/T078 で各 job の ID を渡し `shared_fetch_with` で計上規則 |
| Q2-7 | `jra-audit` の exit 0 と起動時検証が矛盾・robots キャッシュの試験 | 採用 | T084 exit 0/2・T038 に robots 3 ケース |
| Q2-8 | summary キーの生成箇所・assert が未記載 | 採用 | T030/T031/T032/T033/T046/T059/T063/T064/T076/T077/T093/T058/T073 に明記 |
| SC-001/003 | 比較不能除外は「100% 一致」より弱い。合格条件を固定し全件隔離で合格させない | 採用 | T067/T079 |
| SC-005 | 時点別集計では 1 日全体を検証できない | 採用 | T096a(2 場×12R の全コマンド構成で合計を測る) |
| SC-007 | 構造破損ブロックの業務書込み 0 と正常ブロック継続 | 採用 | T051/T054 |
| SC-008 | 禁止表 toggle と「導入前後」は別 | 採用 | T088 分割+T100 で baseline 記録・T106 で比較 |
| SC-009 | 列集合一致では型・NULL・default の変更を検出できない | 採用 | T019 を列定義+制約の比較に |
| SC-010 | 失敗後の再実行・全公式経路の旧期間保護 | 採用 | T027/T070 |
| SC-012 | 2 セッションではプロセス内同期でも合格する | 採用 | T041 を別プロセス競合に |

**暫定案との差分**: 開催中に進められる範囲を「新規ファイルのみ」に狭めた(db の既存モジュールは ⏸)・CLI 骨格を writer から分離・dry-run の分岐点を writer の前へ・parity fixture の正本を稼働 DB から netkeiba archive へ・`jra-fetch` にも kind 許可制・R1 の migration 指定を revision ID へ。
