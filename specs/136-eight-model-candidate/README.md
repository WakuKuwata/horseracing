# 136 — 8 モデル候補の実体確認・校正材料の来歴・133 の現在地

**作業日** 2026-09-20 / **HEAD** `f528289` / **本番 ACTIVE** `mix-129-nj6`（変更していない）

本番切替・配備・既存モデル/証拠の上書き・133 の登録変更は行っていない。DB は読み取り専用。
追加学習は 0 本。新規の自動実行・定期実行は作っていない。

---

## 1. 結論（先に）

1. **8 モデル候補の数値は実在し、独立に再現できた。** 別実装で C1 = −0.002009322213、
   C2 = −0.000628202422 を再計算し、記録値との最大差は **8.7e-19**（60 項目）。
2. **しかし配布できる候補モデルは存在しない。** kill-test が保存したのは**予測だけ**で、
   booster・preprocessor・校正器は 1 本も保存されていない。しかも保存されているのは
   年次 walk-forward の fold であって、bundle の学習末日 2026-08-23 の単一モデルではない。
   **配布にも新しい前向き比較にも、6 本の追加学習が要る。**
3. **「8 等重み」は本番の構成規則と一致しない。** 本番 bundle は
   「各 branch の全 seed を等重み」（2 branch × 3 seed = 6）で、129 は
   *No fake selected_seed* と明記している。同じ規則で A・B を足すと **12 メンバー**になる。
   8 メンバーは「どの seed を引くか」の期待値であって、出荷できる 1 構成ではない。
4. **校正材料の来歴は今も保証されていない。** 既定の λ fit はレース日しか絞らず、
   世代・入力条件・OOS のいずれも検査しない。材料不足は例外ではなく**黙って identity**になる。
   → 来歴契約と strict fit を実装し、回帰テスト 15 件を追加した（既定の挙動は不変）。
5. **133 は主条件が 24 開催日中 1 日しか埋まっていない。**
   9/19 の主条件は**恒久的に失われた**（capture 未実行 + `from-production` は副条件しか書かない）。
   残り約 29〜32 開催日に対して 22 日が必要で、許容できる取りこぼしは 7〜10 日しかない。

---

## 2. 成果物の所在

| 対象 | 場所 |
|---|---|
| kill-test 一式（保全） | `artifacts/136-eight-model-candidate/preserved-killtest-2cced7b2/`（306 ファイル・215 MB） |
| そのハッシュ台帳 | 同 `../preserved-manifest.txt` |
| 独立再計算スクリプト | [`scripts/eight_model_recheck.py`](../../scripts/eight_model_recheck.py) |
| 独立再計算の結果 | `artifacts/136-eight-model-candidate/independent/` |
| 構成規則の凍結 | [`configuration-rule.json`](configuration-rule.json)（sha256 `0485a9b7…`） |
| 校正来歴の監査（codex） | `artifacts/136-eight-model-candidate/calibration-audit/codex-provenance-audit-20260920.md` |
| 133 の状態監査 | `artifacts/136-eight-model-candidate/133-status/133-status.md` + 新しい interim 2 件 |
| 追加研究の計画 | [`research-plan.md`](research-plan.md) |
| 新しい前向き比較の計画 | [`prospective-plan.md`](prospective-plan.md) |

---

## 3. 優先1：8 モデル候補

### 3.1 成果物の監査 — scratchpad にしか無かった

kill-test の成果物はリポジトリに 1 つも無く、`extra_trees` という語は
`docs/accuracy-doubt-and-proposals-20260914.md` にしか出現しなかった。実体は
セッション用 scratchpad（`/private/tmp/.../2cced7b2-…/scratchpad`）にだけ存在していた。
**この場所は消える。** 最初に専用保存先へ複製し、306 ファイルすべてのハッシュが
元と一致することを確認した（`preserved-manifest.txt`）。

保全した中身:

- `member_prereg_v2.json` / `member_build_addendum.json` / `pair_prereg.json` と、それぞれの凍結ファイル。
  pair の事前登録 sha256 は `9b4d6b24…` で、`stage7-pair-primary.json` が記録している値と一致する。
- `s2/cache/joint125/` に **member-year 128 ファイル**。A（`extra_trees_joint`）と
  B（`leaves63_child100_joint`）はそれぞれ **7 年 × 3 seed = 21 本**そろっている。
  受領書の `cache_sha256` は実ファイルと一致し、`smoke=false`、
  `recipe_meta` は 2 候補とも 125 列・`pl_topk`・OOF isotonic・mask 0.5・TE 10 で、
  年と seed 以外は同一。文書が「recipe は 1 種類」と書いた通りだった。
- 基準側の 6 メンバー年次予測は `artifacts/130-mixture-preweight-walkforward/cache/` にあり、
  `stage7-pair-primary.json` が記録する **42 件の cache_sha256 がすべて現物と一致**した
  （欠落 0・不一致 0）。111 snapshot と 130 freeze のハッシュも一致。

**未確認として残すもの:** 受領書とハッシュは「そのファイルが改竄されていない」ことしか示さない。
当時どのコードで学習されたかは `source_hash` の一致までで、学習の真正性の遡及証明ではない。

### 3.2 独立再計算 — 一致した

[`scripts/eight_model_recheck.py`](../../scripts/eight_model_recheck.py) は
**`member_kill_test.py` / `stage5_confirm.py` / `stage7_pair.py` を import しない。**
混合・採点・seed 平均・開催日 bootstrap・品質ガードをこのファイル内に書き直してある。
再利用したのは (a) ハッシュ検証つきの凍結入力層、(b) **本番**の残差補正
`horseracing_serving.mixture_correction`、(c) 正準の started-all ラベルと ECE 定義、の 3 つだけ。

母集団は記録と同じ 23,030 レース / 適格 22,990 / 715 開催日、基準 6 混合の winner NLL は
2.0404787426319655 で一致。

| 比較 | 独立再計算 | 記録値 | 差 |
|---|---:|---:|---:|
| **C1** 8 等重み − 現行 6 | **−0.002009322213** | −0.002009322213 | 0.0 |
| **C2** 8 等重み − A 単独(1/7) | **−0.000628202422** | −0.000628202422 | 0.0 |
| A 単独 − 現行 6 | −0.001381119791 | −0.001381119791 | 0.0 |
| B 単独 − 現行 6 | −0.000925618219 | −0.000925618219 | 0.0 |

98.75% CI、seed sd、年別 7 点、品質ガード 3 種も全て一致。
**比較した 60 項目の最大絶対差 8.67e-19**（倍精度の丸め差）。
R3 自己整合（同一候補を 2 枠 1/8 ずつ = 1 枠 2/8）は **1.11e-16**。

