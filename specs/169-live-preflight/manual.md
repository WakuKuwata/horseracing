# 169 live設定・取得停止preflightの使い方

**このCLIは確認専用。取得・observe・refresh・role/GRANT適用のコマンドは持たない。** 全結果で`can_collect=false`。現在のrole/秘密値/対象raceが未設定ならNO_GOで終了する。165〜168は変更していない。

## 今回整備した設定

`config/manual-observer.env.example`を基に、`config/.env.manual-observer`を0600・上書きなしで作成した。後者はgitignore対象。実パスワードを入れず、接続先確認false/role・秘密値・承認済みinterval未充足のままにしている。現在このファイルだけでliveが有効になったとは扱わない。

通常の`DATABASE_URL`を勝手にexportしたり、deploy/.envや既存のDBroleを変更したりしていない。private fileはPython dotenvで読んで検査し、shellとしてsource/evalしない。

リポジトリrootで:

```bash
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli check \
  --profile config/.env.manual-observer --out /tmp/manual169-profile-check
```

保存済みの設定と検査用DBのcatalogを調べる場合:

```bash
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli inventory \
  --out /tmp/manual169-catalog
```

`inventory`は166の明示したread-only検査接続を再利用する。live DSNの代用ではない。保存済みCompose内部`db:5432`とhost側local15432は黙って変換しない。native live checkにはOWNER URLや166 fallbackが使われない。

結果のファイル集合/SHA/source pin/SQL形状/固定停止条件をオフラインで検証する:

```bash
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli verify \
  --out /tmp/manual169-profile-check
```

全保存先は未使用directoryを指定する。verifyは記録の整合性検証であり、後日のcacheや接続状態が今も有効という保証ではない。新しいcheckが古い結果を上書きすることもない。

## live用設定を具体化する順序

1. 予定するhost/port/cluster/databaseを確認する。既存deploy保存設定はCompose内部向けで、今回検査したlocal clusterとは別の可能性がある。確認なしのhostname置換やownerへの切替で接続を通さない。
2. 基本5表SELECT＋throttle必要列のみ書込できる専用loginを使う。調査済みlocal clusterの非builtin roleはaiuma（superuser）のみだったため、このroleをlive用に採用しない。専用role/秘密値の作成は今回行っていない。
3. [未適用SQL案](evidence/provision-observer-role.draft.sql)と[権限契約](evidence/permission-contract.json)を、確定したDBの管理作業として評価する。SQL案は接続中DBと指定DBの一致、必須変数、既存role衝突を検査し、単一transaction。passwordはSQL/コマンド引数/チャットへ書かず、管理側の秘密管理またはinteractive password設定を使う。**この手順でSQL案を実行したとは扱わない。**
4. private profileへ正しい専用DSN、expected role/DB、endpoint確認、既存の運用承認済みsource intervalを入れる。既存intervalを短くして通さない。今は値が未確定なのでplaceholderを維持した。
5. `check --profile ...`で照合する。PGOPTIONS/PGSERVICE等の暗黙のlibpq上書き、DSN query options、Compose内部hostname、非private file、placeholder、expected role/DB不一致は拒否する。現在版は明示loopback endpointを対象とする。遠隔DB等を黙って取り扱わない。

将来165を使う際には、検査したprofileと同じ明示環境で起動する必要がある。`config.load_profile`/`profile_environment`は秘密値をconsoleへ出さず、設定をshell評価せずに既存165の呼出環境へ反映するための関数である。169 CLI自体は165を起動しない。現在は有効なlogin/対象raceがそろっておらず、起動はしない。

## 必要権限と検査の意味

- `CONNECT`、`USAGE ON SCHEMA public`、基本`races/race_horses/race_results/horses/id_mappings`のSELECT。
- `fetch_throttle_state`のSELECT、INSERT(domain,next_allowed_at,updated_at)、UPDATE(next_allowed_at,blocked_until,block_reason,updated_at)。SELECT FOR UPDATEに必要なUPDATE権限も含む。実予約/行lock/書込probeをしない。
- sequence/DELETE/DDL/予測・結果・購入tableの書込は不要。公式settle本体はfile-onlyであり、公式DB払戻tableの書込権限も不要。
- 列ACL、実login/role/DBの一致、superuser/owner/BYPASSRLS等の過大権限、membership、schema解決、RLS、rewrite、throttle書込に作用するtriggerを検査する。基本5表の通常DMLtriggerはSELECTでは発火しないため、それだけで不成立にしない。

検査用inventoryは接続既定readonlyを強制している。**そのonはliveの実効既定値の証拠ではない。** 将来の有効profile検査では、SQL gateをconnect前に置き、最初のアプリSQLでtransaction READ ONLYにしてSELECT/SHOWだけを行う。startup defaultreadonlyの上書きはせず、liveのnative defaultと検査transaction readonlyを分けて記録し、rollbackする。今回は有効profileがないため、そのnative実測は未実施。

live fetcherは通常transactionでthrottle状態を書くため、native defaultreadonly=onやstandbyでは動かない。guard/特徴snapshotは実装自体が別途READ ONLYにする。ACLがあっても将来の実書込/接続成功を保証したとはしない。

## robots・throttleチェック

既存robots JSONを直読し、schema/origin/status/base64/size/重複key/元時刻/24時間TTL/Allowを検査する。ABSENTは保存404/410のみ。未来時刻・期限切れ・fresh Disallowは不成立。cache API、lock、mkdir、prewarm、取得はしない。

```bash
training/.venv/bin/python -m scripts.roi_explore.live_preflight_169.cli check \
  --profile config/.env.manual-observer --launch-at '2026-12-01T14:45:00+09:00' \
  --out /tmp/manual169-launch-time-check
```

上の日付は構文説明用で、実raceの指定/予約ではない。実施時の固定時刻へ置き換える。予定起動時にcacheが失効すると分かる場合は、その不成立も出す。pathだけのrobots判定は予備検査であり、race_id付き実URLは既存fetcherで再確認が必要。

throttleは`netkeiba.com`の同一bucketをSELECT。現在cooldown、backlog<=3秒、元updated_at、source interval、10秒deadlineを評価する。cacheがmissing/staleでも取り直さず、cooldownを解除せず、slotを予約しない。profile未設定でDB未照会の場合は「行が無い」とせずNOT_CHECKEDを出す。

現在のPASS_NOW_ONLYは将来の空き枠を意味しない。`--launch-at`を指定してもthrottleのlaunch_readyは未確認のままで、実際の予約は将来の既存live fetcherが送信直前に行う。

## 起動直前・発走後へ残す項目

対象race/全頭ID/同日完全card、元出走原本5分、担当者時間、PC時計同期/電源/スリープ/負荷/保存容量、実URLのrobots/TTL、source slot/cooldown/競合は167の時点で再確認する。

quoteはその場の元取得時計から0〜1800秒（配信時計があれば併用）。両予測の耐久readyが判断以前、3段階確認、[T−10分,T−9分)の判断/保存確認が必須。公式原文/全winnerによる精算、不足null/同着別報告も維持する。今のpreflightや過去のreplayから、未到来のquote/ready/公式結果を補完しない。
