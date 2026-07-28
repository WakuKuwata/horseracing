# Contract: 取得の礼儀・プロセス跨ぎのレート制御

**Feature**: 086 | **Spec**: FR-016, FR-017, FR-018, FR-019 | **Research**: D4, D7

---

## 1. 現状の欠陥(実コードで確認)

| 事実 | 所在 | 帰結 |
|---|---|---|
| `_last_fetch` は**インスタンス変数** | `scrape/fetch.py:98,135-142` | 捕捉は毎回新しい fetcher を作るので 1 秒制限がクリック間・ワーカー間で効かない |
| 403/429 が汎用 `FetchError` に潰され**3 回リトライ**する | `scrape/fetch.py:145-159` | 拒否された直後にさらに 2 回叩く。後続の予測も同じことを繰り返す |
| 最悪所要 = 3 試行 × 20 秒 + backoff 3 秒 = **63 秒超** | `fetch.py` + `scrape/cli.py:61`(`httpx.Client(timeout=20.0)`) | どんな短い外側打ち切りも内側が食い破る(FR-018 違反) |

---

## 2. 予約 → 取得の 2 相プロトコル(FR-017)

抑制状態の読み書きは**捕捉トランザクションとは別の短命なセッション**で行う。
捕捉トランザクションは per-race の排他制御(capture-eligibility.md §1 手順 9)のために
取得中も開いたままだが、抑制状態はそれとは**独立にコミットする**。

```text
[別セッションの短いトランザクション #1 — 予約]
  INSERT INTO fetch_throttle_state (domain, next_allowed_at, updated_at)
       VALUES (?, now(), now())
  ON CONFLICT (domain) DO NOTHING                   ← 初回作成の競合を潰す
  row = SELECT * FROM fetch_throttle_state WHERE domain = ? FOR UPDATE

  if row.blocked_until is not null and row.blocked_until > now():
      COMMIT; return SKIP(source_cooldown)          ← FR-016

  wait = max(0, row.next_allowed_at - now())
  if wait > max_wait:                               ← FR-017b
      COMMIT; return SKIP(throttle_backlog)
  UPDATE fetch_throttle_state
     SET next_allowed_at = now() + wait + min_interval,   -- 次の予約
         updated_at = now()
   WHERE domain = ?
  COMMIT
[ローカルで wait 秒だけ待つ]
[別セッションの短いトランザクション #2 — 送信直前の読み直し]  ← FR-017a
  if blocked_until > now():
      return SKIP(source_cooldown)
[取得]
```

**予約は「1 捕捉」ではなく「1 リクエスト」に対して行う**。
`HttpFetcher.get` は本取得の**前に robots.txt を別途送る**うえ、
その送信は `_rate_limit` を**通らない**(`fetch.py:111` → `:112`)。
1 捕捉あたり同一ホストへ 2 本飛び、その間隔が制限されない。
→ **robots.txt の取得も予約を消費する**ように制限をリクエスト境界で定義する。
そのためには `scrape` 側に**継ぎ目**が要る — `HttpFetcher` に注入可能な
**事前フック**(例 `pre_request(url)`)を足し、robots 取得も本取得も同じフックを通す。
既定は no-op なので既存の scrape 利用者の挙動は変わらない。
**この継ぎ目が無いと §6 の robots 間隔テストは実装不能**で、
FR-017 は取得元へのリクエストの半分しか覆わない。

**初回作成の競合**(`ON CONFLICT DO NOTHING`): 行が存在しないとき
`SELECT ... FOR UPDATE` は**何もロックしない**ので、2 プロセスが同時に初回 INSERT を
試みて片方が一意制約違反で落ちる。先に空行を作ってからロックする。

**送信直前の読み直し**(FR-017a): 予約して待っている間に、別プロセスが拒否を受けて
抑制を書いた可能性がある。待機前の判定だけでは、待っていたプロセスが
クールダウン中の取得元を叩いてしまい SC-009 が破れる。

**待機の上限**(FR-017b): 予約は未来時刻を積み上げるので、同時に n 件走ると
n 番目の待機が約 n 秒になる。上限(既定 3 秒)を超える混雑では待たずに見送る。
待機が伸びると単一の実時間の期限(§4)を食い破り、
その間ずっと per-race の排他を握り続けることになる。

