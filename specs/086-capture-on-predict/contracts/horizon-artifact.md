# Contract: 主 horizon の事前登録と新 artifact version

**Feature**: 086 | **Spec**: FR-005, FR-006, FR-007, FR-008, FR-009..012, SC-006, SC-010
| **Research**: D1, D8, D9, D10

---

## 1. artifact のスキーマ追加(必須項目)

```jsonc
"preregistration": {
  "primary_horizon": {
    "minimum_seconds_to_post": 600,
    "maximum_seconds_to_post": 86400,
    "basis": "schedule_jitter_floor_and_next_day_market_ceiling",
    "measured_coverage_of_pre_race_predict_clicks": 0.956
  },
  // … v1 の他の項目はすべて同一
}
```

**必須**である(FR-005)。`maximum_seconds_to_post` に `null` は**許さない**
(「上限なし」は暗黙の全時刻受理と等価であり FR-006 が廃止した挙動そのもの)。

---

## 2. fail-closed の実装位置(D9)

`load_chaos_artifact` の検証ステップで拒否する。**単一箇所**にすることで
`api`(表示)/ `live`(捕捉)/ `training`(報告)の**全経路が一度に fail-closed** になる。

| 条件 | 挙動 |
|---|---|
| `preregistration.primary_horizon` が無い | 型付きエラーで拒否 |
| Mapping でない | 型付きエラーで拒否 |
| `minimum_seconds_to_post` が整数でない / 負 | 型付きエラーで拒否 |
| `maximum_seconds_to_post` が整数でない / `null` | 型付きエラーで拒否 |
| `maximum <= minimum` | 型付きエラーで拒否(**幅ゼロの窓は確認適格が構造的に到達不能**) |
| `primary_horizon` の中に短縮別名 `min_seconds_to_post` / `max_seconds_to_post` がある | 型付きエラーで拒否(FR-004a の命名分離。`chaos_bands.py:1817-1824` は現状これを許している) |

### ブートストラップ経路(U1 — 循環の回避)

窓を必須にすると、**窓を持たない v1 を読む手段が無くなる**。
しかし新 version の発行(§3)と新旧のバイト一致検証(§6)は v1 を**読む必要がある**。

そこで**汎用の生読みは用意しない**。代わりに**単目的の 1 操作**を置く:

```python
upgrade_legacy_artifact_horizon(
    path, *, expected_digest, minimum_seconds_to_post, maximum_seconds_to_post,
) -> tuple[dict, str]   # 新しい payload と新しい digest
```

- **既知の承認済み digest しか受け付けない**(`expected_digest` と一致しなければ拒否)

> **「承認済み」と「現行」は別概念である**(実装者が混同しないこと):
> **承認済み** = 承認 manifest に掲載されている(`status` は問わない)。
> **現行** = `status="active"` の唯一の項目(§3)。
> T017 で v1 を `superseded` に落とした**後**にバイト一致テスト(§6)が走るので、
> 昇格関数が「承認済み = active」と読むと **v1 を拒否して INV-7 が実行不能**になる。
> 昇格関数が見るのは**掲載されているか**だけである。
- **窓以外の検証はすべて通す**(スキーマ・λ・境界・数値安定性・承認 manifest 掲載)
- 窓の検証だけを飛ばし、窓を足した新 payload を返す

**汎用の `read_chaos_artifact_raw` を却下した理由**: digest が自己整合しているだけの
未承認・不正な artifact を `add-horizon` の入力にできてしまう。
「呼び出し元は 2 箇所だけ」という静的テストは**規約であって安全境界ではない**
(規約はいずれ破られるが、引数の検証は破れない)。

新旧バイト一致テスト(INV-7)では、**旧側は不変の fixture を独立に読む** —
昇格関数の出力同士を比べると、関数が λ / 五分位境界 / 確率を
**両側に同じように**壊しても緑になってしまう。
あわせて **payload の差分が `preregistration.primary_horizon` の追加と
`artifact_digest` の再計算のちょうど 2 点**であることも assert する。
**digest を据え置いてはならない** — payload は自分の digest を内蔵し、
`chaos_artifact.py:208-212` が「`artifact_digest` を除いた全体」のハッシュと
照合するので、窓を足した payload は digest も更新しないとローダに拒否される。
digest の再計算は `upgrade_legacy_artifact_horizon` の責務とする。
**`version`(`chaosbands-v1`)は据え置く** — `chaos_readouts.artifact_version` に
永続化される値なので、bump は SC-010 の 10 例外に無い出力変更になる。

