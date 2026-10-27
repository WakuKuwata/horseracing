# 168 保存済み候補・GO確認の独立レビュー

2026-10-27。167の選定・運用規則と166のread-only境界を独立に読み、保存済み情報だけで対象候補を調べる今回の範囲を確認した。独立担当は実DB・HTTP・モデル実行を行わず、165〜167を変更していない。

## 事前提言

- 確認時の実UTC/JST時計とDB snapshotを保存する。対象は実際のpostを使い、T−45分の選定余裕と2026年JRA平地等の条件で判定する。日付だけやrace IDからpostを推測しない。post不明、過去、年非対応、出走不足、結果ありは別の除外理由として残す。
- metadataに基づく候補順（post/race ID）と、実行GOを分ける。操作担当の対応可能時間が未提示なら、メタデータ上の候補があっても対応可能と捏造しない。仮候補と固定対象を分け、全条件がそろわなければ実行対象はnull/NO_GO。
- 未来の保存DB行が0でも、現実に開催がないという意味ではない。DB以外の既存card archive/indexも確認した範囲を示し、その範囲で資料不足と報告する。外部サイトで補充しない。
- 全頭ID/馬番/status、全予定平地カードは、保存原本と照合できる範囲を示す。DB上にrace_numberが並ぶだけで公式カード完全性を断定しない。raw source IDとcanonical IDの完全一致か、明示一意mapを要求し、prefix推測をしない。
- 出走原本取得5分、DB guard5秒、quote取得30分、両予測readyが判断以前、は別条件。DB読取時計/updated_at/mtimeを元取得時計へ付け替えない。未保存のquote/readyを再計算で補わない。
- 起動直前しか確認できない条件は未確認/未到来と記録する。将来の公式結果そのものがまだ無いことと、公式原本を保存・精算する経路が未確認であることは区別する。過去の成功9件だけで未来候補の払戻保存を確認済みにしない。
- 166でread-only接続に成功しても、165用DATABASE_URLや運用throttle書込権限の確認とはならない。権限/robots cache/cooldown/対応時間/電源・時計等が不明ならGOにしない。検査目的のHTTPやDB書込probeは不要。
- DBは166のdefault/transaction read-only境界とSQL台帳を再利用する。robots cacheは既存ファイルの直読を使い、cache更新・lock作成・prewarm・予約・cooldown解除を起こすAPIを呼ばない。
- 今回は候補調査だけ。165 live plan/observe/settle、race予約、scheduler、モデル推論、購入は実行しない。新規収集false・比較追加0・can_adopt_model=falseを維持する。

## 実装・成果物の確認

rootの保存済み候補台帳とGO結果が完成後、実データを直接読み直して選定順・除外/未確認理由・読み取り専用証拠・取得停止が一致するか確認する。

## 実保存資料の独立確認

ローカルarchiveのURL marker 7,750件から、実shutuba 5,629保存版を抽出した。race別に元archive filenameの取得時計が最新の原本3,665件を選び、ページtitleにある本人開催日を全件確認した。2025年792件、2026年2,873件、読取不明0件、本人開催日の最新は2026-10-25。2026-10-27以後の本人カードは0件、独立race_list原本のURL markerも0件だった。

10/25原本に含まれる10/31等への「次」リンクを、未来レースの本人出走資料へ昇格させていない。元取得時計の最新は2026-10-26T04:32:02.460370Z。ファイルmtimeや現在の読取時計で取得時刻を補っていない。全3,665原本の圧縮byteとURL markerのSHAを照合し、全行台帳を保存した。

## 成果物の独立検算

**PASS**。`artifacts/168-saved-candidate-go/run-01` の保存JSONを直接照合した。独立担当はDBを再照会しておらず、DB値はrootが取得したread-only snapshotの保存結果・SQL台帳・設定証拠に対する論理/SHA検算である。

- DB総140,049レース、保存開催日最新2026-10-25、同日36件すべて結果あり。2026-10-27以後の保存行・未来post・当日以後post未保存行は0で、candidate-ledger/仮候補は空。
- DB snapshotとアクセス確認はいずれもdefault/transaction read-onlyがon、repeatable-read、終了はrollback。SQL台帳18+3行すべて正常完了、形状とSHAが一致。Pythonネットワーク試行0。
- DB確認は14:10JST、ローカル出走原本確認は14:11、アクセス確認は14:12の別観測であり、同時snapshotの全GO成立とは扱わない。
- robotsの元取得時計/期限から現在は24時間TTL超過。cache原本SHAが一致し、refreshやslot予約/cooldown解除はない。canonical throttleの現状態がblockedでなくても、未来起動枠の予約/競合確認済みにはしていない。
- 166 fallbackのread-only読取権限とlive用DATABASE_URL/運用権限を分離しており、現在shellのlive設定なし・操作担当の対応可能時間未提示を未確認のまま残した。
- source/modelファイルをhashのみで照合し不一致0。モデル推論、live plan/race予約、observe、公式精算は0。
- 最終判定NO_GO_SKIP、対象race/postはnull。quote30分・両readyの判断前保存・三段階確認・公式払戻条件は変更せず、まだ実行していない事後条件はNOT_YET_DUEとした。

コードレビューで、相対出力pathのresolve、robotsのfreshだけでなくAllow確認、throttleの現在backlog/interval確認を提言しroot実装へ反映された。今回の実値ではいずれの修正もNO_GO結論を変えない。

成果物: `local-inventory.json` / `local-card-ledger.json` / `local-inventory-report.md` / `independent-audit-run-01.json`。再現補助scriptはread-onlyローカル読取だけであり、DB/HTTP/モデル実行の入口を持たない。

## 判定の限界

「調べた保存資料から候補を確定できない」との判定であり、現実に今後開催がないとの主張ではない。全コンピュータ上の全ファイル探索でもない。将来の操作担当、起動時の原本5分・quote30分・両ready、時計/電源/保存容量、候補別の全card/ID対応、公式原本保存経路は別途確認が必要で、今回の未確認をPASSへ変えていない。

最終manifest SHA `fb7f97718f85fdb4c75f1a91d59cdedc52548c540594c713b2c4f452f54a717d` の10基底ファイルと、`input-pins.json`の全入力/source SHAも直接照合してPASS。最終監査結果は `independent-audit-run-01.json`。rootの新規72テスト通過出力も確認した。監査scriptのRuffもPASS。新規の実DB照会やHTTPアクセスを追加せずに検算した。
