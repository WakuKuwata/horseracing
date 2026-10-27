# ROI 研究の検証範囲と追加実行候補（2026-10-05）

今回の追加実行は、R10 の年内順位選択を前年までの情報で決める規則に置き換える検証と、未実行の R11 型の段 0 が優先である。既存の成績を見た後の提案なので、過去期間の再利用は探索として扱う。既存の freeze、結果、採否は変更しない。

## 既に試した内容

| 系列 | 実施済み範囲 | 今回「新しい条件」として繰り返さないもの |
|---|---|---|
| 09/30 固定条件 | 旧 2,356 条件、履歴を 2007 年から再構築した主 1,806 条件＋来歴限定の 550 条件、新規 480 条件 | 休養・実績コース・距離・脚質・年齢・人気・オッズを組み替えた一般的な条件 |
| 10/01 反証 | 480 条件の親集合内置換、15 seed 平均、seed 数曲線、rounds600、直近 8 年学習、既存 p 追加、回帰、過去年で学習する 50 入力の residual context LightGBM | seed を増やす、前走等の context を 2 段目モデルに入れるだけの案 |
| R01 | プレミアム日・払戻率 | プレミアム日自体を新発見とする案 |
| R02 | 判断時と確定時のオッズ・選択の変動、モデル価格感応度 | 確定時の成績を判断時にそのまま期待する案 |
| R03 | 080 のモデル EV による連系選択と来歴監査 | 同じ既存 p を使う連系への横展開 |
| R04 | ens15 の市場条件付き情報、既存 p / anchor の追加情報 | 本番 p との単純合議 |
| R05 | 精算と選定の保存オッズ不整合 | 保存 odds を無条件に確定 odds とみなす集計 |
| R06 | 極端な本命の複勝・プラス 10 | 極端な低オッズ複勝への単純な切替 |
| R07 | 同一事象の券種・組合せへの配分 | ワイド総流しや 3 連複から 6 通りの 3 連単への単純切替 |
| R08 | 損失最小化 frontier、15 seed 全て EV<0.8 の veto、seed 確率の 20% 分位 | 全 seed 合意・下側分位を新しい不確実性指標と呼ぶ案 |
| R09 | 騎手選択・騎手変化・東西遠征・前走レベル・馬主・血統等 13 セル | 東西遠征などの同じセルを追加情報として再提案 |
| R10 | レース内正規化、seed 分散残差・EV>1.2 seed 比率・確率合計・他馬 disagreement の符号制約メタ補正 | seed 分散 / 合意率 / 他馬 disagreement そのもの |
| R11 | **未実行**。報告 5.3 では計算量と増分見込みを理由に保留 | 未実行を不合格と取り扱うこと |
| R12 | 確定グリッドの券種間情報、少数の判断時グリッド | 確定クオートを発走前に利用できるとみなす案 |

R10 の V1/V2 は当年 S3 件数と同数を、当年の全候補の score 順位から選ぶ。これは結果ラベルの直接漏洩ではないが、年末までの件数と score 分布を必要とし、オンラインの買い方にはならない。V2 の seed 分散残差化も当年の odds 分位と OLS を使う（元 prereg に non-as-of と開示済み）。

## 実行できる追加案と重複の境界

1. **R10 V1 のオンライン化（今回実行）**：固定 EV_norm>1.2 と、前年の S3 比率に対応する前年 EV_norm 分位を当年の閾値にする規則。全馬／EV_ens>1 の親集合を別 arm として固定する。当年件数への再調整はしない。既存の正規化仮説の実行可能性を検証する補足であり、新情報の発見ではない。
2. **R11 段 0（今回 root 担当）**：log q を offset とする race-softmax 勝者モデル。現時点の市場列を入力から除いた履歴・context で、市場＋ens15 の過去年学習合議に対する paired race NLL 増分を先に測る。固定 2023/24/25 folds の小規模試行。元 060 の全仕様を検証したとは呼ばず、3 fold の探索とする。q と odds の派生列の除外、レース全頭の group 一致、非同着・1 勝者、年末 cutoff の確認が必要。
3. **R10 V2 の完全な過去移送（保留）**：当年 OLS と当年分位を過去年 OLS・固定 odds bins に置き換え、当年 top-N も除く。N1–N4 とは別の修正として定義できる。ただし seed 不確実性族自体は実施済みなので、新族として検定数をリセットしない。
4. **判断時の価格変化を過去観測だけで補正する方策（前向きデータ優先）**：R02 は機構診断と輸送推定を行ったが、日付を前後に分けた価格変化モデルの方策評価とは異なる。現在 17 日しかなく、時刻帯・価格帯・source の交絡が強い。自由度のある価格予測を追加するより、日付単位に訓練と評価を分けた固定補正の診断または将来記録が妥当。現データだけで採用判定はできない。

median / trimmed mean の厳密な同一演算は今回確認したコードでは見つからなかったが、seed 分散・合意・下側分位・平均数の近傍であり、優先する根拠は弱い。条件付き残差も既に 10/01 の context モデルと R09、R10 で広く試されているため、特徴名を変えるだけでは新規候補にしない。

## 追加実行で使用できる入力

### 2007 年開始の表

