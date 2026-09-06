# Data Model: 買い目パターン採否ゲート(109)

DB スキーマ変更なし。以下は本 feature が読む行、生成する凍結物、証拠、verdict の形。eval 側の関数は numpy 配列と dict を受け取り(pandas 非依存)、DataFrame と parquet の入出力は `scripts/` が担う。

## 1. 賭け候補行(BetRow)— 母集団の 1 馬 1 行

| 列 | 型 | 出所 | 注 |
|---|---|---|---|
| race_id | str(12) | races | 正準キー 1 |
| horse_number | int | race_horses | 正準キー 2 |
| horse_id | str | race_horses | 束の結合キー |
| race_date | date | races | 開催日クラスタのキー |
| year | int | 導出 | fold 年 = 暦年 |
| track_type | 芝/ダ | races | 障 は母集団外 |
| distance | int | races | |
| race_class | str | races | 表記分裂は列挙側で C1/C2/C3 に正準化 |
| field_size | int | 導出 | 登録頭数(`race_horses` 全行=取消含む)。取消馬は買わない |
| sex | 牡/牝/セ | race_horses | |
| interval_days | int/null | 導出 | 前走 started の race_date との差(厳密前・同日なし)。初出走 null |
| prev_finish | int/null | 導出 | 前走の finish_order(stopped は null) |
| tataki_2 | bool/null | 導出 | 前走が前々走から 70 日超(`folklore_candidates.sql` 定義) |
| odds | float | race_horses | closing 単勝 |
| q | float | 導出 | (1/odds)/Σ(1/odds) started 内 |
| fav_q | float | 導出 | レース内 max q |
| entropy | float | 導出 | 066 `normalized_entropy(q)` |
| p | float | OOF 束 | strict-past win 確率 |
| p_rank | int | 導出 | レース内 p 降順順位 |
| p_over_q | float | 導出 | p/q |
| p_past_quantile | float/null | 導出 | そのレース日より前の全 OOF p の累積分布における分位(expanding・2008 年は欠損) |
| entropy_past_tertile | int/null | 導出 | 同方式のエントロピー 3 分位 |
| ev | float | 導出 | p×odds |
| won | bool | race_results | finished かつ finish_order=1 |
| n_winners | int | 導出 | そのレースの finish_order=1 の頭数(同着なら ≥2)。主判定は n_winners=1 のみをマスクで使い、同着行は感度のため保持 |

**不変条件**
- INV-P1: 主判定の母集団の各レースは won=True がちょうど 1 行(同着は `n_winners ≥ 2` のマスクで除外し流れ図に計上・勝者不在は除外)
- INV-P2: 全行に odds と p が非 null(欠ける馬が 1 頭でもいるレースは結果前除外)
- INV-P3: track_type ∈ {芝, ダ}
- INV-P4: 述語入力の欠損(interval_days 等)は行を落とさず「買わない」に倒す

## 2. パターン(BuyPattern)— `patterns.json` の 1 要素

```json
{
  "pattern_id": "M.odds_band.6-11|X.p_rank.1",
  "family": "M×X",
  "conditions": [
    {"axis": "market", "field": "odds", "op": "in_band", "lo": 6.0, "hi": 11.0, "hi_inclusive": false, "available_at": "closing"},
    {"axis": "model", "field": "p_rank", "op": "eq", "value": 1, "available_at": "closing"}
  ],
  "horse_rule": null,
  "label_ja": "オッズ 6〜11 倍 かつ モデル 1 位"
}
```
- `horse_rule ∈ {null, "fav", "cap11", "cap21"}`。null = 自己選択(条件を満たす馬を全て買う)。レース単位条件のみのパターンは非 null 必須。
- `available_at` はパターン全体で最も遅い値を `pattern.available_at` に転記。closing を含むものは結果に `HISTORICAL_CLOSE_SIGNAL` 札。
- 列挙は決定論(軸→帯→H の辞書順)。`patterns_hash = stable_hash(patterns.json の内容)`。

**不変条件**
- INV-F1: pattern_id は一意、総数 393(構造的同一の 9 本は列挙時に除去済み。対照 4 本 `no_bet / favorite / cap11_all / cap21_all` は `controls` に別置、`ev1_all` は X の EV セルの別名)
- INV-F2: 帯は互いに素で軸を被覆する(境界は上限排他)
- INV-F3: 結果列(won / finish_order)を参照する条件は列挙時点で拒否

## 3. 凍結設定(GateConfig)— `gate-config.json`

