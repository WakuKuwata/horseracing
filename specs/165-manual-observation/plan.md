# 165 実装計画と構成

実装は `scripts/roi_explore/manual_observation_165/` に閉じる。既存API、旧予測モデル、162〜164のmodeを変更しない。

| module | 役割 |
|---|---|
| control.py | 事前plan、source/model pin、収集前preflight |
| adapters.py | lazy engine/fetcher、fresh read-only guard、既存polite fetcherへの接続 |
| features.py | 実product builder、same-snapshot履歴/入力、明示ID map |
| backends.py | 型とoriginを固定したLive/Fixture adapter |
| session.py | 一式の排他保存、clock/順序検査、封印、独立再推論、失敗/中断回復 |
| settlement.py | 公式全頭/全winner検証と両腕会計 |
| cli.py | plan/observe/rehearse/verify/settle/abort |

Python環境は `training/.venv/bin/python`。DB/HTTPは遅延初期化で、`observe --enable-collection`以外は接続遮断context内で動く。実DB読取はread-only。将来のfetchには既存robots/rate-limit処理を使い、HTTPキャッシュを使わない。既存fetcherの運用スロットル状態保存は発生しうるため、live全体をDB完全無書込とは呼ばない。

## 実施順

- 独立担当と保存/工程設計を比較する。
- fake engine/fetcherによる境界・snapshot・clockテストを作る。
- 旧138/MEV77の既存builder、実6/15member、151固定混合へ接続する。
- 実時計と実filesystemで、通信を遮断したfixture試運転と失敗ケースを実施する。
- 保存済み公式9レースを別会計replayとして検算する。fixture推論と過去公式結果を結合しない。
- 158〜164関連テストと165テストをまとめて実行し、独立攻撃/成果物監査を確定する。

実行手順は[manual.md](manual.md)。結果は[result-review.md](result-review.md)。収集開始は今回の範囲外。
