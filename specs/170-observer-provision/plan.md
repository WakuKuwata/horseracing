# 170 専用observer roleとlive接続設定の適用

2026-10-27。ユーザーが接続先確認後の専用role作成・必要最小限GRANT・秘密値保存・実接続preflightを明示的に指示した。新規収集は禁止を維持する。

## 確定した対象

稼働API/opsの設定はlocalhost:15432/horseracing。Docker project=docker/service=postgres、volume=docker_postgres_data、15432→container5432と照合する。既存166/168/169の保存済みDBを同じendpointで読取り、database OID5987755、system_identifier7624360747090018342、primary、既知2レース15/16頭の整合を確認した。deploy/.envのCompose内部db:5432を変換する作業ではない。

## 適用範囲

- 同じtransactionでcluster/DB identityを再照合し、同名role不在を確認する。OS profile lockとDB provisioning advisory lockを使う。
- 新roleはhr_manual_observer。NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOINHERIT/NOREPLICATION/NOBYPASSRLS。DB CONNECT/public USAGEと169 contractの必要列SELECT、throttle INSERT3列/UPDATE4列だけを付与する。基本データ/予測/購入にwrite権限を追加しない。
- passwordはメモリで48byte由来tokenを生成し、client側固定SCRAM-SHA-256 verifierをDBへ送る。cleartextをSQLへ送らず、statement/error/duration/sampling logを接続開始時から抑制し、未知extension/eventtrigger/loggingがあれば停止する。secret/verifier/profile本体やそのhashを公開成果物へ保存しない。
- 0600のprivate stagingをfsync/readbackしてからDBを変更する。commit後、既存placeholder一致を確認しprivate profileをatomic publishする。中断・commit結果不明・既存role等はRECOVERY_REQUIREDとし、再作成/rotateを自動で行わない。
- 新manual profileのintervalは確認できた値を短縮せず60秒以上とする。現API/opsにinterval環境指定はなく、保存現行codeのdefaultは1秒。これは全運用の承認値を証明するものではないため、収集開始前の最終確認を残す。
- robots更新、throttle source slot予約/状態write、レースデータ更新、live plan/observe/予測/精算/定期登録は行わない。

適用後は169 checkをそのまま実行し、新role/session/native default/列ACL/read-only transaction/robots/throttleを検査する。別途、必要列のLIMIT0とCOUNT(*)を実read-only接続で確認し、不要table書込権限がないことをcatalogで確認する。190件の既存関連テストと新provision/private helperを検証し、独立担当が秘密file本体を読まず公開証拠から監査する。

取得30分以内・判断前の両予測durable保存・三段階guard・固定判断窓・公式原文精算は変更しない。今回の接続整備の成功は実レース観測GOや収集開始を意味しない。
