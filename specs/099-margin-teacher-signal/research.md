# Research: margin-aware 教師信号(099)

Phase 0 の設計決定。spike(`scripts/margin_teacher_spike.py`・GO・2026-08-24)で凍結済みの
事項は再検討しない(spec「凍結済み」節)。ここでは production 移植の設計自由度だけを決める。

## D1: margin の供給経路 — ラベル側 aux 列(採用)vs 別配線の dict

**Decision**: `build_training_matrix` が margin スケールを計算し、`margin_scale_s2` /
`margin_scale_s3` を**レース内定数のラベル側 aux 列**として frame に載せる(`MKT_ODDS` と
同一契約: feature_cols / feature_hash / feature_snapshots に非混入)。

**Rationale**: predictor の fit は行を weight mask → calib split → argsort と何段も加工する。
race→(s2,s3) の dict を別配線すると、この整列を**新規に**保証する必要があり、まさに 097 の
「shared matrix 注入がスコープを迂回」型の事故面になる。aux 列なら行と一緒にスライス・
ソート・コピーされ、整列は既存機構が保証する。`MKT_ODDS`(060)が同じ問題を同じ形で解いた
前例があり、非混入の leak-guard テストの書き方も確立している(INV-M1/M3 同型)。

**Alternatives considered**: ①fit まで race→tuple dict を渡す(整列保証を自作 = 却下)
②objective 構築時に predictor が DB を再クエリ(fit が DB 依存になり、eval の snapshot
pin と矛盾 = 却下)。

## D2: objective の形 — production への移植

**Decision**: spike の `margin_pl_topk_objective` を `cond_logit.pl_topk_objective` の
`stage_scales=None` 引数として移植する。**シグネチャは既存の `offsets=None` を保存**して
`pl_topk_objective(group_sizes, ranks, offsets=None, stage_scales=None)` とする(spike 版は
offsets を持たないので、そのまま写すと market-offset 対応を失う = codex P1)。ビット一致
テストは **offsets 有無 × sample weight 有無の 4 象限**で表明する。None は現行実装と**勾配・ヘシアンがビット一致**
(spike selftest を production 単体テストへ移す)。`_pl_topk_objective_loop`(正しさの
oracle)にも同じ引数を足し、等価性テストの網に stage_scales 有りのケースを追加する。

**Rationale**: spike で「all-ones = production ビット一致」を既に実証したパターンの移植で
あり、新設計ではない。ステージ発火・中立化・break 規則(039/042)は一切触らない(FR-005)。

**Alternatives considered**: sample weight(LightGBM Dataset weight)での近似 — ステージ別
に効かせられない(行重みは全ステージ共通)ため原理的に不可。

## D3: WinModel / predictor の配線

**Decision**: `LightGBMPredictor.fit` が model_df から aux 列 2 本を `to_numpy()` し、
`WinModel.fit(margin_scales=(n_rows, 2))` へ。`_fit_softmax` は既存の argsort で offsets /
weights と同様に並べ、**group 先頭行**から (n_groups, 3)(s1=1.0 固定)を構成して
objective に渡す。predict 側は無変更(教師信号は fit のみ)。

**Rationale**: offsets(060)・weights(079)が同じ「sorted rows に整列した per-row 配列」
の前例。レース内定数列の group 先頭行取り出しは、argsort 後の group 境界
(`group_sizes_from_race_ids` の累積和)から機械的に得られる。**先頭行方式の前提 =
aux 列がレース内定数**であることは dataset 側の生成(race→値の map)が構造的に保証し、
さらに fit 時に **group 内 min==max・値域 [0.25,1.0]・有限値・s1==1 を `ValueError` で**
検証する(`assert` は `-O` で消えるので使わない = codex P1。1 行だけ壊れた scale を先頭行
方式が隠すのを防ぐ)。

## D4: レシピとモデル同一性

**Decision**: `ModelRecipe.margin_teacher: str | None = None`。None は hash から**省略**
(weight_mask 同型)。受理値は `"v1"` のみで他は `__post_init__` fail-closed。
**hash の canonical 化を `ModelRecipe` に一本化する(codex P0-1)**: `recipe_hash()` の省略
規則を canonical payload メソッドとして切り出し、`CalibSplitFactory.recipe_hash` も**同じ
payload** を使う。現行は Factory 側が `recipe.meta()`(全フィールド asdict)を直接 hash して
おり、フィールド追加だけで **arm E 系の既存 hash が全て変わる** — 「既存 hash 全不変」は
この一本化があって初めて成立する(既存 arm E hash のスナップショットテストで固定)。
`_RECIPE_FIELD_DISPOSITION` に **"forward"** を追加し、かつ **`_make_base()` で
`margin_teacher=self.recipe.margin_teacher` を明示的に渡す(codex P0-2)** — disposition 表は
会計検査であって配線ではない。これを忘れると full-history booster と inner OOF booster が
教師信号だけを無視して正常に学習する(黙殺)。`_scope_columns` は frame に触れないので
aux 列は生存するが、shared-matrix 経路のテストで固定する。

