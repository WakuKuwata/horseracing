# 166 保存済み実DBを使った手動CLI動作・性能検証

ユーザー指示: 新規収集を始めず、実DBの保存データをread-onlyで使い手動CLIの実環境動作と所要時間を検証する。取得30分以内・判断前の両予測保存・公式払戻精算は緩和しない。

## 完了条件

- 接続時からDBの既定read-onlyを有効にし、使用snapshotのtransaction read-only/repeatable-readを記録する。SQL形状/SHA/パラメータ型/所要時間/transaction終了を監査する。DDL/DMLやread-write設定への変更は拒否する。read-only COMMITは同transactionで設定を再検査して許可する。
- HTTP、Python socket、165のquote fetch入口を遮断し、native PostgreSQL読取だけを使う。新規DB書込、収集、学習、scheduler登録は0。
- 対象race/quoteを測定前に固定し、現在保存の全2007年以降poolを読み、materialized feature cacheや履歴期間の短縮を使わない。
- 165のactual guardとactual feature readerが、発走済み/結果保存済みを正しく拒否することを実DBで検証する。
- 別originのcurrent replayに限り、同日結果をcopy上で明示maskして既存product builderと実21モデルを実行する。ターゲットの現在保存最終価格は発走前snapshot価格へ置き換える。元quote時計/postを変更しない。
- 全ターゲット特徴/履歴/消費入力/member/保存完了を実filesystemへ残す。各readyを実時計と結合し、順序と再現をofflineで検査する。現在の再推論は当時判断の予測保存証拠にしない。
- 全pool読取、fingerprint、builder、モデル初回ロード/推論、canonical/SHA/fsync/readback/readyを計測する。process全体はOSの外部timeでも測定する。warmモデル反復は別記し、全特徴再構築の所要時間として使わない。
- 360秒の準備窓・5秒guardと比較しつつ、実HTTP/rate-limit待ちや時間待機が未計測であることを残す。標本数1レースで将来の最大遅延を保証しない。
- 現在replayを過去の公式払戻で採点しない。公式会計は保存済み既存判断/原HTMLの別replayで検算する。比較追加0・採用不可・現在replay精算nullを固定する。

## 判定

古いquoteや当時のready不足を現在の時刻で補完して通過させない。全工程が計算上成功したことと、当時の比較採用条件がそろうことは分ける。実環境検証の成功でもモデルの優劣判定は保留を維持する。