C2 の事前登録 5 条件は私の実装でも全て成立する
（平均 ≤ −0.0004 / CI 上端 < 0 / ガード 3 種 / 最悪年 +0.000169 < +0.0004 / 負の年 6 of 7）。

**数値は合っている。合っていないのは「これが配布できる何かだ」という前提のほうである。**

### 3.3 「3 seed の損失差平均」と「配布する 1 構成」

C1/C2 は **seed ごとの per-race 損失差を平均**している。確率は平均していない。
つまり報告値は *「A と B をどの seed で引いても平均こうなる」* であり、
**特定の 1 個のモデル束の成績ではない。** 実際 seed ごとに開く:

| | seed 42 | seed 43 | seed 44 | 幅 |
|---|---:|---:|---:|---:|
| C1 | −0.002048 | −0.002019 | −0.001961 | 0.000087 |
| **C2** | **−0.000746** | **−0.000573** | **−0.000565** | **0.000181** |

C2 の幅 0.000181 は、足切り −0.0004 までの余裕 0.000228 とほぼ同じ大きさである。

**既存の seed 選択規則は探したが、無い。** 逆に 129 は
「現行 112 の単一 selected-seed テンプレートは 6 メンバーを正直に表現できない」と述べ、
**seed を選ばずに全部入れる**という設計を採った（`specs/129-candidate-mixture-serving/plan.md:66`,
`research.md:9`）。本番 bundle は実際に joint-42/43/44 + anchor-42/43/44 を 1/6 ずつ持つ。

→ 規則が無いのではなく、**本番には規則があって、それは「全 seed を入れる」**である。
その規則を A・B に当てると **12 メンバー**になる。8 メンバーには本番側の対応物が無い。

この判断を成績で選んでいないことを示すため、
**12 メンバーの数値を 1 度も計算する前に**[`configuration-rule.json`](configuration-rule.json)
を書いて凍結した（sha256 `0485a9b7…`）。その中に、C1/C2 の 7 年値は凍結時点で既知だったこと、
規則は候補の成績を一切参照せず本番 bundle の既存構成から読み取ったものであることを明記してある。

**独立レビューが必要な判断として残す。** 12 メンバーを採るか 8 メンバーを採るかは、
維持費（21 member-year 対 63 member-year）と seed 選択を拒む理由の重さの比較であり、
私が数値で決められる問題ではない。

### 3.4 配布候補にそのまま使えるか — 使えない

42 個の pickle を全部読んで確認した。保存されているのは:

```
predictions: {full: {race_id: {horse_id: (win, top2, top3)}}, preweight: {...}}
params_effective, recipe_meta, feature_columns(125), oof_info, train_hash,
valid_ids_sha256, source_hash, runtime, snapshot_sha256, fit_seconds
```

**`model.txt` も `preprocessor.pkl` も `calibrator.pkl` も無い。booster は捨てられている。**
本番 bundle のメンバーは `members/<id>/{model.txt, calibrator.pkl, preprocessor.pkl, metadata.json}`
の 4 点で構成される。

さらに **学習末日が違う**。kill-test の各 member-year は「その評価年の直前まで」で学習した
walk-forward fold である。bundle のメンバーは `train_through = 2026-08-23` の単一モデル。
年次 fold をつなぎ合わせても単一モデルにはならない。

**不足している学習（これが優先1-4 の答え）**

| 項目 | 内容 |
|---|---|
| 必要本数 | **6 本**（A・B × seed 42/43/44、`train_from` 2007-01-01、`train_through` 2026-08-23） |
| なぜ 6 本か | 12 メンバー規則を満たし、かつどの小さい構成の上位集合でもある。**構成の決定を先取りしない** |
| 費用 | 1 本あたり約 950 秒（本番 6 メンバーの実績 934〜1024 秒）。2 worker で壁時計 約 95 分 |
| 追加実装 | `scripts/candidate_mixture_build.py` は MEMBERS を joint/anchor × 3 seed に直書きし、recipe を branch 名から引く。候補の LightGBM 引数（`extra_trees=true` / `num_leaves=63`+`min_child_samples=100`）を同じ 125 列 joint recipe に載せる**別の研究用 builder** が要る。129 の builder は書き換えない |
| 揃える必要があるもの | 特徴列 125・TE 平滑化 10・OOF isotonic 8 ブロック・mask 0.5/seed 20260810・補正 joint_transfer と 130 年次係数・λ は本番規約・`base_eps=1e-6`・入力条件 preweight |

今回は**この 6 本を走らせていない**。理由は、構成を 8 と 12 のどちらにするかの独立レビューが
先に要ること（3.3）と、走らせても 133 の窓が閉じる 12/28 まで本番は動かせないことである。
走らせる判断が出た時点で、上の表がそのまま作業指示になる。

### 3.5 凍結した規則で 12 メンバーを測った（**非拘束・サイジングのみ**）

構成規則を凍結した**後**に、追加学習 0 で 12 メンバー（A・B の全 seed を 1/12 ずつ）を測った。
`scripts/eight_model_recheck.py` の同じ実装で、8 メンバー側の 4 比較は v1 と**完全一致**（差 0.0）。

| 比較 | 点推定 | 98.75% CI | top2 | top3 | win ECE |
|---|---:|---|---:|---:|---:|
| **D1** 12 等重み − 現行 6 | **−0.003649** | [−0.004497, −0.002809] | −0.000516 | −0.000664 | +0.000325 |
| **D2** 12 等重み − 事前登録の 8 | **−0.001639** | [−0.002076, −0.001211] | −0.000226 | −0.000282 | +0.000198 |
| 参考: C1 8 等重み − 現行 6 | −0.002009 | [−0.002424, −0.001588] | −0.000290 | −0.000382 | +0.000128 |

D1 の年別は 7 年すべて負（−0.00202 〜 −0.00511）。品質ガードは top2・top3 とも改善方向で、
win ECE +0.000325 は +0.001 の幅の中。

**この数値をどう読まないか**

- **判定ではない。** 7 年すべて開発期間で、129/130/132/134 と今回の kill-test が使い切っている。
  確認になるのは前向き窓だけで、それは 133 が 12/28 まで占有している。
