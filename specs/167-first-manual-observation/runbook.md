# 初回実レースの手動観測手順

**現在は準備のみ。対象レースは未選定、新規収集は開始しない。** 将来、観測開始の指示があったときにこの手順を使う。初回の目的は一つの実レースで入力→両予測保存→判断→公式精算の来歴がつながるか確認すること。購入・資金投入は行わず、1件でモデルの優劣を決めない。

## 1. 対象を選ぶ規則

初回は**1日1レース、observe起動は一度だけ**。実レース/開催日の特定は今回行わない。実施時は、保存済みの開催・出走メタデータと操作担当の対応時間を使う。

| 必須条件 | 確認すること |
|---|---|
| 対応年/競走 | JSTで2026年のJRA平地、芝またはダート。2027年や障害へ既存モデルを移用しない |
| 時刻/同一性 | 12桁race_id、開催日、会場、race_number、timezone付き予定postが保存原本とDBで一致 |
| 出走集合 | 初回の対象規模はactiveなstartedが2〜18頭。全ID/馬番が一意で、取消/除外を含む保存statusが既知。quoteはstarted全頭で一致させる |
| ID対応 | 公式source horse IDとcanonical IDが完全一致するか、判断前に保存できる一意なnetkeiba ID mapで全頭を照合可能。prefix除去や発走後の新mapで補完しない |
| 入力成立 | 2007年以降の履歴と旧138/MEV77の通常builderを利用できる。同日・同会場の全予定平地カードが保存済みで、first/last-race入力が部分カードで歪まない |
| 未確定 | 当該結果は未保存、未発走。既存のrace予約/同一race観測がない |
| 手作業の時間 | 選定・登録から判断/保存確認まで一人が監視でき、他の重い学習/benchmark/同時観測と重ならない |
| 来歴/実行環境 | 次節のGO条件を開始直前に満たせる。未確認を合格にしない |

候補集合とその資料SHA、確認時刻、除外理由を**T−45分まで**に台帳へ保存する。対応可能な候補を予定post昇順、同時刻はrace_id昇順で並べ、最初の1件を固定する。全条件を満たす候補がなければ当日は見送る。凍結後のpost変更や不成立は当日見送りとし、別レースへ乗り換えない。

人気、オッズの有利さ、両モデルの予測差、EV、推奨頭数、結果で対象や開始時点を選ばない。新馬/クラス/重賞かどうかの追加選別もしない。既知の欠損null、履歴0件はモデル契約どおり扱い、未保存項目を0や推測値で埋めない。両腕の選択が同一/片方または両方が0点でも選び直さない。

## 2. 開始直前のGO/NO-GO

T−16分頃と実起動の直前に確認し、全項目の証拠をsession外の作業台帳へ残す。**一項目でも不明/不成立ならobserveを起動しない。** 以下は初回用の追加運用条件であり、CLIがすべて自動検査するわけではない。

| 運用GO項目 | 証拠と不成立時の扱い |
|---|---|
| 保存出走原本の来歴 | 既存運用で保存済みの出走/取消/予定時刻原本、元取得時刻、SHAを確認。起動直前の実時計から取得5分以内、全active集合/postがDB・固定対象と一致。updated_at、ファイルmtime、今読み直した時刻は元取得時刻の代用にしない |
| DB反映の限界 | この5分は初回の追加運用基準で、実証済みの最大遅延ではない。CLIのguard5秒はDB snapshotの鮮度であり、元サイトの更新反映を保証しない。原本/反映状況が不明なら見送る。後から判明した取消も精算で別途止める |
| 実行設定 | training/.venvとモデル/コード/source SHAが事前planに一致し、公式精算まで再現できる状態で固定。PC時計同期/スリープ抑止/電源/保存容量/保存権限を確認。初回は同時の重い処理を止める |
| live用DB設定 | 165はDATABASE_URL必須。166のfallback URLやread-only engine設定が自動で引き継がれるとは扱わない。guard/特徴snapshotはread-onlyだが、fetcherは既存throttle等の運用状態を書き得る。必要な読取/運用状態権限の範囲を既存設定で確認し、不明ならNO-GO。owner権限への切替で通そうとしない |
| アクセス条件 | 既存robots許可/cacheが予定取得時点まで有効、throttleのcooldown/next_allowed_at/競合で10秒予算を超えないことを既存証拠で確認。未確認なら見送る。cacheの事前取得、間隔短縮、cooldown解除をこの準備で行わない |
| 公式精算の材料 | 全頭結果/公式単勝払戻の原HTMLと元取得時計を保存できる既存経路、および凍結ID対応を確認。DBの最終オッズや払戻行だけでは原文照合の代用にならない |
| 予約と保存先 | race予約なし。registrationとsessionの別保存先、外側の作業台帳/console先を固定。SESSION_DIRは存在しないこと |

