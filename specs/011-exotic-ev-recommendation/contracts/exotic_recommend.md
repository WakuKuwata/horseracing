# Contract: exotic EV 推奨生成

`betting/src/horseracing_betting/` に追加するモジュールの公開契約。実装技術は plan.md 準拠。

## canonical_field(p/q 母集団整合 — R1)

```
canonical_field(predictions: dict[int, float], odds: dict[int, float], *, scratched: set[int] = ()) -> CanonicalField
```

- 入力: `predictions`(horse_number→win_prob、006/race_predictions)、`odds`(horse_number→win オッズ、race_horses.odds)。
- 規則:
  - 母集団 = `win_prob>0` **かつ** `odds>0` **かつ** `not scratched` の horse_number。
  - `p_norm` = 母集団に絞った win_prob を Σ=1 に再正規化。
  - `odds_norm` = 母集団に絞った win オッズ(そのまま、010 が内部で q 正規化)。
  - 除外馬は `excluded`(reason 付き)へ。
- 不変: `set(p_norm)==set(odds_norm)==set(horse_numbers)`。母集団空なら field_size=0(呼び出し側でスキップ)。
- **禁止**: 結果(着順/オッズ確定)参照、p と q の取り違え。

## exotic_ev_bets(009×010 → EV → 上位K)

```
exotic_ev_bets(field: CanonicalField, *, threshold: float = 1.0, top_k: int | dict[BetType,int] = 5,
               bet_types: Iterable[BetType] = ALL_EXOTIC, payout_rates: dict | None = None,
               odds_cap: float = 10000.0) -> list[ExoticBet]
```

- 手順:
  1. `joint = joint_probabilities(field.p_norm, field_size=field.field_size)` → 各券種 P_model。
  2. `est = estimate_market_odds(field.odds_norm, field_size=field.field_size, payout_rates=payout_rates, odds_cap=odds_cap)` → 各券種 O_est。
  3. 各券種の**共通キー**で `ev = p_model * o_est`(o_est が None/∞cap 超過は候補除外)。
  4. selection を JSONB 安全に正準化(R2)。`ev ≥ threshold` を `(-ev, selection_key)` で整列し券種別 top_k。
- 不変: 返る ExoticBet は同一 canonical 母集団由来、`ev≥threshold`、券種別 ≤ K、決定論順序。
- 単勝(win)は 007 の責務。本関数は exotic 6 券種のみ。

## generate_exotic_recommendations(永続)

```
generate_exotic_recommendations(session, *, race_id: int, prediction_run_id: int,
    threshold=1.0, top_k=5, stake=100.0, bet_types=ALL_EXOTIC, payout_rates=None,
    odds_cap=10000.0, logic_version: str | None = None) -> list[Recommendation]
```

- race_predictions(p)+ race_horses.odds(q)+ entry_status を読み `canonical_field` → `exotic_ev_bets` → `recommendations` を INSERT。
- 各行: `bet_type`, `selection`(JSONB 安全配列), `market_odds_used=None`, `estimated_market_odds_used=o_est`, `is_estimated_odds=True`, `pseudo_odds=1/p_model`, `pseudo_roi=ev−1`, `computed_at`(生成時刻), `prediction_run_id`, `race_id`(FR-005 全列)。
- `logic_version` 既定 = `default_exotic_logic_version()`(EV式/閾値/K/stake/控除率/**q ソース=market win odds**/cap/母集団ポリシー/009/010 版。FR-006)。
- append-only(既存行を上書き・削除しない)。冪等性は呼び出し側の重複回避に委ねる(同一 run+race の二重実行は重複行)。
- **禁止**: 結果参照、odds をモデル特徴化、market_odds_used に O_est を格納。

## selection ヘルパ(exotic_selection)

```
to_selection(bet_type, key) -> list[int]            # 009/010 のキー(tuple/frozenset) → 素の JSON 配列(順序券種=順序保持、無順序=昇順整列)
selection_key(selection) -> str                     # (bet_type, tuple(horses)) の決定論タイブレーク文字列
is_hit(selection, finish_pos, *, field_size) -> bool   # 券種別的中(R3)。finish_pos=dict[horse_number→着順]
```

- 順序券種(exacta/trifecta)は順序保持、無順序(quinella/wide/trio)は horse_number 昇順整列、place は単一要素 `[i]`。
- `is_hit` は wide/place で field_size 規則(top2/top3/none)を 009 と共有。**field_size は生成時 canonical 値**。

## エラー/エッジ

- 母集団空・全オッズ欠損 → 推奨 0 件(例外ではない)、`excluded` を監査ログ。
- P_model→0 の券種 → pseudo_odds 発散回避のため候補除外(010 の cap と同方針)。
- 小頭数(≤4)→ place/wide は対象外(009 規則)。trifecta/trio は組数が少ない。
- 決定論: 同一 (predictions, odds, params) → 同一 Recommendation 群(順序含む)。
