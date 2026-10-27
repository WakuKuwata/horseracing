# 133 新旧モデルの発走前ペア比較(mix-129-nj6 vs lgbm-094-cap900)

**目的**: 新モデル(本番 active)の運用を続けながら、旧モデルと**同じ時点・同じ入力**で発走前予測を両方記録し、結果確定後に公平に採点する証拠を蓄積する。本番の切替・再学習は対象外。

**状態(2026-09-20 午前 JST)**: 9/13・9/19・9/20の72レースに記録あり。実提供条件は46レース・2開催日を採点済み、9/20の24レースは結果待ち。馬体重公表前の主条件は23レース・1開催日を採点済み、同条件を記録した9/20の22レースが結果待ち。最終判定は2026-12-28の窓終了後に1回。

## 2026-09-20 完了監査後の修正

登録期間を記録選択時と採点時の両方で検査する。開始日・終了日は含み、期間外または日付不正のレースは除外する。期間外rehearsalは明示的な診断interimだけで扱い、`DIAGNOSTIC_REHEARSAL_NOT_EVIDENCE` とする。

正式な最終判定は事前登録の `primary.regime`（preweight）だけに限定した。`--mode final --regime serving` は `SECONDARY_SUMMARY_NOT_A_VERDICT` / `verdict=null` の記述集計になる。必要改善幅0、24開催日、期間、比較モデル、係数は変更していない。

主条件の `FINAL` は、実装が置かれたリポジトリの `artifacts/133-prospective-paired-comparison/final/<事前登録SHA256>.json` に結果本体・ハッシュ・実装SHAを含む最終記録を一度だけ保存する。保存先は `--output`・`--looks-log-dir`・実行時の作業ディレクトリから独立している。ファイルロックで計算から保存までを排他制御し、保存済みならDBや記録を再読せず同じ結果を出力する。正式結果の保存後に出力コピーが失敗しても、再実行で同じ結果を取り出せる。既存出力が同じ内容なら成功、異なれば拒否。破損した最終記録は自動再計算せず停止する。

`NO_DECISION`・interim・副条件は最終枠を消費しない。任意の `looks.jsonl` に加え、最終記録はログ指定の有無によらず必ず保存する。最終記録ディレクトリは継続利用・保全し、削除や別チェックアウトへの移行で単一判定の履歴を失わないこと。

対象25テスト・serving全223テストPASS。並行2プロセス、出力失敗からの再出力、CLIでのDB再読禁止も確認した。既存72レースの選択と、主条件23レース・1日／副条件46レース・2日の集計・除外・保留・head指標は、同じ入力で修正前および保存済みinterimと完全一致した（集計時刻を除く）。[修正検証](../../docs/evidence/completion-review-20260920-fix/README.md)。過去の事前登録・証拠は上書きしていない。

## 2026-09-20 の再開記録

9/13の採点は9/16に完了済みだったため、既存結果を保存したまま9/19・9/20を追加した。9/20の未発走22レースは同時点の新旧ペアを新規captureし、発走済みの2レースを含む当日24レースと9/19の24レースは、発走前の本番snapshotから旧モデルを再推論した。48レース全てで新モデルの保存勝率を誤差0で再現した。再推論時刻は実時刻を記録し、発走前の入力保存時刻と区別している。

| 条件 | 採点済み | NLL差（新−旧） | 95% CI | 除外・結果待ち |
|---|---:|---:|---|---|
| 馬体重公表前・主条件 | 23レース・1日 | −0.034810632 | 1日のため算出せず | 出走集合変化1、条件未記録26、結果待ち22 |
| 実提供条件・副条件 | 46レース・2日 | −0.010527114 | [−0.033735156, +0.012680929] | 出走集合変化2、結果待ち24 |

実提供条件の開催日別差は9/13が−0.033735156、9/19が+0.012680929。的中数は新17/46・旧16/46。2開催日だけの途中集計であり、採用・優劣の判定は行わない。全出力は `INTERIM_STATUS_NOT_A_VERDICT`、`verdict=null`、`can_adopt=false`。