`next_allowed_at` に**未来の時刻を書く**(予約)ので、同時に来た 2 プロセスが
同じ待ち時間を計算して同時に叩くことがない。`FOR UPDATE` が予約を直列化する。

`min_interval` の既定は **1 秒**(既存の `HttpFetcher(min_interval_s=1.0)` と同じ)。
`max_wait` の既定は **3 秒**。

**制限するのは頻度のみ**(FR-017)。取得元全体に対する同時実行数の上限は設けない —
同一レースへの同時取得は per-race の排他制御が 1 回に収束させ、
単一オペレータのローカル運用では頻度制限で足りるため。

---

### robots.txt に対する 403 は拒否として扱わない(F3)

**`FetchRefused` を送出するのは本取得の 403/429 と、robots.txt の 429 だけ**とする。
**robots.txt が 403 を返した場合は現行どおり「robots 無し = 許可」**に落とす。

理由: `_robot_allows`(`scrape/fetch.py:118-131`)は今、非 200 をすべて
「robots 無し = 許可」に落としている。ここで 403 を拒否に変えると、
**取得元が robots.txt に 403 を返すだけで 5 つの取込 CLI が全部止まる** —
086 の目的(捕捉の礼儀)に対して blast radius が大きすぎる。
403 は誤設定でもよく返る一方、レート制限の信号は 429 である。

**この判断は `scrape` のテストでは守れない**(同テストはネットワークを使わないので、
robots の応答コード分岐を検出できない)。
T019 に **robots 403 → 従来どおり許可 / robots 429 → `FetchRefused`** の
両方のケースを置いて固定する。

---

### `pre_request` フックの契約

```python
PreRequest = Callable[[str], None]   # url を受け、送ってよければ何も返さない
```

| 事項 | 決め |
|---|---|
| 送ってよい | 何も返さず戻る(戻り値は使わない) |
| 送ってはいけない | **`FetchRefused` を送出する**(抑制中 / 待ち行列が上限) |
| 適用範囲 | **robots.txt の取得と本取得の両方**(robots も 1 予約を消費する) |
| 既定 | `None`(no-op)= 既存の取込 CLI の挙動は変わらない |

**`FetchRefused` は 2 箇所の握りつぶしより前に再送出しなければならない** —
`_robot_allows` の裸の `except Exception`(`fetch.py:128`)と
`_fetch_with_backoff` の `except Exception as exc`(`:154`)である。
前者に飲まれるとフックが「許可」に化けて**そのまま本取得に進む**。
後者に飲まれると汎用の `FetchError` になり、捕捉側で
`failed/fetch_failed` と分類される — 実際には抑制中なので
`skipped/source_cooldown` でなければならない。
**どちらもテストが緑のまま SC-009 が漏れる**ので、
検証は「フックが送出したら 1 本も飛ばない」を実 fetcher で assert する。

**期限(FR-018)との関係**: robots の往復も単一期限の内側である。
期限を持つのは `chaos_capture` 側なので、フックは期限を見ない
(期限切れの判定は取得の前後で `chaos_capture` が行う・T036d)。

---

## 3. 拒否・制限応答の扱い(FR-016)

`scrape` に型付き例外を追加する。

```python
class FetchRefused(FetchError):
    """取得元が拒否・制限を示した。再試行しない。"""
    status_code: int
```

`_fetch_with_backoff` は **403 / 429 を受けたら即座に `FetchRefused` を送出する**
(バックオフに入らない・リトライ回数を消費しない)。

**robots.txt の取得にも同じ規則を適用する**(`_robot_allows`・`fetch.py:118-131`)。
現状は非 200 と例外をすべて `rp = None  # no robots -> allow` に潰しているので、
**取得元が robots.txt に 429 を返しても拒否として扱われず、クールダウンも書かれず、
本取得へ進んでしまう**。`_fetch_with_backoff` だけを直すとこの経路が素通りし、
テストは全部緑のまま SC-009 が破れる。

**この変更は scrape 全利用者の挙動を変える**が:

