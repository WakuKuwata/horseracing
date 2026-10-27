# 保存済みDB検証CLI

repo rootで次を実行する。実DBに接続して保存済みデータを読むが、外部HTTPと取得入口は遮断される。`--enable-collection`は存在しない。出力directoryは必ず未使用の場所を指定する。

```bash
training/.venv/bin/python -m scripts.roi_explore.db_runtime_166.cli benchmark --out /tmp/manual166-db-benchmark
training/.venv/bin/python -m scripts.roi_explore.db_runtime_166.cli verify --out /tmp/manual166-db-benchmark
```

`benchmark`の対象は固定の202605040907、quoteは既存chaos snapshot、poolは2007-01-01〜2026-10-25。DB URLは環境の`DATABASE_URL`、未指定時は155と同じlocal PostgreSQLを使う。秘密URLは出力・保存しない。外部hostnameのDNSは遮断されるため、現在のlocalhost/native接続を前提とする。

接続時から `default_transaction_read_only=on`、statement timeout120秒、lock timeout2秒、connect timeout5秒。SQL allowlistとread-only設定の両方を検査する。165のfresh guardをそのまま呼ぶため、readonly transactionの正常終了COMMITは設定を再確認して監査し、166の明示snapshotはrollbackする。

`verify`はDB接続も遮断して動く。manifest/source/model pin、全phase/実時計、入力消費、全memberと平均、過去履歴、ready順序、元quote時刻、DB設定/SQL台帳、固定条件/不採用flagを再検証する。

## 成果物

- `protocol.json`: 事前固定した対象/時刻条件/source/model pin。
- `diagnostic/`, `before.json`, `after.json`, `final.json`: 実DB query結果と元post、発走済み/結果ありの拒否。guard所要時間は開始からchecked_atまで。
- `production-reader-rejection.json`: 未改変165 readerが既存結果を拒否した記録。
- `saved-quote-source.json`, `quote.json`: 元のchaos field/captured_atと現在ID照合。raw HTTP原文/取得intervalの欠落を保持する。
- `source.json`, `replay-mask.json`: full pool件数/fingerprint、同日結果のmask対象と元target結果。全raw歴史poolの値は保存しない。
- `features.json`, `prediction_*.json`, `*.ready.json`: 全ターゲット行列/履歴、実6/15 member、入力binding、耐久保存完了の現在実時計。
- `progress/`, `timing.json`, `clock.json`: 工程別のwall/monotonic、準備窓との比較。内部process時間は最終report書込前までなので、正式実時間は併記する外部`/usr/bin/time -l`値を使う。
- `database-audit.json`: SQL形状/SHA/パラメータ型/所要時間、read-only設定と終了記録。パラメータ値は保存しない。
- `admission.json`: 古quote/発走済み/過去ready不足等の理由、不採用・比較追加0。
- `current-db-official-payouts.json`: 公式保存行の確認。現在replayの払戻/損益はnull。
- `historical-official-replay.json`: 元の保存済み判断へ結びついた公式9レースの別検算。

pandas 3.0.3の`read_sql`はSQLオブジェクトを一度テーブル名として問い合わせ、型変換例外を内部で処理して本SELECTへ進む。固定metadata SQL SHA・SQLAlchemy Select/TextClause型・SQLSTATEなし・同接続/同transaction/直後/同phaseの本SELECT成功が全て一致する場合だけ`handled_metadata_probe`として記録する。他のSQL失敗は緩和しない。

今回のbenchmarkは、保存済み過去レースを扱うため165の`observe`収集入口を起動しない。過去postを未来へ書き換えてsealしたり、保存quoteを「今取得した」と付け替えたりはしない。実時計で生成した予測と元の公式結果を結合して新しいROI比較を作ることもない。
