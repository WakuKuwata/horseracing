# Contract: exotic 疑似ROIバックテスト

評価先行(憲法 III)。券種別採点 + baseline 比較 + 二重疑似明示。

## 券種別採点(exotic_roi)

```
score_exotic(bets: list[ExoticBet], outcome: ExoticRaceOutcome, *, stake: float) -> list[ScoredBet]
```

- 各 bet を `is_hit(selection, outcome.finish_pos, field_size=outcome.field_size)` で採点。
  **`field_size` は生成時 canonical 値**(`ExoticRaceOutcome.field_size` = P_model/O_est を導出した母集団サイズ)。
  実出走頭数ではなく canonical 母集団に合わせ、EV 恒等式と採点の field 規則(top2/top3/none)を一致させる。
- `payout = stake * o_est` if hit else `0.0`(二重疑似)。`profit = payout - stake`。
- **複勝/ワイドはベット単位**(R4): 同レース複数 bet が各々独立に hit/miss・payout を持つ。
- 不変: hit は order 規則(exacta/trifecta 順序、quinella/trio 集合、wide/place 包含)に厳密一致。

## 集計(ExoticRoiReport)

```
aggregate_roi(scored: list[ScoredBet], *, opportunities: dict[BetType,int], skipped: dict[BetType,int]) -> ExoticRoiReport
```

- 券種別 + 総合で: n_bets, n_hits, hit_rate, total_stake, total_payout, **roi=total_payout/total_stake**,
  skip_rate, max_drawdown, max_consecutive_losses。
- **skip_rate** は scored だけからは出せない(見送りは bet にならない)。`opportunities`(候補ありの(レース,券種)数)と
  `skipped`(EV<閾値 等で見送った数)を渡し `skip_rate = skipped/opportunities`。
- `pseudo=True` 固定(全出力に「二重疑似(推定オッズ + PL 外挿)」ラベル)。

## baseline(exotic_strategies — R6)

同一レース・同一 canonical 母集団・同一 stake・同一 K・推定オッズ採点で:

```
lowest_oest_baseline(field, *, top_k, bet_types, ...) -> list[ExoticBet]              # 各券種で O_est 最小(市場最有力)を K 点
uniform_baseline(field, *, top_k, bet_types, seed=DEFAULT_SEED, ...) -> list[ExoticBet] # 候補から決定論シードで K 点均等抽出
```

- baseline も同じ canonical_field・selection・採点経路を通る(条件統一)。
- `lowest_oest` のタイブレーク: `(o_est, selection_key)` 昇順で安定整列し上位 K(同 O_est の決定論)。
- `uniform` は `DEFAULT_SEED`(定数)既定で決定論。seed は params/logic メタに含める。
- 成功判定: EV 戦略の roi が**各 baseline の roi を上回る**(per-baseline 比較、絶対 >1.0 ではない)。両 baseline 超えが理想。

## 期間バックテスト(exotic_backtest)

```
run_exotic_backtest(session, *, date_from, date_to, threshold=1.0, top_k=5, stake=100.0,
    bet_types=ALL_EXOTIC, payout_rates=None, odds_cap=10000.0, seed=DEFAULT_SEED,
    prediction_run_id: int | None = None, model_version: str | None = None,
    strategies=("ev","lowest_oest","uniform")) -> dict[str, ExoticRoiReport]
```

- **予測ソースの決定論**: レースごとの predictions は決定論規則で選ぶ — `prediction_run_id` 指定があればそれ、無ければ
  `model_version`(既定=採用中=adopted モデル)の当該レース prediction を使う。複数該当時は最新 computed_at をタイブレークし監査。
- 対象期間の各レースで: predictions+odds 読込 → canonical_field → 各戦略の bets 生成 → 結果で採点 → 集計。
- 結果は `race_results`(finished かつ finish_order 確定)から `ExoticRaceOutcome` を構築。**買い目生成段階では結果非参照**(採点でのみ参照)。
- 戻り: 戦略名 → ExoticRoiReport(全 pseudo=True)。EV vs baseline を同一条件比較。
- 決定論: 同一 (期間, params, 予測ソース) → 同一レポート。

## CLI(cli.py 拡張)

```
uv run python -m horseracing_betting exotic-recommend --race-id <id> --run-id <id> \
    [--threshold 1.0] [--top-k 5] [--stake 100] [--bet-types trifecta,trio,...]
uv run python -m horseracing_betting exotic-backtest --from 2008-01-01 --to 2008-12-31 \
    [--threshold 1.0] [--top-k 5] [--stake 100] [--bet-types ...]
```

- `exotic-recommend`: 推奨を生成・保存し券種別 EV 上位を表示。全行に **「二重疑似(モデル確率 × 推定市場オッズ)/ is_estimated_odds=true / market_odds_used=null」** を明示。
- `exotic-backtest`: EV / lowest_oest / uniform の券種別 ROI/的中率/見送り率/DD/連敗を表示。冒頭に **「二重疑似(推定オッズ + PL 外挿)評価」** を明示。

## エラー/エッジ

- 期間内に結果未確定レース → そのレースを採点対象から除外(監査ログ)。
- 母集団空・全オッズ欠損レース → 全戦略で 0 bet(スキップ)。
- 同着(dead-heat)→ data-model.md §4 と同一: 順序/集合券種で対象順位が一意でない同着はレーススキップ + 監査、place/wide は圏内同着を的中。
- baseline と EV の母集団・stake・K は必ず一致(条件統一が破れたら ERROR)。
