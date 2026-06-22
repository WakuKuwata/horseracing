# Contract: FeatureMatrix / FeatureRegistry

`horseracing_features` が公開する固定スキーマと metadata 契約。

## FeatureRegistry

```python
class AvailabilityTiming(StrEnum):
    PRE_ENTRY = "pre_entry"      # 出馬表前
    POST_FRAME = "post_frame"    # 枠順後
    POST_WEIGHT = "post_weight"  # 馬体重後
    POST_ODDS = "post_odds"      # オッズ後
    PRE_RACE = "pre_race"        # 直前
    POST_RESULT = "post_result"  # 結果後 (モデル入力から除外)

@dataclass(frozen=True)
class FeatureMeta:
    source: str                 # 由来テーブル/系統
    timing: AvailabilityTiming
    missing_policy: str         # "null" (Unknown, 0 と区別) | "zero_ok" (件数)

REGISTRY: dict[str, FeatureMeta]   # data-model.md の全特徴列を宣言

def model_input_features() -> list[str]:
    """timing != POST_RESULT の特徴列名 (識別列 race_id/horse_id を除く)。"""
```

## 保証 (builder が強制)

- FeatureMatrix の全特徴列が `REGISTRY` に存在する。未登録列は `FeatureSchemaError` (fail-fast)。
- 結果確定 `odds`/`popularity` は REGISTRY に**モデル特徴量として登録しない**。matrix に混入したら未登録
  列として検出される。
- `model_input_features()` は `post_result` を機械的に除外する (INV-F5)。
- 列順・特徴名は決定論的に固定。

## FeatureMatrix

- pandas DataFrame。行 = race-horse (started 馬)。列 = data-model.md の固定スキーマ。
- 識別列 `race_id`, `horse_id` を含む (モデル入力ではない)。
- 欠損は `null`/NaN (missing_policy=null) または件数 0 (zero_ok)。