- より礼儀正しい方向への変更である
- 1 秒・2 秒のバックオフで 429 が解けることは実質ない(機能的損失なし)
- `scrape` の既存テストを**全件**回して回帰が無いことを確認する

捕捉側は `FetchRefused` を捕まえて抑制を書く:

```text
[別セッション・独立にコミット]
UPDATE fetch_throttle_state
   SET blocked_until = now() + cooldown,
       block_reason  = 'http_429',
       updated_at    = now()
 WHERE domain = ?
COMMIT                                  ← 捕捉トランザクションとは無関係に確定させる
```

**これが F1 の核**である。捕捉トランザクションは取得中も開いたままなので、
そこに抑制を書くと `FetchRefused` による巻き戻しで**クールダウンが消える**。
後続の予測実行が同じ 429 を叩き直し、SC-009 が黙って失敗する。
抑制は**必ず別セッションで独立にコミットする**。

### `FetchRefused` の伝播(U4)

`capture_chaos` は `FetchRefused` を捕まえ、抑制を書いたうえで
**`skipped/source_cooldown` を返す**(例外を CLI まで漏らさない)。
CLI の終了コードは 0 のままである — 捕捉の失敗はジョブの失敗ではない
(job-observability.md §3)。取得元が拒否したのは異常ではなく、
「今は取りに行くべきでない」という正常な運用状態である。

| 応答 | cooldown | 再試行(scrape 一般) | 再試行(**捕捉経路**) |
|---|---|---|---|
| 429 | 30 分 | しない | しない |
| 403 | 60 分 | しない | しない |
| その他の非 200 | 抑制しない | 予算内でリトライ | **しない**(**`max_retries=1`** — `0` は「試行 0 回」で HTTP を一度も叩かない・下記) |

**捕捉経路は全ての非 200 で試行 1 回**である。補助機能であり、
失敗しても次の予測実行が拾うので、取得元を叩き直す理由がない。

**設定値は `max_retries=1`(0 ではない)**。
`_fetch_with_backoff` は `for attempt in range(self.max_retries)`(`fetch.py:148`)なので、
**`max_retries=0` は「試行 0 回」= HTTP を一度も叩かずに `FetchError` を投げる**。
字義どおり `0` を入れると**全捕捉が失敗し SC-001 が黙って未達**になる。
しかもスパイ fetcher を使うテストは `_fetch_with_backoff` を通らず、
遅いサーバのテストはむしろ即座に返るので**どちらも緑になる**。
→ 「実際の client が**ちょうど 1 回**呼ばれる」ことを実 fetcher で検証する。

`Retry-After` ヘッダがあればその値を優先する(上限 6 時間)。

---

## 4. 単一の実時間の期限(FR-018)

> **この節が期限に関する正本である**。spec / plan / research / tasks の数値記述は
> すべてここへの参照であり、値を変えるときは T025b の手順で全箇所を同時に直す。

**足し算の見積もりでは不十分**である。見積もりから漏れるものが実在する:

| 漏れるもの | 所在 |
|---|---|
| robots.txt の追加往復 | `fetch.py:111` → `_robot_allows` → `:123` で別リクエスト |
| 頻度制限の待機 | §2 の `wait` |
| 接続・読み取り・プールの各段階 | httpx の `timeout` は段階ごとの上限であって全体の上限ではない |

したがって捕捉は**単調時計による単一の期限**で全体を縛る:

```text
deadline = monotonic() + capture_deadline_s
  ├ 頻度制限の待機
  ├ robots.txt の確認
  ├ 取得(1 回)
  ├ 解析
  └ 保存
各段階の前に残り時間を確認し、尽きていれば skipped(deadline_exceeded)で打ち切る
```

```text
外側(ops subprocess timeout)  : 18 秒 ← predict 経路のみ
                                  (SC-011 の「予測に加わる時間 20 秒以内」は
                                   `uv run` の起動・`killpg`・`wait` を含む実時間なので、
                                   `communicate()` の上限を 20 にすると余裕がゼロになる)
内側(捕捉全体の期限・1 レースあたり・契機で決まる):
  predict_manual / predict_auto            : 10 秒
  daily_operational / explicit_command     : 30 秒
  いずれも試行は 1 回のみ
```

