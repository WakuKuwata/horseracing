# Research: netkeiba 取得礼儀の単一化

092 の実装中(2026-08-12)に行った調査の記録。数値はすべて実コード/実 DB/実行結果から。

## D1. 迂回経路の実査

`OPS_FETCH_MIN_INTERVAL=60` を設定しても効かない経路を、コードを読んで確定した。

```
scrape/src/horseracing_scrape/fetch.py
  _last_fetch: dict[str, float]   # インスタンス変数 → fetcher ごとに独立
  _rate_limit(_domain(url))       # キーがホスト → race/db が別枠
  _robot_allows()                 # _rate_limit を呼ばない
  _fetch_with_backoff()           # _rate_limit はループの外
```

**適用点の発見(これが設計を決めた)**: `_before_request`(= `pre_request` seam)は

```
fetch.py:214   robots.txt 取得の直前
fetch.py:250   リトライループの内側(毎回)
```

の両方で呼ばれる。つまり robots とリトライの迂回は `_rate_limit` を修正する話ではなく、
`pre_request` に DB スロットルを挿せば構造的に閉じる。**プロセス内リミッタを直すのは
筋が悪い**(プロセス跨ぎを解決できない)。

## D2. 086 の既存資産

`RequestPoliteness`(live/chaos_politeness.py)は必要なものをほぼ持っている:

- DB 経由(`fetch_throttle_state`)= プロセス跨ぎ・スレッド跨ぎ
- `INSERT ON CONFLICT DO NOTHING` → `SELECT FOR UPDATE` → `clock_timestamp()` の原子予約
- **sleep 中はロックを保持しない**(commit してから待つ)= ここは正しい
- `blocked_until` による cooldown
- `record_refusal` で 429/403 の cooldown を独立トランザクションで永続化

不足しているのは「単一契約」「送信直前の再検証」「背景ジョブ用モード」「400 の扱い」。

## D3. 配置制約の実査

| 制約 | 根拠 |
|---|---|
| ops は `horseracing_live` を import 不可 | `ops/tests/integration/test_boundary.py:20` の FORBIDDEN |
| `scrape` は db+sqlalchemy に依存済み | `scrape/pyproject.toml` に `horseracing-db`/`sqlalchemy` |
| `_domain` は変更不可 | `_robot_allows` が `f"{domain}/robots.txt"` で robots URL を組む |

→ 中核は `scrape` へ。スロットルキーは `_domain` とは**別の関数**にする。

## D4. 定数の実測

```
_DEFAULT_MIN_INTERVAL_S = 1.0
_DEFAULT_MAX_WAIT_S     = 3.0     ← 1req/分では 2 番目のリクエストで即拒否
_COOLDOWN_S = {429: 1800, 403: 3600}
```

実行して確認:

```
cooldown 対象ステータス: [403, 429]
netkeiba の実ブロックは 400 → cooldown が付くか: False
```

`REFUSAL_STATUSES` には 400 が入っている(`fetch.py:129`)。そこのコメント:

> 403/429 は文書化されたものだが、netkeiba は持続的な負荷に対して空ボディの素の 400 で
> 応じる。これを通常のエラー扱いにしたため、一括処理が全件拒否されながらリスト全体を
> 1 件ずつ延々と回り続けた

**分類はされているが、cooldown の対象になっていない。**

## D5. 補完コストの実測(092 の順序修正の根拠)

サロゲート馬の補完対象:

```
nk_horses=4045  sex NULL=1  birth_year NULL=1  sire_id NULL=1  would_refetch=1
```

→ **一発限りで、繰り返しではない**。コストの実体は新規馬:

| 開催日 | 新規 nk: 馬 | req(2/頭) | 1req/分 |
|---|---:|---:|---:|
| 2026-08-09 | 47 | 94 | 1.6h |
| 2026-08-02 | 57 | 114 | 1.9h |
| 2026-07-12 | 65 | 130 | 2.2h |
| 2026-07-05 | 67 | 134 | 2.2h |

092 の回帰テストが記録した旧順序(fixture 18 頭のレース):

```
['entries', 'profile' × 18, ..., 'odds' ← index 20]
```

**オッズを要求する前にプロファイルを 18 ページ**。本番は 1 頭 2 リクエストなので
約 36 分。その間に発走すれば事前捕捉は永久に失われる。

## D6. stale 窓を広げてはいけない理由(092 で revert した判断)

`recover_stale` は `main()` の**起動時 1 回だけ**呼ばれる(`worker.py:354`、他に呼び出し無し)。

| 窓 | 20 分で死んで再起動 | 結果 |
|---|---|---|
| 900s | 25分 > 15分 → 再キュー | 正しく復旧 |
| 84分 | 25分 < 84分 → 「新しい」 | **永久に RUNNING で固着** |

起動時点で前のワーカーは死んでいるので、そこで見つかる RUNNING は必ず孤児。
窓を広げても守るものが無く、復旧を遅らせるだけ。
**レースの再スクレイプはリクエストの二重消費で済むが、固着はそのレースを失う。**

→ 本来の解は heartbeat(最終進捗で判定)。schema 列と定期 recovery が要る。

## D7. codex レビューの採否

3 回のレビュー(実装設計 / 補完順序 / 礼儀設計)を `codex exec --sandbox read-only` で取得。
[[codex-env-recovery]] の直叩き方式。agent 経由は結果回収を拒むため使わない。

### 採用(092 で実施済み)

| 指摘 | 対応 |
|---|---|
| アーカイブがデコード済みテキストを保存 | 生バイト保存に変更。`_resolve_text` は `errors="replace"` に落ちる |
| オッズ除外が部分文字列マッチ | 正規化パス一致に変更。クエリ順で抜けられない |
| CLI の read-through キャッシュが既定 ON | 既定 None(opt-in)に変更 |
| `make_fetcher` が設定を無視 | `CONFIG.fetch_min_interval` を読むよう修正 |
| NFKC 正規化・同時刻衝突 | 実施 |
| 血統失敗が SUCCEEDED と報告 | `errors=1` 計上に修正 |
| stale 窓を広げると孤児が固着 | **私の変更を revert**(D6) |

### 採用(093 = 本 feature へ)

§4 の欠陥①〜⑦ すべて。

### 不採用・保留

| 指摘 | 判断 |
|---|---|
| `entries → odds → quotes → results` へ並べ替え | **不採用**。odds が「pending 上書きモード」を選び、保護すべき確定オッズを上書きしうる。既存の統合テストがこの契約を固定している。相談してから動いたので踏まずに済んだ |
| 補完を独立ジョブ種別に | **保留**。predict が補完前に走るとデビュー馬の血統なしで確定し、`_has_active_prediction` が再計算を永久に抑止する。予測バリアとセットでなければ危険 |
| results 経路にも prize | **保留**。entries で全レース充足するので機能上は不要。`Race` への新しい書き込み面を増やさない判断 |

## D8. 未確認の前提

- **(a) の robots 永続キャッシュが実際に何リクエスト減らすか**は未計測。
  capture が毎回新 fetcher を作るため毎回 robots を取っている、という読みに基づく。
  実装前に 1 開催日の robots リクエスト数を実測すべき。
- **1req/分での 1 日の総所要**(3〜4 時間)は積算値であって実測ではない。
  礼儀を単一化した後に 1 開催日を通して実測する。