原本5分条件は起動直前にも判定する。既存のより新しい原本を使う場合は元取得時刻で最新を機械的に選び、同じpost/全出走集合であることを再照合する。凍結後の取消・除外・予定post変更が判明した場合は当日見送る。新規refreshやHTTP試し打ちでGO条件を作らない。今この文書の完成をもって、これらの未確認項目が確認済みになったとは扱わない。

## 3. 時間割とコマンド

Tは事前固定した予定post。全記録はtimezone付き、ファイル内はUTC、担当者表示はJSTでそろえる。

| 時点 | 操作 | T=15:00 JSTの説明用例 |
|---|---|---|
| T−45分まで | 候補順/対象/除外理由を凍結 | 14:15まで |
| T−30分まで | live planとregistration-readyの保存完了を確認 | 14:30まで |
| T−16分頃 | GO項目を確認 | 14:44頃 |
| [T−15分,T−14分) | 直前GO後に一度だけobserveを起動 | 14:45:00〜14:45:59… |
| 準備完了〜T−10分 | 両予測保存後はCLIの待機を続ける | 早く終わっても14:50まで待機 |
| [T−10分,T−9分) | final再確認→判断/decision保存→seal保存確認→accepted | 14:50:00以上、14:51:00未満 |
| T−9分以後 | 新しい取得/判断は行わず、検証/停止回復のみ | 14:51以後 |
| 発走後・材料完備後 | 公式evidence→精算→再検証 | 実際の元取得時計に従う |

CLI自体は[T−15分,T−9分)の起動を許すが、初回運用では最初の1分に絞って余裕を確保する。登録もCLIのT−15分より早いT−30分完了を要求する。166の60.55秒を将来の上限とはみなさない。T−14分までに起動できなければ当日見送り。締切/30分制限を後ろへずらさない。

以下は**将来の操作例であり、今回は実行しない**。未設定値を実際の固定台帳から転記する。秘密のDB URLをconsole/台帳へ貼らない。

```bash
cd /Users/kuwatawaku/workspace/horseracing
PYTHON_BIN="$PWD/training/.venv/bin/python"
RACE_ID='UNSET'
POST_TIME='UNSET'
CASE_DIR="$PWD/artifacts/167-first-manual-observation/UNSET"
SESSION_DIR="$CASE_DIR/session"
```

`CASE_DIR`は対象日とrace_idを含む未使用パスに置き換える。作業台帳、PID、consoleログ、verify出力、原公式資料はすべてSESSION_DIRの外側（CASE_DIR直下など）へ置き、SESSION_DIRの中に手作業でファイルを足さない。**sessionを先にmkdirしない。** CLIのmanifestはsession内のファイル集合も固定する。

T−30分までに登録する。plan.jsonを書き換えず、出力されたSHAとregistration-readyを台帳へ転記する。コード/モデル/時刻を登録後に変更しない。

```bash
"$PYTHON_BIN" -m scripts.roi_explore.manual_observation_165.cli plan \
  --race-id "$RACE_ID" --post-time "$POST_TIME" --out "$CASE_DIR/registration"
```

起動直前GOと開始指示がそろったときのみ、出力SHAを転記し、指定の1分間に一度だけ起動する。foregroundで監視し、当該processのPID/起動時計を外側の台帳へ残す。

