# 170 適用済み専用roleとprivate profileの扱い

`hr_manual_observer`の作成と接続設定は適用済み。`apply`の再実行、169の旧SQL案の実行、password再生成、role再作成は不要。今回の成功は収集開始の許可を意味しない。

private設定は`config/.env.manual-observer`に0600で保存した。表示・shell source/eval・コマンド引数への展開・チャットや公開成果物への転記をしない。169の`load_profile`/`profile_environment`で明示的に読む。通常のDATABASE_URLや管理者接続へfallbackしない。既存API/opsプロセスの設定は変更していない。

接続・設定・ACL・保存robots/throttleを再確認する場合は、リポジトリrootから未使用の出力directoryを指定する。以下は確認だけで、取得・予約・更新をしない。

```bash
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli check \
  --profile config/.env.manual-observer --out /tmp/manual170-next-preflight
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli verify \
  --out /tmp/manual170-next-preflight
```

必要列の実読取・全public relation/sequence権限の確認も取得なしで再実行できる。出力先は毎回未使用にする。

```bash
training/.venv/bin/python -m scripts.roi_explore.provision_observer_170.verify_reads \
  --profile config/.env.manual-observer --root /tmp/manual170-next-read-smoke
```

保存された今回の証跡を独立再計算する場合（DB接続なし、profileはlstatのみ）:

```bash
training/.venv/bin/python specs/170-observer-provision/evidence/independent_provision_audit.py \
  --root artifacts/170-observer-provision/run-01 \
  --preflight artifacts/170-observer-provision/run-01/preflight \
  --out /tmp/manual170-independent-audit.json
```

robots期限切れ、対象や原本不足、未承認のinterval、時計や担当時間不足、競合があれば見送りにする。今はcache更新もslot予約も行わない。将来の観測は167の1日1レース/1試行・選定順序・固定時刻・停止手順に従い、検査と同じ明示profileを使う。quote30分以内、判断前の両ready、三段階guard、公式払戻の契約を維持する。

接続障害や中断で役割とprivate設定の整合が不明な場合、既存roleのpasswordを自動rotateしたり、private stagingを削除したり、作成を再試行したりしない。公開receiptのphase、DB commit有無、private final/stagingの存在と所有者/modeを確認して停止する。secretを出さず状態を特定してから回復する。今回のrun-01はcommit・profile公開・新role接続がすべて成功済みである。

169の文書/SQL案は当時の未適用結果として凍結している。現在の適用状況はこの170とrun-01のreceiptを参照する。
