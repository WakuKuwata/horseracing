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
