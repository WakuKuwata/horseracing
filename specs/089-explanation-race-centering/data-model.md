# Data Model: 予測根拠の実効寄与化(レース内センタリング)

**Feature**: 089-explanation-race-centering | **Date**: 2026-08-09(codex レビュー反映済)

DB スキーマ変更なし・migration なし。変更は既存 `race_predictions.explanation`(JSONB,
nullable)の**中身の意味論バージョン追加**のみ。

## explanation JSONB — v1(既存・不変)と v2(新規)

### v1(method_version=1・既存保存行と binary / market-offset モデルの新規行)

```json
{
  "method": "lgbm_pred_contrib",
  "method_version": 1,
  "k": 5,
  "base_value": -0.4317,
  "score": 0.3393,
  "other_contribution": -0.1823,
  "items": [
    {"feature": "prev_finish", "value": 3.0, "contribution": 0.5681}
  ]
}
```

- top-K 選定: |contribution|(生・母集団比)降順、タイは特徴名昇順。
- `center_within_group=False` の出力は現行実装とバイト同一(INV-E5)。

### v2(method_version=2・非 offset の race-softmax 系モデルの新規保存行)

```json
{
  "method": "lgbm_pred_contrib",
  "method_version": 2,
  "k": 5,
  "base_value": -0.4317,
  "score": 0.3393,
  "other_contribution": -0.1823,
  "score_centered": 0.4120,
  "other_contribution_centered": 0.0170,
  "centering_population_size": 14,
  "items": [
    {
      "feature": "jockey_win_rate_vs_field",
      "value": 0.042,
      "contribution": 0.2100,
      "contribution_centered": 0.1850
    }
  ]
}
```

| フィールド | v1 からの差分 | 意味 |
|---|---|---|
| method_version | 1 → 2 | 保存意味論の版。表示分岐キー(厳密一致) |
| items[].contribution | 不変(生) | 母集団比 margin 寄与(監査・INV-E1 用) |
| items[].contribution_centered | **新規** | 生寄与 − レース内平均(実効寄与・表示主値) |
| items の選定 | 変更 | 候補=そのレースで value が全馬同値でない特徴のみ。\|centered\| 降順・タイ特徴名昇順・**K 未満可**(1 頭レースは空リスト) |
| score_centered | **新規** | z_i − mean_race(z) = Σ_f centered(= log p_i − mean_race(log p)) |
| other_contribution_centered | **新規** | score_centered − Σitems.contribution_centered(v2 加法性の検証用・表示の「その他」) |
| centering_population_size | **新規** | センタリング母集団(=予測バッチ started 全行)の頭数 |
| other_contribution | 不変(生) | top-K 外の生寄与合計(INV-E1 用) |
| base_value / score / method / k | 不変 | |

### 不変条件

- **INV-E1(強化・v2 経路)**: base_value + Σ全生寄与 == **独立計算の raw score**
  (呼び出し元が供給する booster **tree margin**=offset 非含・rtol 1e-6)+全寄与の
  有限性。寄与の合算を score と定義し直す自己参照検査は不可。検査失敗はレース atomic に
  NULL。**raw 照合とレース atomic は v2 経路限定** — v1 経路(binary / offset)は
  `expected_raw_scores` を供給せず、失敗系も現行の行単位 None を維持(INV-E5 と両立。
  offset モデルに offset 込み raw を照合させると恒常的に失敗し v1 説明が全滅するため
  =analyze U1)。
- **INV-E2(継承)**: explanation の有無・内容は予測確率(win/top2/top3)に影響しない。
- **INV-E3(継承)**: top-K 選定は決定的(ソートキー明示・タイブレーク特徴名昇順)。
- **INV-E4(新設・v2・生成時)**: 各特徴のレース内 centered 総和 ≈ 0(atol)。切り詰め後の
  保存形式からは検証不能のため生成時検査の責務。失敗はレース atomic に NULL。
- **INV-E4b(新設・v2・保存値から検証可能)**: score_centered == Σitems.contribution_centered
  + other_contribution_centered(許容誤差内)。
- **INV-E5(新設)**: center_within_group=False の出力は v1 実装とバイト同一
  (method_version=1・centered 系キーなし)。binary / market-offset モデル・既存テストの
  後方互換の機械的保証。

## センタリングの母集団(レース単位 atomic)

- そのレースの予測対象馬(started・同一予測バッチ)の**全行** — softmax の分母と同一母集団。
- 1 行でも検査不能(非有限・raw 照合失敗)ならそのレースの v2 は**全行 NULL**(部分保存
  禁止: 部分平均は「レース内平均との差」でなくなるため)。予測は無傷。
- 出走取消・除外馬は元々予測バッチに含まれない(canonical field の再定義はしない)。
- 予測対象 1 頭のレース: 候補ゼロ → items=[]・score_centered=0・
  other_contribution_centered=0・centering_population_size=1。

## 意味論バージョンの決定(保存時)

| モデル | 説明の保存 |
|---|---|
| race-softmax(cond_logit / pl_topk)かつ offset なし | **v2** |
| binary objective | v1(センタリングは誤帰属) |
| market-offset モデル(offset あり) | v1(pred_contrib が offset を説明できない) |
| booster なし(退化) | NULL(現行どおり) |

定数は `METHOD_VERSION_V1 = 1` / `METHOD_VERSION_V2 = 2` に分離(単一定数の書き換えは
binary=v1 と衝突するため)。

## API 応答モデル(additive のみ・型付き変換)

- `ExplanationItem.contribution_centered: float | None = None`
- `Explanation.score_centered: float | None = None`
- `Explanation.other_contribution_centered: float | None = None`
- `Explanation.centering_population_size: int | None = None`
- v1 行: 新フィールドはすべて None(JSONB にキーなし → pydantic 既定)。
- **追加しない場合 `Explanation.model_validate` が v2 のキーを黙って落とす**(research D7)
  ため、この追加は表示の前提条件。応答は「JSONB 透過」ではなく additive な型付き変換。
- openapi は additive 差分のみ(front/admin snapshot 更新+型再生成・drift-check 緑)。

## 状態遷移(保存行のライフサイクル)

- 既存 v1 行: 不変(append-only)。表示は method_version=1 分岐で従来どおり。
- 新規予測: 上の決定表に従い v2 / v1 / NULL。
- 過去レースの v2 化: `serving predict-backfill --force`(既存 044 経路)が新 run を
  append → 読み出しは最新 run 選択で自然に v2 表示へ。旧 run は監査として残る。
  再生成 run と過去 run の確率一致は同一入力条件でのみ成立(データ訂正等では保証しない)。
