# 手動CLIの使い方

リポジトリrootで `training/.venv/bin/python -m scripts.roi_explore.manual_observation_165.cli` を使う。定期処理は登録しない。以下の`rehearse`と`verify`は接続を遮断して動く。保存先は新規directoryを指定する。

```bash
training/.venv/bin/python -m scripts.roi_explore.manual_observation_165.cli rehearse --out /tmp/manual-165-rehearsal
training/.venv/bin/python -m scripts.roi_explore.manual_observation_165.cli verify --session /tmp/manual-165-rehearsal/session
```

`rehearse --scenario field_changed|result_appeared|feature_failure` で不採用の試運転ができる。実モデル21本を使うが馬/入力/quoteは合成で、比較対象には数えない。

## 将来、実観測を開始するとき

今回この経路は実行していない。DB接続設定と既存fetcherのアクセス設定を使う。実行前に予定発走時刻（timezone必須）、レースID、固定モデルの対応年2026を確認する。

1. T−15分より十分前に `plan --race-id <12桁> --post-time <ISO8601> --out <登録dir>` で事前登録する。出力SHAを保持する。コード/モデル変更後は登録を再利用しない。raceIDと日時・postのDB一致は観測時にも検査する。
2. T−15分〜T−9分の間に手動で `observe --plan <登録dir>/plan.json --plan-sha <出力SHA> --out <新規sessiondir> --enable-collection` を一度だけ起動する。この明示フラグがない場合、DB/HTTP構築前に停止する。早く準備できた場合はT−10分まで待機する。
3. `status.json`のstateを確認する。sealedなら`verify --session <sessiondir>`で全SHA/時計/順序/実21本の再推論を検算する。公式資料がまだなければ精算額はnullのまま。
4. 発走後に既存の保存公式資料を `settle --session <sessiondir> --evidence <公式evidence.json>` へ渡す。このコマンドは取得を行わない。訂正はrevisionを1ずつ増やし、observed_atも増やす。同revisionは同内容の再実行のみ許す。未保存/不足/不整合の払戻で0円負けと確定しない。
5. 中断してmanifestがない場合、プロセス停止を確認して `abort --session <sessiondir>` を実行する。稼働中のwriter lockがあれば拒否する。accepted済みはabort不可。中断の記録を残して未採用stagingを除去する。race予約は解除しないので、そのレースを再取得して好都合な記録へ入れ替えない。

liveのrace予約先は `artifacts/manual_observation_registry_165/<race_id>.json`。fixture予約は当該session内に限定する。今回このlive予約は作っていない。

## 保存資料の確認

- `events/before.json`, `after.json`, `final.json`: 各段階のrace/entries/resultsとquery開始/完了。
- `events/quote.json`: 原応答/source/取得開始/受信。以降のMEVもこのquoteから作る。
- `events/features.json`: 旧138/MEV77の全入力、厳密な過去履歴、DB snapshotとsource証拠、ID map。全raw DB poolは保存しない。
- `events/prediction_*.json` と `*-ready.json`: 実member出力/入力消費証拠と耐久保存完了の観測。
- `decision.json`, `seal.json`, `accepted.json`: 固定151の両腕、保存確認のSHA鎖。判断時の両腕はEV>1、1頭100円、上限10頭/1000円、同じソート/予算。
- `clock.json`, `runtime.json`, `manifest.json`: 実時計・環境・全baselineファイル検査。seal後の公式revisionは独立に検査する。
- `settlements/0001/{evidence,result}.json`: 原HTML、全頭結果、全winner払戻と会計。同着は精算するが主評価から分ける。

公式evidenceは `race_id`, `revision`（整数）, `observed_at`（UTC timezone付）, `source="netkeiba_result"`, `raw_html`, `raw_sha256`, `results`, `payouts` の完全形。results行は`horse_id/horse_number/result_status/finish_order`、payouts行は`horse_number/payout_yen`。原HTMLと正規化行の一致を必ず検査する。厳密なschemaは `settlement.py` と `test_manual_settlement_165.py` の例を参照する。

失敗ログには理由と除去対象SHAを残す。外部サービスの例外本文は秘密情報混入を避けるためそのまま保存しない。時計異常、DB遅延、保存不能、期限超過は採用件数を増やすために緩和しない。
