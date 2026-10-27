# 163 実装結果

2026-10-27完了。**①両実モデルへの接続、②取得処理を常時停止したアダプタ、③実時計とローカル保存先での検証を実装した。新規収集・DB接続・学習・定期実行は開始していない。**

MEVは数値66＋カテゴリ11＝77列を消費し、実15本体を平均する。旧予測は138列と明示的な過去出走履歴から、実6本体の校正/補正を通して平均する。全raw特徴・変換後行列・member出力・履歴/補正入力・モデル/source SHAを保存し、保存後の全再推論結果が一致することを確認した。保存133の実入力16頭・340履歴に対する旧予測parity、およびMEVの既存product経路とのparityも専用テストで通過。

初回のモデル接続smokeはloaded Boosterのnative reset操作付近でexit139となったため、独立diagnosis後にresetを使わない経路へ修正。MEVはpredict時のnum_threads=1、旧は保存済み1threadのproduct経路で実行し、修正後のモデルテスト/統合/再推論では再現しなかった。原因の断定はしない。既存本体やproductsourceは変更していない。

保存/読込でdict順が変わることによる消費行列照合の失敗は、artifact定義順への列固定とJSON roundtripテストで解消した。公式replayの馬番keyもJSON上のstring表現へ固定し、読み戻し一致を確認した。どちらも不整合時は採用/検証を拒否しており、誤った前向きデータを作っていない。

## 完成試運転 run-03

ローカル生成の合成3頭・仮の未来postを用い、実時計UTC wall+monotonicで観測した。

| 項目 | 結果 |
|---|---|
| 実モデル | MEV15本＋旧6本 |
| 予測・全入力の保存確認 | 判断以前に完了 |
| quote完了→seal保存確認 | **1.617920秒**（30分以内） |
| 判断時刻 | 2026-10-27 10:09:14.498103 JST |
| seal保存確認 | 2026-10-27 10:09:14.974199 JST |
| 保存後の全再推論 | 全頭確率/消費証跡が一致 |
| 現在仮予定の公式精算 | 結果なしのためpending、両腕の払戻/利益null |
| 新しい比較対象 | **0件** |

現在作ったfixtureを実配信quoteと呼ばず、`local_fixture / synthetic_post=true / eligible_for_prospective=false`を全成果物へ保持した。過去quoteのファイル読込時刻を取得時刻に置き換えていない。停止中fetchは有効化引数や属性書換えでも通信できない。

過去9レース/129頭の公式精算は別の `historical_settlement_only` として再生。保存公式原文のSHA/馬番/額を検算し、両腕とも8点・800円・公式払戻2,590円という既存会計に一致した。過去の取得/判断時計を変更せず、現在sessionへ過去結果を後付けせず、過去に存在しないready/seal receiptも作っていない。新たな有用性評価ではない。

## 検証

新規90件（モデル16、取得41、独立攻撃14、接続/保存19）＋関連既存168件、**計258件通過**。Scoped Ruff通過。162完成run-04も凍結したままverify通過。

独立担当は主163のモデルアダプタ/selector/store/settlerをimportせず、実21本体の再推論、カテゴリ/TE/isotonic/補正/平均、全時計・全頭・SHA、両案の確率/EV/買い目、保存公式HTML単勝額での過去会計を再計算した。

停止中取得/SQLAlchemy・SQLite/socket/DNS/subprocess入口、30分境界/配信時刻/元時計、未来予測・行後更新・旧体重情報、実時計逆行/drift、ready改変、入力/出力/変換行列の改変、未来の公式観測時刻の拒否を確認。封印/実公式精算の規則は維持している。

## 残る境界

実モデルを動かすところまでは確認したが、現在の試運転入力は明示的合成値であり、実レースの最新履歴・発走状態・全頭情報を取得して組み立てる処理はまだ接続していない。実時計検査もローカル環境での保存順序の確認で、配信元の真正性・実約定可能性の確認ではない。

将来の実データ品質試運転へ進む際には、実as-of特徴入力と実レースguardを同じ契約へ接続する必要がある。今回の完了により収集・定期運用が自動的に有効になることはない。

- [保存/接続契約](spec.md) / [実行手順](plan.md)
- [完成report](../../artifacts/163-offline-model-connection/run-03/report.json) / [manifest](../../artifacts/163-offline-model-connection/run-03/manifest.json)
- [現在予定の未精算記録](../../artifacts/163-offline-model-connection/run-03/pending-settlement.json) / [過去公式精算の別replay](../../artifacts/163-offline-model-connection/run-03/historical-official-replay.json)
- [独立レビュー](evidence/independent-review.md)
