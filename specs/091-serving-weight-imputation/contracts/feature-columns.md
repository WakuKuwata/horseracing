# Contract: 特徴列 `prev_weight`

**Feature**: 091 | **Date**: 2026-08-10

## 1. 列定義(凍結)

| 属性 | 値 |
|---|---|
| 列名 | `prev_weight` |
| dtype | `float64` |
| source | `race_horses` |
| availability_timing | `pre_entry` |
| missing_policy | `null`(Unknown。0 と区別する) |
| FEATURE_GROUPS | `weight_history` |
| 単位 | kg |
| FEATURE_VERSION | `features-020` で導入 |

## 2. 導出規約

```
source = race_horses ⋈ races where
           entry_status == started
       AND weight IS NOT NULL
       AND 200 <= weight <= 800

prev_weight(target) = merge_asof(
    target, source,
    on="race_date", by="horse_id",
    direction="backward", allow_exact_matches=False
).weight
```

- `allow_exact_matches=False` が**同日除外**を担う
- 供給元が無ければ NaN。0・集団平均・その他の代用値を入れない
- 同一馬・同日に供給元候補が複数ある場合は NaN(安定ソート任せの暗黙選択を禁止)

## 3. 既存列との関係(実測に基づく)

| 関係 | 内容 |
|---|---|
| 鮮度 | `days_since_last`(既存)が `prev_weight` の鮮度そのもの。実測 100% 一致のため専用列を作らない |
| 有無 | `has_past_race` / `is_debut`(既存)が `prev_weight` の有無そのもの。実測 100% 一致 |
| 当日体重 | `weight`(既存)。`prev_weight` は**代替ではなく併存**する。当日体重を上書きしない |

## 4. 不変条件

| ID | 内容 |
|---|---|
| **INV-W1** | 厳密前のみ参照。同日の走を供給元にしない |
| **INV-W2** | 供給元は started かつ体重非 NULL かつ範囲内のみ |
| **INV-W3** | 対象レースの結果・当日オッズ・同日他レースを変更しても値が変化しない(leak-guard) |
| **INV-W4** | 過去に出走がある行では非欠損。**破れたら fail-closed**(D1 の列縮小の前提が崩れた合図) |
| **INV-W7** | `features-020` の既存 137 列は `features-018` とバイト一致 |
| **INV-W8** | `source_fingerprint` が `features-018` から不変 |
| **INV-W9** | active `lgbm-064-f02acc` の予測が `features-020` の下でバイト不変 |

## 5. FEATURE_VERSION と serving 互換

```
FEATURE_VERSION = "features-020"

COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-020"] = {
    "features-018": "263ef6b7ac5eccf45faf90005a5904de91adfed639b8d3f14a04c4d20f141a3f",
}
```

- pin した hash は lgbm-064-f02acc の artifact metadata から**実測**した値
- `features-019` は 070 の revert で焼却済み。**使用禁止**
- `features-017` は pin しない(lgbm-063 は retired)
- 純加算 1 列なので既存列は additive left-merge により構造的にバイト不変。加えて INV-W7 で一度きり実データ実測する

**なぜ列を追加するのか(既存 `weight` 列に流し込まない理由)**: `feature_hash` は列名のみのハッシュである。既存列に別の意味の値を入れると hash が変わらないため、**古いモデルが黙って新しい意味の値を食う**。017 が踏んだ「値変更 bump の serving fail-close」問題の同型であり、列を足す方が構造的に安全である。

## 6. materialize

- 新しいソース列を読まない(`weight` / `race_date` / `entry_status` は既に loader が SELECT 済み)→ `source_fingerprint` **不変**
- `prev_weight` は as-of 列なので `materialized_columns()` の registry 駆動導出に自動的に入る(STATIC_COLUMNS ではない)
- 実装時に 1 回 re-materialize が必要(現在の parquet は既に stale で fail-closed 状態)

## 7. registry の availability_timing 是正(US3・値不変)

| 列 | 現状 | 是正後 | 根拠 |
|---|---|---|---|
| `carried_weight_ratio` | `pre_entry` | `post_weight` | 実装は 斤量 ÷ 当日体重([static_features.py:41](../../../features/src/horseracing_features/static_features.py))で、当日体重は `post_weight` 宣言 |

- 宣言はメタデータであり**値・列名・列順・`feature_hash` を変えない**(INV-W10)
- registry 全体を監査し、宣言より遅い入力に依存する列が他に無いことを確認する
- この是正は本 feature の採否と独立。**REJECT でも残す**
