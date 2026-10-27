# 171 robots限定更新・運用interval・競合の確認結果

2026-10-27。**robots限定の更新と専用roleでのpreflight再実行が完了した。レースデータは収集していない。** 現在の接続・ACL・robots・throttleは合格したが、運用intervalが未統一なので、観測開始はNO_GOとする。

## robotsの更新

`https://race.netkeiba.com/robots.txt`だけへGETを1回送った。redirect・retry・環境proxy利用はなし。HTTP 404、応答196bytesだったため、既存仕様の「robots未設置」（absent）として24時間cacheを保存した。Allow規則を取得できたという意味ではない。取得・保存時刻は2026-10-27 15:59:42 JST、有効期限は2026-10-28 15:59:42 JST。期限内でも実行時に実URLとcacheを再確認する。

raw応答、cache原本・内容snapshot、取得時刻・hash、HTTP試行ログを新171へ保存した。保存後のreadbackと169 inspectでfresh/absent/404を照合した。旧cacheの時刻だけを更新したのではなく、この1回の応答を保存した。

robots取得にも170の専用roleと既存の共有RequestPolitenessを使用し、canonical `netkeiba.com` bucketを60秒で1枠消費した。そのため今回全体では**throttle制御情報に書込がある**。SQL6件はthrottle INSERT/SELECT FOR UPDATE/UPDATEと時計・cooldownの読取りだけ。レース・予測・購入データ、role、権限は変更していない。拒否cooldownの追加は発生しなかった。

## 運用設定と競合

稼働API1 process・ops2 processの初期環境と保存設定を秘密値を出さず照合した。3 processとも確認済みlocal DBを使い、shared limiter無効化指定はなく、robots cacheも共通のrepo既定先だった。

| 対象 | 確認できた間隔設定 |
| --- | --- |
| 手動private profile | HORSERACING_NETKEIBA_MIN_INTERVAL_S=60 |
| 稼働API/opsの初期環境 | source/ops intervalの明示指定なし |
| 対応する現行codeの既定値 | source=1秒、ops fetch=1秒 |
| 更新前の共有throttle行 | next_allowed_at−updated_at=1秒 |
| 今回のrobots予約 | 同差=60秒 |

これはprocess初期環境と現行code・保存状態の確認であり、各processの現在のメモリ内設定まで直接実測したものではない。**manualの60秒だけでは全callerに60秒を保証できない。** 共有rowは予約の順序を調整するが、各callerが次の予約を自分のintervalで進めるためである。運用者が承認した全体の契約値も未確認のまま。今回、既存processの設定変更・停止・再起動はしていない。

更新前・直後・枠満了後の3回、read-only snapshotで確認した。いずれもqueued/running jobは0件、throttle relation lockは0件。更新前の待ちとcooldownはなく、更新後は今回の60秒枠だけが残った。最終確認ではbacklog=0/cooldownなしで、throttle行も今回予約後から変わっていなかった。API/opsは稼働したままであり、今後のUI操作・enqueue・他端末/プロセス・予定jobによる競合がない証明にはならない。

## preflightと判定

取得直後のpreflightは、今回消費した60秒枠によるbacklogでNO_GOだった。枠の満了を待ち、HTTP取得や再予約なしで再検査した。

最終169 preflightは`OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING`。専用role接続、必要列ACL、native既定とread-only検査transaction、robotsとthrottleの現在状態は合格。`can_collect=false`を維持した。169は複数process間の設定統一を検査するものではないので、今回の運用確認を合わせた171の総合状態は**NO_GO_SOURCE_INTERVAL_NOT_UNIFIED**とする。

観測開始前には全source callerの設定を、承認済みでmanual60秒以上の同じsource intervalへそろえ、必要な再読込/再起動後に再確認する。承認値が60秒より長ければその値を採用し、短縮しない。shared limiter無効化・別DB/別cache経路がないことも確認する。これらの適用やprocess再起動は今回実施していない。

対象レース/最新の完全出走原本、実行担当時間、PC時計/電源/容量、実URLのrobots TTLと送信直前slotは将来の確認事項。取得から0〜1800秒以内のオッズ、判断前の両予測durable保存、取得前/予測後/判断直前のguard、固定判断/保存確認窓、公式原文と全winner払戻による精算は維持する。今回quote・予測・live plan/observe・公式精算は実行していない。

## 検証

新171の20件、既存robots/politenessの42件、169/170/166/168関連229件、計291件が合格した。初回の複数test root同時収集は同名tests packageのimport衝突で止まったため独立診断を依頼し、test processを分けて実行した。製品コードの問題ではなく、未実行のtestを合格扱いにはしていない。Ruffも合格。

独立監査はPASS。1GET/404・原本とcache hash・今回の取得時刻とTTL・throttleだけのSQL6件・read-only検査SQL64件とrollback・source pinを公開証跡から再計算した。監査担当はDB再照会、新HTTP、秘密内容の読取をしていない。

- [実robots更新の記録](../../artifacts/171-robots-refresh/run-01/refresh/result.json)
- [更新前の運用確認](../../artifacts/171-robots-refresh/run-01/operation-before/result.json)
- [最終運用確認](../../artifacts/171-robots-refresh/run-01/operation-final/result.json)
- [最終preflight](../../artifacts/171-robots-refresh/run-01/preflight-final/result.json)
- [独立レビュー](evidence/independent-review.md)
- [独立監査結果](evidence/independent-audit.json)
- [操作手順](manual.md)
- [成果物の受渡し記録](evidence/delivery-receipt.json)
