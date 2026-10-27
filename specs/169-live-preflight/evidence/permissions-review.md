# 169 live165必要権限の独立レビュー

2026-10-27。ソースを独立に確認した。実DB照会・ネットワーク・権限付与・モデル実行は行っていない。既存165〜168のソースを変更していない。

## 最小の実行範囲

未改変165の観測は、DBのCONNECT、public schemaのUSAGEと、`races / race_horses / race_results / horses / id_mappings`のSELECTを使う。guardは最初の3表を読み、旧特徴loaderは最初の4表、MEVのraw SQLも最初の4表、明示ID対応はid_mappingsを読む。必要列は同梱permission-contract.jsonに列挙した。

fetcherは同じengineを使い、`fetch_throttle_state`にSELECT/INSERT/UPDATEを行う。source keyはrace.netkeiba.comのURL文字列ではなく`netkeiba.com`。新行はdomain/next_allowed_at/updated_atを明示INSERTし、PK(domain)に対するON CONFLICT DO NOTHING。その後SELECT FOR UPDATEで全5列を読み、予約時はnext_allowed_at/updated_at、拒否時はblocked_until/block_reason/updated_atをUPDATEする。シーケンス/DELETE権限は不要。表にserial/identity列はない。

`SELECT ... FOR UPDATE`にはSELECTに加えて更新対象表のUPDATE権限が必要。列単位UPDATEを検査する場合、必要4列の権限がそろうことを確認する。INSERTも必要3列を検査する。テーブル単位has_table_privilegeだけでは、十分な列単位grantを誤って不足と判定する可能性がある。

公式精算は165 CLIの保存ファイル処理であり、観測最小権限にofficial_win_payoutsやモデル予測表へのSELECT/INSERT/UPDATEは含まれない。別DB精算監査やpreflightの便宜でそれらを読む場合は別scopeの任意読取権限として示す。

## 既存設定の位置付け

`deploy/scripts/migrate.py:46–54`はAPI用roleへ全表SELECTを与え、全表INSERT/UPDATE/DELETE/TRUNCATEをREVOKEする。したがってapp_roの設計はAPI読取に適するが、同一engineでthrottle状態を書くlive165にはそのままでは不足する。API roleを広げる、owner/superuserへfallbackする、という救済は今回の確認には含めない。

専用role案は `provision-observer-role.draft.sql` に示した。未実行のレビュー資料であり、role/GRANTを適用したとは扱わない。パスワードをSQLや研究出力へ保存しない。新専用roleはbase5表/運用表SELECTと運用表の必要列INSERT/UPDATEだけを与え、予測/特徴/競走データへの書込は与えない。PUBLICや既存membershipからの追加権限も別に実効確認する。

## 実効default read-onlyの確認

166のread-only検査接続はstartup optionsでdefault_transaction_read_only=onを強制する。そこでonと確認できても、未改変165 live接続の実効defaultがon/offどちらかを証明したことにはならない。

165のguard/特徴snapshotは明示READ ONLYだが、throttleは通常transactionを開くだけでREAD WRITEを指定しない。このためliveの実効defaultがonなら、ACLがそろっていても状態更新は失敗する。保存DSN query options、PGOPTIONS、role/database設定、standby状態を確認する必要がある。

安全な実測は、同じ保存DSN/環境を使い、任意アプリqueryより前にtransactionを明示READ ONLYへ設定した接続で、SHOW default_transaction_read_onlyとSHOW transaction_read_onlyを分けて保存する方法。SQLAlchemyの接続初期化SELECT/SHOWもSQL allowlistと監査対象にし、書込/SELECT FOR UPDATE/slot予約は試さない。startup READ ONLY強制を維持する方針なら、catalog/DSN/environment証拠だけでは解消しないdefaultをUNCONFIRMEDに保持してよい。

## ACL以外の必須確認

- 全runtime SQLはschemaを修飾しない。search_pathの文字列だけでなく、to_regclass('races')等が期待するpublic表OIDと同じかを確認する。別schemaやtempの同名表へ解決する設定は不適合。
- RLS/FORCE RLSが有効なら、SELECT ACLだけで全頭・全履歴が見えるとは言えない。throttleのINSERT/UPDATE policyも不明ならNO_GO/要レビュー。安易にBYPASSRLSを付けない。
- fetch_throttle_stateに独自trigger/rule/default/制約が追加されていれば、ACLだけで操作成功を証明しない。既存migrationは通常の5列PK表だが、実DB構造を別途確認する。
- `clock_timestamp()`と`txid_current_snapshot()`のEXECUTEは通常PUBLIC経由だが、実ACLで失われていれば不足。sequence権限を広く与えて解決しない。
- LOGIN/CONNECT、schema USAGE、実効role membership、superuser/owner/bypass/CREATE ROLE等の過剰権限も役割判定に記録する。接続できたfallback roleをlive用roleだと呼ばない。
- 実際の予約競合・lock待ち・source cooldown・robotsの起動時鮮度はofflineで保証しない。事前checkはslot予約やfetcher試し打ちを行わず、起動直前の再確認項目を残す。

すべてソースと保存定義に基づく権限契約であり、権限付与や実write/row lockの成功を確認したものではない。取得30分以内・両予測の判断前保存・公式単勝精算の条件を変更しない。
