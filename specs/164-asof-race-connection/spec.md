# 164 実レース保存入力と発走前確認の接続契約

2026-10-27確定。実レースの保存資料から特徴量・発走時刻・出走馬集合・結果未確定の根拠を抽出し、保存契約へ接続する。新規収集、DB接続、学習、自動運用、実購入は行わず、162/163を変更しない。

全出力は `mode=offline_asof_connection, eligible_for_prospective=false`。過去の資料を現在読み直した時計と、元の観測/生成時計を区別し、過去に存在しない予測・ready・sealを作らない。

## 実保存の入力アダプタ

### 旧モデルの実特徴量

133 capture原本を160の凍結source index/receiptで照合し、全138特徴、元dtype/入力内部digest、全頭ID・馬番、過去started履歴の内部digest、モデル本体SHA、元の時系列を再検証する。source hashを呼出側が任意に指定して既存pinを置き換えることはしない。

元の`acquisition_finished_at`は特徴量が観測済みだった上限時刻として扱う。`record_written_at`はpublish/fsyncより前なので、耐久保存完了時刻とみなさない。明示的nullは保持し、未保存列をnullや0へ埋めない。

旧入力/履歴を現在の実モデルで再推論する場合はcurrent replayとして別保存し、現在の開始/完了/保存確認時計を記録する。元の予測保存receiptの代わりにしない。

### MEV入力

MEVは77列を要求する。実保存parquetの列が77列そろっていても、それだけで判断時点以前の利用可能性を証明しない。今回の候補8表はschemaだけを確認し、7表の完全列を認めるが全件as-of時刻証拠未確認のため値を判断に使用しない。

保存feature snapshotの汎用入口は、原本JSONと原本SHAに加え、生成時刻、観測済み上限、全列source時刻、全行available時刻、履歴available時刻、別の保存確認receiptを要求する。

- 列/行/履歴の利用可能時刻 <= 生成 <= 観測済み上限 <= 保存確認 <= 判断。
- MEVは使用quote原文SHAを保持し、quote受信 <= MEV生成、市場列のsource時刻も受信以後。全頭価格/人気/q・順位・人気馬価格等を再導出して照合する。
- 旧モデルは現在quoteへ依存しない。元の特徴量利用可能時刻がquote以前でも正しく受け入れ、取得後へ付け替えない。
- 完全列/全頭、モデル年・特徴名、旧preweight、厳密な過去履歴を検査する。型変換は固定モデルの入力定義に従い、DBを読み直さない。

## 発走前・出走集合の保存観測

`guards.py`はsource bytesとSHAから再導出する。呼出側の`pending=true`や馬集合だけを信じない。

| 入力形式 | 証明できる範囲 | 厳格採用上の不足 |
|---|---|---|
| 133 capture JSON | 取得時の特徴行/全頭集合、初期結果行数0、予測後の結果行数と発走時刻確認、各元時計 | 予測後/判断直前の馬集合再取得なし。durable guard receiptなし。decision guardなし |
| 保存出馬表HTML | 原文の発走日/時刻、馬ID/馬番、取消/除外、filename UTC取得時計 | HTMLに結果表がないだけでは結果未確定を証明できない。DBpending観測/guard receiptなし |
| `guard_observation_v1` | 保存されたrace/entries/results各query原文と開始/完了時計、保存確認receipt | 完全形式を合成query fixtureで検証。既存partial資料をこの形式へ昇格させない |

HTMLはcanonical URL、raceID、主出走表を確認する。予測ラップ副表が同じCSS classを持つケースは明示的に区別し、未知の重複は拒否する。`nk:`を除去する等の推測結合をしない。

完全query形式では、全馬の状態をstarted/cancelled/excludedとして保存し、started集合を取り出す。結果未確定は結果query配列が空である事実から判定する。観測時刻はqueryの開始/完了両方を検査し、長時間前に開始して今完了したqueryをfreshへ昇格しない。開始時計は当該snapshot/readの開始を表し、古いtransaction snapshotを新しいmethod呼出時計へ置換する用途に使わない。実query実行コードはこの版には含めない。

## 工程間の結合

