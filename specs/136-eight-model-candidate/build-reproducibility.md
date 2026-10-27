# B — 129 build が現行 main から再現できない問題（2026-09-22 決着）

## 結論

**bdc789e はメンバーの学習結果を一切変えていない。実測で証明されている。**
したがって壊れているのは学習そのものではなく、**凍結が縛る範囲が広すぎること**である。
12 月の係数更新に必要なのは「研究チェーン 123/124/125 の再検証」ではなく
「**bundle を再構築できること**」なので、**129 builder の provenance を
136 と同じ形（fit に効く範囲＋入力＋選択証拠の内容ハッシュ）に置き換える**のが正しい直し方である。

## 1. 症状（再掲）

`scripts/candidate_mixture_build.py prepare` が `upstream_state()` で
`ValueError: 125 frozen source/config/runtime/selection changed` で停止する。

原因は `candidate_mixture_build.source_hash()` → `joint_residual_stack.source_hash()` → … →
`scripts/ability_observation.py::source_hash()` が
**`db/ features/ training/ eval/ probability/` の `src` 配下の全 `.py`** を digest していること。

各コミットの blob からハッシュを組み直して照合した結果、**`bdc789e`** が分かれ目である。

## 2. bdc789e が何を変えたか（digest 対象の 8 ファイル）

| ファイル | 変更内容 | メンバー学習への到達性 |
|---|---|---|
| `training/calib_split.py` | `_started_all_outcomes` の SQL に「STARTED 馬に結合」を追加＋`winners ⊆ started` の assert | **到達しない**。builder は `frozen_outcomes()` で `_started_all_outcomes` 自体を `patch` している（`candidate_mixture_build.py:287-296`）。assert は値を変えず、発火もしなかった |
| `eval/dataset.py` | `load_eval_races` の被覆カウントに同じ結合を追加 | **到達しない**。入力は凍結 111 snapshot の pickle で、`load_eval_races` は呼ばれない。`population_masks` は docstring のみ変更 |
| `training/artifacts.py` | 変更は全て `save_model_version` 内（ACTIVE 上書き禁止・テーブルロック・promotion_contract） | **到達しない**。builder は `_write_model` / `build_preprocessor` / `feature_hash` / `categorical_vocab_from_booster` を直接呼び、`db_registration: False` で `save_model_version` を呼ばない |
| `adoption.py` / `promote.py` / `promotion_evidence.py` / `cli.py` / `hpo.py` | 昇格・登録・HPO 系 | **到達しない**。fit 経路が import しない（129 は HPO 未使用） |

## 3. 実測による証明 — kill-test の陽性対照

kill-test は `colsample07_joint` を **positive_control** として登録していた
（「126 の cached member と同じ構成なので、fit すれば再現するはず」）。時系列が決定的である:

| | 日時 |
|---|---|
| 126 の参照予測 fit | **bdc789e より前** |
| 130 の基準 6 メンバー fit | 2026-09-09（**bdc789e より前**） |
| **bdc789e** | **2026-09-13 13:39** |
| kill-test の対照 fit | 2026-09-16 01:30（**bdc789e より後**） |

結果:

| 年 | レース | 馬 | win | top2 | top3 | 並び順不一致 |
|---|---:|---:|---:|---:|---:|---:|
| 2024 | 3,454 | 46,921 | **0.0** | **0.0** | **0.0** | 0 |
| 2026 | 2,298 | 31,765 | **0.0** | **0.0** | **0.0** | 0 |
| 計 | **5,752** | **78,686** | — | — | — | — |

**bdc789e より後のコードで同じ recipe を fit し直すと、bdc789e より前の予測を
78,686 頭ぶん厳密に 0.0 で再現する。** 2 節の静的な到達性解析と一致する。

（対照は `full` regime のみの比較で、kill-test 自身も "Informational: never asserted" と
記録している。preweight は入力側の変換で booster と校正器は共有なので、この対照で足りると判断する。）

### 副次的に確定したこと

- **今日作った 6 本のメンバーは、42 個の kill-test member-year と同じ側のコードで学習されている**
  （どちらも bdc789e より後）。130 の基準 6 メンバーだけが前だが、対照が 0.0 なのでこれも比較可能。
- C1/C2 に「pre-bdc789e の基準 vs post-bdc789e の候補」という交絡は**無い**。

