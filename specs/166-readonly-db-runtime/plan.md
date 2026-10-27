# 166 実DB保存データによる処理時間検証

2026-10-27。新規収集を行わず、165を凍結したまま166の手動benchmark CLIを追加する。AGENTSに従い独立設計意見を取り、過去DBの実ゲート拒否と現在再実行の性能計測を分ける。

対象は計測前に固定する。全工程計測は現在UIでも参照されている202605040907（2026-10-25、15頭）、guard診断は同レースと202604040304/202609040611。オッズは既に存在するchaos snapshot `eed8ee10-ece7-44fb-9c83-9590a935bf42` の原field/元captured_atを使う。確定オッズは判断材料にしない。

## 実行境界

接続時から default_transaction_read_only=on、各snapshotをrepeatable-read/read-onlyとし設定証拠とSQL/rollback台帳を保存する。DDL/DMLは禁止。read-only transactionの正常終了COMMITは、同transactionの設定を再検査して監査記録へ残す。Python socket/HTTP/165 quote fetch入口を遮断し、native libpqのDB読取だけを許す。localhostは127.0.0.1へ固定してPythonDNSを使わない。URL秘密値は保存しない。新規取得、運用スロットル更新、DB更新、学習、定期実行はしない。

165のactual guardを3段階で呼び、過去postと結果ありの拒否を保存する。actual DatabaseFeatureReader.captureも既存結果のため拒否することを確認する。拒否条件を緩和して165のacceptedを作らない。

## 現在再実行の範囲

全2007年以降poolを実DB snapshotで読む。現在保存状態から過去as-of版は復元できないので、同日の結果をメモリ上のcopyで明示的にmaskし、165と同じ旧138/MEV77 builder、実6+15モデル、全行列/member/ready保存を現在の実時計で計測する。targetの現在保存確定オッズはMEV入力で必ず元の発走前snapshot価格へ置換する。ID差は現在の明示mapだけで照合し、当時のmapping証拠とは扱わない。

元quoteのHTTP原文/取得開始終了の間隔が保存されていないことも保持する。古いcaptured_atを現在へ付け替えない。現在の予測readyは歴史判断より後なので、採用不可・比較追加0・現在replay精算nullを固定する。公式払戻は保存公式資料の既存会計replayで別検算し、現在再推論と過去結果を結合しない。

## 計測

接続/guard、全pool読取、元/消費source fingerprint、mask、旧138、MEV77、nativeモデル初回ロード推論、canonical/SHA/fsync/readback/ready、after/final、判定拒否までをphase実時計+monotonicで記録する。現工程一式とprocess全体を分ける。warm再実行はモデル推論/保存のみで全特徴再生成ではない。OS filesystem cacheは制御しない。実HTTP取得・rate-limit待ちは未計測。

165の準備窓360秒、各guard5秒、判断窓60秒、quote1800秒と照合するが、保存データ計測だけで将来liveの成功を保証しない。SQL statement timeout120秒、lock timeout2秒、connect5秒。単一process/単一モデルthreadで実施し、重い全履歴builderの失敗もそのまま記録する。

## 成果物

`artifacts/166-readonly-db-runtime/run-01`以下へprotocol/sourcepin、全phase進捗、DB設定/SQL台帳、原保存quote/IDmap、mask対象、全ターゲット入力/履歴、全member、ready/clock/runtime、status/timing/manifestを保存する。既存165/162〜164の成果物は変更しない。異常が出た場合は元runを保持し、独立diagnosis後に修正版を別runへ作る。
