# 171 更新後の確認・停止手順

今回のrobots取得は1回で完了した。run-01/refreshへ再実行しない。レース取得を伴うfetcher.get、live plan/observe、モデル推論、既存opsの起動/再起動は行っていない。

現在の専用profileと保存robots/throttleを、新たな取得なしで再確認するには未使用directoryを指定する:

```bash
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli check \
  --profile config/.env.manual-observer --out /tmp/manual171-next-preflight
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli verify \
  --out /tmp/manual171-next-preflight
```

process初期環境のallowlist設定と同じ実DBのqueue/session/throttle lockをread-onlyで再確認する場合:

```bash
PYTHONPATH=. training/.venv/bin/python specs/171-robots-refresh/evidence/inspect_operation.py \
  --out /tmp/manual171-next-operation
```

後者は166の確認済み管理用接続をread-only検査に使い、秘密値・raw環境・他sessionのquery本文を出さない。検査自身のSQL本文は監査用に保存する。専用profile検査やHTTP取得時の接続へfallbackする仕組みではない。snapshotは現在の観測であり、将来の無競合を保証しない。

運用intervalは未統一。開始前に全API/ops/scrape/live callerへ承認済みの共通source intervalを設定し、再読込後の確認を行う必要がある。現manual60秒より短くせず、既存承認値がより長いならそちらに合わせる。単にmanual profileや共有throttle行を直して済んだとは扱わない。既存processの再起動はqueue回復・処理再開につながりうるため、収集開始前の別の実施範囲として扱う。

将来、robotsの再更新が必要になり明示的に許可された場合だけ、`scrape/.venv/bin/python -m scripts.roi_explore.robots_refresh_171 --profile config/.env.manual-observer --out 未使用directory`を使える。固定race origin robotsへの1GETだけで、共有slotとcacheを変更する。slot待ち/cooldown/HTTPエラー/redirect/保存不備は停止し、自動retryしない。停止後に新directoryで無断再試行しない。robotsだけの成功をレース収集のGOとしない。

private profileは表示/source/eval/転記しない。0600とGit管理外を維持する。quote30分以内・判断前の両ready・三段階guard・固定窓・公式払戻精算の詳細は165〜170の契約を維持する。