候補には元post、固定判断枠内のreference decision、全頭集合、両特徴量source、quote source、両予測source/ready、before/after/decisionの観測sourceと各stage anchorを持たせる。個別のtimestampだけでなく因果順を検査する。

1. before guardはquote取得開始をanchorとし、その時点以前5秒以内に全queryが始終し、保存確認済み。
2. quoteは原取得時計と保存確認時計を保持。判断まで0〜1,800秒、未来取得を拒否。元raw応答が必要で、chaosの正規化fieldだけでは完全契約としない。
3. 両モデルの特徴量は各入力利用時点までに保存済みであり、使用行列・履歴・source SHAを予測出力へ結合する。MEVにはquote受信/保存後の入力を要求する。
4. after guardの全query開始は両予測計算完了以後。鮮度5秒はafter stage anchorに対して検査する。
5. final guardの全query開始は両予測の耐久保存確認、after anchorの双方以後。最終鮮度5秒は実判断時計に対して検査する。
6. 全stageでrace/post/全頭集合が一致し、結果未確定。anchorはbefore <= after <= decision。before/afterを常に判断直前5秒へ押し込む誤った制約は設けない。
7. 実判断と封印保存確認の双方で、quote取得から30分以内、発走前、発走10分前を含み9分前を含まない固定窓を再検査する。時刻精度はµsまで、細かい値を黙って丸めない。

特徴量・guard・quote・予測の各source原文とreceiptを再読/再検査し、全頭・source/入力/出力digest・モデルmember結合を確認する。予測は実モデルの消費行列/全member証跡へ結ぶ。改変した特徴量、後更新行、将来receiptを、ハッシュだけ整えて受け入れない。

## 保存形式

### 特徴量原本 `model_feature_snapshot_v1`

必須: `artifact_kind, schema_version, mode, eligible_for_prospective, kind, race_id, race_date, generated_at, observed_by_at, rows, history, source_times, row_available_at, history_available_at, quote_sha256`。rowsは全頭のID/馬番/features。旧quoteSHAはnull、MEVは実使用原文のSHA。source_timesは全列を、row_available_atは全頭を明示する。

別`feature_ready_v1` receiptはmode/eligible=false/sourceSHA/available_by_atを持つ。元133入力はこのreceiptがないためcomponentとして復元し、strict durable要件では拒否する。

### 予測原本 `model_prediction_snapshot_v1`

race/date/kind、feature_packet SHA、163と同じ実モデルprediction本文を持つ。run_id、モデル/本体SHA、入力利用/開始/完了、全行の元input_asof/created/updated、全頭確率・全member/消費行列を保持する。別`prediction_ready_v1` receiptを要求し、予測計算 <= 保存確認 <= 判断を検査する。

### 候補とjournal

`journal/candidates/<race>/reference.json`は各source/receiptへのpath/SHA、固定postとreference時刻を保持。`checks.json`は個別gate/理由を全件残す。`components/<race>`には何を証明できたかを保存し、partialをaccepted扱いにしない。

`AsOfEvidenceStore.seal_now`は現在のRealClockでのみ動作し、既に発走したreferenceは新規封印を拒否する。完全な保存packetを使う合成正例はテストできるが、実保存資料の完備性とは分離する。採用時もmode/eligible=false、schedule_origin=saved_guard_packet、real_race_verified=falseを明示する。

decision→seal→acceptedはfsync/readbackと再gateを挟み、1race1件。差替え・途中sealの自動採用を拒否。検証時は元candidate/全source/guard/predictionを再検査し、両腕確率・EV・買い目も再計算する。

## 精算と非保証

新modeにも公式単勝額による精算入口を接続する。現在より未来の公式観測は拒否し、原文/全頭結果/全winner払戻一致を要求。同着は各公式額、不足/不整合は両腕null、訂正はdecisionを変えずrevision追記。確定オッズを使う代替は設けない。163の過去9件の実公式会計も別replayで検算する。

新規取得や当時不足していたreceiptの復元は行わない。SHA/原文/時計の結合は改変整合性を検査するもので、敵対的な全証跡再作成を防ぐ署名ではない。実query/実as-of snapshotを生成する将来adapterは、ここで定めた原文・clock・snapshot/read開始・保存確認を実際に記録する必要がある。
