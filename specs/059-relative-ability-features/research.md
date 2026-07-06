# Research: Within-race relative-ability features (059)

## D1. 最終列集合(憲法 III: 結果を見る前に事前確定)

spike(14列, rel_venue 含む)は AUC +0.00421。本 feature は **rel_venue_win_rate(coverage 11%)を
除外**し、最終 **13 列**を事前固定する。除外判断は「11% カバレッジ=大半 NaN=木がほぼ使えない・
ノイズ源」という**カバレッジ根拠**(OOS 結果ではない)に基づく=憲法 III に適合。以後、実 DB ゲート
再実行後に列を足し引きしない。

命名: 031 の `_ex_self` 前例に倣い、`rel_rel_time_avg` のような二重前置を避けるため **suffix** 方式。

**deviation(leave-one-out 偏差, 11列)** = 自分の値 − 自分を除いた started フィールド平均:
`win_rate_vs_field`, `recent_win_rate_vs_field`, `place_rate_vs_field`, `show_rate_vs_field`,
`dist_band_win_rate_vs_field`, `surface_win_rate_vs_field`, `rel_time_avg_vs_field`,
`rel_last3f_avg_vs_field`, `finish_diff_best_vs_field`, `jockey_win_rate_vs_field`,
`trainer_win_rate_vs_field`

**field percentile rank(2列)** = started フィールド内の順位パーセンタイル:
`win_rate_field_rank`(総合能力軸), `rel_time_avg_field_rank`(スピード軸)

- **Rationale**: 中核 2 軸のみ rank を足す(spike と同一)。全軸に rank を足すと相関冗長・過学習
  リスク。deviation は連続量で相対位置を、rank は分布に頑健な順位を与える相補関係。
- **Alternatives**: (a) 全軸 rank も追加 → 冗長で spike 超えの根拠なし・却下。(b) rel_venue 残す →
  11% coverage で inert・却下。(c) standardize(z-score, /field_std)→ field_std はレース定数で
  softmax 相殺・分母 0 リスク、deviation の方が単純頑健・却下。

## D2. LOO 意味論(031 `_loo_mean` を能力列に転用)

各行 `col_vs_field` = `col` − (同 race_id の **started かつ非NaN** の他馬の `col` 平均)。
- self 除外: 自分が started & 非NaN のときのみ自分の値を分子・分母から控除(031 と同一式)。
- 他馬の非NaN 数が 0 → NaN(単騎・全馬 NaN 等)。0 埋めしない(憲法 IV)。
- 母集団 = `entry_status == STARTED`(031/serving と一致、結果は読まない)。
- **Decision**: 031 の `_loo_mean(df, col)` をそのまま流用(実績あり・bit-parity 済み機構)。
  新規ロジックを増やさない(039 の教訓=経路を増やさない)。

## D3. field rank 意味論

`col_field_rank` = 同 race_id・started 母集団での `col` の percentile rank(pandas `rank(pct=True)`,
既定=同値は平均順位=決定論)。NaN はランク付けせず NaN のまま。self を含む rank(LOO しない)=
「自分がフィールドの何%地点か」を表す純粋な per-horse 量で、フィールド定数ではない(相殺されない)。
**実装注意**: `ability_frame` は non-started 馬も含むため、rank 前に `col.where(is_started == 1)` で
non-started を NaN マスクしてから `groupby("race_id")[col].rank(pct=True)` する(NaN は自動除外)。
full frame にそのまま `rank(pct=True)` を掛けると取消・非出走馬が母集団に混ざり「started 母集団」に
反する(`_loo_mean` の started フィルタと同じ規律)。non-started 馬自身の rank は NaN。

## D4. 結線点(025 単一 as-of 源)

`build_asof_features`(materialize.py)内で全 as-of ブロックを merge した `out` に対し、新モジュール
`build_relative_ability_features(frames, ability_frame=out)` を呼び、結果を merge してから
`cols = [*_KEYS, *materialized_columns()]` で選択。**in-memory / materialized 両経路がこの関数を
経由**(builder.py の `_asof_block` が fallback でも同関数を呼ぶ)ので結線は 1 箇所。

- 入力 `out` には対象 11 能力列が全て含まれる(history: win_rate/recent_win_rate/place_rate/
  show_rate/dist_band_win_rate/surface_win_rate/dist_band 系; pace: rel_time_avg/rel_last3f_avg/
  finish_diff_best; human: jockey_win_rate/trainer_win_rate)。merge 後に計算=依存を単純化。