**廃止するもの**: `training/chaos_bands.py` の `_primary_horizon` にある
`{minimum: 0, maximum: None, artifact_field_present: False}` へのフォールバック
(chaos_bands.py:1810-1816)。分岐そのものを削除し、共有純関数
`probability/chaos_eligibility.py::primary_horizon(artifact)` に置き換える。

**副作用**: `primary_horizon` を持たない既存の artifact fixture がすべて弾かれる。
fixture 更新をタスクに明示的に積む(黙って通る fixture を残さない)。

---

## 3. 新 version の発行(create-only)

| 項目 | 扱い |
|---|---|
| v1 `e782c255adde…` | **書き換えない**。ファイルもそのまま残す |
| 新 digest | `preregistration.primary_horizon` を足しただけ。他は**完全同一** |
| λ2 / λ3 / 五分位境界 / `fit_through` / `valid_from` / `fit_input_hash` / `race_set_hash` | **不変** |
| `config/chaos_bands_approved.json` | 新 digest を `status="active"` で追加し、v1 を `status="superseded"` に変える(行は残す) |
| 現行の解決 | **`status="active"` の唯一の項目**を読む。`active` が 0 件または 2 件以上なら型付きエラー |

### 084 の現行解決は壊れている(実測)

`load_current_chaos_artifact` は `approved[-1]` を現行としている
(`live/src/horseracing_live/chaos_capture.py:541`)。
しかし `config/chaos_bands_approved.json` は
**`status="active"` の e782c2… を先頭に、`status="superseded"` の f190e65c… を末尾に**
置いている。つまり現行のコードは**superseded の artifact を現行として読んでいる**。

`status` フィールドは manifest に存在するのに**一度も参照されていない**。
086 で `status="active"` による解決に是正する(末尾追記に頼らない)。
これは 086 とは独立に存在する 084 の欠陥である。

**荒れ度の値・バンド・λ は一切変わらない**(SC-010)。
検証: 旧 artifact と新 artifact で同じ凍結フィールドから `chaos_readout` を計算し、
バンドと全確率がバイト一致することを assert する(INV-7)。

**遡及の心配がない理由**: 報告が単一の digest に絞られている(§4)ので、
旧 digest を参照する観測は新しい報告に入らない。
計画時点で `chaos_snapshots` が 0 行だったことは**利便であって設計の前提ではない** —
migration は行数に依存させず、遡及分岐は実際に走らせて検証する(tasks の遡及テスト)。

---

## 4. 適格性の非遡及(FR-007)

**非遡及は報告の構造から従う。観測ごとに artifact を解決し直す仕組みは要らない。**

前向き報告は `load_prospective_rows(..., artifact_digest=...)` で
**単一の凍結設定に絞って**読む(`training/chaos_bands.py:1714` の
`WHERE ChaosReadout.artifact_digest == artifact_digest`)。
したがって報告に入る観測の artifact は**常に報告対象の artifact そのもの**である。

```text
報告(digest = X)に入るのは、artifact_digest = X の readout を持つ観測だけ
```

将来 v3 で窓を変えても、v3 の報告に v2 の観測は**入らない**。
遡って昇格も降格もしないことが構造的に保証されている。

**窓を持たない旧設定を指す観測(086 以前の遡及行)も同様**に、
新しい設定の報告には現れない。特別な除外理由も、観測ごとの artifact 解決も要らない。

**現行 artifact の読み込み**(表示・捕捉・新規の報告)は窓が無ければ型付きエラーで止める
(fail-closed)。これは別の話である。

---

## 5. 前向き報告への追加(FR-011・FR-012)

`prospective-report` の出力に純追加する(既存キーは不変 = SC-010)。

**`primary_horizon` の出力キーを凍結する**(F3)。084 の `_primary_horizon` は
`mode` / `minimum_seconds_to_post` / `maximum_seconds_to_post` / `artifact_field_present` の
4 キーを返し、報告にそのまま出していた(`chaos_bands.py:2266`)。
086 では**フォールバックが消えるので `artifact_field_present` は常に true = 情報量ゼロ**になる。

| キー | 086 での扱い |
|---|---|
| `mode` | **残す**。値は常に `artifact_seconds_to_post_window` |
| `minimum_seconds_to_post` | 残す |
| `maximum_seconds_to_post` | 残す。`null` は取り得ない |
| `artifact_field_present` | **削除**(常に true で意味が無くなるため) |

