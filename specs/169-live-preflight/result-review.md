# 169 live接続・必要権限・実行前チェックの結果

2026-10-27。**取得停止のpreflightを整備した。現在はNO_GO。新規収集、live観測、権限付与、robots更新、slot予約は行っていない。**

## オフラインで解消・整備したもの

- `config/manual-observer.env.example`と、gitignore対象の`config/.env.manual-observer`を作成。private fileは0600・上書きなし。既存deploy設定/環境は変更せず、接続先確認false、秘密値/承認済みintervalはplaceholderのまま。
- 明示profileだけを読むloaderを実装。OWNER/fallback、暗黙のPGOPTIONS/PGSERVICE等、DSN query options、Compose内部hostの自動変換、非private/symlink/placeholderを拒否。秘密URL/passwordや任意例外本文をconsole/証拠へ出さない。
- `init-profile / inventory / check / verify`専用CLIを実装。observe/refresh/GRANT/自動起動の入口はなく、すべて`can_collect=false`。
- actual165の権限を列単位で確定。DB CONNECT/schema USAGE、基本5表SELECT、運用`fetch_throttle_state`のSELECT＋INSERT3列/UPDATE4列。sequence/DELETE/予測・結果table書込は不要。
- 専用roleの[未適用SQL案](evidence/provision-observer-role.draft.sql)を用意。接続中DB一致/必須変数/既存role衝突を検査し、単一transactionで部分適用を防ぐ。SQL案をDBへ実行していない。
- robotsの保存元時刻/24時間TTL/Allow/予定起動時失効、throttleのcanonical key/現在cooldown/3秒待ち上限を検査。既存cacheを直接読み、lock・refresh・予約・解除をしない。
- 検査用強制read-onlyと将来liveのnative既定値を区別。設定不成立で未照会のthrottleは「行なし」とせずNOT_CHECKEDを出す。

## 実確認できたことと残る不足

最終正本は `artifacts/169-live-preflight/final-02`。catalog確認は**2026-10-27 14:49:03 JST**（正確な値はJSONのchecked_at/DB時計）、profile検査はその直後。時間依存の状態はあくまで確認時点の記録。

| 項目 | 結果/残る作業 |
|---|---|
| 保存deploy設定 | app_ro向け`db:5432`、Compose内部用。owner URLも存在するがlive検証へ使わず、host側local15432へ黙って置換しない |
| host側調査DB | 稼働containerはCompose project=docker/service=postgres、15432→5432。deploy意図のclusterと同一と断定しない |
| 調査local clusterのrole | 非builtinはaiumaのみでsuperuser/owner等の過大権限。live用に採用不可。このclusterには専用observer/app_roは未作成。別clusterのrole不存在まで断定しない |
| 必要ACL/DB設定 | contractと適用案を確定。実際の専用login、接続先、秘密値、承認済みintervalは未確定/未適用。fallbackの権限が足りても解消済みとはしない |
| 実live profile | privateひな型のplaceholderにより、**DB接続前にNO_GO**。live roleでの認証/ACL/native defaultは未測定。権限不足をownerで補完しない |
| 検査接続のread-only | administrative inventoryは既定/transaction on、repeatable-read、rollbackを実確認。これはliveのnative defaultを測った値ではない |
| robots | 元取得10/26 13:30:43 JST、期限10/27 13:30:43 JSTで期限切れ。保存rules自体はodds pathをAllow。TTLを延ばさず、新規取得をしないので未解消 |
| throttle | netkeiba.comは確認時点でcooldownなし/backlog0。future slot/競合は未確認。source intervalは現環境の既定値を記録しただけで運用承認済みとみなさない |
| profileでのthrottle | 無効profileでDB未照会。NOT_CHECKED_CONFIGURATION_OR_CONNECTION_BLOCKED。inventory側の別接続結果をprofile成功へ流用しない |
| 予定起動 | race/時刻/担当者が未選定。予定時刻を渡した場合もrobotsの既知失効だけ判定し、将来throttle空きを確定しない |

## 起動直前・発走後まで残る確認

専用設定の準備ができても、167の対象race・全頭ID/同日全card、取得5分以内の元出走資料、担当者時間、時計同期/電源/スリープ/負荷/容量を実時点で確認する必要がある。

実URLのrobots許可/cache期限、source cooldown/競合/送信直前の予約、実HTTP時間は現在のcheckで確定しない。preflightが今合格しても、将来の実リクエストを許可したわけではない。

**取得30分以内、判断前の両予測耐久保存、3段階guard、[T−10分,T−9分)の判断/保存確認、公式原文/全winnerの払戻精算は維持する。** quote/ready/公式結果は未到来。今の再推論や読取時計で補わず、不足は未採用/未精算のままにする。

## 検証

新規access53件＋独立config/権限/runner攻撃65件、関連DB境界51件＋168判定21件、**計190テスト通過**。全offline条件がfakeで合格しても収集不可、予定launchでTTL切れならNO_GO、秘密非露出、未設定時constructor未到達、実効defaultと強制probeの区別、最初のアプリSQL READ ONLY、例外rollback、列ACL、role/session binding、過大権限/RLS/trigger、固定条件差替え拒否を確認した。Ruff/diff-checkも通過。

独立担当は169本体をimportせず、最終2packetのmanifest/source/SQL20行・read-only/rollback・既存cache原SHA/TTL/Allow・private file0600stat・設定不成立のlive SQL0行・全停止flagを直接監査してPASS。private file本体/秘密値は監査成果物へコピーしていない。

最初の診断で基本read表のDMLtriggerまで一律に止めていた過剰判定を、独立確認後、実INSERT/UPDATEをするthrottle表のtriggerへ限定した。基本表のRLS/rewrite/search_pathの確認は維持。最終はその修正版で再取得したfinal-02で、旧診断01も保持した。

今回、role/GRANT/password発行、データ書込/rowlock probe、cache更新/期限変更、source slot予約、モデル推論、live plan/observe/settle、定期処理、購入は0。既存165〜168のsource/成果物は変更しない。

- [実行手順と現在の不足](manual.md) / [実装範囲](plan.md)
- [権限契約](evidence/permission-contract.json) / [権限レビュー](evidence/permissions-review.md)
- [catalog確認](../../artifacts/169-live-preflight/final-02/inventory-01/result.json)
- [private profile検査](../../artifacts/169-live-preflight/final-02/profile-check-01/result.json)
- [独立監査](evidence/independent-audit-final-02.json) / [独立レビュー](evidence/independent-review.md)
- [190テスト](evidence/tests.txt) / [提出時SHA](evidence/delivery-receipt.json)
