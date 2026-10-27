# 169 live接続・必要権限・取得停止状態のpreflight

2026-10-27。新規収集なしで、保存済み設定と読み取り専用catalog照会から接続/権限不足を調べ、preflight専用CLIを追加する。165〜168は凍結する。

## 境界

- CLIはinit-profile/inventory/check/verifyのみ。observe/refresh/GRANT/schedulerを起動する経路を作らない。can_collectは全結果でfalse。
- liveのcheckは明示private profileまたは現環境のDATABASE_URLだけを使う。OWNER/fallbackは使わず、Compose内部hostをlocalへ書き換えない。秘密値は成果物/例外/consoleへ出さない。
- administrative inventoryは既存166のread-only fallbackを使うが、live接続確認とは分離する。接続既定readonly強制onをlive実効defaultの証拠と呼ばない。
- 将来の有効profile検査は、SQL gateをconnect前に付け、最初のアプリstatementでtransaction READ ONLYとする。native defaultをstartup readonly overrideで隠さず、実効default/実login/DB/ACLを検査しrollbackする。今回は有効な専用profile/roleがない場合、その実測は未実施のままにする。
- 権限は基本5表SELECT、throttleだけSELECT/必要列INSERT3・UPDATE4。CONNECT/USAGE/column ACL/builtin/roleflag/membership/search_path/RLS/triggerを確認する。実write/row lockのprobeはしない。
- robots既存JSONを厳密直読し、更新/lock/prewarmをしない。throttleはcanonical sourceの現状をSELECTし、予約/解除をしない。現状と予定起動時のTTL/競合を分ける。

## オフラインで進めるもの

安全な設定loader、0600/上書き禁止のprivateひな型、独立した接続先記載、読み取り専用preflight、最小権限contractと未適用SQL、未設定/過大権限/期限切れで止まるテスト、起動直前の未確認台帳。

role作成/パスワード発行/GRANTや接続先の確定は、この確認だけで適用しない。稼働local clusterとdeploy保存設定の対象が異なる可能性を保持する。最低権限の具体SQLをレビュー可能な形で用意し、必要な変更と現在の未充足を明示する。

取得30分以内、判断前の両予測耐久保存、3段階guard、固定判断/封印窓、公式原文での精算は維持。今のcheckを通しても、未選定race/担当者/quote/readyを確認済みにしない。
