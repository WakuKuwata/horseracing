# C13: 前向き比較(161〜172)を始めるかどうかの材料

2026-10-28 22:45 JST 時点。**判断は利用者のもので、この文書は判断材料の整理だけ。取得・観測・設定変更は一切していない**(確認はすべて read-only・通信なし)。

## 1. 何を決めるのか

161 で設計し 162〜172 で道具を揃えた「A=市場 q+ROI / B=+旧 preweight を同一 quote で判断前に保存し、公式払戻で精算する手動観測」を**実レースで始めるか**。始めない限り、ROI 系の発走前の対等比較は 159 の **9 レース・129 頭・4 開催日**(両腕の買い目が完全一致・差 0)のまま増えない(160 で保存資料からの拡張は不能と確定)。

判断の前提になる既決事項:

- 役割は決着済み(151/157/158/159/160): ROI 系が主・旧予測は比較用。前向き比較は「旧予測を足す価値があるか」の最後の未決を**手動の少数例で確かめる**計器であり、採用判定の道具ではない(165: `can_adopt_model=false` 固定)。
- 161 §6: 最初の 2 開催日は保存品質の**試運転**(利益で判定しない)。本比較は必要期間・終了日を開始前に固定してから。
- 167: **1 日 1 レース・observe 起動 1 回**。人気・オッズ・予測差・EV で対象を選ばない(予定発走の昇順で先頭)。

## 2. いま満たしているもの(確認済み)

| 項目 | 状態 | 根拠 |
|---|---|---|
| CLI 一式 | plan/observe/rehearse/verify/settle/abort 実装済み・試運転 run-02 で sealed→verify→native 再推論まで成功 | 165 result-review |
| 実 DB read-only での所要 | process 全体 **60.55 秒**・peak RSS **4.02 GiB**(M5 Pro・24 GiB) | 166 |
| 専用 role | `hr_manual_observer`(login 可・非 superuser)が実 DB に存在。必要列 ACL は PASS | 170・本日の preflight `ACL_AND_SETTINGS_PASS_NOW_ONLY` |
| private profile | `config/.env.manual-observer`(0600・gitignore)。profile/共通設定/実 policy とも 60 秒で一致 | 172・本日の preflight |
| source throttle | `netkeiba.com` 1 行・cooldown なし・backlog 0 秒(10/27 15:59 の robots 取得以降は未消費) | 本日の preflight |
| 停止ゲート | `manual_observer_172.py observe` は `PAUSED_NOT_STARTED`(exit 3)・ops の更新 POST は 503・worker は起動しても PAUSED 終了 | 本日実行 |
| 判断時刻の保存 | 2026-10 の全 264 レースに `post_time` あり(167 の固定窓 [T−15,T−9) の前提) | 実 DB |

## 3. いま満たしていないもの(開始前に要るもの)

| # | 項目 | 現状 | 何が要るか |
|---|---|---|---|
| 1 | **対象レースの将来出走表** | 実 DB の最新開催日は 10-25・`race_date >= 今日` は 0 行。保存 shutuba 原本も 10-25 まで(168) | 次の開催日の race_list 1 本+出走表(1 レース 1 本)の**新規取得**。167 は「全同日平地カード保存済み」を要求するので 1 レースだけでは足りない(最低でも同会場の全レース) |
| 2 | **`collection_paused` の解除** | 172 の policy は pause 以外を拒否し、**設定の編集だけで再開する切替は用意していない**(`source_operations_172.py` が `collection_paused=true` 以外を reject) | 「再開を許す policy」へのコード変更と、その承認。ops 経由(`POST /ops/v1/days/{date}/refresh`)で取るなら ops/worker の再起動も要る |
| 3 | **robots cache** | `race.netkeiba.com` は 10/27 15:59:42 JST 取得(404=absent・Allow 扱い)・**10/28 15:59:42 JST に失効済み**。preflight は `robots_cache_expired` で `BLOCKED_NOW` | 起動の 24 時間以内に 171 の script で 1 GET(明示許可が条件・自動 retry なし)。D18 と同じ |
| 4 | **担当時間** | 未提示 | 1 レースにつき **T−45 分(候補凍結)〜T−9 分(封印)** の連続立会い+発走後の公式資料確認と settle。起動は [T−15,T−14) の 1 分間に 1 回だけ。T−14 分までに起動できなければ当日見送り・別レースへ乗り換えない |
| 5 | **起動直前の確認** | 未到来 | 原本取得 5 分以内・時計同期/電源/スリープ/容量・実 URL の robots・slot 競合・他の重い処理の停止(169 の `launch_only_unconfirmed` 9 項目) |
| 6 | **C14(表示値の更新規則)** | 未決 | 161 と残タスク 14 は「前向き比較で何が出たら表示値を変えるか」を開始前に決めるとしている。決めずに始めると事後の読み替えになる |
| 7 | 本比較の必要期間・終了日 | 未固定 | 161 §6。下の §5 を見ると、167 の手動 1 日 1 レースでは本比較の標本に届かない |

## 4. 次の開催日と当日の流れ(試算)

