# Data Model: 評価ハーネス

新テーブルは MVP では作らない。既存データを読み、baseline 結果は `model_versions.metrics_summary` に書く。
本書は評価データセットの論理構造と metrics_summary の jsonb 形を正本化する。

## 入力 (読取、Feature 001/002 の既存テーブル)

| 用途 | 取得元 |
|---|---|
| レース・日付 | `races` (race_id, race_date) |
| ラベル | `labels.derive_labels(session, race_id)` → finished のみ win/top2/top3 |
| 出走状態・odds | `race_horses` (entry_status, odds=結果確定時, popularity) |
| 結果 | `race_results` (finish_order, result_status) |

## 評価データセット (logical)

walk-forward の各 valid レースについて構築:

- **started 馬** (母集団): `race_horses.entry_status='started'` の馬。取消・除外は除外。
- **採点母集団**: started のうち finished の馬 (= derive_labels の対象)。DNF は予測は出すが採点除外。
- **ラベル**: 採点母集団の各馬に win/top2/top3 ∈ {0,1} (同着は finish_order 共有のため複数 win 可)。
- **odds** (市場 baseline 用): `race_horses.odds`。null/<=0 は微小ウェイト (R6)。

## 不変条件 (ハーネスが検証)

- **INV-E1**: 予測は各馬 `0<=win<=top2<=top3<=1`。違反は即 fail (R5)。
- **INV-E2**: レース内合計が許容内 (`|Σwin-1|<=0.05` / `|Σtop2-2|<=0.10` / `|Σtop3-3|<=0.15`)。超過は fail。
- **INV-E3**: train の race_date は valid 窓より厳密に前 (expanding、R1)。
- **INV-E4**: 全馬非完走のレースは評価から除外。空 valid 窓は空 fold として扱う。
- **INV-E5**: 評価は決定論的 (乱数なし、(race_id, horse_id) 安定ソート)。

## metrics_summary (model_versions.metrics_summary jsonb) の形

baseline (および将来モデル) の評価結果を格納する標準形:

```json
{
  "eval": {
    "scheme": "expanding_yearly",
    "valid_years": [2008, 2009, "..."],
    "tolerance": {"win": 0.05, "top2": 0.10, "top3": 0.15},
    "ece_bins": 10,
    "overall": {
      "win":  {"log_loss": 0.0, "brier": 0.0, "auc": 0.0, "ndcg": 0.0, "ece": 0.0},
      "top2": {"...": 0.0},
      "top3": {"...": 0.0}
    },
    "by_fold": [
      {"valid_year": 2008, "n_races": 0, "win": {"log_loss": 0.0, "...": 0.0}}
    ],
    "by_field_size_ece": {"win": {"8": 0.0, "16": 0.0}}
  }
}
```

- `model_versions` 行: `model_version` (例 `baseline-market-v1` / `baseline-uniform-v1`)、
  `model_family='baseline'`、`adoption_status='candidate'`、`metrics_summary` = 上記。
- 将来モデルも同じ `metrics_summary` 形で保存し、同一評価条件で比較する。

## P2 (deferred) 永続化テーブル

`eval_runs` / `walkforward_window_results` 相当を非破壊で追加し、fold 別結果を正規化保存して検索・多
モデル比較を強化する (US4)。MVP では作らない。
