# Contract: 取得(JraFetcher)と解析(jra/parse)

## 1. JraFetcher(`scrape/src/horseracing_scrape/jra/fetch.py`)

既存 `HttpFetcher` は変更しない。共有するもの: `RequestPoliteness`(`pre_request` / `record_refusal`)・`robots_cache.shared_cache()`・archive 書込み関数。

```
JraFetcher(
    policy: RequestPoliteness,            # 必須(None を許さない=共有スロット迂回不可)
    capture_guard: Callable[[], CaptureWindow | None],  # 送信直前・スロット待ちの前後で呼ぶ
    archive_dir: Path | None,
    client=httpx.Client(follow_redirects=False, timeout=20.0, headers={"User-Agent": ...}),
)
.get(url, *, kind) -> JraResponse            # 索引・馬場断片
.post(url, cname, *, kind) -> JraResponse    # cname 遷移・accessJ
.requests_sent: int                          # robots・リダイレクト先・再試行を含む送信数
```

### 送信前(すべての HTTP・robots を含む)
1. `capture_guard()` が窓を返せば `CaptureWindowRefused`(送らない)。
2. `policy.pre_request(url)`(共有スロット予約・cooldown は**待たずに拒否**)。
3. もう一度 `capture_guard()`。
4. 送信。

### 応答分類

| 応答 | 例外 / 戻り | cooldown | 再送 |
|---|---|---|---|
| 200 + 復号成功 + エラー画面でない | `JraResponse` | — | — |
| 200 + 「パラメータエラー」画面 | `JraParameterError` | なし | なし(呼び出し側が辿り直し ≤1) |
| 3xx → Location に `error013` | `JraParameterError` | なし | なし |
| 3xx その他 | `JraParameterError("unexpected redirect")` | なし | なし(ループしない) |
| 400 | `JraParameterError` | なし | なし |
| 403 / 429(robots 含む) | `FetchRefused` | あり(Retry-After 優先) | なし。run 停止 |
| 503 + Retry-After | `FetchRefused(503)` | あり | なし。run 停止 |
| 5xx / 接続エラー | `FetchError` | なし | POST なし。GET(索引・断片)1 回 |
| 復号失敗(cp932 strict) | `JraDecodeError` | なし | なし |

### 復号
HTTP header の charset → meta charset の順。`shift_jis` / `sjis` / `x-sjis` / `windows-31j` / `ms932` は **cp932**。`errors="strict"`。UTF-8 宣言(accessJ)はそのまま。

### archive(ページ種別の許可制)

| kind | archive |
|---|---|
| index / daylist / entries / changes / results / payouts / baba_fragment / accessJ | 可(鍵 `sha256(url + "\n" + cname)`、`url.txt` には url と cname の sha256 だけ) |
| odds_win(単複・前日最終) / odds_exotic | **不可**(時刻つき追記=オッズ時系列。憲法 V) |

## 2. navigator(`jra/navigate.py`)

```
index(kind) -> JraPageRef                      # 固定 cname: D pw01dli00/F3, O pw15oli00/6D, S pw01sli00/AF, H pw01hli00/03, I accessI(GET)
day_links(index_page, date) -> list[JraPageRef]   # 「4回東京11日」+日付 を --date と照合。無ければ unsupported_date
race_links(day_page) -> list[JraPageRef]         # オッズのみ(1 レース 1 ページ)
```

- cname は `JraPageRef` にだけ持ち、保存しない。run をまたいで再利用しない(FR-001)。
- `JraParameterError` を受けたら index から**1 回だけ**辿り直す(`rewalks=1`)。2 回目で FAILED。

## 3. 解析器(`jra/parse/*.py`)— 入力は復号済み `str`、出力は data-model §4 の値オブジェクト