既存 `from-production` は `serving` 条件のみを保存する。そのため9/19の24件と9/20のcaptureを逃した2件は主条件では `regime_not_recorded` として除外される。下記の当初設計でsnapshotを主条件へ算入可能としていた記載より、この実装上の制限を優先する。結果を見てから条件や選択規約を変更していない。

保存記録の優先順位は既定どおりcapture→shadow→snapshotで、72レースは重複計上していない。対象unitテスト12件PASS。scorerを使用しない別計算で、保存勝率から各レースNLLと全体平均を再計算し、22captureの発走前保存時刻と48snapshotの再現記録も確認した。この別計算はDB結果ラベル自体の独立監査ではない。

成果物: [主条件](evidence/interim-preweight-2026-09-20.json)、[副条件](evidence/interim-serving-2026-09-20.json)、[再計算・入力時刻確認](evidence/resumption-check-20260920.json)。次は9/20の結果取込後のinterim採点。比較窓・モデル・係数・事前登録は維持する。

- 事前登録(結果を見る前に凍結): [preregistration.json](preregistration.json) — sha256 `b7352356d031582239a2596039fb48a77514fa7315832a940356bd1bb537b11b`
- 実装: `serving/src/horseracing_serving/paired_prospective.py`(CLI `capture` / `from-production` / `score` / `snapshot-roundtrip`)
- テスト: `serving/tests/unit/test_paired_prospective.py`(修正後25件・serving unit 全体223件緑)
- 記録: `artifacts/133-prospective-paired-comparison/records/`(`capture/`・`production_snapshot/`・`days/`。git 非追跡・約 6MB/開催日)
- 証拠: [evidence/](evidence/)(interim 採点・rehearsal 診断・snapshot 再現性・looks.jsonl)
- 直前レビュー: [131 review-2026-09-13](../131-mixture-production-serving/review-2026-09-13.md) の「次の新旧モデル比較で守るべき条件」7 点を本設計の要件にした(後述の対応表)

## 比較対象の固定

| | 新モデル | 旧モデル |
|---|---|---|
| model_version | `mix-129-nj6`(active・2026-09-09 override 昇格) | `lgbm-094-cap900`(candidate に降格) |
| 本体 | 6 member(joint-42/43/44 = 125 列・anchor-42/43/44 = 138 列)各 900 本・OOF isotonic 8 ブロック・weight mask 0.5・TE 10。bundle sha256 `28f39715a9c1…`・学習締切 2026-08-23 | 単一 pl_topk booster(arm E・900 本・weight mask)+ isotonic。artifact は 129 の anchor.json(sha256 `80fd7734f2d7…`)でファイル SHA を固定 |
| 係数 | 残差補正 2026 用(fit 窓 2025 末まで・出典 SHA つき)。2026 年の窓でのみ比較 | なし |
| WIN | 6 member の補正後 win の等重み平均(**再クリップ・再正規化なし**=131 レビュー修正後の本番と同じ) | booster → isotonic → clip → レース内正規化 |
| 連対率・複勝率 | 本番規約 = 平均 win から Harville + stage 割引 λ2/λ3。**研究の head 平均は `new_research_heads_not_served` として別名保存し採点に使わない** | 同じ規約(predict_race + λ) |
| λ | 記録内では両モデルとも 129 で凍結した λ2=0.85839 / λ3=0.71566(2026-08-24 以前で 1 回 fit)。加えて当日の本番 λ(本番と同じ関数・締切で再 fit)でも両モデルの heads を保存 | 同左 |
| 入力 | features-021・138 列(hash `663fe86c…`)の as-of 行 + 2007 年以降の started 履歴(新モデルの prior_gap 用) | 同じ行 |

## 記録の仕組み

`capture --date D` は 1 開催日の全レースを台帳(`days/<date>.<ts>.json`)に載せ、以下を満たすレースだけ記録する。

