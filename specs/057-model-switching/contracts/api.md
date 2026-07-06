# API 契約: 複数モデル切り替え基盤

read-only(全 GET)・OpenAPI 純追加。front/admin snapshot byte 一致を維持。

## 1. `GET /api/v1/races/{race_id}/predictions`(拡張)

### 追加クエリパラメータ

| 名前 | 型 | 既定 | 意味 |
|---|---|---|---|
| `model_version` | string | (なし) | 予測に使うモデル。省略時=採用モデル(現行挙動)。 |

### 挙動

- `model_version` 省略 → 現行と完全同一(active → computed_at DESC → run_id DESC の run 選択、各馬確率不変)。
- `model_version` 指定・該当 run あり → そのモデルの最新 run(computed_at DESC → run_id DESC)を返す。監査エンベロープ(`run.model_version` 等)は選択 run のもの。
- `model_version` 指定・該当 run なし(モデル不在含む)→ **typed 404** `{"status":404,"code":"prediction_unavailable","detail":"model {mv} has no prediction for race {race_id}"}`。**採用モデルへ暗黙フォールバックしない**。未処理 500 にしない。
- `bet_type` 併用時の joint 計算は選択 run の canonical p 上で行う(既存ロジック不変)。

### レスポンス追加フィールド(純追加)

```jsonc
{
  // ... 既存フィールド不変 ...
  "available_models": [
    { "model_version": "lgbm-055", "display_name": "意思決定支援モデル",
      "purpose": "市場から独立した予測", "adoption_status": "active", "is_selected": true },
    { "model_version": "lgbm-057-acc", "display_name": "精度最優先モデル",
      "purpose": "過去走オッズ込み(将来)", "adoption_status": "candidate", "is_selected": false }
  ]
}
```

- このレースに永続化済み run を持つモデルのみ。active → 表示順。空配列可(予測未生成レース = 既存の typed-empty と併存)。
- 既存フィールド(race_id/run/horses/market_prob_source/canonical_consistent/odds_as_of/odds_source/joint*)は不変。

## 2. `GET /api/v1/models`(拡張)

`ModelVersionRow` に `display_name` / `purpose`(いずれも nullable)を純追加。未設定は null。他フィールド・順序不変。admin レジストリ/詳細が透過表示。

## 3. 契約不変(テストで固定)

- read-only: 全 path GET のみ(既存不変テスト維持)。
- OpenAPI 純追加(削除・変更なし)。生成後 front/admin の `openapi.json` + `schema.d.ts` を再生成し、front↔admin snapshot byte 一致 + drift-check 緑。
- 後方互換: `model_version` 省略時の run 選択・各馬確率は既存テストで不変を固定。追加フィールドは既存の個別フィールド assert を壊さない。

## 4. 書込(API 外・参考)

用途メタ設定は CLI: `set-model-label --model-version <mv> --display-name <name> --purpose <text>`(training/registry 層、DB read-write)。API/admin には書込を足さない(read-only 維持)。