契約は [contracts/gate-config.md](contracts/gate-config.md)。要点: 評価規約・判定式・降格・対照・窓境界(確認窓終端は freeze 時点の最終確定レース日)・seed・反復数(主 20,000 / 自己検証はサイズ内側 20,000・検出力内側 5,000・外側 5,000 / 2,000)・生存規則(K=5・Jaccard 0.9)・δ=0.02・Holm α=0.025・`expected_races_per_year`(記録のみ)・`patterns_hash`・`bundle_digest`・`code_sha`。`_` 前置キーは hash 除外(`gate_config_hash` の既存規約)。

## 4. 母集団(Population)— `population.json`

`population_hash = stable_hash(sorted race_id 列)`、窓ごとの `rows_hash`(行スナップショットの hash)と `day_universe`(窓内の sorted race_date 全体=bootstrap のブロック集合)、年別レース数、流れ図 `{total_in_db, excluded_pre_result: {not_in_bundle, jump, missing_odds, missing_p}, excluded_post_result: {no_winner, dead_heat}, kept}`、凍結設定 `expected_races_per_year` との差(記録のみ・拒否しない)。

## 5. 証拠(Evidence)

**賭け行(parquet・窓ごと・`artifacts/109/<window>-bets.parquet`)**: `pattern_id, race_id, horse_number, horse_id, race_date, odds, q, p, won, dead_heat, n_winners, payout(=odds if won else 0), stake(=1)`。`selection_hash` をパターンごとに併記(sorted (race_id, horse_number) の stable_hash)。

**集計(JSON・窓ごと・パターンごと)**: `n_bets, n_races, n_days(そのパターンが stake>0 の日数), n_hits, roi, ci_low, ci_high, p_profit_one_sided, p_futility_one_sided, simultaneous_upper_maxT, max_single_hit_share, leave_one_hit_out_roi, demoted_reason, mde_80(= (z_{1−α/m} + z_{0.80})·sd(replicates)・α=0.025・m は生存者数、screening では 1), by_year[{year, n_bets, roi, payout_share}], second_aggregation_match: true, available_at`。
- 対照: `no_bet` は `roi=null, accounting_sentinel=1.00`。

**自己検証(`evidence/selftest.json`)**: サイズ(global / partial)の誤採用率と二項片側信頼限界、代表 5 パターンの検出力曲線と 80% MDE と降格率(エッジ形 3 種)、陰性対照(ρ=0.796)、τ 推定値、反復数。

## 6. 生存者(`evidence/survivors.json`)

`{survivors_hash, patterns_hash, population_hash, gate_config_hash, ranked: [{pattern_id, lcb_discovery, lcb_qualification, rank_key, merged_from[]}], screened_out_counts: {discovery, qualification, rank, duplicate}}`。

## 7. verdict(`verdict.json`・append-only)

```
{
  gate_config_hash, patterns_hash, population_hash, survivors_hash, bundle_digest,
  code_sha (凍結: patterns.json を生成したコード), run_code_sha, run_tree_dirty,
  windows: {discovery, qualification, confirmatory},
  evidence_refs: [{window, path, sha256}],
  controls: {no_bet, favorite, cap11_all, cap21_all},
  states: {ADOPT_CLOSE: n, NOT_ADOPTED: n, RULED_OUT: n, NO_DECISION: n, SCREENED_OUT: n},
  per_survivor: [{pattern_id, state, available_at, historical_close_signal, p_profit_one_sided, p_futility_one_sided, holm_rank, holm_threshold, roi, ci, simultaneous_upper_maxT,
                  sensitivities: {week_block, month_block, dead_heat_split, dead_heat_half}, reason}],
  limitations: [closing_price_leak, payout_approximation, dead_heat_handling, no_correction_history, win_only],
  resume_conditions: [...],
  conclusion_ja: "凍結した探索で、2019 年以降の closing オッズ条件つき回収率が 1 を超える候補を…"
}
```

### 状態遷移(research D10 と同一)

```
1. SCREENED_OUT(discovery | qualification | rank | duplicate)
2. NO_DECISION(cannot_run | demoted | zero_denominator_replicate | sensitivity_split)
3. ADOPT_CLOSE(profit Holm reject in ALL analysis versions)  ※ selftest も同じ decide() と分析版集合で判定
4. RULED_OUT(profit not rejected ∧ futility(c=1.02) Holm reject)
5. NOT_ADOPTED(neither rejected)
```
優先順位は番号順。ADOPT の帰無は ROI ≤ 1.00、δ=0.02 は futility 専用。
