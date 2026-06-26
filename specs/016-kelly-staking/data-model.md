# Data Model: Kelly 賭け金最適化 (016)

スキーマは **最小変更**: 既存 `recommendations` に nullable 列 `stake_fraction` を 1 本追加（migration 0006）。
他は 011/012 の契約を踏襲。backtest はレポート（非永続）。

---

## 1. `recommendations` テーブル拡張（migration 0006）

既存列（0003 + 011/012 で使用）はそのまま。**追加列**:

| 列 | 型 | NULL | 意味 |
|---|---|---|---|
| `stake_fraction` | `Numeric` | YES | Kelly 実効 fraction（λ·cap·配分適用後、bankroll 比）。flat(011/012) 行は NULL。 |

- **後方互換**: nullable・既存行は NULL のまま。011/012 の生成パスは本列に触れず NULL を維持。
- **既存列の Kelly での意味**（011/012 踏襲）:
  - `selection` JSONB: 011 の to_selection（順序券種=順序配列 / 無順序=整列配列 / 単一=単一馬）。
  - `market_odds_used`: 実 exotic オッズ（012 present 時）／推定時 NULL。
  - `estimated_market_odds_used`: 010 推定オッズ（推定経路）／実時 NULL。
  - `is_estimated_odds`: 推定オッズ使用 = true（= double_pseudo、API 導出と同一）。
  - `pseudo_odds`: 1 / P_model(c)。
  - `pseudo_roi`: edge(c) = P_model(c)·O(c) − 1。
  - `logic_version`: Kelly 設定一式を構造化エンコード（下記）。
  - `prediction_run_id` / `race_id` / `computed_at` / `bet_type`: 既存どおり。
- **再現性（憲法 V）**: 絶対 stake = `stake_fraction` × bankroll(config)。fraction と config（logic_version）で
  過去 Kelly 推奨を完全再現・監査可能（SC-009）。

### `logic_version` エンコード（例）

`kelly-v1;alloc=exact;lam_real=0.25;lam_est=0.10;cap_bet=0.05;cap_tot=0.10;omin=1.5;bank=100;odds=real|est;p009=...;o010=...`

allocation 方式（exact / heuristic）・λ・cap・O_min・初期 bankroll・odds 源・009/010 版を含む。

### バリデーション

- `stake_fraction ∈ [0, cap_bet]`（個別上限）、Σ over (race,bet_type) ≤ cap_total。
- `is_estimated_odds=true` ⇒ `market_odds_used IS NULL` かつ `estimated_market_odds_used IS NOT NULL`（012 規約）。
- `pseudo_roi = pseudo_odds 由来の P_model と O から edge` が整合（pseudo_roi = P_model·O − 1, P_model=1/pseudo_odds）。
- edge ≤ 0 の行は存在しない（見送りは不保存）。

---

## 2. Kelly Config（実行時パラメータ、非テーブル）

CLI 引数 / 設定として渡し、logic_version に焼き込む。永続テーブルは持たない（再現は logic_version 経由）。

| パラメータ | 既定 | 意味 |
|---|---|---|
| `lambda_real` | 0.25 | 実オッズの fractional Kelly 係数 |
| `lambda_est` | 0.10 | 推定オッズの保守的係数 |
| `cap_bet` | 0.05 | 1 買い目の bankroll 比上限 |
| `cap_total` | 0.10 | (race,bet_type) 合計上限 |
| `o_min` | 1.5 | 最小オッズ閾値（分母安定化） |
| `min_edge` / `min_edge_est` | 0 / >0 | 採用 edge 下限（推定はより厳しく） |
| `bankroll` | 100.0 | 推奨生成時の現在資金（stake 算出基準） |
| `allocation` | `exact` | `exact`（期待対数成長最大化）/ `heuristic`（個別+比例縮小） |
| `enable_estimated` | true | 推定オッズ経路の Kelly 有効/無効 |

---

## 3. BankrollBacktestResult（レポート、非永続）

011 の backtest と同様に**返り値レポート**。永続化しない。

| フィールド | 意味 |
|---|---|
| `period` | 評価期間（開始/終了 race_id 範囲） |
| `strategy` | `kelly` / `flat` |
| `allocation` | exact / heuristic（kelly 時） |
| `terminal_bankroll` | 終端資金 |
| `log_growth_rate` | 平均対数成長率 Σlog(W_t/W_{t-1})/N |
| `max_drawdown` | 最大ドローダウン |
| `ruin_probability` | 破産確率（実経路 0/1 + block bootstrap 推定） |
| `variance` | リターン分散 |
| `max_losing_streak` | 最大連敗 |
| `n_bets` / `n_hits` / `hit_rate` / `skip_rate` | 件数・的中率・見送り率 |
| `segment` | `real` / `double_pseudo`（分離集計） |
| `baseline_comparison` | flat との同一条件比較・success 判定（リスク調整後成長で優位か） |

### 集計規則

- **walk-forward 順**で bankroll 逐次更新。ruin 閾値割れで停止。
- **実 / 二重疑似 区間を分離**（実オッズ payout と推定オッズ payout を混合しない）。
- **block bootstrap**（時系列ブロック保持）で ruin 割合を推定（i.i.d. シャッフル禁止）。
- success は単なる ROI>1 ではなく、log 成長率・最大DD・破産確率の併記でリスク調整後優位を判定。

---

## 4. 関係・不変条件

- recommendations(Kelly 行) → prediction_runs（model_version）→ race_predictions（P_model 由来 p）。
- recommendations(Kelly 行) → races / exotic_odds(012, 実) または market_odds(010, 推定)。
- **リーク境界**: stake_fraction / pseudo_odds / オッズ / q は features・training に出現しない（leak-guard test）。
- **確率整合性（憲法 IV）**: P_model は 009 の canonical field（取消・除外を除外し再正規化）に基づく。
