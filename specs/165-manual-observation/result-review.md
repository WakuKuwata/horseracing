# 165 実装結果

2026-10-27完了。**手動観測CLIを実装し、通信/DB接続を遮断した試運転で保存一式を検証した。新規収集は開始していない。**

`plan / observe / rehearse / verify / settle / abort` を追加した。将来のobserveは、明示有効化・事前登録SHA・コード/モデルpin・固定時刻窓が一致した場合だけ実adapterを構築する。定期処理と自動再試行はない。

## 一回の実行で残すもの

- fresh read-only queryによる取得前・両予測後・判断直前の発走前/出走馬集合/結果未保存確認。
- 取得開始/受信時刻を伴うオッズ原応答と、そのquoteを直接使ったMEV77列、旧138列と厳密な過去履歴。
- 実旧6本/MEV15本の全member出力、消費入力/モデル/SHA、atomic publish/fsync/readback後の両ready receipt。
- 固定151の同一条件での両腕判断、seal/accepted、実時計wall/monotonic log、runtime、status、全baseline manifest。

取得から判断/保存確認まで30分以内、両予測保存後のfinal確認、判断以前の両保存、発走前と全頭一致を検査する。すべての工程時刻を実時計logと照合する。条件不成立や保存失敗は不採用にし、理由/SHAを残して未採用stagingを除去する。実行中のabortをprocess lockで拒否し、中断回復は既存台帳を保って冪等に行う。race予約は失敗でも解除しない。

精算は保存済み公式HTML/全頭結果/全winner払戻だけを使う。同着は各winner公式額で精算し主評価から分け、結果不足・払戻不足・不整合は両腕null、訂正は連続revision。確定オッズの代用、horse IDの推測変換はしない。

## 試運転と検証

最終 `run-02` は合成3頭のquote/入力と実21モデルを使用し、実時計・実保存先でsealed→verify→native再推論まで成功した。quote受信から判断まで約0.658秒。fixtureは採用不可で、実観測に昇格できない。

取消/出走集合変更、予測後の結果出現、特徴生成失敗の3試運転はいずれも `verified_failure`。acceptedと未採用quote/特徴/予測は残らない。

**関連469件を検証済み**（158〜165の一括466件PASS、一括テスト実行中に追加した独立回復3件も含む独立30件PASS。重複27件を除く）。新規165は124件、既存関連345件。Ruffとdiff-checkも通過。既存feature builderのDataFrame fragmentationに関するPerformanceWarningが1件あり、失敗はない。

fake engine/fetcherで実接続境界とreadonly snapshotを検証し、通常のproduction feature builderも接続した。独立担当は165本体をimportせず、最終packetのSHA/全工程時計/ready/出走集合/判断窓、native21本、混合EV/選択を再計算して一致を確認した。

保存済み公式9レース129頭の原HTMLも別会計replayで検算し、既存の両案800円→2,590円と一致した。現在の合成予測へ過去結果を結合していない。

## 検証できていない範囲

実DBや実HTTPでは動かしていないため、実環境の遅延・DB量・アクセス制約下で固定窓内に収まることは未実証。将来のliveには既存polite fetcherを使い、運用スロットル状態の保存は発生しうる。今回のDBアクセス/新規資料取得/学習/定期登録はすべて0。

全ターゲット行列と履歴は保存するが、歴史raw DB poolは件数とfingerprintのみ。全rawからの独立した特徴再生成を保証するものではない。現在の実レース比較データ追加は0件で、ROI単独対旧予測追加の優劣判断は引き続き保留。

- [手順](manual.md) / [保存仕様](spec.md) / [構成](plan.md)
- [最終report](../../artifacts/165-manual-observation/run-02/report.json) / [verify](../../artifacts/165-manual-observation/run-02/verification.json)
- [一式のmanifest](../../artifacts/165-manual-observation/run-02/session/manifest.json)
- [独立レビュー](evidence/independent-review.md) / [独立検算](evidence/independent-audit-run-02.json)
- [提出時点のSHA台帳](evidence/delivery-receipt.json)
