# 172 source interval統一・収集停止維持の独立設計レビュー

2026-10-27。依頼範囲はAPI/ops/手動CLIのsource intervalを60秒以上にそろえ、再起動でレースデータ収集を始めず、設定・競合・preflightを再確認すること。独立担当はHTTP/DB接続、private設定本文の読取、process操作、既存165〜171の編集をしていない。既存codeと公開済み171結果だけを読んだ。

## 起動経路の事実

- `api/.../app.py` のlifespanはengine/session factoryの生成のみ。外部fetch/worker起動はない。調べたAPI routerも保存データの利用が中心で、外部sourceのfetch経路はない。
- `ops/.../app.py` もstartup自体はengine生成だけ。ただしopsのrefresh/predict/range等のPOSTは新jobをenqueueできる。
- `ops/.../worker.py:main` は起動直後にsingleton獲得とorphan/stale回復を行い、その後persistent IO/CPU laneを開始する。CPU laneのpredictにもcaptureがある。queueが今0件でも後続enqueueや回復による取得がありうる。
- `OPS_WORKER_CONCURRENCY=0` は安全な停止指定ではない。値検査より前にrecoveryがあり、その後エラー終了する。停止用の既存公式flagも今回読んだworker経路にはない。
- `OPS_CAPTURE_ON_AUTO_PREDICT=0` はauto由来predictのcaptureしか止めない。manual_ui由来predictは `_live_capture_chaos` を呼ぶため、この指定だけを全取得停止とは扱えない。
- `scripts/stack.sh restart all` はworkerを通常起動する。選択component以外も含む包括restartは今回使わない。SIGTERM停止はinflightをqueueへ戻す経路があるため、停止完了とqueue状態の再確認が必要。
- 165手動CLIは既定でcollection無効、explicit enableのpreflight後だけLiveBackendを使う。profileをshell sourceせず、169の明示profile loader/environmentで与える。元165〜171を変更せず新しい運用wrapperで共通policyを適用する案は凍結契約と整合する。

## 推奨する反映順序と停止条件

1. 共通の非秘密policyを有限・60秒以上として厳密に検証する。既存により長い承認値があるなら短縮しない。`HORSERACING_NETKEIBA_MIN_INTERVAL_S` とopsのcaller minを同じ下限へ設定し、shared limiter無効化を許さない。
2. 先に取得・enqueue停止を有効化する。workerを明示停止したままにするか、worker.mainのimport/call・recovery・lane生成より前で待機する独立paused起動経路を用意する。環境markerを置いただけで元codeが尊重すると思わない。
3. ops APIの取得を生むPOSTも停止させ、設定反映作業中にqueueが増えないようにする。startupだけの無取得と、その後のUI操作による無取得は別に検証する。
4. API/ops-apiだけを選択再起動し、旧pid消滅・新pid/configを確認する。旧parentだけを止めてuv/python childを残さない。workerをpausedで起動する場合、元mainが呼ばれていないこと・recovery/claim/fetcher生成/SQL/HTTPがないことを証拠にする。
5. 手動CLIも同じ非秘密policyとprivate profileを明示loaderで読む。直接古いCLIを起動する場合の制約と、正式な新wrapper経路を文書化する。今回はobserve/planは実行しない。
6. processの初期環境だけでなく、新wrapperが読んだ実効値とpaused状態を公開可能なhealth/receipt等で確認する。秘密を含む全環境/DSN/接続URL/ファイルhashを出さない。
7. read-onlyのqueue・throttle・session/lockを前後で照合する。HTTPやsource slotを実際に消費する負試行で確認しない。preflightは専用roleでそのまま再実行し、robots TTLは過去の更新時刻を保持する。新robots取得も今回は範囲外。

## 検証の焦点

- policyが欠落/不正/NaN/Infinity/60未満/limiter無効化なら安全に停止し、既定1秒へ戻らない。
- paused workerがworker.main、queue recovery/claim、fetcher factory、subprocess captureを呼ばない。
- paused opsの受付停止はDB依存関数やenqueue前に働く。GET/healthは利用できる。
- 共通設定の反映には再読込が必要。OpsConfigはimport時に固定されるため、ファイルだけ変えて既存processに反映したとは扱わない。
- `source_interval_s` は各process環境を読む。共有rowの現在値だけを60秒にしても、次のcallerが1秒設定なら契約は戻るため、全入口の値を確認する。
- API・ops-api起動やローカルhealth GETは許可された確認だが、新規レース取得0、モデル推論0、observe0、robots再取得0、source slot予約0を維持する。
- 取得30分以内、判断前の両予測durable保存、三段階guard、固定判断窓、公式払戻精算の契約は変更しない。設定反映成功と将来観測GOは分ける。

現時点の設計上の重大懸念は、既存workerを通常再起動すること、auto_captureだけのflagで全停止とみなすこと、環境markerだけで停止が実装されたとみなすことの3点。新wrapper等の実装と反映後packetがそろった段階で再レビューする。

## 実装前半の確認

`config/source-operations.json`、`scripts/source_operations_172.py`、`scripts/stack.sh`差分、新42件のtestを読んだ。現時点で既知の取得開始経路に重大な未解消問題は見つからなかった。