- **「12 は 8 より良い」という対等な比較ではない。** ただし理由は初稿に書いたものとは違う。

  > **訂正（2026-09-22・独立レビュー指摘）**: 初稿は「D2 の差の大半は seed 平均の効果」と
  > 書いたが、**これは裏付けの無い主張だった**。正しい構造は次のとおり:
  >
  > | 構成 | 現行 6 の重み | A の重み | B の重み | **A+B 合計** |
  > |---|---:|---:|---:|---:|
  > | 8 等重み | 6/8 = 0.750 | 1/8 | 1/8 | **0.25** |
  > | 12 等重み | 6/12 = 0.500 | 3/12 | 3/12 | **0.50** |
  >
  > **12 にすると候補枝の合計重みが 2 倍になる。** D2 は
  > 「seed をまとめた効果」と「候補枝の重みを倍にした効果」の**和**である。厳密には
  > `D2 = [L(U) − mean_s L(M_8,s)] + [L(M_12) − L(U)]`（`U = .75F + .125Ā + .125B̄`）。

  **実測した（追加学習 0・`recheck-v3`）**:

  | 項 | 点推定 | 98.75% CI | D2 に占める割合 |
  |---|---:|---|---:|
  | seed をまとめた効果（重み固定） | −0.000166 | [−0.000313, −0.000115] | **10.1%** |
  | **候補枝の重み 0.25 → 0.50** | **−0.001473** | [−0.001865, −0.001064] | **89.9%** |
  | 合計（= D2） | −0.001639 | | 100% |

  恒等式の残差は 2.2e-19。

  > **訂正の訂正**: 初稿の「大半は seed 平均」は**裏付けが無いどころか逆だった**。
  > **12 が 8 に勝つ分の 9 割は「候補枝に 2 倍の重みを与えたこと」から来ている。**

  **これは 12 を推す根拠ではなく、むしろ慎重になる理由である。**
  pair の事前登録は **「重みの探索は一切禁止・w = 1/8 等重みで凍結」**と明記している。
  全 seed 規則は「探索」ではなく単一規則の一貫適用だが、
  **その帰結である重み配分は一度も検定されていない。**

  > **さらに訂正（レビュー指摘）**: 「134 が混合後の重み調整を閉じた」は**広すぎた**。
  > 134 の枝重みの確認結果は **+0.000121 CI [−0.000203, +0.000476]** で、
  > 検定したのは**既存の joint/anchor 配分**であって、新しい枝ではない。
  > 134 は注意の根拠にはなるが、この配分が失敗することの根拠にはならない。
  >
  > **また 89.9% は D2 に占める割合であって D1 ではない。** 現行 6 に対しては
  > `U − F = −0.002175` で、そこから再配分が更に `−0.001473` を足して D1 = −0.003649 になる。

  いずれにせよ D2 は **A・B が測定値より強いことの証拠ではない。**

### 3.7 構成の決定 — **U（全 seed・登録済み配分）**。凍結規則を明示的に改訂した

分解の結果を受けて、[`configuration-rule-amendment.json`](configuration-rule-amendment.json)
（sha256 `cfd7df53…`）で **`configuration-rule.json` の「等重みメンバー」要件を改訂**した。

| 構成 | joint | anchor | A | B | 成果物 |
|---|---:|---:|---:|---:|---:|
| 事前登録の 8 | 0.375 | 0.375 | 0.125 | 0.125 | 8 |
| 等重み 12 | 0.25 | 0.25 | **0.25** | **0.25** | 12 |
| **採用: U** | 0.375 | 0.375 | 0.125 | 0.125 | **12** |

U は **A・B の 3 seed を全部入れつつ、枝の配分は事前登録どおりに保つ**
（joint/anchor の各 seed が 1/8、A・B の各 seed が 1/24）。

**採用理由**:

1. **一度でも事前登録の検定を通った配分は 0.375/0.375/0.125/0.125 だけ。** 等重み 12 はそれを変える。
2. **seed を選ばずに済む。** 129 は seed 選択を明示的に拒んでいる（*No fake selected_seed*）。
   さらに今回は**選ぶなら事後選択になる** — per-seed の C2（42 −0.000746 / 43 −0.000573 /
   44 −0.000565）を既に読んでおり、**seed 42 は 3 つの中で最良**である。
   どんな「成績非依存の規則」を掲げても、外からは反証できない。
3. 8 に対する増分は **−0.000166** と小さく、**種類が素直**（seed 平均＝分散低減であって配分変更ではない）。
4. **6 本の成果物は既に存在する**（9/20 学習済み）ので、今回の 12 成果物のコストは支払い済み。

**独立レビューとの差**: レビューは分解を見て推奨を **12 → 8 に変更**し、
「全 seed 被覆が必須なら U を選ぶ」と述べた。**この決定は全 seed 被覆を必須と扱う**。
レビューの 8 推奨は「文書化された成績非依存の抽選」が前提だが、
**per-seed の数値を読んだ後にその抽選は成立しない。**

**この決定が主張しないこと**: U が 8 や 12 より前向きに優れること（7 年すべて開発期間）。
等重み 12 が誤りであること（将来の候補として有効）。

---
- **D1 の seed sd = 0.000000 は不確実性が小さいという意味ではない。**
  12 メンバーは 1 個の成果物なので seed 方向の広がりを持たない。
  **再学習分散はここでは 1 ミリも測れていない。** CI は保存予測に条件づけた開催日サンプリングだけである。
- **維持費**: メンバー成果物は 6 → 12 に倍増する。
  ただし**「係数更新のたびに booster を学習し直す」は成立しない**
  （独立レビュー指摘）。`candidate_mixture_bundle.py` は既存モデルと別途用意した係数を
  組み合わせるだけで fit を伴わない。増えるのは**保管・検証・読み込みの対象数**である。
  また **C2 の足切り −0.0004 を 6 倍して要求するのも誤り**で、それは新しい規則の発明になる。
  **12 メンバーの増分維持価値は今回測っていない。**

未測定の改善幅を足し合わせてはいない。D1 は D1 として、C1 は C1 として単独で読む。

### 3.6 候補メンバーを実際に学習した（9/20 実行）

3.4 で「6 本の追加学習が要る」と書いたものを、同日中に着手した。
ドライバは新規 [`scripts/candidate_bundle_136.py`](../../scripts/candidate_bundle_136.py)。

**129 の builder は書き換えていない。** 代わりに、その検証済みの全窓 fit 経路
（凍結入力・started-all の OOF outcome patch・fit 検証・artifact 書き出し・
**予測がビット一致することを要求する直列化往復**）をライブラリとして呼び、
メンバー表・recipe・作業ディレクトリ・config だけを差し替えている。

**recipe が kill-test と同一であることを、学習を始める前に機械照合した**:
`make_recipe()` の `recipe_meta` が **42 個の member-year 受領書すべてと一致（不一致 0）**。
出荷済みの production `joint-42` recipe との差は **`params` だけ**
（`[["n_estimators",900]]` → `[["extra_trees",true],["n_estimators",900]]`）。

#### 途中で判明した重大な事実：**129 の build は現行 main から再現できない**

`prepare` が `upstream_state()` で落ちた。原因は 129 の `source_hash` が
`joint_residual_stack.source_hash()`（123/124/125 の研究選択チェーン）を畳み込んでおり、
その底が **`db/ features/ training/ eval/ probability/` の `src` 配下の全 `.py`** を digest
していることにある。

