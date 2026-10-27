# D — 校正来歴を本番経路へ結線するかの判断（2026-09-22）

## 結論

**結線しない。** 厳密な OOS 制約を現行の runtime λ 経路に当てると、
**材料が構造的に足りず fail-closed で止まる**。これは実装の問題ではなく前提の問題で、
「保存済み予測の中に OOS 材料が十分ある」という仮定が**現行構成では成り立たない**。

12 月に本当にやるべきは、runtime 経路の是正ではなく **manifest 経路の解錠**である。

## 1. 実測 — OOS 材料は足りない

| | |
|---|---:|
| 本番 ACTIVE | `mix-129-nj6` |
| その `train_through` | **2026-08-23** |
| 予測を持つレース（全期間） | **9,460** |
| うち `race_date > train_through`（＝真に OOS） | **252** |
| `fit_stage_discount` の要求（**ステージごと**） | `DEFAULT_MIN_RACES = 300` |

**9,460 のうち 9,208（97.3%）が現行 active にとって in-sample。**
残る 252 は要求 300 に届かない。

→ `fit_product_stage_discount_strict` を本番に結線すると、
`sd.fallback` が立って **`InsufficientQualifiedMaterial` を送出する**。
**それは実装が正しく動いた結果である。**

## 2. なぜこれは「フィルタで直る」問題ではないのか

active モデルは常に**直近まで**学習される。だから保存済み予測のほぼ全部が
そのモデルにとって in-sample になる。**これは構造であって、絞り込みの巧拙ではない。**
どんな述語を書いても、存在しない材料は作れない。

`legacy-runtime` の λ 推定は「レース日が対象日より前」しか見ていないが、
**その条件を厳しくすればするほど材料はゼロに近づく。**
現状が動いているのは、**in-sample 材料を使っているからこそ**である。

## 3. では何をすべきか — manifest 経路

074/076 が作った **manifest 経路**は、λ を **OOF 予測**から fit する。
OOF は fold ごとに「その年より前だけで学習したモデル」が予測するので、
**材料が定義上 out-of-sample** になる。ここでは 1 節の壁が存在しない。

manifest-required モードは既に fail-closed で、runtime fit に落ちない
（`serving/pipeline.py:197-203`）。ops 側の欠落も既にエラー（`runner.py:328-339`）。

**唯一のブロッカーは世代の直書き**:

```
probability/src/horseracing_probability/calib_manifest.py:36   BASE_MODEL_VERSION = "lgbm-063"
                                          :267, :358           書き込み時にこの値を刻む
                                          :420-423             読み込み時にこの値と照合して拒否
```

現行 active は `mix-129-nj6` なので、**どんな manifest を作っても世代照合で弾かれる。**
（この所見は 108 が既に記録しており、今回コードで再確認した。）

現在の稼働設定は `<prefix>_CALIB_MODE` 未設定 ＝ **`legacy-runtime`**（`runner.py:351`）。

## 4. 今回作った来歴契約の位置づけ

結線しないが、捨てない。3 つの用途がある。

1. **不可能性を可視化・監査可能にした。** 「OOS で λ を fit している」と思い込むことは
   もうできない。`fit_product_stage_discount_strict` を呼べば、
   材料が無いことが**数字つきの例外**で返る。
2. **manifest 経路で使える。** OOF 束は本当に OOS なので、
   `MaterialProvenance` の述語（世代・入力条件・`producer_train_through`）が
   そのまま意味を持つ。
3. **研究経路の材料選択**に使える。

既定は `provenance=None` のままで、**本番の挙動は 1 バイトも変えていない**。
133 の凍結 λ も無傷。

## 5. 12 月の係数更新に向けた作業順序

1. **`BASE_MODEL_VERSION` の世代固定を解く**（書き込み・検証の両方）。これが無いと manifest は死んだまま。
2. mixture 世代の OOF 束から manifest を生成する（074 の生成 CLI が必要／078 が該当）。
3. `MaterialProvenance` を manifest 生成側の材料選択に当てる。
4. **そのうえで** `CALIB_MODE=manifest-required` に切り替える。

**1〜4 はいずれも 133 の窓（〜12/28）の外で行うか、少なくとも
133 の凍結 λ を動かさないことを確認してから行う。**

## 6. 実施した — 世代固定の解除（2026-09-22）

5 節の手順 1 を実施した。

### 何が問題だったか

`calib_manifest.BASE_MODEL_VERSION = "lgbm-063"` が**書き込みにも検証にも**使われていた:

- `build_manifest` / `build_manifest_v3` が全 manifest にこの定数を刻む
- `verify_manifest` がこの定数との**等値**を要求する

→ **どの世代の manifest も、自分自身の verifier に拒否される。** 現行 active は
`mix-129-nj6` なので、manifest 経路は**構造的に一度も使えなかった**。

### 直し方 — 定数を消すのではなく、束縛を正しい場所に残す

**本来の束縛は既に正しい場所にあった**: `calib_activation.load_calibration` が
`manifest["base_model_version"] != active_model_version` を検査している
（`calib_activation.py:140-145`）。「この manifest はこれから使うモデルのものか」という、
意味のある問いはここで答えられている。`verify_manifest` の定数等値はその**冗長な複製**で、
しかも常に失敗する側だった。

| 箇所 | 変更 |
|---|---|
| `verify_manifest` | 定数等値 → **形の検査**（非空文字列）。`expected_base_model_version=` で呼び出し側が固定もできる |
| `build_manifest` / `build_manifest_v3` | `base_model_version` を**必須キーワード**に（既定値を残すと「別世代の manifest に lgbm-063 と刻む」嘘が続く） |
| `oof_manifest.build_oof_manifest` | 世代を**attestation から読む**（`attestation["base_model_version"]`）。呼び出し側に主張させない |
| `evaluation` 側の世代との内部整合検査 | **そのまま残した**（正しい検査） |

`BASE_MODEL_VERSION` 定数自体は歴史的既定として残し、何であって何でないかを docstring に書いた。

### 弱めていないことの担保

新規テスト **14 件**（probability 8・training 6）:

- `mix-129-nj6` 世代の manifest が verify を通る（**従来は不可能だった経路**）
- lgbm-063 も従来どおり通る
- 呼び出し側が世代を固定でき、違えば `generation mismatch`
- **世代が空・空白・None・数値なら拒否**（定数を外したせいで欄が無意味にならないこと）
- attestation に世代が無ければ**推測せず拒否**し、しかも
  **attestation 不一致という、より具体的な診断を先に出す**
- **`calib_activation` の本来の束縛は生きている** — 別世代の manifest で別モデルを校正できない

**変異で確認**: `calib_activation` の `base != active_model_version` を潰すと
最後の 1 件だけが落ちる。

全スイート緑（training 642 / serving 291 / live 152 / betting 190 / probability 349 / eval 577）。

### まだ本番は切り替えていない

これは**5 節の手順 1 だけ**である。手順 2（mixture 世代の OOF 束から manifest を生成）と
手順 4（`CALIB_MODE=manifest-required`）は未実施で、**現在の稼働は `legacy-runtime` のまま**。

## 7. 言えないこと

- 既報の実害 **+0.000014 は再現していない**。今回示したのは
  「経路が開いている」ことと「厳密化すると材料が足りない」ことであって、
  現在どれだけ汚染されているかの測定ではない。
- manifest 経路に切り替えたときの精度差も**未測定**。
  「正しくなる」とは言えるが「良くなる」とは言えない。
