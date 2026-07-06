# Data Model: 複数モデル切り替え基盤

Phase 1。スキーマ差分と API 契約の型。

## スキーマ変更(migration 0011_model_purpose)

### `model_versions`(既存テーブル、PK=`model_version`)に列追加

| 列 | 型 | Null | 既定 | 意味 |
|---|---|---|---|---|
| `display_name` | TEXT | YES | NULL | 人間可読のモデル名(例「意思決定支援モデル」)。技術 ID とは別。 |
| `purpose` | TEXT | YES | NULL | 用途説明(例「市場から独立した予測。p vs q 併記用」)。 |

- **不変条件**: `model_version`(PK)・既存列(`label_schema`/`adoption_status`/`metrics_summary` 等)は一切変更しない。追加のみ。
- **既存行**: null のまま(遡及書換なし)。以後 CLI で populate。
- **downgrade**: 2 列 drop。
- **head 波及**: alembic head `0010`→`0011`。head を assert するテスト(features/live 等、0008/0009 前例)を 0011 へ更新。

### ORM(`db/src/horseracing_db/models/prediction.py` `ModelVersion`)

`display_name: Mapped[str | None]` / `purpose: Mapped[str | None]` を追加(いずれも `mapped_column(Text)`)。

## API 契約の型(pydantic, `api/src/horseracing_api/schemas.py`)

### 新 `AvailableModel`

```
AvailableModel:
  model_version: str
  display_name: str | None = None
  purpose: str | None = None
  adoption_status: str          # "active" 等(採用バッジ判定)
  is_selected: bool             # この応答が返している run のモデルか
```

### `PredictionResponse` に純追加

```
PredictionResponse:
  ... 既存フィールド不変 ...
  available_models: list[AvailableModel] = []   # このレースに run を持つモデル(空=未生成)
```

- 既存フィールド(race_id/run/horses/market_prob_source/canonical_consistent/odds_as_of/odds_source/joint*)は不変。
- `available_models` は「このレースに永続化済み prediction_run を持つ」モデルのみ。**決定的順序: active-first → created_at DESC → model_version**(051 `list_model_versions` と同一規則、憲法 V 再現性)。空配列可(予測未生成レース)。

### `ModelVersionRow`(051 レジストリ)に純追加

```
ModelVersionRow:
  ... 既存 ...
  display_name: str | None = None
  purpose: str | None = None
```

## クエリ(`api/src/horseracing_api/queries.py`)

- `list_model_versions`: 透過で `display_name`/`purpose` を含める(既存の active-first 順不変)。
- 新 `available_models_for_race(session, race_id, selected_model_version)`: `prediction_runs` の distinct `model_version` を `model_versions` に JOIN し、`AvailableModel` の材料(display_name/purpose/adoption_status)+ `is_selected` を返す。**順序は active-first → created_at DESC → model_version(決定的、051 と同一)**。1 クエリ。read-only。

## 選択ロジック(`api/src/horseracing_api/selection.py`)

`select_prediction_run(session, race_id, model_version: str | None = None)`:
- `model_version is None` → 現行(active-first → computed_at DESC → run_id DESC)。**完全不変**。
- `model_version` 指定 → `PredictionRun.model_version == model_version` で絞り、`computed_at DESC → run_id DESC`。active-first の case は適用しない。
- 該当 run なし → `None`(router が typed 404)。

## 状態遷移 / バリデーション

- モデル選択パラメータ: 任意。指定時は `model_versions` に存在しかつそのレースに run が必要。どちらか欠けたら typed 404(`prediction_unavailable`)。存在しない model_version も同 404(500 にしない)。
- `is_selected`: 応答が返した run の `model_version` と一致する `available_models` 要素のみ true。
- 用途メタ: 自由文字列(バリデーションなし)。空文字は許容せず未設定は NULL。

## 不変(leak / read-only / probability)

- `display_name`/`purpose`/`available_models` はいずれも**モデル特徴量に流入しない**(表示専用、leak boundary II)。
- 追加エンドポイント/フィールドは全て GET・read。書込は CLI のみ。
- 予測確率(win/top2/top3)は既存 run の永続値をそのまま返す(IV 不変)。
