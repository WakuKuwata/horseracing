# 166 実DB読み取り専用の実環境検証結果

2026-10-27完了。**保存済み実DBをread-onlyで使い、手動CLIの主要処理が完走することと所要時間を確認した。新規収集・DBデータ変更・学習・定期登録は行っていない。**

通常の165 `observe` はHTTP取得を含むため起動せず、166の手動benchmarkから165の実guard/実reader/実builder/実モデル/耐久保存を使用した。過去レースを発走前へ偽装して165のacceptedを作っていない。

## 計測結果

正式対象は `artifacts/166-readonly-db-runtime/run-03`。事前固定した202605040907（2026-10-25、15頭）を、保存済み発走前chaos snapshotと現在保存の全履歴で再実行した。新規モデル学習やmaterialized feature cache、pool短縮はない。

| 工程・指標 | 実測 |
|---|---:|
| 外部timeによるprocess全体（report書込/終了まで） | **60.55秒** |
| 内部process（最終report書込前まで） | 60.405秒 |
| 取得前確認〜入力生成/両推論/保存〜判断不採用記録 | **59.069秒** |
| 起動から主工程終了まで（準備窓との比較に使う） | **59.941秒 / 360秒** |
| 全legacy pool読込 | 6.250秒 |
| 全MEV pool読込 | 6.751秒 |
| 元source fingerprint | 9.248秒 |
| 旧138特徴生成 | 5.522秒 |
| MEV77特徴生成・入力結合/原source hash | 20.933秒 |
| product source fingerprint・特徴保存 | 9.711秒 |
| 旧6本初回推論＋耐久保存 | 0.272秒 |
| MEV15本初回推論＋耐久保存 | 0.128秒 |
| 旧/MEVのwarm推論＋保存（別計測） | 0.227秒 / 0.088秒 |
| 取得前 / 予測後 / 判断直前guardのsnapshot開始〜確認 | 0.002229 / 0.005676 / 0.002524秒 |
| process peak RSS | **4,313,186,304 byte（約4.02GiB）** |

Apple M5 Pro、物理メモリ24GiB、macOS arm64、Python3.12.13/training環境。モデルは各1thread、process初回のnativeモデルロードを含む。OS/filesystem cacheは制御していない。warm値は推論/保存だけであり、全履歴再生成の時間ではない。

入力は2007-01-01〜2026-10-25の**68,500レース、race_horses970,677行、race_results965,618行、horses177,726行、MEV started flat935,057行**。データ読取/特徴生成/整合性hashが時間の大半で、21本推論/保存は約0.4秒だった。

同日の結果はcopy上だけでlegacy466行を除外、MEV455行の着順/結果状態/上がり/着差をmaskし、その項目と元target結果を保存した。現在保存のtarget最終価格は入力へ流さず、元発走前snapshot価格に置換した。全ターゲット入力行列・厳密な過去履歴・member出力・実ready時計は一式に保存した。

## 条件と読み取り専用の確認

接続既定とtransactionのread-onlyがon、repeatable-readであることを実DBで確認した。DDL/DMLをSQL境界でも拒否し、read-only COMMITは同transactionの設定を再検査して終了記録を残した。166の明示snapshotはrollback。HTTP/Python socket/取得入口を遮断し、取得試行0件だった。

SQL台帳54行のうち、49行が正常完了、5行はpandasの既知のclient型照会例外だった。固定metadata SQL・厳密な型/key集合・SQLSTATEなし・同接続/同transaction/隣接/同phaseの本SELECT成功の一致に限り分類し、**未処理のSQL失敗は0件**。一般のSQL失敗を成功扱いにしたわけではない。

3レースの実guard診断と本処理3段階では、元post・現在結果ありを保持して拒否した。未改変165のactual feature readerも `target_results_already_present` で停止することを実DBで確認した。

元quoteは2026-10-25T00:20:05.686758Z、再実行は10/27。元時計を現在へ付け替えず、約185,257秒の古さ、発走済み、当時の判断より遅い両ready、raw HTTP/取得interval欠落、当時as-of特徴版の未証明を不採用理由へ残した。**取得30分以内・判断前の両予測保存条件は緩和しておらず、比較追加は0件。**

公式単勝は保存済み公式HTML/元判断9レース129頭を別に再照合し、両案8点800円→公式2,590円の既存会計と一致した。現在再推論へ過去結果を結合しない。現在replayの払戻/損益はnullで、モデル優劣の結論は保留を維持する。

## 検証と限界

**新規84件＋既存関連469件、合計553テストが一括通過（113.62秒）。** 新規はDB境界51件と独立攻撃33件。Ruff/diff-checkも通過した。既存feature builderのDataFrame fragmentationに関するPerformanceWarningが1件あり、計算失敗はない。

独立担当は166/165本体をimportせず、SHA、全phase/実時計/ready/順序、元quote時計、同一全頭、read-only設定/SQLhash/metadata限定分類、native15+6本、旧isotonic/補正と保存公式9件を再計算して一致した。独立監査補助のpandas3 null表現だけを補正し、実モデルや保存証拠は変更していない。

今回の一式は処理時間の検証用current replayであり、当時のas-of入力の復元でも真の発走前観測でもない。raw歴史poolは件数/fingerprintで、全値保存ではない。実HTTP取得・robots/rate-limit待ち、判断窓までの実待機、発走前の動的取消/更新、複数レースの同時処理は未計測。単一workloadの約60秒という結果だけで将来の最大遅延や実運用成功は保証しない。

処理は今回の360秒準備窓に収まった。実レースの完全な比較記録を増やすには、既定停止のままの165観測入口で、元quote/両予測/三段階確認/readyをその場で残す必要がある。今回はその収集開始を行っていない。

初期run-01（readonly COMMITを一律拒否）とrun-02（完走約60.42秒、metadata例外分類前）も診断記録として保存した。正式判定はsourceを固定し直して再計測したrun-03。既存165/162〜164のsource/成果物は変更していない。

- [実行手順](manual.md) / [要件](spec.md) / [計画](plan.md)
- [計測台帳](../../artifacts/166-readonly-db-runtime/run-03/timing.json) / [外部process計測](../../artifacts/166-readonly-db-runtime/run-03-process-time.txt)
- [不採用理由](../../artifacts/166-readonly-db-runtime/run-03/admission.json) / [DB設定・SQL台帳](../../artifacts/166-readonly-db-runtime/run-03/database-audit.json)
- [manifest](../../artifacts/166-readonly-db-runtime/run-03/manifest.json) / [独立監査](evidence/independent-audit-run-03.json)
- [独立レビュー](evidence/independent-review.md) / [関連テスト](evidence/related-tests.txt) / [SHA台帳](evidence/delivery-receipt.json)