**どのコミットが壊したかを特定した**（作業ツリーを触らずに、各コミットの blob から
同じハッシュを組み直して照合）:

| ref | 118 source_hash | |
|---|---|---|
| `bdc789e^`（= `c1dfb14`） | `33106805…` | **凍結値と一致** |
| **`bdc789e`** | `5783c24a…` | **不一致** |
| `f528289` / `HEAD` | `5783c24a…` | 不一致 |

→ **コミット `bdc789e`「Fix promotion evidence, prediction population, and training consistency」**
（`training/{adoption,artifacts,calib_split,cli,hpo,promote}.py` と `eval/dataset.py` を変更）
**が 111→…→129 のチェーンの再現性を壊している。私の変更より前の話である**
（私の 2 ファイルを HEAD 内容に戻しても一致しない）。

**これは builder が設計どおり fail-closed で止まっているのであって、バグではない。**
だが意味は重い: **本番 bundle `mix-129-nj6` を現行 main から作り直せない。**
12 月の係数更新の前に、再凍結するのか `bdc789e^` の木で作るのかを決める必要がある。

#### 候補側の来歴は 125 チェーンに縛らないことにした

これらの候補を選んだ証拠は kill-test であって 123/124/125 ではない。
125 に縛るのは**他人の来歴を借りる**ことになるので、`source_hash` / `upstream_state` を
候補向けに定義し直した:

- `source_hash` = **fit を左右しうる全ソース**（5 パッケージの `src` + serving loader + 両ドライバ）。
  129 の凍結値と一致することは要求せず、**prepare で凍結して各 fit の前後で再検査**する
  （= 6 本の間にコードが動いていないことの保証）。
- `upstream_state` = **111 snapshot** + **出荷済み joint メンバーの metadata**（16 drops の出所）
  + **kill-test の事前登録 6 件と 42 member-year 受領書全部**（選択の証拠）。

#### 実行

| 段階 | 結果 |
|---|---|
| `prepare` | PASS（6 fit を登録・学習なし） |
| `smoke`（n_estimators=5・2 OOF・2007 年まで） | **6/6 PASS**。直列化往復も全件ビット一致 |
| `train`（900 本・8 OOF・2007-01-01〜2026-08-23） | 12:26 JST 起動・2 worker × 1 thread |

**1 巡目の実測**（所要 17.6 分・2 本並列）:

| メンバー | fit 秒 | 直列化パリティ | max_abs_diff | head 値 |
|---|---:|---|---:|---:|
| `extra_trees_joint-42` | 996.7 | PASS | **0.0** | 2,649 |
| `extra_trees_joint-43` | 1040.2 | PASS | **0.0** | 2,649 |

出荷済みメンバーの 934〜1024 秒の帯とほぼ同じ。`num_threads=1`・booster 8 本。
ピーク RSS **6.2 GB**で、2 worker では約 12.4 GB。24 GiB 機では swap を使う
（実測 swap 10.9/12 GB）ものの、所要は基準比 +5% に収まった。
**1 worker に落とせば安全側だが所要は倍**になる。

`train` は builder 自身が「6 件の smoke 受領書が揃うまで本 fit を許さない」規律を持っており、
それを満たしてから起動している。

**smoke 成果物の metadata で、パラメータが実際に booster まで届いていることを確認した**
（`params` が LightGBM に認識されず黙って無視される、という 099/kill-test が警戒した故障モード）:

| | `actual_params` の差分（出荷済み joint-42 比） | 列数 | 目的関数 | 校正 |
|---|---|---:|---|---|
| `extra_trees_joint` | **`extra_trees: true` を追加**のみ | 125 | `pl_topk` / `group_softmax` | `isotonic_strict_past_oof`・degenerate でない |
| `leaves63_child100_joint` | **`num_leaves` 31→63・`min_child_samples` 20→100** | 125 | 同上 | 同上 |

他のパラメータ（`colsample_bytree` 1.0・`learning_rate` 0.05・`reg_lambda` 1.0・`subsample` 1.0）は
出荷済みメンバーと同一。`objective: binary` と出るのは pl_topk が callable だからで、
**出荷済みメンバーも同じ表示**になる。

#### 6 本が揃ったあと、bundle に残るのは「補正係数の結線」だけ（追加 fit は 0）

出荷済み bundle のメンバーは 4 ファイルに加えて `correction` ブロックを持つ:

```json
{"coefficients": [...5 項...], "terms": ["gap_log","prior_gap_log","female_sin","female_cos","centered_logp"],
 "fit_through": "2025-12-31", "valid_year": 2026, "source_study": 125, "source_sha256": "..."}
```

**候補の係数は新しく推定しない。** kill-test の主条件は `joint_transfer` で、
**`joint-42` の同一年の係数を全候補に移送し、`centered_logp` だけ候補自身の win から再計算する**
規約だった（`pair_prereg.json`: 「126 用に係数を再推定した結果ではない」）。

> **訂正（2026-09-22・独立レビュー指摘）**: 初稿はここを「**同一 seed** の joint 係数を移送」と
> 書いていたが**誤り**。実装は `member_kill_test.py:216`
> `TRANSFER_SOURCE = {"joint_transfer": "joint-42", ...}` で、**候補の seed に関わらず
> 常に `joint-42` の係数**を使う（私の独立再計算 `eight_model_recheck.py:116` も同じで、
> だからこそ 8.7e-19 で一致した）。
> **seed を合わせる配線にすると、測った規約とは別物になる。**

C1/C2 はその規約で測った数値なので、**別の規約で出荷したら測っていない構成を出荷することになる。**
→ **bundle の 6 メンバーはすべて `joint-42` の 2026 年係数を持たせる。**

移送元は 130 の凍結係数で、`joint-42` の 2026 年値は 125 の seed-42 `coefficients.json` と
**完全一致**することを確認した（`[0.08719158525096903, −0.03686843894557583, −0.054936519362538926,
−0.17066893848912204, 0.02777443237859144]`）。

→ **組み立てに必要な追加学習は 0。** 残るのは (a) 8 か 12 かの構成決定、
(b) その決定に沿った `bundle.json` の生成と、移送規約の明記である。

#### **今日作ったメンバー自体には、性能の測定値が 1 つも無い**

ここを混同しないこと。

- **C1 / C2 / D1 を測ったのは kill-test の walk-forward メンバー**（年ごとに、その年より前までで学習）で、
  各年の評価はその年に対して out-of-sample だった。
- **今日作った 6 本は `train_through = 2026-08-23` の全窓モデル**で、
  **2020〜2026-08 のすべてを学習に見ている**。この 6 本を開発期間のどこで採点しても in-sample であり、
  **どんな数字も出してはならない。**
- 配布形にするために必要だったのがこの 6 本で、**その配布形の性能を測れる窓は前向き窓しかない**
  （[`prospective-plan.md`](prospective-plan.md)）。