- 共通policyはschema/未知key/重複key/finite/60秒以上/値一致/limiter有効/paused=trueを厳密検査し、継承した長いintervalの短縮も拒否する。
- wrapperは設定反映後にsource関数、OpsConfig、API型/ops型RequestPolitenessの実効intervalを算出して一致を確認する。実測に使うsession factoryは呼ばれると例外になるので、この算出ではDBへ接続しない。
- worker componentは`horseracing_ops.worker`をimportせず、runtime receiptを保存して終了する。stackは通常workerを起動せず、daemon PIDも保存しない。
- ops APIはouter ASGIで全非GET/HEAD/OPTIONSへ503を返し、body/dependencies/enqueue前で停止する。既存appのlifespanと読取は委譲する。
- API/ops APIはapp/uvicorn起動前からPython socket connect/connect_ex/create_connectionを停止する。これはPython接続経路の防御であり、DNSや全native/uvloop経路を物理的に禁止した証明ではない。今回の既知取得入口停止と併せて評価する。
- manual-preflightは共通policyとprivate profile intervalを照合し、profile環境下の実効policyを確認してから169 checkへ渡す。今回observeへ進む分岐はない。
- stackのruntime report名は秒単位時刻なので、同一componentを同秒再起動するとimmutable publish衝突で安全停止する可能性がある。今回の実操作では固有名または時間差を確保する必要がある。

worker停止前のrunning/inflight確認、旧worker/childの消滅、ops/API新PIDの起動とruntime値、ops POST503、queue/throttle不変、preflightを反映後packetで確認する。独立担当はまだprocess操作やDB/HTTP照会をしていない。

## 起動維持のrescue診断

rootの報告では旧worker/ops/APIの停止とqueue0確認後、stackのAPI/ops開始はstartを表示したが、次cellで両PIDが消滅し、log0byte/runtime receipt未作成/loopback拒否となった。独立担当は最新版wrapper/stackを読み、logのsizeを確認しただけで、process起動/停止やDB/HTTPを実行していない。

最新版のexecve分岐はsame PIDで実行し、policy適用済みenvを同じPythonへ渡す。既知の静的失敗ならトップレベル例外ハンドラがJSONを出すため、log0byte/receiptなしはPythonの初期実行前後でprocess groupが回収された仮説と整合する。ただしこれは状態からの推定であり、原因確定とまでは扱わない。

nohupはSIGHUPの扱いを変えるがOS sessionを分離しない。macOSにsetsidコマンドがなくても、Pythonのsubprocess.Popenで`start_new_session=True`、stdin=DEVNULL、stdout/stderr=private local log、close_fds=Trueを使える。実サービスを無断で繰り返し起動する前に、無害な短時間sleep子をこの方法で起動し、次cellで生存するか確かめる案をrootへ提案した。別案はforegroundのmanaged exec sessionとして明示的に維持すること。どちらもworkerを通常起動しない制約は維持する。

確認用の起動で秘密値をargvへ移したり、raw環境やDSNを出力したりしない。起動方式変更後もAPI/opsのpause wrapper・ops outer503・旧PID消滅・新PIDの実効値確認を省略しない。

## 反映後の公開packet監査

公開packetに対するstdlibのみの独立auditorを作成し、`independent-audit.json`へPASSを保存した。独立担当は引き続きDB再照会・HTTP・秘密本文読取・process操作をしていない。

- 共通policyは60秒/paused/limiter有効。API/opsのruntime4種実効値と初期process環境が60秒で一致し、listener PIDとruntime PIDも一致する。
- 旧worker→旧ops→旧APIの停止完了順を確認した。新API/opsは停止後の新PIDで、無害sleep子のsession分離smokeも次cell生存・後始末あり。stackと別API起動設定はwrapper経由になっている。既存のcredential-bearing launchファイルは構造だけを確認し、本文やhashを公開保存していない。
- workerは停止前dry-startと停止後runtimeの両方でmain未import/未呼出・recoveryなし・PAUSED_NOT_STARTED。daemon/PIDfileは残っていない。
- ローカルHTTP5件はloopbackだけ。API/ops openapiとAPIのモデル読取は200、未定義POSTと実refresh POSTはops outerの503。queueを増やした形跡はない。
- 4つのread-only運用snapshotでqueued/running jobとthrottle lockは0。DB identityは171と同じ。throttle行は171のrobots予約後から不変で、今回新slotやcooldownを作っていない。robotsも171の保存原本hash/時刻のまま。
- 検査SQL48件のhash・phase・完了・rollbackを再計算した。169 preflightは設定/ACL/robots/現在throttleがPASS、状態はOFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING、can_collect=false。quote30分/両ready前Dの条件も維持。

手動CLIについてはprivate profile60とmanual-preflightの実効policy60を確認した。これは旧165の直接observe起動を共通wrapperで強制した証明ではないため、正式なpaused manual入口を追加して旧direct実行を今回の運用外と明示する案をrootへ提案した。取得開始を許す分岐は今回不要であり、正式入口もpauseのままにする。

## 正式手動入口を含む最終追監査

ユーザーの明示した追加要件に合わせ、別module `scripts/manual_observer_172.py` が正式手動入口として実装された。既に稼働しているAPI/opsのwrapper sourceは変更されておらず、初回監査のsafe source hashとの一致を確認した。

新手動入口のobserveは共通policy・private profileのtarget/interval・実効policy60秒を検査後、PAUSED_NOT_STARTED/exit3で停止する。旧165 CLIのimport/call、DB接続、HTTP、source slot、モデル推論、公式精算へ進まない。enable-collection flagもなく、paused=falseや不正policyは拒否する。private設定本文は独立担当が読んでいない。

`manual-observe-paused.json`とinvocation、同じ入口からの新manual-preflight runtimeと169 packet、post-manualのread-only運用snapshotを追監査した。実効値60秒、observe未実行、専用role preflightは現在条件PASS/can_collect=false、API/ops PID・設定は継続、queue/lock0、robots/throttle不変を再計算した。

最終独立監査は`independent-audit-final.json`でPASS。read-only SQLは全75件（初回48件＋追加manual-preflight20件＋post-manual snapshot7件）を検証した。tests-finalの282件合格も確認。今回の監査は公開証跡とsourceだけで、DB再照会、新HTTP、秘密本文読取、process操作は行っていない。
