# 170 専用role・live接続設定の適用結果

2026-10-27。接続先の確認、専用role作成、必要列だけの権限付与、private接続設定、実接続preflightまで完了した。**新規収集は開始していない。設定・権限は合格、観測開始の総合判定はNO_GO。**

稼働API/opsの設定、Dockerのport/volume、保存済みレースを照合し、対象はhost側127.0.0.1:15432のhorseracingと確認した。database OIDは5987755、cluster system_identifierは7624360747090018342。role作成transaction内でも再照合してから適用した。Compose内部用のdeploy設定や稼働アプリの環境設定は変更していない。

`hr_manual_observer`を非管理者・membershipなしで作成した。明示的に付与した権限はDB CONNECT、public USAGE、既存契約の6表の必要列SELECT、fetch_throttle_stateのINSERT3列/UPDATE4列だけ。レース・結果・予測・購入データへの書込、DELETE/TRUNCATE、sequence、schema CREATEは付与していない。全public27 relationとsequenceの実効権限も実接続で照合した。

乱数生成した秘密値はgitignore対象の`config/.env.manual-observer`へ0600で保存した。client側でSCRAM-SHA-256 verifierを作り、DBには平文passwordをSQLとして送っていない。接続開始時からstatement/error/duration/sampling/parameterのログ経路を抑制・検査し、private stagingのfsync/readback→DB commit→atomic公開の順で保存した。公開証跡には秘密値、verifier、private設定本体やそのhashを含めていない。HBAのpassword challenge自体を検証したという主張はしない。

新roleで169 preflightを実行し、current/session login、DB、必要列ACL、native default readonly=off、検査transaction readonly=onを確認した。設定不足・権限不足は0件。追加のread smokeで6表の必要列投影と既知レースのcount(*)=15が成功し、両検査ともrollbackした。throttleの実書込probeは行っておらず、将来の予約成功を保証するものではない。

変更したDB内容はrole catalog・DB/schema/列ACL・roleのDB別既定設定である。raceデータやthrottle状態は変更しておらず、throttle前後digestも一致した。robots更新、source slot予約、model inference、live plan/observe、公式精算、新規収集はいずれも実施していない。

保存robots cacheは期限切れのままなので総合NO_GOを維持する。manual profileのsource intervalは60秒。確認できたAPI/ops環境ではinterval指定がなく保存codeのdefaultは1秒だったが、全運用の承認値を確認済みとは扱わない。開始前には承認済みintervalを再確認し、より長ければ短縮しない。対象レース・最新の完全出走原本・担当時間・時計/電源/容量・実URLのrobots・slot競合も実行時の確認事項として残る。

取得から0〜1800秒以内のオッズ、判断以前の両予測durable保存、取得前/予測後/判断直前の確認、固定判断窓、公式原文と全winner払戻による精算の契約は変更していない。未到来の条件を今回のpreflightで確認済みにしない。

関連テスト229件合格（新170が39件、既存169/166/168が190件）。独立担当は秘密内容やDBを再読せず、公開証跡の列集合・時系列・SQL31件・rollback・source pin・0600を再計算しPASSとした。

- [操作・停止・回復の手順](manual.md)
- [実適用の証跡](../../artifacts/170-observer-provision/run-01/provision-receipt.json)
- [専用roleでのpreflight](../../artifacts/170-observer-provision/run-01/preflight/result.json)
- [独立監査](evidence/independent-audit.json)
- [関連テスト](evidence/tests.txt)
- [成果物の受渡し記録](evidence/delivery-receipt.json)
