# 170 専用role・live設定の独立レビュー

2026-10-27。ユーザーは接続先確認後の専用role作成・最小権限付与・秘密の安全保存・実接続preflightを明示的に依頼している。今回の変更範囲にrole/GRANTは含まれるが、新規収集・robots更新・slot予約は含まれない。独立担当はDB接続、role変更、HTTP、モデル推論、private設定の読取をしていない。

## 事前設計の確認

- 接続先はhost/portだけで確定せず、実clusterのsystem_identifier・DB OID/名前・server接続情報・既知の保存race・Dockerのport/volume情報を照合する。Compose内部設定をlocalへ黙って変換しない。role変更の同じtransactionでidentityを再照合し、予期しない既存roleや競合は拒否する。
- 169の契約を維持する。基本5表のSELECT、DB CONNECT/schema USAGE、fetch_throttle_stateのSELECTおよび必要列INSERT3/UPDATE4だけを明示grantする。sequence/DELETE/DDL/モデル・結果・購入tableへの書込を与えない。既存roleの権限やpasswordを黙って変更しない。
- 専用roleはNOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS、membershipなし。liveの通常transactionでthrottle操作できるnative defaultを設定する。特徴/guardは既存の明示READ ONLYを維持する。
- 秘密は十分な乱数をローカルメモリで生成し、0600のignored private profileだけへ保存する。SQL、console、例外本文、public artifact、秘密を含むファイル/文字列のhashへcleartextやverifierを出さない。psycopg/libpqの固定SCRAM-SHA-256 client暗号化を使い、認証秘密のSQLログにも注意する。
- private fileとDB commitは一つの原子操作にならない。private stagingを先にfsyncしてからrole transaction、commit後にatomic profile公開とする。中断状態ごとに秘密を含まないreceiptを残し、既存roleをrotateしたりprofileを消したりして回復しない。DB commit済み・file公開前も秘密が失われないようstagingを保全する。
- provision中のSQL監査はrole/GRANTの形状だけに限り、PASSWORD verifierを含むstatementは保存しない。サーバstatement/duration/error/parameterログ、追加audit/event triggerの経路を確認し、抑制できない秘密出力があれば停止する。
- source intervalは確認できた既存値を短縮しない。保守的な60秒を採用しても、未確認のactive設定がさらに大きい可能性まで否定しない。承認値/競合の未確認はlive開始条件として残す。
- 実接続preflightは新専用roleで行い、session/current user・DB・native default、全必要列ACL・過大権限・schema/RLS/operational triggerを照合する。認証確認成功を収集開始許可と同一視しない。robots期限切れ、未選定race、quote30分、両ready前Dは別gate。

## 検証予定

公開されたtarget/provision receipt・最終preflight・manifest/SHAを直接読み、専用role/必要列だけの権限、private fileのmode/非公開、収集0、未到来条件の保持を確認する。秘密値を知るための読取はしない。完成時の結果を追記する。

## 公開された接続先確認の確認

root保存の `target-readonly.json` と `runtime-endpoints.json` を直接読んだ。API/opsの既存process設定はlocal15432/horseracingに一致しており、read-only確認はsystem_identifier `7624360747090018342`、DB OID `5987755`、既知保存raceの15頭/16頭と結果件数を記録している。これは実在する保存済みアプリDBの確認であり、Compose内部hostの無断置換ではない。独立担当が別DB接続して再照会したという意味ではない。

必要列だけのSELECTへ限定する案も、169の通常loader/MEV SQL/guard/ID mapの消費列と整合した。単なるtable_select=falseを不足と扱わず、全必要列の実効ACLで確認する。source intervalは既存processの選択した環境キーが未指定で、保存repoの既定1秒を確認した範囲。新しい手動profileへ60秒以上を使っても、運用者が承認した未知の値まで確認済みとはしない。

## 実適用後の独立監査結果

`independent_provision_audit.py` を新170の公開 packet に実行し、`independent-audit.json` は `independently_verified`。provision モジュールや169の判定関数を import せず、JSON、SQL本文、列集合、SHA、時系列、公開robots cache本文から再照合した。秘密profileは内容を読まず `lstat` だけで regular/0600/nlink1を確認した。

- 接続先の再確認後、専用role作成・最小列GRANT・DB commit・profile atomic公開の4段階が順序どおり完了。SCRAM verifier生成方式とログ抑止guardを実装レビューし、公開artifactにpassword/verifier/接続URL本文がないことを検査した。秘密値のhashも公開しない。
- `hr_manual_observer` は非管理者・membershipなし。6表の読取可能列が169契約と完全一致し、writeはthrottle INSERT3列/UPDATE4列だけ。native defaultはoff、preflightの実transactionはrepeatable read/read-onlyで、SQL20件とrollbackの記録が整合。
- 追加のread smokeは6表の必要列投影がすべて成功。列SELECTだけで既知raceの `count(*)=15` も成功した。publicの全27 relationとsequenceの実効ACLを照合し、契約外表への読取や書込、DELETE/TRUNCATE/sequence権限がない。read-only SQL11件とrollbackを確認。
- raceデータ変更・throttle状態変更・slot予約・robots更新・モデル推論・新規収集はいずれもなし。前後のthrottle digestは一致。独立担当はDB再照会をしていないため、この確認は保存された実行証跡の独立再計算による。
- preflightの設定・ACLはPASSだが総合状態は `NO_GO`。保存robots cacheは期限切れで、更新せず停止している。対象race、実行時刻・operator、fresh guard、判断時quote age30分以内、両予測のdurable保存完了、decision/seal期限、将来の公式資料・全勝者払戻は実観測前後の未到来gateとして残る。

HBAがtrustの場合も専用role接続自体は成功しうるので、公開結果だけからwire上のpassword challengeを検証済みとはしない。SCRAM verifierの安全な保存と新roleによる実接続成功を確認した、という範囲で扱う。HBA設定変更や負試行は実施しない。