- 次の開催は **10/31(土)・11/1(日)**(東京・京都。11/1 は天皇賞・秋)。JRA の開催日程ページ(`jra.go.jp/keiba/calendar2026/2026/10/1031.html`)と netkeiba の WIN5 対象レース掲載で確認。10/25 の保存出走表にも「次 10/31」のリンクがあった(168)。今日は水曜なので、出走表の公開は木〜金。
- 取得の数(source interval 60 秒): race_list 1 本+1 会場 12 レースの出走表 12 本+nk 馬の profile 補完(scrape_entries が自動で走る・頭数分)= **最低 13 分、profile 補完を含むと数十分**。2 会場なら倍。金曜夜〜土曜朝に 1 回取れば足りる(1 レース 1 観測なので当日の再取得は不要・quote だけ observe が 1 回取る)。
- 当日: 対象は「対応可能な候補を予定 post 昇順で先頭」=通常 **1R(10:00 前後発走)** になる。担当時間が午後しか無いなら、候補集合を「担当時間内に発走するレース」に絞って先頭を取る(167 §1 はこれを許している=「操作担当の対応時間を使う」)。
- 観測 1 回 = T−15 分起動 → 約 60 秒で両予測保存 → T−10 分まで待機 → [T−10,T−9) で判断・封印。その後は触らない。
- 精算: 発走後に通常運用で結果と公式払戻が保存されてから(取得停止中なので**これも新規取得**。worker を動かすか、`scrape` CLI で結果ページを 1 本取る)、evidence.json を作って settle を 1 回。

## 5. 手動 1 日 1 レースで何件集まるか

- 159 の実績: 9 レースで買い目 8 点(4 レースは無購入)= **1 レースあたり約 0.9 点**。
- B8(製品の前向き現況)の「判断保留」解除の目安は **300 点**。現在 8 点(38 レース・的中 1・145%)、混合列は 4 点。
- 167 どおり 1 日 1 レースなら **1 開催日 ≈ 1 点**。年間の開催日は約 100 日なので 300 点は**約 3 年**。2 開催日の試運転では 2 点前後。
- 1 日の全レース(36)を取っても約 32 点/日=10 開催日(約 5 週末)で 300 点だが、**165 は 1 process 1 レースで同時観測を禁じ(167 §1)、定期起動は憲法の先送り事項**。全レース化は 161 §2 の「別段階の整備」であり、今回の判断の中には無い。

つまり**今決められるのは「試運転 2 開催日を始めるか」だけ**で、それが本比較の標本に育つ経路は未整備。試運転の成果は「保存契約が実レースで通るか」(来歴・5 分条件・10 秒 deadline・固定窓)であり、ROI の数字ではない。

## 6. 始める場合の最短手順(承認が要る箇所に ★)

1. ★ C14 を決める(残タスク 14)。
2. ★ pause 解除を許す policy 変更(コード・テスト・172 の独立監査と同型の確認)。`collection_paused=false` を受け付ける形にしても、**worker の自動追随(predict_auto 等)は C15 と絡む**ので、再開の範囲(ops の days refresh だけか・worker も動かすか)を先に決める。
3. ★ 171 の手順で robots を再取得(起動の 24 時間以内。金曜夜なら土曜日中は有効)。
4. 金曜夜: 対象会場の race_list+出走表を取得(ops `days/{date}/refresh` か `scrape list-races`+`scrape-entries`)。取得後に `hr_manual_observer` の preflight を再実行し `OFFLINE_PREREQUISITES_CHECKED_LAUNCH_PENDING` を確認。
5. 土曜: T−45 分までに候補凍結→ `plan` → T−16 分 GO 確認 → [T−15,T−14) に `observe` 1 回 → verify。
6. 発走後: 結果・公式払戻の取得(新規取得)→ evidence.json → `settle` → verify。
7. 日曜も同じ。2 日終えたら 161 §6 の監査(対象数・成功数・失敗理由・鮮度・遅延・未精算)を残し、本比較の期間を決めるかどうかを改めて判断。

## 7. 始めない場合

- 残タスク 13 は「保留」として理由(標本が育つ経路が未整備・担当時間)を記録。D18(robots)は不要になる。
- 9 レース・差 0 の結論保留が続く。製品表示は影響なし(B8 の 38 レースは通常運用の `oddsobs=quote` 行から増えるが、これも取得再開が前提)。
- 旧予測の比較用保管(154)はそのまま。C15(旧予測の自動生成停止)は独立に決められる。

## 8. 確認に使ったもの

- 実 DB(read-only): `races`(最新 10-25・将来 0 行・10 月の post_time 264/264)、`fetch_throttle_state`、`pg_roles`、`market_ev_predictions`(quote 行は 10-12 以降 121 レース)。
- `scripts/manual_observer_172.py preflight`(NO_GO・robots_cache_expired)/ `observe`(PAUSED_NOT_STARTED・exit 3)。出力は scratchpad のみ。
- `artifacts/robots_cache/9a9ed120b9d660f31e7f.json`(fetched_at 2026-10-27T06:59:42Z)。
- specs 161/165/166/167/168/169/170/171/172 の manual・result-review、`live/orchestrate.py`・`ops/runner.py`・`scrape/pipeline.py`(day discovery は `discover_races`、保存は `scrape_entries`)。
- B8 API(`GET /api/v1/market-ev/prospective`): 38 レース・8 点・混合 12 レース・4 点。