「kill-test が −0.002009 だったから今日のメンバーも −0.002009 である」とは言えない。
言えるのは「**同じ recipe・同じ選択規約で作った、配布できる形が存在するようになった**」までである。

**この段階でも混合は組んでいない。モデルの昇格も、本番 ACTIVE の変更も、DB 書き込みもしていない。**
8 メンバーか 12 メンバーかの決定は未了のままで、6 本はそのどちらの上位集合でもある。

### 3.8 loader の一般化と bundle の組み立て（2026-09-22）

#### 6 固定は 4 箇所にあった

| 箇所 | 何が固定されていたか |
|---|---|
| `mixture_model.validate_manifest` | `len(members) != 6`・`weight != 1/6`・`IDENTITIES` の ID 順・`profile` 固定・`source_study == (125 if joint else 118)` |
| `mixture_model._load_member` | `actual_params != PARAMS`（本番の LightGBM 引数と完全一致を要求）・`columns_for(branch)` が `pruning`/`anchor` のみ |
| `mixture_model.predict_mixture` | メンバー ID 列が `IDENTITIES` と一致することを要求 |
| `mixture_correction.average_member_predictions` | `len(members) != 6` かつ**重みなし平均** |

#### 形だけを一般化し、厳しさは 1 つも緩めていない

**プロファイル表**（`PROFILES`）を導入し、メンバー集合・重み・期待パラメータ・
補正の出所を**プロファイルが宣言する**形にした。検査項目は 1 つも減らしていない
（モジュール定数から読む代わりにプロファイルから読むだけ）。
`mix-129-nj6` は従来どおり legacy エントリを通り、重みは**リテラルの `1/6`**、
ID 順は `IDENTITIES`、期待パラメータは `PARAMS` のままである。

#### 1 ULP の発見 — **等重みは素の平均と bit 一致しない**

`average_member_predictions` に `weights=` を足したが、**既定（`weights=None`）は従来どおり
「6 本・素の平均」**に留めた。理由は実測で分かった:

```
x.mean(axis=0)            → 0.3609438311260585
tensordot([1/6]*6, x)     → 0.36094383112605843
```

数学的には同じだが**最後の 1 ULP が違う**（前者は pairwise 加算して 1 回割る、
後者は `w_i * x_i` を累算する）。**133 が本番モデルに対する対比較を記録している最中**なので、
出荷済み bundle の提供確率を理由なく 1e-16 動かすわけにいかない。

→ ルーティングを `average_for_profile(profile, predictions, weights)` という
**名前のある関数に切り出し**、legacy プロファイルは素の平均を通す。
**変異テストで確認**: この分岐を消すと対応テストだけが落ちる。

#### 不変条件のテスト — **合成 manifest ではなく実物に対して**

新規 `serving/tests/unit/test_mixture_profile_invariance.py`（**26 件**）。
出荷済み `bundle.json` を直接読み、

- 133 の事前登録が記録した sha256 `28f39715…` と一致すること
- 現状の形（6 本・1/6・ID 順・branch・source_study）がそのままであること
- **現行 validator が拒否するもの 11 通りを、一般化後も全部拒否すること**
  （5 本・7 本・重み違い・順序違い・seed 違い・branch 違い・未知 profile・
  source_study 違い・valid_year 違い・`can_adopt`・`train_through` 違い）

を固定した。**一般化が「穴を広げた」のではなく「形を広げた」ことの担保**である。

#### 組み立て結果

[`scripts/assemble_candidate_bundle_136.py`](../../scripts/assemble_candidate_bundle_136.py)
→ `artifacts/136-candidate-bundle/`

| | |
|---|---|
| `bundle_id` | `136-candidate-allseeds-registered-alloc-v1` |
| sha256 | `c0241d5383bc4869fbacf1e092355dd4dea06bfbe975ae28db8d425837a1ef43` |
| メンバー | **12 本が本番 loader で読み込み成功** |
| 重み合計 | `1.0`（厳密） |
| 枝配分 | pruning 0.375 / anchor 0.375 / extra_trees 0.125 / leaves63 0.125 |
| 補正 | 候補 6 本すべてが **joint-42 の係数**（production 6 本は自分の係数） |

メンバーファイルは**コピー**した（`contained_path` が symlink と親ディレクトリ脱出を禁止するため）。
manifest が全ファイルの SHA256 を固定しているので、コピーがずれれば読み込みが落ちる。

#### U の実測値（追加学習 0・非拘束）

| 比較 | 点推定 | 98.75% CI | top2 | top3 | win ECE |
|---|---:|---|---:|---:|---:|
| **U 対 現行 6** | **−0.002175** | [−0.002641, −0.001737] | −0.000313 | −0.000416 | +0.000130 |
| 参考 C1（8 等重み）対 現行 6 | −0.002009 | [−0.002424, −0.001588] | −0.000290 | −0.000382 | +0.000128 |

年別は 7 年すべて負（−0.00130 〜 −0.00312）。`U − C1 = −0.000166` で、
これは分解の seed 平均の項と**厳密に一致**する。

**この数値は U という構成の開発期間での値であって、組み立てた bundle の測定値ではない。**
メンバーは全窓 fit なので、開発期間のどこで採点しても in-sample である。

#### 本番は 1 バイトも動いていない

- 出荷済み `bundle.json` の sha256 は **`28f39715…` のまま**（133 の事前登録と一致）
- ACTIVE は **`mix-129-nj6`** のまま
- 全スイート緑: serving **291** / live 152 / betting 190 / training 636 / eval 577 / probability 341

---

## 4. 優先2：校正材料の来歴

### 4.1 監査（独立担当 = codex、read-only）

完全版は `artifacts/136-eight-model-candidate/calibration-audit/codex-provenance-audit-20260920.md`。
20 を超える fit 経路を `file:line` つきで表にしてある。中核は:

- **既定の runtime λ fit はレース日しか絞らない。** `model_calibration.py:452-467`（top-k）と
  `:296-311`（p）の `DISTINCT ON` は「レースごとの最新 run」を取るだけで、
  model_version も入力条件も学習末日も見ない。
- `_latest_run_predictions` には `base_model_version` の絞り込みがあるが（`:240-241`）、
  **bulk ローダはこれを通らない。**
- serving は `_resolve_stage_discount` に model_version を渡しているのに、
  **manifest 分岐にしか転送していない**（`pipeline.py:197-203`）。runtime 分岐は日付だけ。
- **材料不足は例外にならない。** `stage_discount.py:177-184` と `model_calibration.py:207-212` が
  identity を返し、それが「校正できました」として通る。
- λ は「その日の対象レースの実効 regime を見る前に」解決されるので、
  **1 つの λ が同じ日の別々の入力条件に使われうる**（`pipeline.py:134-148, 447-462`）。