これは SC-010「084 の既存出力が不変」に対する**意図した例外**であり、
「主 horizon の必須化以外は不変」という SC-010 の但し書きの範囲内である。

`primary_horizon` は**トップレベルではなく `analysis_unit` の下**に出る
(`training/chaos_bands.py:2266` は `"analysis_unit"` ブロック内で emit している)。
既存の位置を動かすと SC-010「既存キー不変」に反するので、**位置も含めて検証する**。

```jsonc
{
  "analysis_unit": {
    "primary_horizon": {
      "mode": "artifact_seconds_to_post_window",
      "minimum_seconds_to_post": 600,
      "maximum_seconds_to_post": 86400
    }
  },

  "by_capture_trigger": [
    {"trigger":"daily_operational","n":…,"confirmation_eligible":…,"selection_biased":false},
    {"trigger":"predict_manual",   "n":…,"confirmation_eligible":…,"selection_biased":true},
    {"trigger":"predict_auto",     "n":…,"confirmation_eligible":…,"selection_biased":false},
    {"trigger":"explicit_command", "n":…,"confirmation_eligible":…,"selection_biased":true},
    {"trigger":"legacy_unknown",   "n":…,"confirmation_eligible":0, "selection_biased":null}
  ],
  "user_selected_share": 0.0,   // (predict_manual + explicit_command) / (全体 - legacy_unknown)
                                //  分母 0(全行が legacy_unknown)なら null
  "exclusions": { "field_changed_after_capture": 0 },   // 既存カウンタへの純追加(FR-002b)

  "prospective_selection_bias": {
    "policy_primary_source": "daily_operational",   // 方針(定数)
    "observed_primary_source"  // 同数のときは契機名の辞書順(決定的): "predict_manual",    // 実測の最多契機
    "primary_source_claim_violated": true,          // 方針と実態が逆転しているか
    "user_selected_role": "supplementary",
    "removable": false,
    "note": "予測実行由来の観測は利用者が選んだレースに偏る。日次の中立な捕捉が
             主たる観測源であり、予測実行由来のみで観測群が構成された場合は
             選択バイアスを除去できない。"
  }
}
```

**`user_selected_share` は常に出力する**(0 のときも省略しない)。
偏りが「無いこと」も測定結果として記録に残す。

**`predict_manual` と `predict_auto` を合算してはならない**。
データ更新の後に自動で積まれる予測は利用者が選んだものではないので中立であり、
合算すると選択バイアスを過大に見積もる。

既存の `by_capture_horizon` は**バケットを広い窓に合わせて拡張する**。
現行は `0-9m / 10-29m / 30-59m / 60m+`(`training/chaos_bands.py:67-70`)だが、
登録した窓 [600, 86400] では **`0-9m` は構造的に常に空**で、
**実測の中央値 24,124 秒と 71% のクリックが `60m+` の 1 バケットに潰れる**。
これでは「窓内の成熟度分布を開示する」という残リスクの緩和策が機能しない。
→ 次の 6 バケットに分割する(境界は秒・両端を含む)。
**`_capture_horizon` は窓内フィルタ(`chaos_bands.py:1920`)を通った行にしか
呼ばれない**という前提に依存するので、この前提が崩れたら下限も閉じる必要がある:

**表は窓下限が 600 の場合の実体化**である。**最下位の下限は artifact の
`minimum_seconds_to_post` から導出する**(`add-horizon` は任意の下限を受け付ける)。

**バケット構成は窓 `[minimum, maximum]` から機械的に導出する**
(散文の規約にしない — 本契約 §2 が「静的テストは規約であって安全境界ではない」と
述べているのと同じ理由で、上下どちらの端も式で閉じる)。

**分類関数**も同じモジュールに置く:

```python
def capture_horizon_bucket(
    seconds_to_post: int, *, minimum_seconds_to_post: int, maximum_seconds_to_post: int
) -> str:
    """観測 1 件をラベルに落とす。該当が無ければ ValueError(暗黙に丸めない)。"""
```

`training/chaos_bands.py::_capture_horizon` はこれを import して使う
(該当バケットが無いと `ValueError` を送出する現行仕様に合わせる)。

**置き場は `probability/chaos_eligibility.py`**(`api` / `live` / `training` の三者が共有する層)。
`training` は import して使い、**再実装しない**。

