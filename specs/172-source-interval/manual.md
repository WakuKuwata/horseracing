# 172 共通設定と停止中の正式起動手順

共通設定は`config/source-operations.json`。現在はsource/opsとも60秒、shared limiter有効、collection_paused=true。API/ops/workerの標準起動は`bash scripts/stack.sh ...`、手動観測の正式入口は`scripts/manual_observer_172.py`とする。旧165の`--enable-collection`直接実行や、wrapperを通さない旧uvicorn/worker直接起動は今回の正式運用外。

APIの保存済みデータ閲覧は利用できる。opsの非読取要求は503で停止する。workerはDOWNが正常で、start/restartでもmain前のPAUSED終了となる。並列度0やauto capture flagだけを停止手段にしていない。

```bash
bash scripts/stack.sh status
bash scripts/stack.sh restart worker
```

API/opsの切替が必要な場合は、現在のqueue/runningと停止状態を確認して対象componentだけを操作する。API/ops wrapperは新OS sessionで起動してruntime記録を待つ。stackの成功表示だけで反映を断定せず、listener PID・runtime・local読取・503を確認する。今回front/admin/postgresは再起動していない。

新規取得なしで手動profile/commonの一致と専用role preflightを確認する（出力先は毎回未使用）:

```bash
training/.venv/bin/python scripts/manual_observer_172.py preflight \
  --profile config/.env.manual-observer \
  --out /tmp/manual172-next-preflight --report /tmp/manual172-next-policy.json
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli verify \
  --out /tmp/manual172-next-preflight
```

停止ゲートの動作確認:

```bash
training/.venv/bin/python scripts/manual_observer_172.py observe \
  --profile config/.env.manual-observer --report /tmp/manual172-next-paused.json
```

これは観測を実行せず、`PAUSED_NOT_STARTED`と終了コード3を返す。共通設定・profile・実policyの60秒を照合した後、旧観測CLIのimportやDB/HTTP/予測前に停止する。`--enable-collection`を追加しても受理しない。private profileの表示/source/evalや秘密値の引数展開はしない。

将来の取得開始は別の明示した作業として扱う。今回のpolicyはpause以外を拒否し、編集だけで収集を開始する切替は用意していない。165〜171の保存契約と過去の証跡は凍結している。開始時に取得30分以内、判断前の両ready、三段階guard、固定窓、公式払戻精算を改めて満たす必要がある。


## active モード(2026-10-31・利用者決定)

利用者の決定で policy の `collection_paused` を **false** にできるようにした(「捕捉のスケジューリングは一旦止め、手動更新と予測を受け付ける」)。切替は `config/source-operations.json` の 1 キーだけで、他は両モードで同じ:

- 情報源の間隔 60 秒・共有リミッタ必須・継承した間隔を短くしない・`OPS_CAPTURE_ON_AUTO_PREDICT=0`(自動追随側の荒れ度捕捉 off)は active でも wrapper が強制する。
- api は active でも Python の外向き接続を遮断したまま(読み取り専用)。ops-api は `PausedOps` で包まず素の app、worker は wrapper が同一プロセスで `horseracing_ops.worker.main` を起動する(`--detach`・`logs/worker.pid`・`logs/worker.log`)。
- `GET /ops/v1/status` は `collection_mode=active`・`writes_accepted=true`。policy と process の食い違いは**両方向**で `policy_mismatch=true`(file=active なのに process=paused は「切替後に再起動していない」)。
- **C1(2026-10-29 決定)を同時に実装**: refresh(更新ボタン・一括)は予測ジョブを自動追随させない(`OPS_PREDICT_ON_REFRESH` 既定 false・2 箇所=runner の run_one と enqueue_race の fresh 再利用分岐)。expected_return の自動 enqueue は従来どおり。予測は「予測する」ボタンから。
- 手動観測 CLI(`scripts/manual_observer_172.py`)と 174 の T−3 捕捉(`live near-post-capture`)は **paused=true を要求したまま**。active 中は起動できない(排他)。捕捉に戻すときは policy を true に戻し `bash scripts/stack.sh restart ops-api` と `bash scripts/stack.sh stop worker`(paused の worker は start しても main 前で終了)。
- 手動予測(predict_manual)は 086 の荒れ度スナップショット取得(1 req・締切 10 秒)を伴う。これは active/paused と独立。

```bash
bash scripts/stack.sh restart ops-api   # policy 切替後は必ず(status の policy_mismatch で検知できる)
bash scripts/stack.sh start worker      # active なら daemon・paused なら main 前で終了
curl -s http://127.0.0.1:8001/ops/v1/status
```

2026-10-31 08:39 JST に切替・ops-api 再起動・worker 起動(io=2 cpu=3)。10/31 の capture-only 試運転は 08:32 に停止し、`decision_quotes` は 0 行のまま。

### 2026-10-31 追記: 情報源の間隔を 10 秒に