- 既に閉じているもの: manifest-required は runtime fit に落ちない（`pipeline.py:197-203`）、
  公式 OOF 生成は年 < Y で学習して年 Y を予測する（`oof_generate.py:124-145`）、
  manifest 適用は `target_date > fit_through` を要求（`calib_activation.py:158-165`）、
  ACTIVE モデルの上書きは既に禁止（`artifacts.py:322-328`）、
  ops の manifest-required 欠落は既にエラー（`runner.py:328-339`、監査項目 6 は解消済み）。

**未確認:** 実 DB 上で実際に何行が別世代・別条件・in-sample かは測っていない（codex は DB に
到達できなかった）。`+0.000014` という既報の実害も**再現していない**。経路が開いていることと、
実際に汚染されていることは別である。

### 4.2 実装したもの

新規 [`probability/src/horseracing_probability/calibration_provenance.py`](../../probability/src/horseracing_probability/calibration_provenance.py)

- `MaterialProvenance(model_version, input_regime, producer_train_through, material_as_of, min_races)`
  — 呼び出し側が**明示的に宣言する**契約。`model_version` は必須。
- `run_conditions()` / `race_conditions()` は SQLAlchemy 条件を返し、
  **`DISTINCT ON` の前に**適用される。後から絞ると、不適格な新しい run が勝ってから落ちるので、
  本来使える古い run ごとレースが消える。
- `input_regime` は 091 の `;wregime=` を見る。マーカーが無い run は
  **要求された regime として黙読しない**（`REGIME_ABSENT` を明示したときだけ選ばれる）。
  前方一致も禁止（`serving_replay` は `serving` を満たさない）。
- OOS は **厳密に後**（`race_date > producer_train_through`）。等号は in-sample。
- `material_as_of` は `computed_at` の上限。as-of 再現は「その時点で存在した予測」を
  聞いているので、レース日では答えられない。
- `InsufficientQualifiedMaterial` / `CalibrationProvenanceError`。

`model_calibration.py` の変更

- `load_p_samples` / `load_topk_samples` / `fit_product_stage_discount` に
  `provenance=None` を追加。**既定は pre-136 と同一のクエリ**（回帰テストで固定）。
- 新 `fit_product_stage_discount_strict(session, *, before_date, provenance, ...)`
  — `provenance` は**必須**。適格材料が `min_races` 未満、または fit が identity に落ちたら
  **`InsufficientQualifiedMaterial` を送出する**。
  **別世代・別 regime・古い学習レース・無制限ローダへの再試行は一切しない**
  （同じ estimand のままで広げられるクエリは存在しないため）。成功時は
  `(StageDiscount, audit_dict)` を返し、実際に強制した契約を `describe()` で記録する。
- identity の判定は **`StageDiscount.fallback`**（fitter 自身が「fit を断った」と言う旗）を読む。
  `lambda2 == lambda3 == 1.0` の比較にしない。**現行 fitter では両者が区別できない**
  （`fallback=True` の返り値は必ず λ=1.0）ので、後述の kill-test は monkeypatch で作った。
  エラーと監査には `n_races_l2` / `n_races_l3` を載せる。

**本番には結線していない。** すべての呼び出し側は `provenance=None` のままで、
133 の凍結 λ も本番配備も変えていない。結線は別の意図的な変更として行うべきもの。

**ついでに確認した 2 点（変更していない）**

- `_wregime_lv` は `_predict_persist` の**最後**に適用されるので、serving が書く
  `logic_version` では `;wregime=<r>` が常に末尾にある（`pipeline.py:335-338`。
  `;calib=` と `;coefstale=` はその前）。だから `endswith` 分岐が主で、
  `contains(";wregime=<r>;")` は将来末尾に何か足された場合の保険。
- `pipeline.py:533` の既存の regime フィルタは `contains(f";wregime={wregime}")` で、
  **前方一致に対して脆い**。現状 `serving` と `full_info` はどちらも他方の接頭辞ではないので
  無害だが、3 つ目の値（例 `serving_replay`）を足した瞬間に静かに誤マッチする。
  今回は触っていない。

### 4.3 独立レビューと、それで直したもの

実装後に独立レビュー（codex・read-only）にかけた。**判定は NEEDS FIXES** で、
「SQL の位置・`fallback` 信号・既定クエリの同一性・クリーンアップの適用範囲は妥当」だが
**4 種類の実欠陥**が指摘された。全件修正した。

| 指摘 | 内容 | 修正 |
|---|---|---|
| **LIKE ワイルドカード** | `contains()` / `endswith()` は LIKE にコンパイルされ、`_` と `%` がワイルドカードになる。**`full_info` が `fullXinfo` にも一致していた** | `autoescape=True` を全箇所に。専用テスト + 変異で確認 |
| **型・タイムゾーン検証** | `datetime` は `date` の派生なので `producer_train_through` に timestamp が通り、DATE 列との比較がセッション timezone 依存になる。`material_as_of` は型も tz も未検証（`computed_at` は tz-aware） | `datetime` を明示的に拒否・`material_as_of` は aware な `datetime` を要求 |
| **監査の件数が過大** | `n_qualified_races = len(raw)` は適格な予測が 1 件も無いレース（`{}`）まで数えていた | `n_races_in_window` と `n_qualified_races` に分離 |
| **エラー文言が嘘をつきうる** | 境界で詰まった fit（標本は十分）にも「N < required」と書いていた | `shortage` フラグで文言を分け、十分な標本のときは「fitter が自分の理由で断った」と書く |

**さらに 1 件、私が受け入れた設計指摘**: strict 経路は `provenance=None` しか拒まず、
`input_regime=None` かつ `producer_train_through=None` の契約は**両方のガードを無効化したまま
契約に見える**。→ `require_complete=True` を既定にし、両方が未指定なら
`CalibrationProvenanceError`。意図的に外す場合は `require_complete=False` を明示させる。

**レビューが指摘したテストの穴も全部埋めた**（下記）。

### 4.4 テスト — **25 件**

新規 `probability/tests/integration/test_calibration_sample_provenance.py`。

| 何を固定するか | 件数 |
|---|---|
| (a) 別世代の新しい run が適合する古い run を置き換えない（p / top-k）・**regime を揃えて世代だけで判定させる分離テスト** | 4 |
| (b) 別入力条件・マーカー無しは不適格・前方一致しない・**LIKE ワイルドカードが漏れない**・`REGIME_ABSENT` の**肯定**と否定・**後続セグメントがある場合の SQL 分岐** | 8 |
| (c) 学習済みレースの後日予測・境界は等号を含まない・`material_as_of`・**型/tz 検証** | 4 |
| 材料不足は例外・広げない・`provenance` 必須・**契約の不完全を拒否**・fitter の fallback を読む・**成功経路と監査の件数** | 7 |
| 既定経路が **pre-bulk の per-race 実装**と一致（`provenance=None` との比較ではなく） | 2 |

