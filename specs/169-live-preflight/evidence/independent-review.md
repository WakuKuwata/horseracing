# 169 live接続・実行前チェックの独立レビュー

2026-10-27。独立担当は165のactual backend/adapter/feature reader、166のread-only境界、168のGO結果と、共通politeness/robots実装を読んだ。DB・HTTP・モデル・role変更は実行していない。

## 必要権限の切り分け

- `CONNECT`（対象DB）と`USAGE`（public schema）。165のguard/feature/history/ID取得は `races, race_horses, race_results, horses, id_mappings` のSELECT。
- 同じengineのfetcherは `fetch_throttle_state` にINSERT ON CONFLICT DO NOTHING、SELECT FOR UPDATE、UPDATEを行い、運用状態transactionをCOMMITする。table SELECT/INSERT/UPDATEが必要。DELETE/DDLは不要。
- `fetch_throttle_state` のPKはtext domainでserial/identityではないため、当該処理のsequence権限は不要。select-for-updateは単なるSELECT権限だけでは足りない。
- 公式原文の単勝精算はローカルファイル処理。将来の公式取得・DB保存経路は別であり、そのwriter権限を165観測roleへ丸ごと与える理由にはならない。
- deployのapp_roはmigrate.pyでtable DMLをREVOKEする読み取り専用serving roleの設計。そのsaved DSNを見つけただけでlive captureへ適合したとはしない。

## 検査と設定の境界

実際の接続先/roleを明示した候補profileを使い、owner/fallbackへ黙って切り替えない。接続調査はstartupからread-onlyとし、DML/権限変更probeを行わない。既存role・table/column ACL、database/schema ACL、RLS/forceRLS/policy、standby、role/databaseのdefault readonly設定とURI optionsを区別する。

read-only inspectの強制オプションによって`default_transaction_read_only=on`を見たことは、元profileの通常transactionがwrite可能か否かの証明ではない。検査が上書きした設定と、保存role/database/URIの既定値を分ける。RLSやtriggerが存在するとtable ACLだけの合格では不足。role owner/superuser/BYPASSRLSで通せることをleast privilegeの合格にしない。

保存.envはsource/execせずパーサで読む。URL全文・password・例外本文・secret digestをconsole/成果物へ出さない。`${...}`等の未解決placeholderを自動のhost/role変更で救済しない。パスワード非設定はpeer等の正常認証もあり得るので、実際のread-only接続成否と照合する。profile保存が必要なら秘密設定はgit外/mode0600で扱い、自動の収集開始を含めない。

userは接続設定と必要権限の確認を依頼しており、role作成/GRANT適用までは今回実行しない。足りない場合は最小のreviewable設定/SQLと適用後に再確認する項目を残す。owner URLへの代替やapp_roの広範なDML付与で解決しない。

## 実行前チェック

robotsは既存cache JSONを直読し、schema/origin/outcome/status/bodyサイズ/base64/取得時計をvalidateする。freshだけでなくexact UA/pathへのAllowを必要とし、TTL境界と未来stampを拒否する。cache read/lock/prewarm自体を新取得にしない。

throttleはcanonical key `netkeiba.com`。DB時刻でcooldownとbacklog/max_wait3秒を判定し、source intervalの正当性も見る。slot予約やcooldown解除をしない。現在のPASSは将来競合なしや取れるslotを保証しない。

preflight-onlyでprofile/ACL/cacheが整っても、候補/原出走表5分/担当者/起動窓/3guard/quote1800秒/両予測ready前D/公式払戻が未確認なら`ready_to_collect`にしない。新規収集を許可するフラグやobserve自動呼出を今回のcheck CLIへ設けない。

## 完成後

新169のソース・fake/offlineテスト・保存結果を独立照合して、具体的な不足と起動直前の未確認を確認する。165〜168は凍結維持する。

## 最終独立検証（final-02）

**PASS**。独立攻撃テストは **65件PASS（0.16秒）**、RuffもPASS。全てfake/offlineで、独立担当によるDB再照会・HTTP・モデル・権限付与は0件。

- 未設定/OWNERだけの設定からfallbackしない、private0600/leaf symlink/placeholder/Compose内部host/DSN options/継承PG変数を拒否することを検査した。profileのrepr/public/全成果物へ秘密URL/passwordを出さず、環境contextが例外時にも復元し、load後に注入されたPGOPTIONSも拒否する。
- 最小の列ACLがそろってもcan_collect=false、read-only ACLだけではthrottleのINSERT/UPDATEが不足し、1列不足を列名で記録する。superuser/owner/RLS/過大権限/search_path/role membership/session・current login不一致を拒否する。
- native profile probeはengineへのSQL gateをconnect前に設置し、最初のアプリSQLでREAD ONLYを宣言、SHOW native defaultとtransaction readonlyを分離し、例外でもrollbackする。これはfake connectionでの順序検査であり、実native profile接続成功を意味しない。
- READ ONLYの基本5表ではDML timestamp triggerはSELECTで発火しないため、存在だけで止めない。実INSERT/UPDATEするfetch_throttle_stateの独自triggerは別レビューを要求する。実効RLSやsearch_pathのgateは維持した。
- 全offline設定/ACL/現在cache/throttleがfakeでそろってもcan_collect=falseとlaunch未確認を維持する。予定launchでcache失効が既知ならNO_GO。8種類のscope/TTL/両ready必須flagをmanifestまで整合更新してもverifyが拒否する。
- init-profileは既存ファイルを上書きせず0600でplaceholderだけを作り、CLIにはobserve/refresh/grant/enable-collection入口がない。

`independent_preflight_audit.py` は169本体をimportせず、正式 `artifacts/169-live-preflight/final-02` の2packetを直接読んだ。全manifest/source SHA、20行のread-only SQL形状/SHA/正常終了、rollback/read-only proof、元robotsファイルSHA/TTL/Allow、秘密URL非露出、固定ゼロflagを照合して一致した。private profileは内容を読まずfile mode0600だけを確認した。

- inventory packet: manifest `15ee2336c27300862457834ff06d706b3850cab65f61d13fc405cae583149c2a`。管理用read-only調査でありnative live接続ではない。local clusterで非builtin roleはaiuma（superuser）だけで、そのroleをliveへ採用していない。
- profile-check packet: manifest `04bff7e2deae987bd539ff6f0d6ef0c1ae0ddd81805a1c10a1ec8593044d7ada`。placeholder/endpoint未確認/承認済みinterval不足により接続前NO_GO、SQL0行、native default未測定、throttleは未照会として明示した。
- deployのapp_ro設定はhost=db:5432のCompose内部向け。local15432と同じclusterと証明しておらず、hostnameを書き換えたりowner URLへ切り替えたりしない。
- cacheは期限切れのまま、現在throttleだけのPASSは将来slot/競合の保証にしていない。role作成/GRANT/lock/slot/cache更新は適用0件。SQL provisioning draftはDB名一致guard/ON_ERROR_STOP/transactionを備えた未実行資料として保持している。

設定/検査経路のオフライン整備は完成したが、実live用の正しいcluster・専用login/秘密値・権限は未充足。対象race/担当者/原出走表5分/起動時アクセス状態/quote30分/両ready前D/公式結果は別の未確認・未到来である。現結果は両packetともNO_GOであり、収集開始や実レース成功の実証ではない。

- [独立JSON検算](independent-audit-final-02.json)
- [独立検算コード](independent_preflight_audit.py)