- `artifacts/market_ev/rows_2007.parquet`：931,017 頭、2007-01-06〜2026-09-22。SHA256 `99f699502424c65a4f2dcd998bd105c2537551952b931b2c4a19f28399468b2b`（ens15 / R10 登録値）。現市場、馬・騎手・調教師履歴、`race_ok`, `dead_heat`, `won` 等を含む。`running_style`, `finish_order`, `last_3f` 等は当該結果列なので特徴にしない。
- `artifacts/roi_strict2007_20260930/input/rows.parquet`：同じ 931,017 頭・期間。同日履歴と旧 108 の `p/p_top2/p_top3` を追加。旧 p の特徴履歴下限には実効環境記録の残余があり、今回の fresh ens15 の代用にはしない。
- 新 20 特徴は `scripts/roi_explore/new_pattern_features_20260930.py` で再導出できる。480 条件／2 段目 residual context の検証済み特徴である。

### ens15 の個別 seed 保存予測

- seed 1：`artifacts/roi_explore/results/armC_binary_drop-sameday+weightlive_from2007_ens15/predictions.parquet`
- seed s=2..15：`artifacts/roi_explore/results/armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15/predictions.parquet`
- 全 15 ファイルを 10/05 に直接読み取り確認：各 **786,049 行**、2010-01-05〜2026-09-22、`(race_id, horse_id)` 重複 0。列は `race_id, horse_id, race_date, year, odds, ret, pred`。
- `pred` は確率そのものではない。`EV_s = 1 + pred_s/100`, `p_s = EV_s/odds`, `p_bar=mean_s(p_s)`。`ret` はラベル側の払戻であり入力禁止。
- 各ファイル SHA は `artifacts/roi_explore/missed_20261004/R10_norm_meta/prereg.json` に全 15 本記録。追加実行時は現ファイル SHA と整合を確認する。
- 本体は `artifacts/market_ev/mev-ens15-v1/seed_{01..15}/model_{2019..2026}.txt`。`ensemble.spec.json` の `booster_years` は **2019–2026 の 8 年**。予測表の 2010–2018 と、本体が保存されている年を混同しない。
- `seed_01/model.spec.json`：binary、300 rounds、train_from=2007、当年 Y の cutoff=Y−1、sameday / weightlive 除去、66 特徴。各 seed の同一 key・odds と ensemble 演算 parity は追加評価器で再確認する。

### 判断時点データ

- `artifacts/roi_explore/missed_20261004/R02_odds_drift/pairs.parquet`：7,562 頭×観測、2026-08-02〜10-04。564 pair、444 race、17 日（R02 報告）。同じ race の複数 pair を独立レースと数えない。
- 列：`pair_id, race_id, horse_id, horse_number, O_J, EVe_J, EVs_J, O_F, EVe_F, EVs_F, q_F, source, tb, hours, race_date, official_payout_yen, won, dead_heat, O_settle, ret` 等。
- `feats_cand.parquet`：8,368 頭、2026-08-01〜10-04、候補の再構成特徴。判断時 weather/going の完全再現は別問題で、R02 は research parity と production parity を区別している。

### 掲載払戻・確定オッズ

長期の単勝は `won*stored_odds*100` による近似精算。以下の「official」命名列は保存 HTML の掲載払戻を抽出したものなので、原データの来歴を併記する。

- `artifacts/roi_explore/missed_20261004/R05_settlement/archive_payouts.jsonl`：2,136 version 行、504 race ID。`race_id, version, is_latest, archive_path, archive_sha256, fetched_at_utc, tansho, wakuren, status`。空の `tansho` と version 重複があるため、そのまま合算しない。
- `archive_horses.jsonl`：6,656 頭、499 レース。`horse_number, finish_text, popularity_page, result_page_odds`。結果ページの実データ。
- `archived_final_odds.csv`：6,716 頭、504 レース、2026-08-15〜10-04 の 18 日。`race_id, race_date, horse_number, horse_id, stored_odds, result_page_odds, popularity_page, differs, db_won, official_payout_yen, affected`。504 全件に有効 HTML 払戻があるという意味ではない。
- `affected_races.csv`：3,498 レース、2025-10-04〜2026-10-04。`timing_class`, `timing_affected`, `archive_tansho_ok`, `winner_identity_ok`, `affected`, `correctable` 等。報告上 489 affected、うち 2026-07-04〜09-22 が 474。2026-07-04〜08-09 の 239 件は復元不能。
- 先行版 `artifacts/roi_recheck_20260930/data_audit/archived_win_dividends.jsonl` は 408 レース／409 払戻、旧 strict 入力の正常・非同着平地との交差 394 レース。新 R05 と無条件に結合せず、source/version 優先を定義する。
- `artifacts/roi_explore/missed_20261004/R08_frontier/tickets_main.parquet`：486,002 ticket 行、2025-01-05〜2026-09-22。`race_id, race_date, year, policy, bet, sel_key, stake, payout, odds, won, veto, p_low, null_rate`。これは既存政策の買目であり、全馬・全買目の払戻原本ではない。

## 参照

- [10/04 総合報告](../roi-missed-patterns-20261004/report.md)
- [10/01 反証報告](../roi-pattern-exploration-20260930/followup-20261001.md)
- `scripts/roi_explore/missed_20261004/r04_common.py`, `r08_frontier.py`, `r09_cells.py`, `r10_norm_meta.py`
- `artifacts/roi_explore/missed_20261004/R10_norm_meta/prereg.json`
- `scripts/roi_explore/residual_context_model_20261001.py`

本インベントリはコード・定義・入力 metadata の読み取り調査。追加条件の ROI を先に見て候補を選んでいない。過去の報告結果は候補の重複と限界を理解するために閲覧した。