```bash
PLAN_SHA='UNSET'
"$PYTHON_BIN" -m scripts.roi_explore.manual_observation_165.cli observe \
  --plan "$CASE_DIR/registration/plan.json" --plan-sha "$PLAN_SHA" \
  --out "$SESSION_DIR" --enable-collection
```

取得前確認→オッズ原応答→旧138/MEV77生成→旧6/MEV15推論と耐久保存→予測後確認→必要な待機→判断直前確認→判断/封印の順。quoteを取り直して選び直す操作はしない。fetcherは10秒deadline、オッズ要求の再試行なし（max_retries=1）。robots確認は別要求になり得るため、要求数が必ず1回とは言わない。

早く準備できた場合のT−10分までの無出力待機は正常。時計やイベントを読むだけにし、ready/clockファイルを手編集しない。最終確認の後でも、取得から判断/保存確認まで0〜1800秒、配信時計があればそちらも30分以内、両予測のreadyが判断以前、全頭/post一致、結果未保存が必要。未来/古いオッズ、後日生成予測、DBのcomputed_atを保存完了receiptの代用にしない。

## 4. 成功判定と停止・回復

終了コード0だけでは成功としない。CLIは失敗statusをJSONで返す場合もある。`status.state=sealed`、accepted/manifest/clockが完備し、次の**verify成功**を初めて観測成功とする。

```bash
"$PYTHON_BIN" -m scripts.roi_explore.manual_observation_165.cli verify --session "$SESSION_DIR"
```

`status=verified`、origin=manual_live、evidence_admissible=true、can_adopt_model=falseを確認する。decision/保存確認/accepted時計が[T−10分,T−9分)に入り、両readyが判断以前であることはverifyで照合される。公式未保存ならpending/nullのままで正常。両腕は同じ固定151係数/EV>1/1頭100円/最大10頭・各1000円の仮想会計。買い目が同一や0点でも成功記録を維持する。

| 事象 | 操作/記録 |
|---|---|
| 開始前GO不足、予定post/集合変更、開始窓逸失 | 起動せずNO_GO。理由/元資料/時刻を外側へ残す。当日の代替raceなし |
| preflight/SHA/DB/robots/403・429/通信/保存/feature/model失敗 | 再取得・再起動せずFAILED。既存cooldown/robotsを迂回しない。status/error/failed_step/除去対象SHAを保存。原quote/入力/予測stagingはCLIの失敗処理で削除され得る |
| 取得後の取消/除外/post変更、結果出現、30分超、guard5秒超 | 同じ失敗扱い。馬を落として続行、post修正、現在時計への付替え、片腕採用をしない |
| T−9分までに封印確認がない/制御不明、時計異常、担当者の停止判断 | 当該foregroundへCtrl+C。応答しない場合のみ確認済みの当該PIDを停止。他processへの一括停止をしない。停止理由/実時計を外側へ記録 |
| process終了、acceptedなし、manifestなし、plan/invocationあり | lock解放後に下記abort。既存receipt/失敗台帳を保持する。abortは稼働processを停止するコマンドではない |
| manifestあり、acceptedなし、state=failed | verifyでverified_failureを確認して終了。観測のやり直しなし |
| acceptedあり、manifest/clock等不足、またはverify失敗 | **RECOVERY_REQUIRED**。成功数/比較に入れずファイルを保全。abortはacceptedを拒否する。accepted削除/再封印/後からclock再生成をしない |
| 予約だけ/planだけ等、abort前提のplan・invocationが不完全 | RECOVERY_REQUIREDで保全。手動でsessionや予約を消して再実行しない |

```bash
"$PYTHON_BIN" -m scripts.roi_explore.manual_observation_165.cli abort --session "$SESSION_DIR"
```

`active_session_cannot_abort`はまだwriterが動いている合図。process終了を確認するまで繰り返さない。`accepted_session_cannot_abort`は回復調査へ回す。race予約は失敗/中断でも解除しない。source変更でverify不能になった場合も、pinやmanifestを新コードへ書き換えず、対応する凍結環境で照合できるまで保留する。運用台帳/console/verify結果はsession外に保存する。

## 5. 公式単勝払戻による精算