1. 発走時刻が未来・結果行 0・取得時刻が現在以前(129 の `validate_capture`)。発走済みは理由つきで skip(日の残りは記録する)。
2. 特徴行列を 1 回構築 → 同じ行から **preweight**(当日体重 3 列を全馬 NaN=主条件)と **serving**(本番の 091 規則=一部でも未公表なら全馬 NaN)の 2 regime を用意し、各 regime で新旧両モデルを予測。入力行そのもの(型つき)・履歴・SHA を保存。
3. 予測後に**別接続**で発走時刻と結果行数を再確認(古い事実で書かない)。取得開始/取得完了/各モデルの予測完了/書込の時刻を別々に記録。
4. DB 上の体重公表状態(0 / 一部 / 全馬)を記録。preweight は「公表前と同じ入力条件」であって「公表前に取った証明」ではないので、採点では公表状態を条件別に分ける。
5. 本番との対応: そのレースの**発走前**の本番 run(prediction_runs は append-only で computed_at 不変)を紐づけ、wregime・λ・永続化 heads・capture との win 最大差を保存。当日の本番 λ も同じ関数で再 fit して保存。
6. 排他作成(temp → fsync → `os.link`)。同じレースの 2 度目は拒否。DB は `REPEATABLE READ READ ONLY`。

`from-production --date D` は手動 capture を逃した日の**代替ソース**: 発走前の本番 run の `feature_snapshots`(生の 138 列)から行を復元し、新モデルの win を 1e-9 で再現できた場合だけ、その行で旧モデルを予測する(旧モデルの予測は後刻だが入力は凍結済みと明記)。

`score` は結果を結合し、事前登録の除外規約(順序固定・件数を出す)を適用して採点する。`--mode interim` は状況確認(verdict なし・`looks.jsonl` に記録)、`--mode final` は窓終了後の 1 回だけ。

## 既存の予測・入力スナップショットの再利用可否(調査結果)

| 対象 | 判定 | 根拠 |
|---|---|---|
| mix-129-nj6 の 9/13 本番 run(9/12 23:13 JST・wregime=serving) | **発走前・再現可能**。24/24 レースで snapshot から win を 5.6e-17 以内で再現、本番 λ で top2/top3 も 0.0 差 | `records/production_snapshot/`・各記録の `snapshot_parity` |
| 旧モデルを新モデルの snapshot 行で予測 | **妥当**。入力が本番 run と同一だった 16 レースで、発走前 live 予測と 0.0 差 | [evidence/old-model-replay-parity-2026-09-13.json](evidence/old-model-replay-parity-2026-09-13.json) |
| mix-129-nj6 の 9/12 以前の run(9/5・9/6・9/12・2026 年 backfill) | **算入しない**。9/12 は発走後(20:30 JST)の評価用 run、9/5・9/6・backfill は結果確定後の生成で、しかも booster(8/23 まで学習)にとって in-sample | DB `prediction_runs.computed_at` vs `races.post_time` |
| lgbm-094-cap900 の過去 run(9/5・9/6 は発走前 run あり) | **算入しない**。自身の snapshot は jockey_id/trainer_id が **TE 符号化後の値**で保存されており入力を復元できない(0/36 レースで再現失敗・最大差 0.09)。新モデルの発走前 run も 9/13 以前には無い | [evidence/snapshot-roundtrip-lgbm094-2026-09-05.json](evidence/snapshot-roundtrip-lgbm094-2026-09-05.json) / [09-06](evidence/snapshot-roundtrip-lgbm094-2026-09-06.json) |
| 129 の shadow prospective 記録(9/13 00:16 UTC・preweight/serving) | 算入可(ソース `shadow_129`・優先度は capture の次)。候補の研究 heads は win から本番規約で再導出 | `select_records` のアダプタ |

結論: **過去時点の入力を再現できる発走前ペアは 9/13 以降にしか存在しない**。旧モデルの過去 run は入力復元不能、新モデルの 9/12 以前の run は発走前でない。

## 採点(事前登録の要点)

