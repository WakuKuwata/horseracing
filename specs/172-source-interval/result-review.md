# 172 source interval統一・取得停止・独立監査

2026-10-27。**API・ops・正式な手動観測CLIのsource intervalを60秒で統一し、取得停止を維持した。** API/opsは新PIDで反映済み。workerは停止、opsの更新依頼は受付前503、手動observeは旧観測処理前の停止となる。新規レース収集、robots更新、モデル推論は行っていない。

## 反映した内容

共通のpublic設定は [source-operations.json](../../config/source-operations.json)。source/ops fetchをともに60秒、shared limiter有効、collection_paused=trueとした。非有限値、60未満、interval不一致、無効limiter、pause以外、継承したより長い契約を短縮する設定は拒否する。秘密値はこのファイルへ入れていない。

`scripts/stack.sh`のAPI/ops/workerと、別のAPI開発起動設定を共通wrapperへ接続した。既存DB接続とAPI artifact設定を保持し、private observer profileは変更していない。wrapperはimport前に設定を適用し、実際のsource_interval_s、OpsConfig、RequestPolitenessの間隔を検査する。新しいAPI/opsの初期process環境とruntime記録の双方で60秒を確認した。

| 対象 | 反映・停止の確認 |
| --- | --- |
| API | PID9300、実効60秒、8000のlistenerと一致、保存済みmodel一覧GET=200 |
| ops API | PID9314、実効60秒、8001のlistenerと一致、更新POST=503 |
| worker | 旧PIDと親の終了を確認。restartしてもmain import/recovery/claim前でPAUSED終了、常駐PIDなし |
| 手動preflight | 専用profile=60秒、共通設定=60秒、実policyも60秒。169 read-only検査へ接続 |
| 手動observe | 正式入口で共通/profile/targetを確認しPAUSED_NOT_STARTED、終了コード3。旧165 CLI import/call、DBアクセス、slot予約、予測、精算なし |

opsはouter ASGIで非GET/HEAD/OPTIONSをアプリのbody処理・dependency・enqueueより前に拒否する。存在しないprobe URLで先に拒否を確認し、実在するレースのrefresh URLでも503を確認した。workerは通常起動時のrecoveryやlane開始を避ける。API/opsにはPython socket接続を止める補助防御もある。これは既知の取得入口停止を補強するもので、OS全体の外向き通信遮断を実装したという意味ではない。

初回切替ではnohup起動の子が実行セル終了後に消えたため、API/opsの反映済みとは扱わず停止状態を確認した。独立診断と無害なsleep子の試験後、start_new_sessionでOS sessionを分離する方式へ修正した。現在はセルを越えたprocess生存、実listener、local HTTP、runtime設定まで確認済み。workerはこの間も取得処理へ再起動していない。

## 保存状態とpreflight

切替前・停止直前・切替中・反映後・手動gate確認後をread-only snapshotで確認した。queued/running job、throttle relation lockはすべて0件。throttle行は171のrobots予約後から変わっておらず、今回source slotを消費していない。robotsも171のcache原本のまま。DB role/ACLは変更していない。API/opsの設定変更とプロセス切替を実施した一方、レース・予測・購入データを更新する処理は実行していない。

正式手動入口から再実行した169 preflightは `OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING`。専用role接続、必要列ACL、readonly検査、robots・現在のthrottleは合格。172の総合状態は **SOURCE_INTERVAL_UNIFIED_COLLECTION_PAUSED**、`can_collect=false`。171で残ったinterval未統一は解消したが、取得開始を許可した結果ではない。

実レースの対象・最新完全card、担当時間、時計/電源/容量、実URLのrobots有効期限、送信直前のslotと競合は開始直前の確認事項。現在無競合でも将来の無競合とはしない。取得から0〜1800秒以内のオッズ、判断前の両予測durable保存、三段階guard、固定判断/保存確認窓、公式原文・全winner払戻精算は変更していない。

## 検証と証跡

新しい共通起動/pauseの43件、手動入口の10件、既存関連229件の計282件が合格した。Ruff、bash構文検査、実local HTTP、worker pause起動、専用role preflightも合格。実sourceへのHTTPは0件で、localの読取・503確認だけを行った。

正式手動入口を含む独立監査はPASS。三者の実効60秒、旧worker終了と停止起動、ops受付前503、手動observeのexit3、queue/lock0、robots/throttle不変、read-only SQL75件とrollbackを公開証跡から再計算した。監査担当によるDB再照会、新HTTP、秘密本文の読取りはない。

- [反映結果](../../artifacts/172-source-interval/run-01/result-final.json)
- [手動observe停止の実行記録](../../artifacts/172-source-interval/run-01/manual-observe-paused.json)
- [正式入口からのpreflight](../../artifacts/172-source-interval/run-01/preflight-manual/result.json)
- [local HTTP確認](../../artifacts/172-source-interval/run-01/local-http-smoke.json)
- [独立監査の最終結果](evidence/independent-audit-final.json)
- [操作・停止手順](manual.md)
- [テスト](evidence/tests-final.txt)
- [成果物の受渡し記録](evidence/delivery-receipt.json)