共通規則(FR-008):
- 必須要素(見出し・表・列)が無ければ `ParseError`(そのレース/ページは無書込み)。表そのものが「未発表」の形なら `NotYetPublished`。
- 開催日ページは `race_header` ごとに独立に判定し `JraRaceBlock.state` を返す。1 レースの失敗が他のレースを巻き込まない。
- 見出し「YYYY年M月D日（曜）K回<場>N日」+ `race_number` → `build_race_id(year, venue_code, kai, nichime, race_no)`。索引で選んだ日付・回・日次と一致しないブロックは `parse_failed`。
- ID は `jra/identity.py` の境界つき正規表現。未知 ID は None(書込み側で隔離)。
- 数値は全角→半角(NFKC)してから厳密に解釈。解釈不能は None(必須列なら ParseError)。

| parser | 入力ページ | 出力 | 主な判定 |
|---|---|---|---|
| `parse_entries_day(html, *, expected_date)` | 出馬表(開催日) | `JraDayPage[JraEntry]` | 枠/馬番(取消は None)/馬名+ID/馬体重(増減)/性齢/負担重量/騎手+ID/調教師+ID/単勝オッズ列は**読まない**(オッズページが正)/発走時刻/距離・コース/`取消` `除外` |
| `parse_changes(html, *, expected_date)` | 変更情報(accessI) | `tuple[JraChange, ...]` | 場・R ごとの「出走取消 N番 馬名」「発走時刻変更 HH:MM」「騎手変更 N番 旧→新」 |
| `parse_win_odds(html, *, expected_date)` | 単複オッズ(1 レース) | `JraWinOdds` | 「HH時MM分現在オッズ」→ official_at(JST・分)、「前日最終」→ kind=prev_day_final、`odds_tan` / `odds_fuku`(min-max)/ `h_weight` |
| `parse_results_day(html, *, expected_date)` | 結果(開催日) | `JraDayPage[JraRaceResult]` | 着順(数字/中止/失格/取消/除外)/枠/馬番/馬名+ID/タイム/着差/コーナー通過順/推定上り/馬体重(増減)/人気(監査用)/レース単位のラップ・上り 4F/3F・天候・馬場/`refund_area` → `JraPayoutSet` |
| `parse_payouts_day(html, *, expected_date)` | 払戻(開催日) | `JraDayPage[JraPayoutSet]` | `refund_unit` 内 `dl/dt/dd`・`div.line` 複数=同着・「発売なし」「返還」 |
| `parse_cushion(html, *, fetched_at)` / `parse_moisture(html, *, fetched_at)` | `_data_cushion.html` / `_data_moist.html` | `tuple[JraTrackReading, ...]` | `div#rc? title=<場>` → venue_code、`div.time` + 年補正、`span.mg/.m4c` + `data-condition` |
| `parse_kaisai_info(json_text, *, fetched_at)` | accessJ | `tuple[JraTrackReading, ...]` | `kaisai_info[].jyoname/ba_s/ba_d/weather` → going_turf / going_dirt / weather(time_basis=fetched) |

払戻ブロックの状態判定: 券種見出しあり+行あり → `complete`、見出しあり+「発売なし」→ `not_sold`、見出しあり+返還のみ → `refund_only`、見出しなし(結果未確定・未発表)→ `not_published`、行の形が想定外・組番重複・金額不正 → `parse_failed`。

## 4. fixture(`scrape/tests/fixtures/jra/`)

raw バイトのまま保存(`.sjis.html` / `.html` / `.json`)。`manifest.json` の各 entry: `kind`, `file`, `url`, `cname_sha256`(平文は書かない), `fetched_at`(JST), `raw_sha256`, `charset`, `race_date`, `venue_code`, `note`。初期集合は 2026-10-31(結果・払戻・東京)と 2026-11-01(索引 4 種・開催日一覧・出馬表・変更情報・東京 11R 単複 8:22・馬場断片・accessJ)。構造変更 fixture(列欠落・見出し変更・別日)は raw をコピーして要素を削った派生。