**フラグではなく契機に紐づける**: 外側の打ち切りと遅延制約が無いのは
日次だけでなく**操作者が手で 1 レースを流す場合**も同じなので、
`--race-id` という**引数の形**ではなく `--trigger` の**意味**で分ける。

**写像の置き場は `live/chaos_politeness.py` の純関数 `deadline_for(trigger)`** とする。
**CLI 層だけに写像を置いてはならない** —
`capture_chaos` を CLI 以外から呼ぶ経路(将来の日次結線・テストヘルパ)が
主たる観測源に予測用の 10 秒を掛けてしまう。

**ops はこれを import できない**(`ops` は `live` に依存せず、
`ops/tests/integration/test_boundary.py` が機械的に禁じている)。
そこで ops は**同値の定数 `_CAPTURE_DEADLINE_S` を自分で持ち、argv で明示的に渡す**。
live 側の `predict_*` 既定はフォールバックであって実効値ではない。

**ずれを検出する仕組み**(これが無いと FR-018 が黙って破れる):
`ops` のテストが `live/src/horseracing_live/chaos_politeness.py` を
**ソーステキストとして読み**、`predict_manual` に対応する数値が
`ops._CAPTURE_DEADLINE_S` と一致することを assert する
(このリポジトリの leak-guard / import-graph テストと同じ grep 型。
import しないので境界は保たれる)。
**「両方に定数がある」だけのトートロジーにしてはならない** —
片方を書き換えても緑のままなら、正本を宣言した意味が無い。

**日次経路の期限を分ける理由**: 日次には外側の打ち切りも遅延制約も無く、
FR-012 が「主たる観測源」と定めた経路である。
予測の体感(55 秒に対する上乗せ)を根拠にした 10 秒を掛けると、
netkeiba が遅い日に**主ソースが静かに欠測する**。
**FR-018 の「内側 < 外側」の不等式は外側が存在する predict 経路にのみ適用**する。
どちらも**1 レースあたり**の期限であり、コマンド全体ではない
(日次は 36 レースを順に処理する)。

**086 の当初案から下げた理由**: 当初案は内側 25 秒 / 外側 45 秒だったが、
予測本体は実測 55 秒なので 45 秒の上乗せは**最大 82% 増**で
「体感的に遅らせない」という性能目標と矛盾する。
捕捉は補助機能であり、取り逃がしても次の予測実行が捕捉する。
**1 回試行・10 秒**の方が単純で、取得元にも優しい。

**捕捉専用の fetcher ファクトリを新設する。`_make_fetcher` は変えない。**
`scrape/cli.py::_make_fetcher`(`:56`)は **捕捉経路だけでなく scrape の全 ingest CLI が
共有している**(`:71, 162, 200, 229, 242` の **5 箇所** — fixture 捕獲 / entries / odds / results / laps)。
ここの timeout と `max_retries` を書き換えると、**日次取込の read timeout が 20s→5s に落ち、
リトライも消える**。しかも scrape の既存テストはネットワークを叩かないので回帰確認では検出できない。

→ **`make_capture_fetcher` を `live/chaos_politeness.py` に新設**し、捕捉経路だけがそれを使う。
`_make_fetcher` の timeout(20 秒)と `max_retries` は**不変**である
(`_make_fetcher` は `max_retries` を渡しておらず、`HttpFetcher` の既定 3 を継承している。
検証は「3 のままであること」= 明示指定が増えていないことを見る)。
「実際に構成されるクライアントを検証する」テストは、
**捕捉側が新ファクトリを使っていること**と
**既存 scrape 経路の fetcher が 20s / `max_retries=3` のままであること**の両方を assert する。

**捕捉が使うクライアントの timeout は `httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)`**
とする(各段階の上限。合計が内側の期限を超えうるのは承知のうえで、
**最終的な打ち切りは単調時計の期限が行う**)。
検証は「20 秒のままでない」でも「内側の期限以下」でもなく、
**各段階の値が上の 4 つと完全に一致する**ことを assert する
(「以下」だと別の組でも通ってしまい、値をここに固定した意味が無い)。

**検証は静的な不等式では足りない** — 遅い HTTP サーバ(robots.txt を含む)に対して
実際の CLI を走らせ、実時間・排他の解放・子孫プロセスの不在を確認する。

