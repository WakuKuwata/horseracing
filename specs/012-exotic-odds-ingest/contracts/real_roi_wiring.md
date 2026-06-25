# Contract: 実 exotic オッズの推奨/バックテスト配線 + 乖離評価

011 の exotic 経路に実オッズを優先配線し、推定にフォールバック。評価先行で推定 vs 実の乖離を計測。

## 実オッズ読込(betting/exotic_market.py)

```
load_real_exotic_odds(session, race_id: str) -> dict[tuple[str, tuple[int, ...]], float]
```

- 戻り: `(bet_type, tuple(selection)) -> odds`(`exotic_odds` の最新値)。キーは 011 の `to_selection` と同一正準形。
- **禁止**: 結果参照(採点は別経路)、母集団の独自正規化。

## 推奨配線(betting/exotic_recommend.py 拡張)

```
generate_exotic_recommendations(session, *, race_id, prediction_run_id, ..., use_real_odds=True)
```

- 011 の `canonical_field`→`exotic_ev_bets`(候補生成)を**必ず経由**。各候補 selection で `load_real_exotic_odds` を引く:
  - **ヒット**: `market_odds_used=実オッズ`・`is_estimated_odds=false`・`estimated_market_odds_used=null`・`EV=P_model×実オッズ`・
    `pseudo_roi=EV−1`・`pseudo_odds=1/P_model`。決定時の実オッズを market_odds_used にスナップショット(憲法 V 監査)。
  - **ミス**: 011 推定にフォールバック(`is_estimated_odds=true`・`estimated_market_odds_used=O_est`・二重疑似)。
- **行単位で実/推定を区別**(混在させない)。EV≥閾値 上位 K は 011 と同一(実オッズの EV と推定の EV を同一閾値で比較)。
- append-only。logic_version に実オッズ優先方針・フォールバックを含める。

## 採点(betting/exotic_roi.py 拡張)

```
score_exotic(bets, outcome, *, stake, real_odds=None) -> (scored, n_unscoreable)
```

- 的中買い目: 実 final オッズがあれば payout=stake×**実オッズ**(`pseudo=false`)、無ければ stake×O_est(`pseudo=true`)。
- **推奨後取消**(推奨時に存在した馬が後で取消)→ void/skip(payout 計上せず監査)。011 の dead-heat/None 規律(`is_hit` が None を
  返す順序/集合券種の同着はレーススキップ、複勝/ワイド圏内同着は的中)を**実オッズ採点でも継承**。
- 集計(`aggregate_roi`)は**実払戻と疑似払戻をラベル分離**(real_roi / pseudo_roi を別指標で報告)。
- **リーク・ガード**: バックテストの**買い目決定は実最終配当を入力にしない**(後知恵)。選定は 011 推定 O_est(事前可得)で行い、実最終
  配当は payout/採点のみに使う(II)。`run_exotic_backtest` は `load_real_exotic_odds` を**採点にのみ**渡す(`exotic_ev_bets` の EV
  入力には渡さない)。`generate_exotic_recommendations` の実オッズ EV 利用はライブ(result-pending)前提。

## 乖離評価(betting/exotic_divergence.py)

```
exotic_divergence(session, *, date_from, date_to, model_version=None, payout_rates=None) -> dict[str, DivergenceReport]
```

- 各レースで 011 推定 O_est(canonical_field→estimate_market_odds)と `exotic_odds` 実値を**同一 selection**で対応付け、券種別に:
  - `coverage_rate`(実が存在した組み合わせ割合)、`n_pairs`
  - `log_ratio = log(実/推定)` の median / MAE / P90
- 推定= baseline、実=実測、推定側は二重疑似ラベル。カバレッジ率を必ず併記。
- 決定論(同一入力→同一レポート)。

## CLI(betting/cli.py 拡張)

```
uv run python -m horseracing_betting exotic-divergence --from <d> --to <d> [--model-version ...]
```

- 券種別の coverage_rate / log 比 median/MAE/P90 を推定= baseline 明示で表示。

## エラー/エッジ

- 実オッズ完全欠損レース → 全推奨が 011 推定(二重疑似)、乖離評価は coverage_rate=0 を明示。
- selection キー不一致が起きうる箇所は必ず to_selection 同一配列で突合(スカラ/順序/整列差を吸収)。
- 実オッズと推定で母集団が違う懸念 → canonical_field を単一経路として共有(独自正規化しない)。