## 4. 3 つの選択肢の評価

| 案 | 評価 |
|---|---|
| **(a) 現行 main で研究チェーンを再凍結** | **不可**。この対照が示したのは**メンバー fit 経路**の不変性であって、123/124/125 の集計・選択経路の不変性ではない。再凍結すると「その証拠を作っていないコード」に証拠を結び直すことになる |
| **(b) `bdc789e^` の worktree で作る** | 可能だが不要。fit が不変と実証された以上、古い木を持ち出す理由が無い。運用も面倒 |
| **(c) digest 対象を fit に効く範囲へ絞る** | **方向は正しいが、既存の fail-closed をその場で弱めるのは筋が悪い** |

## 5. 推奨 — (c) の正しい形：129 builder に 136 の provenance を移植する

**研究の凍結（111/123/124/125）は歴史的記録として一切触らない。**
12 月に必要なのは bundle の再構築だけなので、`candidate_mixture_build` の
`source_hash` / `upstream_state` を、`scripts/candidate_bundle_136.py` と同じ形に置き換える:

- `source_hash` = **fit を左右しうるソース**（5 パッケージの `src` + serving loader + builder 自身）。
  凍結値との一致は要求せず、**prepare で凍結して各 fit の前後で再検査**する
  （= その build の間にコードが動いていないことの保証）。
- `upstream_state` = **111 snapshot**・**recipe の出所**・**選択証拠**の内容ハッシュ。
  上流ドライバを live に再実行して verify させない。

**この置き換えは既存の証拠を 1 つも書き換えない。** 変わるのは
「次に build するとき何を要求するか」だけである。

## 6. 実施した（2026-09-22）

**変更は `scripts/candidate_mixture_build.py` の 2 関数だけ。** 研究側のドライバ
（118/123/124/125）には一切触っていない。

### `upstream_state()`

`research.verify()` / `s.verify()` の**live 再検証を読み取りに置き換えた**。
残した検査は全部そのまま:

- 125 の凍結 config hash と runtime
- 118 の `source_files` 全件の digest
- `verify_historical_certificate()`（method/freeze の内容ハッシュと完全性フラグ）
- `final-integrity.json` の status と `input_hashes | documentation_hashes` **全件**
- `independent-review.json` の method/run_freeze/summary/retention 束縛
- 各 contrast の report と evidence の digest が audit の記録と一致すること

**実測: 55 ファイルを内容ハッシュで束縛したまま動作する。**

waiver は `SOURCE_DRIFT_WAIVER` として定数化し、**build freeze に記録される**
（何を免除し、何を今も強制し、根拠がどこで、いつ誰が壊したか）。

### `source_hash()`

`research.source_hash()` の畳み込みをやめ、**fit に効くソース 173 ファイル**
（5 パッケージの `src` + serving loader + builder 自身）の内容ハッシュにした。
凍結値との一致は要求せず、**prepare で凍結して各 fit の前後で再検査**する。

### 実証

現行 main で新しいビルドを prepare → verify まで通した（一時 WORK・学習なし・後で削除）:

```
PREPARE PASS: six final fits registered; no DB write or fitting
VERIFY OK
  jobs registered   : 6
  source_hash frozen: 53ce7d6e786b19f312616ee19e222a58 ...
  waiver recorded   : recursive whole-source-tree hash equality of the 118/123/124/125 research chain
  moved source_hash still refused: Frozen build/source/runtime/input changed
```

**最後の 1 行が重要**: ビルド中にコードが動いたら今も拒否する。緩めたのは
「研究が走った当時の木と一致すること」であって、「このビルドの間に動かないこと」ではない。

全スイート緑（training 642 / serving 291 / live 152 / betting 190 / probability 349 / eval 577）。

### 残る限界

- `source_hash` を「5 パッケージ全部」のままにすると、**無関係な修正コミット 1 本で必ず再び壊れる**。
  これは 2026-09-13 に一度起きたことがそのまま繰り返されるという意味である。
  真に fit に効くファイルだけへ絞るのが理想だが、その線引き自体が判断を含むので、
  **今回は「絞る」ことを推奨せず、「live 再検証をやめて内容ハッシュで縛る」ことだけを推奨する。**
- 129 builder の変更自体は未実施。**これは決定を要する変更なので、承認を得てから行う。**