**Rationale**: bool でなく str にするのは、将来の別 variant(ソフト飽和等・別 spec)が
別のモデル同一性になるべきだから。disposition の追加は忘れると既存の
`_check_recipe_fields_accounted_for` が ArmNotServable を出す — 「新 field を黙って無視する
arm E」(097 で実害が出た形)を機構が防ぐ。

## D5: ゲートの実行形

**Decision**: 標準 confirmatory paired-eval CLI(068/073/v4)をそのまま使う。アームは
recipe spec 文字列で表現 — candidate `pl_topk:oof_isotonic:mteach=v1`(+ rounds 900 +
wmask 0.5/20260810)vs active 同一 − mteach。両アーム CalibSplitFactory(arm E)=
**現行本番と同じ構成**(095 の再測定と同じアーム形)。窓 2019-01-01..2026-08-23・
min_eval_days 400・seed_noise sd_fold 0.001816(v4 必須)・subgroups 有効。
artifact_kind = full_walk_forward(昇格適格)。gate-config は本 plan で凍結し hash 照合。

**Rationale**: 判定式・CI・ガードを新設しない(088 の教訓 =「新しい判定式を書かない」)。
アームを arm E 構成にするのは「本番が今動いている形との差 = 教師信号のみ」にするため。
holdout 構成で測ると「教師信号 × 校正方式」の交絡が入る。

**Alternatives considered**: 097 型の専用 driver — 反実仮想操作(マスク等)が無いので不要。
標準 CLI で足りるものに driver を書くのは事故面の追加。

## D6: 構造 assert(FR-010)

**Decision**: ①paired-eval CLI に 2 つの実行前検査を追加(汎用 harness には触れない):
(a) candidate と active の recipe_hash が同一なら fail、(b) **canonical recipe payload の
差分が厳密に `margin_teacher: None→"v1"` の 1 フィールドのみ**であることを検査(codex
P0-3: hash 不一致だけでは「mteach が hash に入るが配線で無視される」黙殺を検出できない。
差分 1 フィールド検査は交絡アーム=別の設定差の混入も同時に閉じる)。②スケール不発の検出:
**fold ごとの実行中 assert は足さない**(harness の Protocol 契約)が、**run 完了後・verdict
書き出し前の post-hoc 検査は行う**(T017a: diffs_by_day の非ゼロ差 ≥1・candidate factory の
fit_info 統計で scale_lt1>0。CLI は factory を自分で構築しているので run 後に fit_info を
読むのは Protocol 違反ではない)。加えて (a) 実 DB 形状の統合テスト(SC-002)と (b) smoke
手順で事前固定。

**Rationale**: ①は監査(2026-08-24)の「同一アームは正常な見た目の全ゼロレポートを出す」
finding の最小閉鎖で、mteach= の綴りミス(セグメント黙殺 = 085 で実害が出た形)も同時に
捕まえる(綴りを間違えると両アームが同一レシピになり hash が一致する)。②を実行時 assert
にしない理由: harness は factory を不透明に扱う契約(foldfit の Protocol)で、fit_info を
覗く配線を足すのは契約違反。テスト+統計記録で同じ故障モードを覆える。

## D7: 監査統計

**Decision**: fit_info / artifact metadata に `margin_teacher` 統計を記録。**全レース平均
でなく実際の booster fit 行に対する**ステージ別の source_available(margin が計算できた)/
scale<1(実際に減衰)/ fire∩scale<1(発火ステージのうち減衰)件数と fireable 平均を持つ
(codex: scale=1.0 は「大差で cap」と「時計欠損で中立」を混同するので出所を分計する)。OFF のとき key 自体を出さない(既存 metadata はバイト不変 — 060/085 の前例)。

**Rationale**: spike run 1 のステージ 3 中立バグは**スケール平均の印字でしか発覚しなかった**。
「実際に変調されたか」は結果から遡って検証できないので、fit 時に記録するしかない。