**変異で殺せることを確認した 5 件**（それぞれ対応テストだけが落ちる）:

| 変異 | 結果 |
|---|---|
| `autoescape=True` を外す | 1 failed |
| `model_version` 述語を消す | 2 failed |
| `contains(";wregime=<r>;")` の分岐を消す | 1 failed |
| OOS 境界を `>` から `>=` に | 1 failed |
| identity 判定を `sd.fallback` → `λ==1.0` に戻す | 1 failed |

**レビューが指摘した「これは kill-test ではない」も残している**:
`test_strict_fit_rejects_a_fitter_fallback_not_just_a_short_sample` は
現行 fitter では `fallback` と λ 比較を区別できないので、docstring にその旨を明記した。

**レビューが挙げた残課題のうち、今回対応しなかったもの**（本番結線の前提として記録）:

- `model_version` の文字列一致は**世代の必要条件であって十分条件ではない**。
  `PredictionRun` に成果物 digest の列が無いため、これ以上は現スキーマでは詰められない。
  `MaterialProvenance.describe()` がこの限界を毎回記録する。
- `material_as_of` は `PredictionRun` を絞るだけで、**STARTED 判定と結果は現在のテーブルから読む**。
  完全な as-of 再現ではない。
- `min_races` と `provenance.min_races` は測っているものが違う（前者は fitter の**ステージごと**、
  後者はロードする**レース数**）。docstring に明記し、監査に両方の件数を出すようにした。

### 4.5 途中で見つけた既存の欠陥（直した）

`probability/tests/conftest.py` の autouse クリーンアップが **106 の append-only トリガに当たって中断し、
テスト間で DB 状態が漏れていた。** その結果、既存の `test_load_p_samples_bulk.py` も含めて
integration テストが軒並み error/fail していた。`eval` は commit `f528289` で同じ修正を受けている。

同じ欠落を全パッケージで調べ、**`probability` / `live` / `features` / `ingest` / `scrape` の 5 つ**に
`SET LOCAL session_replication_role = 'replica'` を追加した
（`betting` / `serving` / `training` / `eval` / `db` は既に対応済み）。

修正後の各パッケージ:

| パッケージ | 結果 | 備考 |
|---|---|---|
| `probability` | **341 PASS**（新規 25 件を含む） | conftest 修正前は integration が軒並み error |
| `features` | 455 PASS / 5 skip | |
| `scrape` | 328 PASS | |
| `ingest` | 33 PASS | |
| `eval` | 577 PASS | 既に修正済みだった |
| `live` | 149 PASS / **3 FAIL** | 修正前は 5 FAIL + **5 ERROR**。error は解消し、残る 3 は既存 |

### 既存の失敗 16 件は修正した（E・2026-09-22）

`model_calibration.py` の変更を revert しても同一に再現する、私の作業とは無関係な失敗だった。
**全 16 件とも「テストが古い契約を書いていた」側**で、**production コードは 1 行も変えていない。
fail-closed のガードも 1 つも弱めていない。**

**根本原因**: `bdc789e` が ACTIVE 昇格ゲートを意図的に締めた
（`docs/existing-logic-fixes-plan.md` §1/§6）。`adoption.evaluate_promotion` が新たに
`can_adopt` / `eligible_for_verdict` を**厳密な bool** で要求し（以前は `artifact_kind` を
省けば適格判定ごと素通りした）、`subgroup_assurance == "full"` を要求し、
`promotion_evidence.stamp_report` が作る証拠ブロック（凍結 gate-config・採点窓・
**両アームの学習契約が登録時の記録と一致すること**）を要求するようになった。
加えて `save_model_version` は既存 ACTIVE があれば新モデルを CANDIDATE のままにする。

| 失敗 | どちらが誤り | 修正 |
|---|---|---|
| `training` 7 件 | テスト | fixture が 3 キーの裸 dict で**後段のガード**に届かず、全行が `candidate` を返して下限と等値の区別がつかなくなっていた。版ゲート自身の観測量（`cause == "verdict_contract_too_old"`）で判定する形に書き直し、空振りしないよう end-to-end のアンカーを 1 件追加 |
| `live` 3 / `betting` 4 / `serving` 1 | テスト | 原因は 1 行。`_synth.make_active_model` が CANDIDATE を作るので `_active_model_version` が `None` を返していた。`adopt_verdict()` を**本番の `stamp_report` 経由**に書き直し、`<model>-ref` を先に登録して `reference_model_version` に渡す形に |
| `serving::test_resolution_zero_one_many` | テスト | `make_active_model` を 2 回呼んで「複数 ACTIVE」を作っていたが、登録は**正しくそれを拒否**するようになった。2 ACTIVE は壊れたレジストリなので、その状態を直接書き込む形に変更し、**登録が ACTIVE を奪わないことの assert を追加** |

**変異で確認**: `adoption.py` の `n < MIN_CONTRACT_VERSION` を `n != ...` に変えると
10 件中 6 件が落ちる ＝ そのファイルが存在する理由である退行は今も捕まる。

| パッケージ | 修正前 | 修正後（私も独立に再実行して確認） |
|---|---|---|
| `training` | 7 failed / 628 passed | **636 passed** / 7 skipped |
| `live` | 3 failed / 149 passed | **152 passed** |
| `serving` | 2 failed / 263 passed | **265 passed** |
| `betting` | 4 failed / 186 passed | **190 passed** |

変更は**テストファイル 5 本のみ**（`{betting,live,serving}/tests/_synth.py`・
`serving/tests/integration/test_date_and_resolve.py`・
`training/tests/unit/test_contract_version_lower_bound.py`）。`git diff --stat` で確認済み。

---

## 5. 優先3：133 の現在地

独立担当が現行コード・現物・読み取り専用 DB で監査した
（全文 `artifacts/136-eight-model-candidate/133-status/133-status.md`）。

### 記録状況（2026-09-20 時点・72 レース / 3 開催日・選択時の不採用 0）

| 開催日 | 採用ソース | 主条件 preweight | 副条件 serving |
|---|---|---|---|
| 9/13 | capture ×24 | **23 採点**、1 除外 `field_changed_after_capture` | 23 採点、同 1 除外 |
| 9/19 | production_snapshot ×24 | **0 — 24 件すべて `regime_not_recorded`** | 23 採点、1 除外 |
| 9/20 | capture ×22 + snapshot ×2 | 22 件が結果待ち、2 件 `regime_not_recorded` | 24 件が結果待ち |

**主条件 23 レース / 1 開催日採点済み。** 副条件 46 レース / 2 開催日。
DB 実査でも 9/20 は結果 0 行（`max(race_date)` = 2026-09-20）。