---

### 捕捉トランザクションと DB のタイムアウト設定

排他は取得の**前**に取るので(`capture-eligibility` §1)、
**HTTP I/O のあいだ捕捉トランザクションは開いたまま**になる。
idle-in-transaction の最大時間は**その契機の期限と同じ**
(predict 10 秒 / daily・explicit 30 秒)。

運用 DB が `idle_in_transaction_session_timeout` をこれより短く設定していると
**日次経路が静かに落ちる**(主たる観測源が無言で欠測する)。
quickstart §1 の前提確認でこの設定を確認する。

---

## 5. 外側打ち切りの記録(FR-019・FR-018a)

subprocess が打ち切られた場合、直前に DB のコミットが確定していたか判別できない。

```text
outcome = "unknown"
reason  = "outer_timeout"
```

**成功とも失敗とも決めつけない。** 次回の予測実行では capture-eligibility §2 が
「行があるか」を DB の事実として読むので、どちらであっても正しく収束する。

**子孫プロセスまで終了させる**(FR-018a): `uv run` は中間の起動プロセスであり、
これだけを終了させると実際の捕捉プロセスが生き残る。
生き残ったプロセスは per-race の排他を握り続け、
記録が「結果不明」になった後で保存を完了させうる(記録と DB が食い違う)。
→ 新しいプロセスグループで起動し、打ち切り時は**グループ全体**に終了を送る。

---

## 6. 検証

| 検証 | 期待 |
|---|---|
| 別インスタンスの fetcher を 2 つ作って連続取得 | 2 回目が **1 秒以上待つ**(現状は待たない) |
| 429 を返す | `FetchRefused` が **1 回目で**送出される・スパイの呼び出しは **1 回** |
| **捕捉が巻き戻っても抑制が残る** | 捕捉トランザクションを rollback しても `blocked_until` が DB に残る |
| 429 の直後に別プロセスから捕捉 | `skipped/source_cooldown` かつ **取得 0 回**(SC-009) |
| **待機中に別プロセスが抑制を書く** | B が予約して待機 → A が 429 で抑制 → B は **取得 0 回**(FR-017a) |
| **抑制行が存在しない状態で同時 2 プロセス** | どちらも初期化に失敗しない(一意制約違反が起きない) |
| **多数の同時予約** | 待機が上限を超えたら `skipped/throttle_backlog`・全体の期限を超えない(FR-017b) |
| `FetchRefused` が CLI まで漏れない | 終了コード 0・`outcome` が `skipped` |
| `blocked_until` 経過後 | 通常どおり取得する |
| **遅い HTTP サーバに対する実 CLI** | robots.txt を含めて実時間が内側の期限内・排他が解放される・**子孫プロセスが残らない**・打ち切り後に遅れて行が増えない |
| **robots.txt が 429 を返す** | `FetchRefused` として扱われ、クールダウンが書かれ、本取得へ進まない |
| **実 fetcher で robots + 本取得** | 同一ホストへの 2 本が **1 秒以上離れる**(robots も予約を消費する) |
| **実 fetcher での取得回数** | 非 200 でも `client.get` が**ちょうど 1 回**(`max_retries=1`。`0` だと 0 回になる) |
| **実 CLI が叩く URL から導出した抑制キー** | 抑制行のキーと一致する(独自に組み立てていない) |
| **既存 scrape 経路の fetcher** | timeout 20s / `max_retries=3` の**まま変わっていない**(日次取込を壊さない) |
| **非 200 のときの対象 URL への呼び出し** | ちょうど 1 回(**robots.txt は別に数える** — 同じ `client.get` を使い、`self._robots` はインスタンス変数で捕捉は毎回新 fetcher を作るので初回のホスト接触は必ず 2 本) |
| **`pre_request` が `FetchRefused` を送出したとき** | **robots.txt を含め HTTP が 1 本も飛ばない**(2 つの `except Exception` に飲まれない) |
| 捕捉経路が構成する httpx クライアント | 各段階の timeout が**§4 の表の値と完全一致**(「内側の期限以下」だと別の組でも通る) |
| 外側打ち切り | ジョブ記録が `outcome="unknown"` で残る |