## D8: verdict 分岐

**Decision**: ADOPT → `train-evaluate --register-candidate` 相当で candidate 登録のみ
(`evaluate_promotion` は verdict/assurance/servability の多重ゲートで自動 active を既に
防ぐ)。REJECT → dataset/recipe/predictor/cli の**結線のみ** revert し、`pl_topk_objective`
の `stage_scales` 引数+単体テストは**非結線保全**(None 既定は現行とビット一致なので、
production に残っても挙動不変 = 062/070 の「モジュール保全」より強い形で、コード自体を
残せる)。測定数値は spec 末尾に転記(FR-012)。

**Rationale**: `stage_scales=None` が厳密に現行同一である以上、REJECT 時に objective から
引数を剥がすのは「動かない証拠を作るために動くコードを消す」ことになる。revert すべきは
**呼び出し側の結線**(recipe field・CLI セグメント・dataset の aux 列生成)だけ。

## D9: codex second opinion(憲法・品質ゲート)

**Status**: **取得成功**(本日 6 回のインフラエラー死の後、対象ファイルを 6 本に絞った
レビューで完走)。指摘 8 件の採否:

| # | 指摘 | 採否 | 反映 |
|---|---|---|---|
| P0-1 | `CalibSplitFactory.recipe_hash` が `meta()` 全体を hash するため、フィールド追加だけで arm E 系の既存 hash が全て変わる | **採用**(私の見落とし) | D4: canonical payload を ModelRecipe に一本化・両 Factory 共有・既存 arm E hash のスナップショットテスト |
| P0-2 | disposition "forward" だけでは arm E に届かない(`_make_base` の明示渡しが必要)。黙殺すると booster が教師信号だけ無視して正常学習 | **採用** | D4: `_make_base(margin_teacher=...)` 明示+shared-matrix 経路テスト |
| P0-3 | hash 不一致検査は silent no-op を検出できない | **採用** | D6: canonical payload 差分が厳密に 1 フィールドである検査を追加。effective>0 は fit_info 統計+実形状テスト+smoke 手順(quickstart 3)で固定 |
| P1-4 | spike 版 objective は offsets を持たない — そのまま移すと market-offset 対応を失う | **採用** | D2: `offsets=None, stage_scales=None` 併存・ビット一致は offsets×weights 4 象限 |
| P1-5 | 先頭行抽出は検証なしだと破損を隠す。`assert` でなく `ValueError` | **採用** | D3: min==max・値域・有限・s1==1 を ValueError で |
| SQL | 「次馬の時計欠損→中立」を守るなら window に時計 NULL の finished 行も含め、差分 NULL→1.0 に落とす(spike は window から除外していた=意味が微妙に違う)。該当件数を監査 | **採用** | 実装は契約どおり(欠損→中立)・spike との意味差は件数を監査して記録 |
| 統計 | 全レース平均でなく fit 行ベース・出所分計 | **採用** | D7 改訂 |
| raw NLL | ゲートでも校正前 diff を診断値として残す | **部分採用** | 標準 paired-eval に raw 収集は無く、追加は scope creep。booster 効果と校正後効果の分離は spike の raw 数値(−0.00442)を証跡として引用し、ゲートは出荷可能 pipeline 全体を測ると明記(codex 自身の整理と同じ) |

arm E の isotonic が各アームで再 fit されることは交絡ではなく「教師信号込みの出荷可能
pipeline の効果」である、という codex の整理を採用ゲートの解釈として明記する。

**補助セルフレビュー checklist(codex 取得前に実施・取得後も全項目が D1-D8+D9 反映で
覆われていることを確認済み)**:
- [x] 整列事故(mask/sort/split 後のスケールずれ)→ D1 の aux 列方式で構造回避+fit 時
  レース内定数 assert
- [x] OFF の完全不変 → 勾配ビット一致テスト+recipe_hash 不変テスト+既存スイート無改修
- [x] run 1 SQL バグの再発 → CTE 形の実装+「ステージ 3 が実質変調される」実形状テスト
- [x] 黙殺経路(mteach= 綴りミス・disposition 漏れ)→ アーム同一性 fail-closed+
  _RECIPE_FIELD_DISPOSITION の既存 fail-closed
- [x] 交絡(教師信号以外の差)→ 両アーム同一構成(arm E・rounds・mask・seed)を gate-config
  で凍結
- [x] serving 影響 → 予測経路に margin の読み取りゼロ(教師信号は fit のみ)・E2E バイト
  一致テスト(SC-004)
