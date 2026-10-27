# 171 robots限定更新・interval・競合確認の独立レビュー

2026-10-27。独立担当は保存sourceだけを読み、HTTP、DB照会、秘密profile本文の読取、165〜170の変更を行っていない。ユーザーが許可した外部取得はrobotsのみであり、race/odds/card/result取得や観測開始の許可とは分ける。

## 実施前の設計意見

- 固定した`https://race.netkeiba.com/robots.txt`だけを1回送る。redirectを追わず、retry/別host/HTML・odds取得経路へ広がらないclient境界を持つ。既存liveと同じUser-Agentを使う。
- `HttpFetcher.get(robotsURL)`はrobots自己確認と本体取得が重複しうる。`_fetch_robots`も非429の失敗やtransport例外をallow/Noneへ畳むため、今回の厳格な更新成功の根拠にしない。固定URLの直接GETを明示的なshared `RequestPoliteness.pre_request`に通し、実HTTP400/403/429では既存cooldown処理を使う。429以外の失敗もNO_GOとし、旧cacheを新しいallowに置換しない。
- robotsもcanonical `netkeiba.com`の共有枠を消費する。正常送信の予約で`next_allowed_at/updated_at`が変わることと、拒否時にcooldownが伸びる可能性を証跡に明示する。今回全体を`database_write=false`や`slot_reserved=false`とは記載しない。レースデータ書込0・レース取得0とは区別する。
- 200のrules、404/410のexplicit absenceだけをdurable cacheへ保存し、サイズ上限を守る。書込はbest-effortなので、`put`のreturnだけで成功にせず、同一origin/schema/status/body/元時刻のreadbackと169の厳格inspectでTTL・Allowを再確認する。更新後のcache原本を新171の証跡へ凍結して、将来cacheが変わっても今回を監査できるようにする。
- 既存`refresh_lock`はthread lock獲得後のfile lock失敗でも`yield True`になりうる。厳密なcross-process singleflightが必要な更新では、実file lock獲得を確認し、lockを取得できなければ送らない。cache公開まで同一lock内で行う。
- profile60秒はそのprocessの設定である。`_interval_for`は各processの`max(caller min, source_interval_s)`であり、共有rowに永続policy値は存在しない。manual60秒だけではops/API等が1秒設定で後から予約することを防げず、「全運用で60秒に統一済み」とは報告できない。現実に稼働するprocessのallowlist環境、起動設定、shared limiter有効、接続先DBの一致、ops設定を照合する。
- 実行中のqueue/job、共有throttle状態の前後サンプル、可能ならactive session/lock等を読取り、現在の競合を報告する。短期間row不変・backlog0だけから競合不存在や将来空き枠を断定しない。稼働プロセス再起動/全設定変更は別影響があるため、単なる確認の依頼から自動で行わない。
- 更新後の169 preflightは新roleの同一private profileで実行する。確認処理そのものはDB read-only/rollbackであり、更新段階のthrottle writeと分ける。設定・robots・現在throttleが合格しても、`can_collect=false`と未選定race/元原本/担当時間/時計/実URL/将来競合を維持する。
- quote0〜1800秒、判断前の両予測durable保存、三段階guard、固定判断・保存窓、公式原文と全winner払戻による精算は変更しない。

## 独立担当の検証範囲

この段階は設計レビュー。実際のHTTP応答・DB状態・runtime環境を独立担当が再照会したという証明ではない。公開された実行証跡がそろった段階で境界と結果を再確認できる。

## robots-only実装の実行前レビュー

`scripts/roi_explore/robots_refresh_171.py`とoffline tests、sanitizedなoperation-before結果を読んだ。固定GET URLをtransportで強制し、redirect/retry禁止、private専用role、既知target照合、shared RequestPoliteness、拒否時cooldown、同一cache lock、cache原本保存と169 inspectの構成は妥当。race collector/modelへの呼出はない。operationのAPI/ops既定1秒とmanual60秒、operator承認未確認、queue空と将来競合未確認を分ける表示も適切。

readbackで旧fresh cacheの同body/statusを誤って更新成功にできる点を指摘した。今回の開始時刻以降かつ現在以前のfetched_atを必須にする修正と、silent put＋fresh同body旧cacheテストの追加を確認した。実HTTP前の重大な未解消事項は見つからなかった。このレビューは実HTTPや実DB状態の独立再照会ではない。

最終追加のSQL boundaryは既存policyのSELECT/SHOWとfetch_throttle_stateのINSERT/UPDATEだけを許可し、SQL本文とhashのみを記録してparameter値を保存しない。固定URLのtransport試行ログもfinallyで保存する。これらのsourceを実HTTP前に確認した。

複数test rootの同時実行で起きた`No module named tests.test_robots_refresh_171`は、scripts/testsとscrape/testsが同名top-level packageであることによるpytestのdefault import衝突と診断した。指定したrobots/cacheとpolitenessの2 unit filesは`tests.*`のabsolute importを使わないため、`--import-mode=importlib`または別process実行が妥当。製品コードの失敗として修正する必要はない。scrapeの他testには`tests.conftest/_synth`依存があるため、importlibを全suiteの一般解とはしない。

## 実行後の公開packet独立監査

`independent_audit.py`を公開run-01へ実行し、`independent-audit.json`は`independently_verified`。製品の判定関数をimportせず、stdlibだけでJSON/hash/時刻/SQL/manifestを再計算した。DB、HTTP、秘密profile本文を再読していない。

- transport記録は固定robots URLのGET1件、redirect/retry0。HTTP404・raw196bytesのhashと、cache原本hash・canonical snapshot内容・absent/空body・今回のfetched_at・24時間TTLが一致した。404はrobots未設置として保存された事実である。
- policy SQL6件はthrottle初期INSERT、行lock付きSELECT、DB時計、必要列UPDATE、DB時計、cooldown再読取りの順。SQL本文hash・parameter非保存を確認し、他データへの書込はない。
- 運用3snapshotでは各3process・queue/lock0、manual60秒と他process初期環境の未指定/default1秒、運用承認未確認、将来競合未確認を維持。共有throttleは今回予約による60秒差になり、直後から最終まで変わらなかった。
- 取得直後NO_GOは今回slotのbacklogによる。枠満了後の169は現在robots/throttle・接続ACLがPASSし、`OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING`と`can_collect=false`を保持。複数callerのinterval未統一は別の運用NO_GOとして妥当である。
- read-only照会のSQL hash・phase・rollback、source pin、preflight manifest inventory/hash、quote1800秒/両ready前判断/将来gateの維持を確認した。公開packetにcredential-bearing DSNや完全なSCRAM verifier patternはなかった。秘密内容を知る検査ではない。

監査初回のSQL判定は、ACL SELECTの文字列literalにあるDELETE/TRUNCATEをmutationと誤判定した。rootへrescue相談し、single-quoted literalsとquoted identifiersを除去してkeyword判定する修正後に一度再実行しPASSした。製品や実行証跡の不備を隠して通したものではない。

結果文書とmanualもレビューした。robots取得のみの完了と現在preflight合格を、観測開始のGOから区別できている。291件のtest結果とimport衝突の扱い、process設定変更/再起動が未実施である点も適切で、重大な過大主張はない。