- **materialize-safe**: LOO/rank は per-race 決定的で pool-end 非依存(各行の as-of 入力が
  strictly-before で不変 → フィールド集約も不変)。031 と同じく `materialized_columns()` に含める。
- **source_fingerprint 不変**: 新ソース列(races/race_horses/race_results/horses の生列)を 1 つも
  読まない(既存 as-of 出力の後処理のみ)→ fingerprint 拡張不要(031/041 と同じ)。

## D5. リーク境界(憲法 II)

- 入力は全て strictly-before as-of 列。他馬の今走結果・オッズ・同日値を参照しない。
- self の今走結果も不参照(自分の as-of 値も strictly-before)。
- **leak-guard test**: 対象レースの (a) 全馬の finish_order/result_status、(b) odds、(c) 同日別
  レースの結果 を改変しても、対象レースの 13 列が **bit 不変**であることを検証(023/031 拡張型)。
- odds/結果はモデル特徴にしない不変(FR-004 leak-guard・憲法 II)を維持。

## D6. 採用ゲート + pl_topk overlap 検証

1. **事前登録 feature-eval**(binary, baseline=features-013): `training feature-eval --drop-groups
   relative_ability`。spike 13列版(rel_venue 除外)で LogLoss/AUC/ECE/fold を実 DB 再現。
2. **本番 pl_topk model-eval**: `--objective pl_topk --calibration isotonic --target-encode
   jockey_id,trainer_id` で候補 walk-forward OOS。win LogLoss が lgbm-056(0.21615)を下回るか、
   top2/top3 非悪化かを確認。**overlap リスク**(within-race 相対化 vs softmax 相対化)はここで実測。
3. 機械判定が False でも総合改善ならユーザー判断(023/039/056 前例)。

## D7. codex second opinion(憲法 VI 品質ゲート)

初回起動はハング(~49 分無応答)したが、**speckit-analyze 中に起動した codex は完走**し構造化レビュー
を返した(**second opinion 取得済み**=憲法 VI ゲート充足)。指摘の採否:

- **採用(docs 修正済み)**: (a) `model-eval --objective pl_topk` の baseline が binary(cli.py:279)
  で feature の pl_topk 価値を測れない → T012 を「lgbm-056 と同一プロトコルの train-evaluate 直接比較」
  に是正(最重要指摘)。(b) plan.md 残存の「cli.py 既定 drop-group 変更」矛盾 → 「変更なし」に訂正。
  (c) leak-guard に未来行不変を追加(T006d)。(d) top2/top3 非悪化を T012 で明示測定。(e) SC-005 の
  全 ruff/パッケージスイープを T015 に反映。(f) 単騎 rank=1.0 の許容を明記。
- **確認(analyze で修正済み)**: started 母集団 rank マスク(C1)・命名 suffix(T1)・列数13確定(U1)・
  FR 番号(R1)は codex も Confirmed。
- **不採用/据え置き**: 非 started 行の `_vs_field` 出力は下流で started 母集団に絞られ immaterial
  (`_loo_mean` が既に started/self/NaN を除外=started 馬の偏差は正しい)→ 特別マスク不要。

**self-review checklist**(補助):
- [x] LOO は per-horse-varying か(フィールド定数でないか)→ 枠帯でなく能力の偏差/順位=馬ごとに
  異なる。spike の AUC +0.0044 が経験的裏付け。
- [x] 新規リーク面ゼロか → 入力は既存 as-of 列のみ・生列読まない・leak-guard test で固定。
- [x] materialize bit-parity を壊さないか → per-race 決定的・pool-end 非依存・fingerprint 不変。
  bit-parity test 必須。
- [x] 経路を増やさないか(039 教訓)→ 031 の `_loo_mean` 流用・単一結線・postprocess 不変。
- [x] FEATURE_VERSION bump の波及 → materialize manifest / metrics_summary の feature_version、
  旧 materialize は 1 回再生成必要(055 と同型・quickstart に明記)。
- [x] 列選択リーク(憲法 III)→ 13 列を D1 でカバレッジ根拠で事前確定・OOS 後に変えない。

**残リスク**: pl_topk overlap でゲインが縮む可能性(D6-2 で実測して潰す)。codex の独立視点は
得られなかったが、設計は 031 の直接踏襲 + spike の経験的検証で担保。