### 窓と不足

登録窓 2026-09-13 〜 2026-12-28、`min_eval_days` = **24**。主条件は **1 日 / 24 日**。
9/20 の結果が入れば 2 日。**残り 22 日が必要**で、残る開催日は概算 29〜32 日
（土日 28 + 12/28 + 祝日月曜の可能性。DB には 9/20 より先のレースが無いので概算）。
**取りこぼせるのは 7〜10 日しかない。**

### 恒久的に失われたもの

- **9/19 の主条件は回収できない。** capture が走らず、`from-production` は
  `regimes: {"serving": ...}` しか書かない（`paired_prospective.py:855`）。
- 9/20 の 2 レース（`202606040601`, `202609040601`）は 10:00 JST の capture 時点で発走済み。

### 新しい interim（今日、修正後の scorer で、**新しいファイルに**保存）

`artifacts/136-eight-model-candidate/133-status/interim-{preweight,serving}-agentB-20260920.json`
（`specs/133-.../evidence/looks.jsonl` は 6 行のまま。追記していない）

| 条件 | n | 開催日 | 点推定 | 95% CI | 状態 |
|---|---:|---:|---:|---|---|
| preweight（主） | 23 | 1 | **−0.034810632** | [null, null]（1 日 < 最小 2） | `INTERIM_STATUS_NOT_A_VERDICT` |
| serving（副） | 46 | 2 | −0.010527114 | [−0.033735156, +0.012680929] | 同上 |

`computed_at` と `record_dirs` を除けば**保存済み interim と完全一致**した。
9/20 の修正が既存の集計を動かしていないことの確認にもなっている。

### 9/20 修正の効力（コードで確認・`file:line` は監査文書）

- 登録窓は**選択と採点の両方**で強制（`select_records:1085,1097` / `score_records:1312,1318`、両端を含む）。
- **FINAL は登録主条件 preweight でのみ**（`final_verdict:1453-1457` が副条件に
  `SECONDARY_SUMMARY_NOT_A_VERDICT` / `verdict=null`、`publish_score_once:1528-1531` が第二の関門）。
- **登録 SHA ごとに FINAL は 1 つ**（`FINAL_RECEIPT_DIR:79-82` は CLI で上書きできず、
  `flock` は `compute()` の**前**、既存 receipt は DB を読まずに再出力）。現在ディレクトリは不在＝FINAL 未発行。
- 過去 run の遡及利用は拒否される（`production_link:423` が `computed_at < scheduled_start` のみ、
  `from_production:768-770` は発走後 run へ fallback しない、再現は 1e-9 一致を要求）。

### 保留レコードに運用上の締切は無い（9/20 に確認）

9/20 に capture した 22 件は結果待ちだが、**今夜あわてて何かする必要は無い**
（全文 `artifacts/136-eight-model-candidate/133-status/133-pending-safety.md`）。

- **期限・GC・失効が存在しない。** 記録は毎回ファイルから読み直され、`pending`/`excluded` は
  都度計算されて記録に書き戻されない（`:1086-1090`, `:1214-1248`）。書き込みは create-only。
- **結果の部分取込は `results_incomplete` の PENDING** で、次回実行で採点に回復する（`:1234-1235`）。
  結果 0 件のガードが field 比較より**前**にある（`:1223-1224`）ので、
  「まだ来ていない」だけで誤除外されることはない。
- **`field_changed_after_capture` は capture 後の取消**が原因で運用では避けられない。
  実 DB で 2 件とも取消と確認（9/13 `202606040404`・9/19 `202606040508`・`extra` は空）。
  発生率は確定 48 レース中 2 件（約 4%）= レースを時々失うが**開催日ごとは失わない**。

**唯一の実リスクは記録の消失**: `artifacts/` は gitignore なので
`records/` が evidence の**唯一の原本**である。消さない・移動しない・
そのディレクトリを持たないチェックアウトに切り替えない。

### 未確認・要判断

- JRA の実カレンダー（残り開催日は概算）。**ただし 9/21（敬老の日）は過去 7 年すべて 24 レース開催**で、
  9/19=開催 5 日目・9/20=6 日目と続いているので 3 日目にあたる公算が大きい。
- 9/20 が確実に確定するか。結果ラベルの正しさ。
- **FINAL 単一性の耐久性**: `artifacts/` は gitignore なので receipt はローカルにしかない。
  別チェックアウトや `artifacts/` 消去では見えない。
- **判断を要する論点（私は実行していない）**: `from-production` に主条件も書かせれば、
  凍結行に体重 3 列を NaN にする固定入力条件を当てるだけなので、将来の 9/19 型の取りこぼしを防げる。
  ただし**結果が既知になった後で記録を足す**ことになるため、日を選べば選択バイアスになる。
  登録は production_snapshot を主解析に含めることを明示的に許しているので、
  「窓内の全日に一律適用する」なら整合するが、これは登録の運用解釈の変更であり、
  **利用者の判断事項**として残す。

---

## 6. 次に必要な具体作業（優先順）

1. **明日 9/21 の朝、09:45 JST より前に capture を回す。** 手順は
   [`capture-runbook.md`](capture-runbook.md)。**推奨は 09:15 と 09:40 の 2 回。**
   第 1 レースの発走は 3 開催日とも 09:45 JST ちょうどで、9/20 の 10:00 実行は 15 分遅かった。
   早すぎて skip されたレースは 2 回目で回収できる（同一レースは append-only で重複しない）が、
   **遅れたぶんは主条件では二度と埋まらない。**
2. **構成の独立レビュー**: 8 メンバー（seed を 1 つ選ぶ）か 12 メンバー（全 seed）か。
   **6 本の学習は 9/20 に着手済み**で、どちらの上位集合でもあるので構成決定を待っていない。
3. **6 本の完了確認 → 研究用 bundle の組み立て**（`bundle.json` 相当・混合はまだ作っていない）。
4. **`mix-129-nj6` を現行 main から再現できない問題**（3.6）。**12 月の係数更新の前に要決着。**
5. **校正来歴の結線**: 4.2 の機構を実運用経路へ入れるかの判断。同じく 12 月の前。
   結線する場合は、`_resolve_stage_discount` が対象レースの実効 regime を **λ を解決する前に**
   確定するよう `pipeline.py:134-148, 447-462` を直す必要がある。
   本番結線の前提は 4.3 末尾の「残課題」に列挙した。
6. **追加研究**: C は**中断点まで実測済み**（[`research-c-breakpoint.md`](research-c-breakpoint.md)）。
   2026 実条件で **−0.006602**、CI [−0.013597, **+0.000504**] でゼロ跨ぎ。
   拡張（42 fit ≈ 11 時間）に進むなら**先に継続基準を凍結する**こと。A1 は未着手。
