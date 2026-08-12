# Contract: explanation v2(レース内センタリング済み寄与)

**Feature**: 089-explanation-race-centering(codex レビュー反映済)

040 の `contracts/prediction-explanation.md` は v1 の正本契約として維持し、同文書に
「v2 は 089 の本契約を正とする」旨を追記する(契約競合の解消・FR-016)。

## 1. 計算契約(compute_explanations)

```
compute_explanations(booster, X, feature_cols, *, k=5,
                     center_within_group=False,
                     expected_raw_scores=None)
  -> list[dict | None]   # X の行順に整列
```

- `center_within_group=False`(既定): **v1 実装とバイト同一の出力**(METHOD_VERSION_V1、
  centered 系キーなし)。既存呼び出し・binary / market-offset モデルの後方互換(INV-E5)。
  **失敗系も現行どおり行単位 None**(レース atomic は v2 経路限定 — v1 の後方互換を
  失敗ケースでも壊さない)。
- `expected_raw_scores`(任意・長さ n_rows): 説明とは独立に計算された **booster tree
  margin(市場オッズ offset を含まない)**。供給時は base + Σ全生寄与 との照合
  (rtol 1e-6)を行う(自己参照でない真の整合性検査= INV-E1 強化)。未供給時は従来の
  内部整合検査のみ(後方互換)。**供給は v2 経路(center_within_group=True)のときのみ**
  とする — offset モデルでは `raw_predict` の値が offset 込みで pred_contrib の合算
  (tree margin)と恒常的に不一致になり、無条件供給すると v1 説明が全滅する
  (analyze U1 で検出した欠陥経路)。
- `center_within_group=True`: v2 出力。手順は固定:
  1. `booster.predict(X, pred_contrib=True)` で全行の寄与行列を 1 回計算(現行同一)。
  2. 整合性検査(**レース単位 atomic**): 全行の寄与が有限、かつ(供給時)raw 照合成立。
     1 行でも失敗 → **全行 None**(部分平均は softmax 母集団とずれるため部分保存禁止)。
  3. 列平均 mean_f を**全行**で計算し `centered[i,f] = contrib[i,f] − mean_f`。
  4. INV-E4(生成時): 各 f の Σ_i centered[i,f] ≈ 0(atol)。不成立なら全行 None。
  5. **候補除外**: そのレースで value が全馬同値の特徴を候補から外す(全馬 NaN は同値。
     NaN と非 NaN の混在は同値でない)。
  6. top-K 選定: 候補を |centered| 降順・タイ特徴名昇順(INV-E3)。**K 未満可**。
     候補ゼロ(1 頭レース等)は items=[]。
  7. 保存値: items は生+centered 併記。`score_centered = Σ_f centered[i,f]`、
     `other_contribution_centered = score_centered − Σ items.contribution_centered`
     (INV-E4b: 保存値から検証可能)、`centering_population_size = n_rows`。
     `base_value`/`score`/`other_contribution`(生)は v1 と同一定義。
- 例外時は `[None] * len(X)`。**防御範囲は pred_contrib 呼び出しだけでなくセンタリング・
  ソート・JSON 化を含む説明経路全体**とし、呼び出し側(serving)でも二重に防御する
  (説明失敗は予測を妨げない=040 防御姿勢の維持・強化)。

## 2. 呼び出し契約(serving)

- `predict_race` は次の条件で `center_within_group=True` を渡す:
  `model.objective in WinModel.SOFTMAX_OBJECTIVES` **かつ** `model.market_offset is None`。
  - binary → False(独立確率ではセンタリングが誤帰属)。
  - market-offset モデル → False(softmax 実入力 = offset + tree margin のうち pred_contrib
    は tree 部分しか説明せず、「レース内相対スコアの分解」の主張が成立しない)。
    `offset_centered` の保存による v2 拡張は deferred。
- `expected_raw_scores` は **center=True のときのみ**既計算の `raw` を渡す(v2 対象は
  offset なしなので raw = booster tree margin。**追加の booster.predict 不要**)。
  center=False(binary / market-offset)のときは渡さない — offset モデルの `raw` は
  offset 込みで照合が恒常的に失敗し、v1 説明を全滅させるため(analyze U1)。
- 判定集合は training の `WinModel.SOFTMAX_OBJECTIVES` を単一の正本として参照する
  (文字列リテラルの二重管理禁止)。
- 予測確率(win/top2/top3)・snapshots は説明計算の変更前後・有無でバイト一致(INV-E2)。

## 3. API 契約(additive のみ・型付き変換)

- 追加フィールド(すべて additive・v1 行は None):
  - `ExplanationItem.contribution_centered: float | None = None`
  - `Explanation.score_centered: float | None = None`
  - `Explanation.other_contribution_centered: float | None = None`
  - `Explanation.centering_population_size: int | None = None`
- 既存フィールドの削除・改名・型変更なし。`method_version` は既存 int のまま(値 2 が
  新たに流れる)。
- openapi.json は additive 差分のみ。front/admin の committed snapshot・生成型を同期し
  drift-check 緑。
- v1 保存行の応答: 新フィールド None(0 埋め禁止)。

## 4. 表示契約(front)

- 分岐は **`method_version === 2` の厳密一致**(`>= 2` 禁止 — 将来 v3 の誤表示防止)。
  1 → v1 表示。それ以外の未知版 → 「未提供」扱い。
- **v2**:
  - 主表示値(バー・符号付き数値)= `contribution_centered`。
  - タイトル「レース内でのスコア寄与(上位k要因)」。
  - 注記(常時)=「同一レース内の平均に対する、レース内正規化前の相対スコア寄与です
    (最終確率の内訳ではありません)」+既存の因果注記。
  - 「その他の特徴(合算)」行= `other_contribution_centered`(centered で意味論統一)。
  - items 空(1 頭レース等)=「このレースでは比較できる差がありません」(ゼロ項目の
    水増し表示禁止)。
  - `contribution_centered` が null/非有限の item を含む v2 は生値へフォールバックせず、
    explanation 全体を「未提供(形式不整合)」として扱う。
- **v1**: 現行表示・注記を維持(退行なし)。未提供(null)は現行の「スコア寄与は未提供
  です」を維持。

## 5. 禁止事項

- 保存済み v1 行の書き換え・削除(append-only、憲法 V)。
- explanation 由来の値をモデル特徴・学習・推奨選定に還流(憲法 II、既存 leak-guard 維持)。
- 表示層での寄与の再センタリング・再計算(保存値の転記のみ)。
- 「確率への寄与」「勝つ理由」等、softmax 後確率の内訳と誤読させる文言
  (正しくは「レース内正規化前の相対スコア寄与」)。
- 検査の自己参照化(寄与合算を score と定義して再合算する検査)。
- 部分母集団でのセンタリング(レース単位 atomic 以外の保存)。
