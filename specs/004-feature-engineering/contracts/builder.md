# Contract: builder / history (as-of 不変条件)

`horseracing_features` の中核 API。

## loader

```python
def load_frames(session, start_date=date(2007,1,1), end_date=None) -> Frames
    # races / race_horses / race_results を pandas DataFrame で一括ロード (2007+)。
```

## history (as-of 集計)

```python
def build_history_features(frames) -> pd.DataFrame
    # 各 race-horse について、race_date < R の履歴のみで過去成績/履歴件数特徴量を計算 (research R1)。
    # 不変条件: race R の特徴は race_date >= R の race を一切使わない (INV-F1)。
    #   完走前提系は finished のみ、career_starts は started、件数系は別系統 (INV-F3)。
    #   出走歴ゼロは null (INV-F2)。
```

## static_features

```python
def build_static_features(frames) -> pd.DataFrame
    # 発走前静的 (レース条件・馬属性・馬体重・枠)。timing は各列の metadata に従う。
```

## builder

```python
def build_feature_matrix(session, *, start_date=..., end_date=..., low_history_max=2) -> pd.DataFrame
    # static + history + 件数 + フラグ を結合し固定スキーマの FeatureMatrix を返す。
    # 全列が REGISTRY に登録済みかを検証し、未登録列は FeatureSchemaError (INV-F4)。
    # 決定論的 (INV-F6)。
```

## encoding (P2)

```python
def fit_target_encoding(frames, *, train_cutoff: date) -> dict
    # train_cutoff より前のみで fit。未知カテゴリは既定値。valid/test を見ない (US3)。
```
