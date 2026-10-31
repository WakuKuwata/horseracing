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


## 捕捉時間帯の譲り合い(2026-10-31・同日午後)

利用者の決定「予測は直前でなくても実行したい・制限はかけたくない」を受け、**active のまま 174 の T−3 捕捉を動かせる**ようにした。捕捉側の `collection_paused=true` 要求は外し(bool であればよい)、代わりに ops が捕捉時間帯だけ取得を譲る(`ops/src/horseracing_ops/capture_window.py`):

- 窓 = policy の `capture_only_dates` に今日(JST)が載っているとき、DB の平地レース(`track_type≠障`・post_time あり)の `[min(post_time) − 6 分, max(post_time))`。6 分 = 目標 180 秒 + 窓 60 秒 + 同時刻ずらし 60 秒 + 余裕 60 秒。捕捉プロセスの登録はしないので、**捕捉を起動しなくても窓は効く**(その日に捕捉しないなら policy から日付を外す)。
- 窓の中: 更新 POST(レース・日)は `409 capture_window`、worker の IO レーンは refresh_race/refresh_day を claim しない(queued のまま待つ・失敗にしない)、窓の前に claim されて走り続けている refresh は fetcher の `pre_request` で `CaptureWindowRefused`(408・cooldown なし)となり sub-step が partial/failed で終わる。手動予測の 086 荒れ度取得は `capture.reason=capture_window` で見送る。**予測・買い目生成・購入記録・期待回収率の再計算・範囲更新(取得なし)は常に通る**。
- `GET /ops/v1/status` の `capture_window`(date/starts_at/ends_at/active/n_races/detail)。front は `データ更新`/`この日を更新` だけを「〜HH:MM まで受け付けません」で無効化(他のボタンは有効)。admin の範囲更新は取得をしないので対象外。
- 限界: 同時刻 3 会場だと捕捉の 3 本目は目標 +120 秒で、窓の前縁の余裕は 60 秒ぶん食われる(捕捉自体は ±60 秒の窓を持つ)。post_time が当日変わると窓も動く(status は読むたびに再計算)。

運用(active 中の捕捉日): 前日 `scripts/near_post_ops.sh entries <date>`(60 秒間隔・時間帯外に)→ 当日朝 `morning`(robots 1 GET・dry-run は 1 日歩く作りなので gate 行を見たら止めてよい)→ `capture` を最初のレースの 6 分前より早く起動 → 夜 `results`。worker を止める必要はない。