観測成功と精算成功を分ける。初回観測の成功を公式資料待ちの間に取り消したり、未精算を損失0円として計上したりしない。

1. 発走後、既存の許可済み経路で**保存された**当該raceの全頭結果と単勝払戻原HTMLを使う。`settle`自体は取得しない。今回、公式資料の新規取得も行わない。材料がない場合は未精算のまま待つ。
2. 外側の資料台帳へsource/ref、元取得observed_at、元archive/原byte SHA、復号encoding、UTF-8 HTML SHAを保存する。`observed_at`は元取得時計で、seal/postより後かつ現在以前。JSON作成時刻、ファイルmtime、再読取時計で補わない。
3. `raw_html`は復号した原文、`raw_sha256`は**raw_html.encode('utf-8')のSHA256**。gzipや元EUC-JP byteのSHAと混同しない。原文内race ID、全出走結果/馬番、全winner単勝額を確認し、凍結されたID対応だけで正規化する。
4. 外側へevidence.jsonを作る。必須キーは `race_id,revision,observed_at,source,raw_html,raw_sha256,results,payouts`。sourceは`netkeiba_result`、revisionは1から連続。resultsは`horse_id/horse_number/result_status/finish_order`、payoutsは`horse_number/payout_yen`。金額は100円あたりの印字公式単勝額。手計算EV/最終オッズ/勝馬人気で埋めない。
5. まず観測sessionのverifyを行い、元decisionを変えず、用意したevidenceを一度だけ渡す。

```bash
EVIDENCE_PATH='UNSET'
"$PYTHON_BIN" -m scripts.roi_explore.manual_observation_165.cli settle \
  --session "$SESSION_DIR" --evidence "$EVIDENCE_PATH"
"$PYTHON_BIN" -m scripts.roi_explore.manual_observation_165.cli verify --session "$SESSION_DIR"
```

| 精算結果/状況 | 最終扱い |
|---|---|
| 全頭/全winner/原文一致、通常の単独winner | settled。両腕それぞれ公式額で精算し、検証済みの通常例だけ主比較へ記録 |
| 同着 | 全winnerの別々の公式額でsettled。dead_heat=true、主比較から分けて報告。最終オッズで代算しない |
| 結果不足・払戻未保存・原文/ID不整合 | pending/incomplete/inconsistent。**両腕とも払戻/損益null**。0点側も勝手に精算しない。元資料/理由を保全 |
| 判断後の取消/除外・全頭集合変化・返還 | 現CLIは返還を自動会計しない。field変更/不足として未精算・主比較保留。公式返還額の手計算補完や再予測をしない |
| 両腕とも0点 | 全公式資料がそろい正常精算できて初めて両腕0円。観測成功だが優劣の根拠にはしない |
| 公式訂正/不足資料の後日完備 | 後の実取得時計を持つ新しい保存原本を、次の連続revisionとして追記。前版/元decisionを保持。採点は最新の検証済みrevisionを使い、好都合な版を選ばない |
| 精算途中で中断/ファイル片側のみ | RECOVERY_REQUIRED。原文/既存revisionを保全して書込状態を調べる。新規取得で時計を作り直したり、同名ファイルを上書きしたりしない |

**完成済み同revisionへsettleを再実行しない。** 現CLIは毎回accounted_atを記録するため、同内容でもimmutable_record_conflictになり得る。完了済みはverifyだけを使う。正規化ミスの修正でも、同じ元取得時刻を新時刻に偽装して次revisionを作らない。同時刻原本の修正や部分書込の回復は、別途整合性を確認できるまで未精算で保留する。

## 6. 初回を終えるとき

外側の台帳に、選定1件/見送り理由、起動回数、観測検証成否、各工程時計/quote age/両ready、停止/失敗理由、公式revision/status、同着/返還/未精算の区分を残す。取得失敗や0点を消して成功率を上げない。現在の165/166 replayやfixtureは実観測件数へ混ぜない。

観測検証成功→公式通常精算の1件がそろってもモデル採用は行わない。初回は保存契約の運用確認であり、ROI単独対旧予測追加の結論は少標本のため保留する。
