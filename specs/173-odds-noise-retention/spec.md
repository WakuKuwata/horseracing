# 173 オッズノイズ学習(候補 B)の受入測定 — 判断時該当の締切保持率と EV_F/EV_J

**事前登録(2026-10-29・実行前にこの文書と `prereg.json` を凍結・sha256 を `prereg.sha256` に記録)。研究のみ・本番非結線・DB 読み取り専用・新規取得なし・再学習なし。**

## 位置づけ

- 2026-10-08 の独立評価(`docs/roi-independent-eval-20261008/report.md` §4 候補 B)は「判断時相当のオッズずれを注入して ens15 を再学習し、判断時 EV を較正する」案を挙げ、受入を **ROI ではなく「判断時該当の締切保持率 ≥0.5(現 0.17〜0.20)・該当馬の EV_F/EV_J 中央値 ≥0.95(現 0.81)」** とした。
- この学習は **140 の ①-a `mev-ens15-judged-v0`(15 seed・2026 年 booster・`artifacts/market_ev/judged/`)として既に実施済み**(生成器=R02 の 17 開催日・帯別 a/b/s・ρ=ICC 0.080、学習行=未摂動+注入の 2 倍、特徴 66+`log_min`)。140 は受入を NLL(規則 (i))で定め(plan D8「保持率・ROI は使わない」)、①-a は確定 OOS で +0.00042 と規則 (i) 不合格だった。**保持率と EV_F/EV_J は 140 で測っていない。**
- 本 173 は **再学習せず**、既存の 15 seed を独立評価の受入基準で一度だけ測る。候補 B を「新規の学習案」として繰り返さない(重複の明示)。

## 対象データ

1. **主集合(事前登録の受入対象)**: `artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet`(R02 の組=1 レース 1 判断時点・443 レース・5,967 頭・17 開催日・2026-08-02〜10-04。判断時オッズ `O_J`・確定オッズ `O_F`・`hours`・現行 ens15 の `EVe_J`/`EVe_F`・単 seed の `EVs_J`・`days_since_last`・as-of 特徴列)。独立評価の「R02 443 集合」と同一。sha256 は結果に記録。
   - **限界(明記)**: 140 の生成器はこの同じ 17 開催日で fit されている。したがって主集合での測定は、注入分布に対して **in-sample** である(booster の教師は 2025 年以前なので結果ラベルの漏れは無い)。
2. **副集合(生成器の fit 期間外・判定には使わない)**: 実 DB の 2026-10-05〜10-25、140 の protocol v2 の母集団(結果確定済み・発走前の最後の ens15 計算が全 started 馬に残る・非同着・平地・レース内 `odds_observed_at` 単一)。判断時オッズ=保存 `odds_used`・確定オッズ=現在の `race_horses.odds`(139 の確定値上書き後)。h は中央値 20 分前後で主集合(中央値 3.7 時間)とは別の帯。`oddsobs=quote` でない日は dry_run として件数だけ出す。

## 腕

| 腕 | 判断時の予測 p̂_J | 判断時 EV_J |
|---|---|---|
| ens15(現行・基準) | `mev-ens15-v1` 2026 booster 15 seed 平均。市場 12 列は `O_J` から `r02_drift.market_block` で再計算 | p̂_J × O_J(`EVe_J` とのパリティを fail-closed で検査) |
| judged(候補 B) | `mev-ens15-judged-v0` 15 seed 平均。同じ特徴 + `log_min = log(h 分)` | p̂_J^judged × O_J |

締切側(保持の判定先)は **本番定義=ens15 を `O_F` で評価した EV_F**(=138 の凍結表と同じ側)。`EVe_F` および主集合の `F_S*` フラグとのパリティを検査する。診断として judged の `log_min=0`・`O_F` 版も出す(judged 自身の締切版)。

## 指標(rule r ∈ {S1,S2,S3,S4}・凍結定義 `horseracing_eval.attention_rules.match_mask`・S5 は単 seed 条件なので judged 腕では定義しない)

- `J_r(arm)` = 判断時に腕の EV_J と `O_J`・`days_since_last` で r に該当する馬の集合。`n_J`。
- **保持率** = |J_r(arm) ∧ F_r(ens15, O_F)| / |J_r(arm)|。
- **EV 比の中央値** = median over J_r(arm) of EV_F(ens15, O_F) / EV_J(arm, O_J)。
- 併記: 判断時較正 Σwon/Σp̂_J(J_r 内)、hours 帯別(0.25/0.5/1/3/10/40 時間)の n_J と保持率、judged 締切版での保持率、`official_race` の行のみの公式払戻 ROI(参考値・判定に使わない)。
- 区間: 開催日ブロック bootstrap(`horseracing_eval.bootstrap.block_bootstrap_counts`・b=4000・seed=20261029)の percentile 95%。主集合は 17 開催日しか無いので区間は粗い。

## 受入基準(事前登録・結果を見て変えない)

- **主判定 = S3・judged 腕・主集合**(S3 は探索後固定でない凍結条件で n が最大。S1/S2 は posthoc)。
  - `n_J(S3, judged) < 20` → **NO_DECISION**(該当が消えただけでは保持率を論じられない。件数と EV_J 分布を報告)。
  - 保持率 ≥ 0.50 **かつ** EV 比中央値 ≥ 0.95 → **PASS(次段へ進める価値あり)**。PASS は ROI の改善を意味しない。
  - それ以外 → **REJECT**(候補 B はこの構成では閉じる)。
- 基準腕の再現: ens15 の S3 保持率が `scored_pairs` の `J_S3/F_S3` から直接数えた値と一致し、`EVe_J`/`EVe_F` とのパリティ(最大絶対差 < 1e-6)が成立しないときは **測定無効**(結果を出さない)。
- 副集合・S1/S2/S4・帯別・judged 締切版・ROI は記録のみ。主判定を変えない。
- 閾値・h 帯・seed・b・集合の定義は実行後に変更しない。PASS でも本番結線・表示変更はしない(別 feature の事前登録)。

## 出力

- `artifacts/173-odds-noise-retention/result.json`(append-only・入力 sha・model sha・protocol sha・全指標)
- `specs/173-odds-noise-retention/result-review.md`
- `scripts/roi_explore/odds_noise_retention_173/score_retention.py` + `scripts/tests/test_odds_noise_retention_173.py`

## レビュー

codex は ChatGPT アカウントで 400 のため unavailable(本セッションで継続)。代替セルフレビュー: (1) 主集合と独立評価の集合が同一か(443 レース・5,967 頭・sha) (2) 市場 12 列の再計算が本番 `build_features` と同一の実装(r02_drift.market_block=140 と同じ)か (3) 基準腕が `EVe_J`/`EVe_F`/`F_S*` を再現するか(fail-closed) (4) 保持率の分母が「判断時該当」で、分子の締切側が本番定義か (5) 生成器 in-sample の限界を結果に明記するか (6) 判定を結果後に動かしていないか(prereg sha 照合)。