利用者決定「netkeiba は 1 req/10 秒・外部でないものは制限不要」。policy の `source_interval_seconds`/`ops_fetch_min_interval_seconds` を 60→**10** にし、wrapper の下限を `MIN_SOURCE_INTERVAL_S = 10`(`scripts/source_operations_172.py`)・`scripts/near_post_ops.sh` の下限も 10 に揃えた。共有リミッタ(DB 背後・全経路)はそのまま。予測・買い目生成・購入記録・期待回収率は情報源に触らないので従来どおり無制限(CPU レーン並列 3)。1 レースの更新(6 req)は約 1 分、日単位 24 レースは約 25 分。08:51 JST に ops-api/worker を再起動し、status・runtime report・`near_post_ops.sh check` の 3 箇所で実効 10 秒を確認。手動観測 CLI の private profile(`config/.env.manual-observer`)は 60 のままで、`manual-preflight` は policy との不一致で止まる(凍結経路なので触らない)。


## 捕捉時間帯の譲り合い(2026-10-31・同日午後 → 2026-11-02 にレース単位へ縮小)

利用者の決定「予測は直前でなくても実行したい・制限はかけたくない」を受け、**active のまま 174 の T−3 捕捉を動かせる**ようにした。捕捉側の `collection_paused=true` 要求は外し(bool であればよい)、代わりに ops が捕捉の瞬間だけ取得を譲る(`ops/src/horseracing_ops/capture_window.py`)。

**2026-11-02 の縮小**: 初版は窓を開催日まるごと(`[min(post_time) − 6 分, max(post_time))`・約 7 時間)で切り、更新 POST を 409 で拒否・worker は refresh を claim しない作りだった。11/01 に捕捉を起動していない日まで更新が丸一日止まり、利用者が「オッズは動くので定期的に取れないと使い物にならない」と却下 → 次のとおり改めた。

- 譲る時間(hold) = レースごとに `[fetch_at − (間隔 + 5 秒), fetch_at + 20 秒]`。`fetch_at` = 捕捉が 1 リクエストを送る瞬間 = `post_time − (180 + k·60) − 60` 秒(k = 同時刻レースの race_id 順の番号・live `schedule_day` の `window_open`。10/31 実測は 21/21 が post − 240.0 秒)。間隔は共有リミッタの実効値(現在 10 秒)なので 1 レース約 35 秒・1 日約 13 分。前側は「hold 開始前に取った予約の `next_allowed_at` が fetch_at より手前に収まる」余裕、後側は捕捉が 20 秒ほど遅れても間に ops の予約が入らない余裕(締切 18 秒)。捕捉が定刻から 30 秒以上遅れると best-effort(2 レーンの予約が重なれば締切を割りうる)。
- 対象日は policy の `capture_only_dates` に今日(JST)が載っているとき。DB の平地レース(`track_type≠障`・post_time あり)から計算(live `day_races` と同じ選択)。ops は live を import できないので規則を複製し、`specs/174-near-post-decision-quotes/fixtures/capture-schedule-parity.json`(live が生成)を ops/live 双方のテストで照合する。捕捉プロセスの登録はしない(起動していない日の hold は更新を最大 35 秒待たせるだけ)。
- hold の中: **更新 POST は常に 202**。worker の fetcher は scrape の `before_reserve` フック(`RequestPoliteness`・毎回の予約試行の直前に走る)で hold の終わりまで眠ってから予約する(拒否しない・ジョブは succeeded のまま)。hold 開始前に失った予約争いを hold の中で取り直す経路を塞ぐため、フックはループの外ではなく各試行の直前に置く。待ちの上限は 120 秒(hold は構造上 35 秒・隣接会場の重なりでも 70 秒程度)。手動予測の 086 荒れ度取得は hold の終わりを待ってから subprocess を起動する(見送らない)。**予測・買い目生成・購入記録・期待回収率の再計算・範囲更新(取得なし)は常に通る**。
- `GET /ops/v1/status` の `capture_window`: `starts_at`/`ends_at` はその日の hold の外枠、`active` はいま hold の中か、`hold_ends_at` は開いている hold(隣接は結合)の終わり、`hold_seconds` は 1 レースの長さ。front の `データ更新`/`この日を更新` は**無効化しない**。hold 中だけ「発走直前の判断記録を取得中(〜HH:MM:SS)。更新はその後に進みます」を薄く出す(status は 60 秒ごとの取得なので出ないこともある。ops 側の待機は表示と無関係に効く)。
- 限界: 保護は共有スロットの予約順に対するもので HTTP 完了までは保証しない。post_time が当日変わると hold も動く(status は読むたびに再計算)。

運用(active 中の捕捉日): 前日 `scripts/near_post_ops.sh entries <date>`(60 秒間隔・時間帯外に)→ 当日朝 `morning`(robots 1 GET・dry-run は 1 日歩く作りなので gate 行を見たら止めてよい)→ `capture` を最初のレースの 6 分前より早く起動 → 夜 `results`。worker を止める必要はない。
