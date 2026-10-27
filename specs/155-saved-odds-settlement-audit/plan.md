# 155 実装計画

DB抽出はtraining/.venv、解析・テストはBeautifulSoupを備えたscrape/.venv、pandas/pyarrow/sqlalchemy/psycopg/BeautifulSoup。既存依存だけ、1process・各1thread。新規155ファイルのみ追加、既存dirty領域を保護する。

1. export.pyでschemaにある必要列を明示したSQLを固定し、2007以降・監査日までをREAD ONLY/REPEATABLE READで抽出。SQL/transaction設定/観測時刻/行数/各parquet SHAをmanifestへ保存。接続情報は保存しない。
2. archive.pyは保存gz/url.txtだけを読み、各版のhash/時刻/結果有無/parse状態と最新結果表の全頭/払戻を返す。DB修復やpipeline関数を呼ばない。
3. analysis.pyで上記独立品質軸・年別coverage・保存snapshotと現DBの差を出す。小さい合成例でexact/near/同着/100円・欠落・重複・集合不整合・時刻不明を検証する。
4. source/spec/plan/test・DBexport・全使用アーカイブ・既存研究入力をprotocol/SHAへ固定して監査を実行。異常の発見で購入条件や旧研究を修正しない。
5. 独立担当が新実装をimportせず全レース品質/主要件数と既知異常を再計算。結果レビューに監査済み範囲と証明できない範囲を分けて記録する。

独立設計意見（design_prediction_ablation）を採用: 完全一致と±10円を区別、同着と返還/特払は別、結果coverageはID交差、全頭確定には原HTML照合、取得時刻proxyと真正quoteを区別、research/current/archiveを別層として扱う。来歴反証役odds_provenance_reviewは結果上書きが部分的でもrace時刻が付くこと、NULL公表時刻が確定証明でないこと、実購入精算とattentionの公式払戻精算が別経路である点を指摘した。
