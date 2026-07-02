# Data Model: Harville stage 割引 (049)

**スキーマ変更なし**(migration なし、head=0008 不変)。全て実行時の値オブジェクトと既存カラムへの記録のみ。

## StageDiscount(値オブジェクト、非永続)

| フィールド | 型 | 意味 |
|---|---|---|
| lambda2 | float | 2着ステージの冪。1.0=素の Harville |
| lambda3 | float | 3着ステージの冪。1.0=素の Harville |
| n_races_l2 | int | λ_2 フィットに使ったレース数(同着除外後) |
| n_races_l3 | int | λ_3 フィットに使ったレース数 |
| fallback | bool | identity fallback(サンプル不足/境界張り付き)発動 |

- 不変条件: 0.1 ≤ λ ≤ 5.0(フィット時)。identity は λ2=λ3=1.0。
- `lambda2 == 1.0 and lambda3 == 1.0` は既存導出コードパスへの明示分岐(バイト一致、FR-001)。

## λ フィットサンプル(読み取り専用ビュー、非永続)

| フィールド | 源 | 意味 |
|---|---|---|
| race_id | prediction_runs | 12 桁 JRA-VAN id |
| race_date | races | 厳密前境界判定(race_before、race_id タイブレーク) |
| win_vector | race_predictions.win_prob | started 馬の正規化済み p(engine 正規化を通す) |
| finish_1/2/3 | race_results.finish_order | 確定 1〜3 着の horse_id(result_status='finished' のみ) |

- λ_2 サンプル条件: 1 着・2 着とも一意。λ_3: 1〜3 着すべて一意。除外件数を表面化(D5)。
- 結果(finish_order)は**フィットのラベルとしてのみ**使用。選定・特徴に不使用(憲法 II)。
- 既存 `load_p_samples`(勝者のみ)は不変。新 loader を追加。

## logic_version への記録(憲法 V)

採用時、推奨/予測の logic_version に追記(046/048 の `pcal=...` と同一規律):

```
sdisc=harville;l2=<λ2:.5f>;l3=<λ3:.5f>;n2=<n_races_l2>;n3=<n_races_l3>
```

identity fallback 時は `sdisc=identity`。非採用・λ 未指定の経路は従来文字列とバイト不変(後方互換)。

**注**: serving(素の p でフィット)と betting(two_gamma 適用後の p' でフィット)は**別フィット値**になる(分布一致原則、research D4)。各経路の lv に各自の λ̂ が記録されるため監査上は区別可能。

## 評価レポート(AdoptionReport 同型、ファイル/stdout のみ)

- fold 別: valid_year / n_races / baseline・candidate の top2/top3 LogLoss・ECE / fold λ̂
- overall: 両構成の top2/top3 全指標 + win 指標一致検証(diff==0)+ reliability bins(帯別 pred_mean/realized)
- gate: PRIMARY/MUST/ガードの各判定と数値、採否ブール
- exotic 比較: 複勝/ワイド/三連複の pseudo-ROI(λ=1 vs λ̂)と差分

## 関係(既存エンティティへの影響)

- `race_predictions.top2_prob/top3_prob`: 採用時、割引済みの値が入る(win_prob は不変)。スキーマ・CHECK(PROB_MONOTONIC)不変 — 単調性は導出が構成的に保証。
- `recommendations`: 複勝等 exotic の P_model が割引済みになる(採用時)。カラム不変、logic_version で判別。
- API/openapi: 変更なし(値の意味が校正済みになるのみ)。