```python
# 内側の境界。窓に完全に含まれるものだけを採る。
_EDGES = [1800, 3600, 10800, 21600, 43200]

def capture_horizon_buckets(minimum: int, maximum: int) -> list[tuple[str, int, int | None]]:
    edges = [e for e in _EDGES if minimum < e < maximum]
    bounds = [minimum, *edges]
    out = []
    for i, lo in enumerate(bounds):
        hi = bounds[i + 1] - 1 if i + 1 < len(bounds) else None
        out.append((_label(lo, hi), lo, hi))
    return out
```

**ラベルは両端から作る**(片端だけを式にすると破綻する):

- `hi is None` かつ `lo >= 3600` → `f"{lo // 3600}h+"`(最上位。例 43200 → `12h+`)
- `hi is None` かつ `lo < 3600` → `f"{lo // 60}m+"`(**時間単位にすると `0h+` になる** —
  `maximum <= 1800` の窓では内側の境界が 1 つも入らず、この枝だけが残る。
  ローダは窓の幅に上限を課さないので、この場合を閉じておく)
- `hi + 1 <= 3600` → `f"{lo // 60}-{(hi + 1) // 60}m"`(分単位。例 600,1799 → `10-30m`。
  **`<` にすると `lo=1800, hi=3599` が時間分岐に落ちて `0-1h` になる** — 境界は閉じる)
- それ以外 → `f"{lo // 3600}-{(hi + 1) // 3600}h"`(時間単位。例 3600,10799 → `1-3h`)

**窓 `[600, 86400]` での実体化**:

| バケット | 下限 | 上限 |
|---|---|---|
| `10-30m` | 600 | 1799 |
| `30-60m` | 1800 | 3599 |
| `1-3h` | 3600 | 10799 |
| `3-6h` | 10800 | 21599 |
| `6-12h` | 21600 | 43199 |
| `12h+` | 43200 | **開いたまま(`None`)** |

**この導出は上下対称に窓へ追随する**:

- 窓下限を 300 にすると最下位が `5-30m` になる(下限だけを式にしていた旧規則は
  下限 1800 以上で `31-29m` という破綻したラベルを作っていた)。
- 窓上限を 20000 にすると `21600` 以降の境界が**そもそも生成されない**ので、
  構造的に空のバケットが生じない(SC-010 の例外 5 が直したのと同じ欠陥の再発を、
  散文でなく構造で防ぐ)。
- したがって**ローダは窓の下限に上限値を課さない** — `minimum < maximum` と
  型・符号だけを見る。表示の都合を安全境界に持ち込まない
  (084 が当初推奨した T−30 分窓 `[1800, …]` も、この規則なら正当に発行できる)。

**最上位バケットの上限は閉じない**。`86400` ちょうどの観測は窓内(FR-007・両端を含む)なので、
`86399` で閉じると `_capture_horizon` が `ValueError` を送出する
(`training/chaos_bands.py:1797` の `_capture_horizon` は該当バケットが無いと `:1803` で例外にする)。
`0-9m` は登録した窓では構造的に到達しない(生成規則上そもそも作られない)。
層別は**記述であって主判定ではない**。
多重比較方針は v1 と同一(単一の事前指定主要評価項目 `s_ge_20`・補正なし)。

---

## 6. 検証

| 検証 | 期待 |
|---|---|
| 窓なし artifact を読む | **3 経路とも**型付きエラー(api / live / training)= SC-006 |
| 既知の承認済み digest で `upgrade_legacy_artifact_horizon` | 成功する |
| **未承認・不正な digest** を渡す | **拒否される**(汎用の生読みが存在しないことの担保) |
| 報告の `primary_horizon` キー | `mode` / `minimum_seconds_to_post` / `maximum_seconds_to_post` の 3 つ・`artifact_field_present` は無い |
| `status="active"` が 2 件 / 0 件の manifest | 型付きエラー |
| 現行 artifact の解決 | `status="active"` の項目を返す(末尾の項目ではない) |
| 窓なし artifact を指す遡及行 | **新しい digest の報告に現れない**(単一 digest スコープ・§4) |
| `maximum: null` | 拒否される |
| `maximum <= minimum` | 拒否される |
| 境界値 600 / 86400 | **両端とも窓内**と判定される |
| 599 / 86401 | 窓外 |
| 新旧 artifact で同一フィールド | readout がバイト一致(INV-7) |
| v1 のファイル | 変更されていない(digest が一致) |
| 契機別内訳 | **5 契機**すべてが 0 件でも行として出る・`predict_manual` と `predict_auto` が合算されない |
| `user_selected_share` | 0 のときも出力される |