- 主指標: レース単位 winner NLL の対差(新 − 旧・負=新が良い)、preweight 条件、1 レース 1 観測。
- 副指標: win の Brier(レース単位)、win/top2/top3 の馬単位 log loss・Brier・等質量 ECE(凍結 λ の本番規約)、1 位一致率・1 位的中率、leave-one-day-out の影響。
- CI: 開催日クラスタ bootstrap(`race_day_cluster_bootstrap_ci_v1`・B=20,000・seed 20260913)。95% が判定用、98.75% は 112 枠との連続性のため併記。
- 除外(順序固定): not_prospective / identity_mismatch / regime_not_recorded / prediction_missing / **results_not_ingested(保留)** / field_changed_after_capture(取消・追加・出走集合変化=除外) / **results_incomplete(保留)** / dead_heat_or_no_unique_winner(1 着同着=除外) / 未知の result_status は例外で停止。2・3 着の同着は finish_order≤k をそのまま採用。
- 条件別(記述のみ・判定に使わない): 頭数帯・距離帯・芝ダ障・クラス帯・体重公表状態・ソース。
- 窓: **2026-09-13〜2026-12-28**(係数の 2026 有効期間内)。最終判定は窓終了後・保留ゼロ・窓内全レースに結果あり・**24 開催日以上**。24 日は 129 の検出力計画(日 SD 0.0123・効果 0.01 が 98.75% で検出可能)に由来する**完了条件**であって必要改善幅ではない。**必要改善幅は 0**: 95% CI 上限<0 → NEW_BETTER、下限>0 → OLD_BETTER、それ以外 INCONCLUSIVE(点推定は常に報告)。
- 不確実性の区別: 比較対象は両方とも固定 artifact なので CI は将来レースの標本変動のみ。再学習のばらつき(sd_fold 0.001816 は 100/129 の単一モデル値・この bundle の再学習分散の実測ではない)は**別欄に注記し CI に足さない**。
- 129 の確認 draft 窓(9/12〜12/28・未予約・移送 sd を CI に加算)はこの登録で置き換える。112 の年 8 枠の予約は独立レビューを要するため運用者の判断に残す(本読み出しは 9/9 の override 昇格の事後確認)。

## 9/13 の実行結果

| 項目 | 値 |
|---|---|
| capture(新ツール) | 24/24 レース・取得開始 09:43:36 JST・最終書込 09:44:03 JST・1R 発走 09:45 JST。体重公表状態: 公表前 22・全馬公表 2(両場の 1R) |
| 129 shadow(preweight + serving) | 24/24・09:16〜09:17 JST |
| production_snapshot | 24/24・新モデル win 再現 ≤5.6e-17・本番 λ(0.86561/0.71118)で top2/top3 差 0.0 |
| 本番 run との入力ドリフト | 6/24 レースで capture の新モデル win が本番 run(前夜 23:13 JST)と最大 0.00975 違う。原因は 00:03 UTC の results 取込で前週レースの通過順が埋まり展開・コーナー系 as-of 列が動いたこと(131 レビュー §4 と同じ現象)。**ペアは記録内で同時なので比較には影響しない**が、「保存 run 同士の比較」が無効であることの実例 |
| interim 採点 | 24 レース全て `results_not_ingested`(保留)。除外 0・識別不一致 0。`evidence/interim-2026-09-13T0050Z.json` |
| rehearsal 診断(129 の 9/5・9/6・8/29・8/30・結果既知=証拠ではない) | 144 レース・4 日・差 +0.001507・95% CI [−0.0082, +0.0112]・1 位一致 88.9%。129 の結果(+0.00151・128/144)を新パイプラインで再現 |

## 次回開催日の手順(待機条件つき)

```bash
export DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
W=artifacts/129-candidate-mixture-serving
SHA=28f39715a9c1dba13755ee44f3a439099d5aaffd94685ac11e4d23166b388471
R=artifacts/133-prospective-paired-comparison/records
E=specs/133-prospective-paired-comparison/evidence
P=specs/133-prospective-paired-comparison/preregistration.json
```

1. **出馬表取込後・体重公表前・1R 発走前**(前夜〜当日朝): 
   `serving/.venv/bin/python -m horseracing_serving.paired_prospective capture --date YYYY-MM-DD --bundle $W/bundle.json --bundle-sha256 $SHA --anchor $W/anchor/anchor.json --out-dir $R`
   (約 30 秒。発走済みのレースは skip される。同日 2 回目は既存レースを拒否する)
