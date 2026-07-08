# Data Model: 060 市場残差型・精度最優先モデル

**スキーマ変更なし・migration なし。** 既存テーブル/artifact への追記のみ。

## 既存エンティティの利用(read のみ)

| エンティティ | 利用 | 備考 |
|---|---|---|
| `race_horses.odds` | read | 対象レースの単勝オッズ → q。closing-leaning(013 以来の既知の限界、metadata に開示) |
| `race_results` | read(ラベルのみ) | 学習ラベル(win/finish_rank)。offset には不使用(挙動 guard で固定) |
| `model_versions` | 既存列のみ | `lgbm-060-mkt` を adoption_status=candidate(非 active)で登録。display_name/purpose は 057 CLI |
| `prediction_runs` / `race_predictions` | 既存列のみ | logic_version に `mkt=logq` マーカー追記(文字列、`sdisc=`/`reg=` と同型) |

## 派生値(非永続・実行時再構成)

### 市場確率 q / 市場 offset

- 定義: `q_i = (1/odds_i) / Σ_j (1/odds_j)`(j = started かつ有効オッズの馬、010 定義)
- `offset_i = log(clip(q_i, 1e-6, 1.0))`
- **特徴列ではない**: feature_cols / feature_hash / FEATURE_VERSION / feature_snapshots / materialized parquet のいずれにも入らない
- 有効性判定: odds が非 null・数値・> 0。1 頭でも無効なレースは学習除外 / serving typed skip(research D4)

### TrainingMatrix 補助列(in-memory のみ)

- `mkt_odds`: float64、(race_id, horse_id) で race_horses.odds を結合。`finish_rank`(042)と同型のラベル側補助列で `feature_cols` 外

## Model artifact metadata(metadata.json / metrics_summary)追記

```json
{
  "market_offset": {
    "kind": "log_q_devig",
    "source": "race_horses.odds",
    "q_clip": 1e-6,
    "limitation": "closing-leaning odds; retrospective accuracy model"
  }
}
```

- キー欠如 = offset なし(既存モデル後方互換、serving は既存経路のまま)
- serving `load_serving_model` はこのキーを ServingModel に透過し、pipeline が offset 構成を分岐

## State transitions

- モデル登録: (ゲート全通過) → candidate 登録(非 active)。自動昇格なし。不通過 → 登録なし・評価結果のみ spec/tasks に記録
- serving 予測: オッズ完全カバー → 予測永続化(lv に mkt=logq)/ 欠損 → typed skip(予測行を作らない)
