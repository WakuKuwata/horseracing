# 168 初回観測候補・GO確認結果

2026-10-27。**今回は見送り（NO_GO_SKIP）。保存済み資料から選べる将来候補は0件で、実行対象は未選定のまま。新規収集は開始していない。**

## 調べた保存範囲

| 確認範囲 | 結果 |
|---|---|
| 実DB全開催メタデータ | 140,049レース。最新開催日は2026-10-25 |
| 今日10/27以降のrace_date | 0行 |
| 確認時刻より未来のpost | 0行 |
| 最新保存日10/25 | 36レース、36件とも結果保存済み。過去観測へ流用しない |
| ローカルarchive URL marker | 7,750件 |
| 保存shutuba原本 | 5,629版・3,665レース。元取得時刻でrace別最新を取り、全3,665の本人開催日を確認 |
| ローカル原本の最新開催日 | 2026-10-25。10/27以降の本人出走表0件、日付不明0件 |
| 独立したrace_list保存URL marker | 0件 |

DB確認は**14:10:22 JST**、ローカル原本確認は14:11:49 JST、アクセス状態確認は14:12:05 JST。DB内はread-only/repeatable-readの一貫snapshotだが、これら全てが同時のsnapshotという意味ではない。

10/31等へのナビゲーションリンクは存在したが、本人開催日の出走表や全頭カードの保存ではないため候補にしなかった。これは**現実に次の開催がないという意味ではなく、調べたDB/archiveに必要な将来資料がない**という結果である。外部サイトで補充せず、全コンピュータの全ファイルを網羅したとも主張しない。

## GO条件の確認結果

| 項目 | 状態・不足/未確認 |
|---|---|
| 対象候補 | **不成立**。将来DB行・保存将来出走表とも0件。post/race_id順で固定する対象がない |
| 元出走原本5分以内、全同日平地カード、全頭ID対応 | **対象なしで未証明**。今回DBを読んだ時計を元取得時刻に付け替えない。最新の原本取得も10/26 13:32:02 JSTで、将来対象用の原本ではない |
| live接続設定 | **この手動実行環境のDATABASE_URLが未設定**。検査は166のread-only fallbackで実施。fallback側の権限があっても165 live用role/運用状態書込権限を確認済みとはしない |
| robots既存cache | **期限切れ**。元取得10/26 13:30:43 JST、24時間期限は10/27 13:30:43 JST。古いrules自体はodds APIをAllowだが、鮮度条件を満たさない。refresh/prewarmなし |
| source throttle | canonical key `netkeiba.com` は確認時点でblockなし。next_allowed_atは過去。ただし**起動時の競合/待ち時間は未確認、slot予約なし** |
| 操作担当の対応時間 | **未提示・未確認**。対応可能と仮定せず、当日の代替raceも選ばない |
| コード/モデル | 既存165のsource240ファイル・model関連43ファイルをSHA照合し不一致0。pickle読込/推論なし。対象live planとの一致は未登録のため未確認 |
| PC/保存先/実行直前環境 | 実UTC/JST/DB時刻と現在の成果物保存は確認。起動時の時計同期/電源/スリープ/負荷/容量/対象session先は未確認 |
| 公式原文保存・精算経路 | 165の実装と過去の保存原本は存在するが、未選定候補の原文保存/凍結ID対応は未確認 |
| 取得30分以内のquote | **未到来**。今回は取得しない。将来の判断/封印時に元received_atから0〜1800秒、provider時計があればそちらも必須 |
| 判断前の両予測耐久保存、3guard、固定窓 | **未到来**。今の再推論や後日のreadyで埋めない。両readyが判断以前、判断/保存確認が[T−10分,T−9分)を維持 |
| 全頭結果/公式単勝払戻 | **未到来**。将来の実レース後に公式原文/全winnerで精算し、不足は両腕null。今回settleは実行していない |

## 実施したこと・していないこと

実DBは接続既定/transaction read-onlyがon、repeatable-readを確認し、2回の読取snapshotをrollbackで終了した。SQL台帳を保存し、HTTP・Python socket・165のquote取得入口を遮断した。robotsは既存ファイルの直読みで、lock/cache更新・throttle予約・権限変更をしていない。

新規live plan/registration/observe/モデル推論/公式精算/予約/定期処理/購入は0。保存オッズ・予測・EVの値を候補選びに使っていない。候補を作るための新規refreshも行っていない。今回の比較対象追加は0件、モデル採用/優劣の結論は保留を維持する。

新規候補・アクセス判定21件＋既存DB境界51件、**72テスト通過**。価格/予測信号に依存しない順序、UTC/JST同値、45分境界、過去/結果あり/不明post/年非対応/全頭不一致、freshだがAllow=false、待ち3秒超を検査した。Ruff/diff-check通過。相対出力pathのローカル分析不具合は独立確認して修正し、同じ保存snapshotだけで再分析した。DBを再取得して都合のよい対象へ差し替えていない。

独立監査もPASS。DB再照会をせず、保存SQL21行・read-only設定/rollback/時計・全入力/出力SHA、全3,665原本のSHA/本人日付、候補0・NO_GO・未到来条件・未登録/未推論/未収集を直接照合した。既存166のsource245ファイルと167の提出済み6ファイルもSHA不変を確認した。

## 次回に必要なもの

保存済みの将来開催・出走原本が存在し、担当者の対応時間とlive実行環境/アクセス状態を確認できる段階で、新しい時点の台帳として再確認する必要がある。それまで収集は始めない。過去レースのpostを未来へ移したり、元時計を更新したりして候補数を増やさない。

- [GO/未確認の全台帳](../../artifacts/168-saved-candidate-go/run-01/assessment.json)
- [作業台帳](../../artifacts/168-saved-candidate-go/run-01/operator-record.json)
- [DB snapshot](../../artifacts/168-saved-candidate-go/run-01/snapshot.json) / [アクセス状態](../../artifacts/168-saved-candidate-go/run-01/access.json)
- [ローカル原本の全件確認](evidence/local-inventory-report.md) / [全3,665件の原本台帳](evidence/local-card-ledger.json)
- [独立レビュー](evidence/independent-review.md) / [72テスト](evidence/tests.txt)
- [manifest](../../artifacts/168-saved-candidate-go/run-01/manifest.json) / [提出時点SHA](evidence/delivery-receipt.json)