2. 手動 capture を逃したが ops が発走前に予測していた日: 
   `... from-production --date YYYY-MM-DD ...(同じ引数)` — 新モデルの win が再現できたレースだけ記録される。
3. **結果取込後**(夜の「この日を更新」の後): 
   `... score --records $R --records $W/shadow/prospective --records $W/shadow-regime-serving/prospective --preregistration $P --mode interim --regime preweight --output $E/interim-<UTC時刻>.json --looks-log-dir $E`
   保留(pending)が 0 になり scored に移ることを確認する。通過順の翌日公開は採点に無関係。
4. **最終判定(2026-12-29 JST以降・窓内全レースに結果あり・24 開催日以上)**: 
   `... score ... --mode final --output $E/final-readout.json --looks-log-dir $E`

待機条件: 9/13 の結果取込(夜)で初めて採点対象が生まれる。判定は窓終了まで出さない。

## 131 レビューの「守るべき条件」との対応

| 条件 | 対応 |
|---|---|
| 1 同一入力・同一時刻 | 同じ `prepare_race_inputs` 行・同じ履歴。入力行を型つきで保存し SHA も記録 |
| 2 regime 明示 | preweight 主・serving 併記。full_info は混ぜない(公表状態は条件別) |
| 3 完全 OOS | 窓は両モデルの学習締切・係数 fit・凍結 λ fit(8/24)より後。当日の本番 λ は本番挙動の記録としてのみ保存 |
| 4 head の導出を揃える | 本番規約 + 事前登録の凍結 λ。研究 head 平均は別名保存・不採点 |
| 5 WIN 不変 | `_display` が eps=0 で組み立て、win が動けば例外(テスト `test_display_keeps_win_exact…`) |
| 6 履歴母集団の同一性 | 131 の `assert_history_matches_features` を capture 経路でも呼ぶ |
| 7 判定契約 | 日クラスタ CI・必要日数・単一最終判定。移送 sd の加算は意図的に外し、その理由を登録に記載 |

## まだ判断できないこと

- **新旧の優劣**: 発走前の実測は 9/13 の 24 レース(結果未取得)のみ。130 の −0.0090 は 2020〜2026 の walk-forward、129 の rehearsal +0.0015 は 4 日・結果既知。どちらも将来レースの証拠ではない。
- **連対率・複勝率の本番 λ の妥当性**: 本番 λ は「各レース最新の保存 run」(世代混在・2026 は in-sample)から毎日 fit される。記録には本番 λ の heads も残すが、判定用の heads は凍結 λ。
- **公表前 0 件の意味**: DB の体重 0 件は取込遅延でも起きる。公表状態は DB 上の事実として記録しており JRA の公表時刻ではない。
- **窓の完走**: 手動 capture を逃した開催日は `from-production`(ops の発走前予測がある場合のみ)で補えるが、前夜の refresh が無い日は証拠が欠ける。窓は延長しない。
- **112 の枠**: 本比較を 112 の年 8 回枠に計上するかは独立レビュー取得後の運用者判断。

## codex レビュー(設計・同セッション)

採用: 予測後の再検証を別接続の新しい事実で行う / 取得開始・完了・予測完了・書込の時刻分離 / 入力行そのものの保存 / 開催日の全レース台帳(分母) / 本番 λ での両モデル heads 併記 / snapshot 由来記録の体重状態を「不明」扱い / 排他作成の原子化 / B=20,000 と leave-one-day-out / 1 着以外の同着規約の明文化 / 再学習 sd は注記のみ / 登録時刻と 9/13 記録の扱いを明記。
不採用: 「preweight を主条件から外す」(ユーザー指定の主条件。公表状態を条件別に分けることで対応)。
保留: 旧モデル自身の snapshot 往復検証は不能と判明(TE 符号化後の保存)→ 新モデル snapshot 行での旧モデル予測を live capture と照合する形で代替した。
