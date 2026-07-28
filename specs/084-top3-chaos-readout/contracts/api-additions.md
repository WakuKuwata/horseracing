# Contract: API 純追加 (rev2)

**対象**: `GET /api/v1/races/{race_id}/predictions` の応答に **1 フィールド純追加**。
新エンドポイントは作らない。全 path GET のまま。

```text
PredictionsResponse:
  ...既存フィールドは一切変更しない...
  race_dispersion: RaceDispersion | null      # 066 — 無改修
  race_divergence: RaceDivergence | null      # 066 — 無改修
  race_chaos:      RaceChaos | None = None    # ★ 084 純追加
```

形は [data-model.md](../data-model.md) §6 を正とする。

---

## 契約

| ID | 内容 |
|---|---|
| API-1 | **純追加のみ**。既存フィールドの型・意味・値を変更しない。`race_dispersion` の出力はバイト不変(SC-004) |
| API-2 | OpenAPI drift-check 緑。`front/openapi.json` と `admin/openapi.json` を再生成し**両者 byte 一致**を維持 |
| API-3 | 生成型 `schema.d.ts` を front / admin 両方でコミット |
| API-4 | 全 path GET・書き込みなし。既存の AST / import-graph 境界テストを維持。**`live` を import しない**(DB テーブルを直接読む) |
| API-5 | snapshot 不在・artifact 不在・invariant 違反はいずれも `race_chaos.status="unavailable"` で表現し **HTTP 200 のまま**。予測本体を 500 にしない |
| API-5a | **永続 readout と再計算の一致**(FR-020a): `chaos_readouts.artifact_digest` が現行承認 digest と一致すれば**永続値を返す**。不一致なら再計算し乖離を検出可能にする。記録値と表示値が黙って乖離してはならない |
| **API-6** | **`race_chaos` は予測 run に依存しない。run 選択より前に構築し、typed-empty 応答にも含める**。市場のみの計器がモデル run の有無で消えてはならない。`race_chaos` 自体が `null` になるのは「そもそも組み立てを試みなかった」場合のみで、試みた結果は必ず `available`/`unavailable` のいずれかを返す |
| **API-7** | 「全 number は nullable」に**しない**。`status="available"` 形状では数値フィールドは**必須・非 null**。応答生成は**明示 keyword マップ**で行い `Model(**dict)` を禁止する(075 の splat-null 事故の再発防止)。pydantic は `extra="forbid"` |
| API-8 | `is_pseudo=true` / `is_market_derived=true` を常に返し front の単一 `PseudoValue` 経路を通す。pseudo をバッジ無しで描画しない不変テストを維持 |
| **API-9** | **CI で `app.openapi()` を両 committed snapshot と比較する**。現行 `check-openapi.sh` は committed 同士の比較のみで、実行中のスキーマがずれても検知できない |
| API-10 | 読み取り時に engine を **2 回**呼ぶ(生 λ=1 と補正)。`bet_type` 指定時は既存の p ベース 009 呼び出しが**追加で**走る(入力分布が違うので再利用できない)。**p95 をフルパスで計測**し、**`(content_digest, artifact_digest)`** をキーにキャッシュする(数値は field 内容と artifact だけで決まるので `snapshot_id` はキーに含めない) |

---

## front 表示契約

| ID | 内容 |
|---|---|
| FE-1 | **主値は確率 `P(S≥20)`**。バンドはその粗いラベル。E[S] は副次 |
| FE-2 | 述語と**論理的に等価な**ラベルを表示する: 「人気順合計が20以上」「**1〜3番人気が勝ち、2着か3着に二桁人気**」「二桁人気が勝つ」。「2・3着」の省略は and と誤読される(and 版は 0.44% で 24 倍違う)ので**禁止**。**「内訳」と呼ばない** |
| FE-3 | `total_collapse` には **λ 補正が効かない生の市場質量**である旨を併記する(`lambda_sensitive=false`) |
| FE-4 | `calibration_status="provisional"` のとき **「参考値(最終オッズでは検証済み／発走前オッズで検証中)」**と表示。「暫定」は壊れていると読まれるので使わない |
| FE-5 | 生の質量は**方法詳細**に置く。主枠に同じ事象の百分率を 2 つ並べない |
| FE-6 | エントロピー(066)は**折り畳み詳細**に格下げし見出しを**「市場の支持集中度」**に。066 の結果主張キャプション(`front/src/lib/dispersionLabels.ts` の `BAND_CAPTION`)を撤去 |
| FE-6a | 084 のバンドは **066 と別語彙**(`t3_calm`..`t3_wild`「上位3着の荒れ度: 揃う〜崩れやすい」)。両パネルにスケール名を明示し、同名 5 段スケールが 2 つ並ばないようにする |
| FE-14 | **禁止語の不在をテストで固定**: 「暫定」(FR-020)および利益語・edge・妙味(FR-023)が描画テキストに現れないこと |
| FE-7 | 少頭数では「人気合計の可能範囲は 6〜21」「二桁人気の馬はいません」と**平易な日本語**で説明する(`[6, 21]` だけでは伝わらない)。`within_field_size_percentile` を「同頭数のレースの中では低め/高め」として副表示 |
| FE-8 | 構造的ゼロは確率でなく**「該当馬なし」**と描画する(内部値は 0.0) |
| FE-9 | 数値直下に「市場オッズを上位3着構成へ変換した参考値です。市場にない独自情報や収益上の優位性を示しません」を常時表示。**「市場の意見だから EV 中立」とは書かない** |
| FE-10 | 損益色・レースのソート・CTA を実装しない。**過度な小数精度を出さない**(整数%まで) |
| FE-11 | loading / typed-empty / typed-error / unavailable を**別々の UI 状態**として描き分ける |
| FE-12 | **`hasPreds` と独立に描画する**(API-6 と対) |
| FE-13 | 鮮度を常時表示: 「発走○分前・HH:MM 取得」。`capture_strength` が `confirmatory` でない場合はその旨を明示する |

---

## unavailable_reason の値域(事前登録・変更禁止)

| 値 | 意味 |
|---|---|
| `no_snapshot` | 凍結行が無い |
| `partial_market_odds` | field の一部にオッズが無い(部分再正規化はしない) |
| `invalid_popularity_ranks` | popularity が 1..n の順列でない |
| `field_too_small` | n < 3 |
| `artifact_unavailable` | artifact 不在 / digest 不一致 / 承認 manifest 不一致 |
| `out_of_validity_window` | `target_date <= fit_through` または `target_date < valid_from` |
| `invariant_violation` | Σ(順序三つ組) が許容誤差外(fail-closed) |
