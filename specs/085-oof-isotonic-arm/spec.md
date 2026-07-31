# Feature 085: Arm E — full-history booster + strict-past OOF isotonic

**状態**: **実行済み(2026-07-31)= ADOPT・ただし昇格しない**(§11)。実装は commit `850cadf`
**作成**: 2026-07-25
**系譜**: 068 US2(A/B/C-D)の後継。068 の登録簿は改変しない(新しい日付付き登録)
**設計レビュー**: codex 3並列(統計/実装/製品化)— `docs/plan/codex-085-review.md` に採否記録

---

## 1. なぜこれをやるのか(観測された事実のみ)

production の active モデルは booster が `model_fit_through=2020-09-06` までしか学習していない
(直近 29% = 27.8 万行は isotonic 校正専用)。068 US2 はこれを是正する 4 arm を事前登録した。

2026-07-25 のマルチ codex レビューで `OofCalibratedPredictor._make_base()` が `calib_frac` を
渡しておらず、**C/D の "full-history booster" が実際には既定 70/30 split のままだった**ことが
判明(main 97c6989 で修正)。修正後に広窓で再測定した結果:

| ゲート | 結果 |
|---|---|
| winner NLL(PRIMARY) | **−0.011879**、CI [−0.014061, −0.009694] → PASS |
| stat guard / recent 3y・5y / top2・top3 | 全 PASS(top2 −0.00125 / top3 −0.00186 = 改善) |
| critical subgroup(2026_only / nk / 2026_nk) | **全 PASS** |
| **calibration(ECE 非劣化幅 0.001)** | **FAIL**(cand 0.003010 vs act 0.000803) |
| → 判定 | **REJECT**(gate_hard_fail) |

`out/073-calibsplit-CD-verdict.json`(n=26,049 races / 813 race-days / contract v2)。

**読み**: booster に直近 6 年を学習させる効果は窓に依存しない実在のゲイン(−0.012 は 036 の
target encoding −0.0134 に匹敵)。落ちたのは校正器側で、単一 γ の power は A の
ノンパラメトリック isotonic に絶対校正で劣る。**2×2(booster データ量 × 校正器族)のうち
「full-history booster × isotonic」セルだけが未測定**。

## 2. 仮説(事前登録の本体・OOS を見る前に固定)

> **H**: full-history booster + strict-past OOF isotonic(arm E)は、A(70/30 isotonic holdout)
> に対し race-level winner NLL を改善し、かつ v2 契約の全ガード(ECE 非劣化幅 0.001 を含む)を
> 満たす。

対立仮説の明示: OOF サンプルは inner booster(履歴が短い)由来なので、その校正曲線が
full-history booster のスコア分布に転移しない可能性がある(§6 の失敗モード)。**null も成功**
= 「2×2 の最後のセルも駄目だった」は booster データ配分の議論を閉じる価値ある結論。

## 3. arm E の定義(実装前に凍結する契約)

C/D と同じ `OofCalibratedPredictor` 機構(full-history booster `calib_frac=0.0`・expanding
strict-past day-block OOF)を使うが、校正器を差し替える。**codex 2/3 が同時に指摘した最大の
設計欠陥を反映**: 「C/D と同一機構」と「A と同一スコア空間」は現コードでは両立しない。

### 3.1 スコア空間(**この feature の最重要決定**)

| | 現 C/D | A(production) | **arm E(採用)** |
|---|---|---|---|
| 校正器の入力 | `predict_race()` の出力(identity clip + renormalize 済み) | `WinModel.predict()` の生 race-softmax | **`WinModel.predict()` の生 race-softmax** |

serving は `model.raw_predict()` → `calibrator.transform()` の順で適用する
([serving/predictor.py:88-89](serving/src/horseracing_serving/predictor.py:88))。E の校正器は
**serving の校正器が受け取るのと同一のベクトル**で fit しなければならない。C/D の
`predict_race()` 経由サンプルは identity clip(1e-6)+ 再正規化を通っており、tail で異なる。
power-γ は正規化ベクトル上で定義された作用なので実害が小さいが、**ECE を争点にする実験で
この不一致を残すのは不可**。

→ `LightGBMPredictor` から「校正前の race-softmax 確率」を返す共有メソッドを切り出し、
OOF サンプル生成はそれを呼ぶ。正規化済み確率から逆算して復元してはならない。

適用時のパイプライン(A と同一・serving と同一):
`raw PL score → race softmax → isotonic → clip → race renormalize → Harville`

### 3.2 ラベル母集団(started-all)

OOF ブロックの各レースについて、`RaceContext.started_horses` の順で 1 頭 1 行:

| ケース | 扱い |
|---|---|
| 通常 | FINISHED かつ finish_order==1 が 1、他の出走馬は 0 |
| **同着** | **レースを落とさず、1着の全馬を 1**(現 `_single_winners` は同着レースを捨てるので **E では使えない**) |
| DNF / 中止 / 失格 | 結果行があれば 0 として含める |
| 取消・除外 | 出走馬でないので行を作らない |
| 結果欠損・部分 ingest | **0 に化けさせない**。監査理由付きで除外 |
| 勝ち馬が予測/出走馬に不在 | 契約エラー(全 0 レースにしない) |

### 3.3 十分性(fail-closed)

以下のいずれかで **isotonic を fit せず identity にフォールバックし、`sufficient=False` と
機械可読な理由を記録**。confirmatory 実行では **中断または NO_DECISION**(黙って
「full-history booster + identity」を arm E として採点しない):

- 有効 race-day < 2×n_oof / 完全 OOF レース数・行数が下限未満 / 単一クラス
- 有限かつ相異なるスコアが 2 未満 / started・結果・予測のカバレッジ照合失敗

下限値(レース数・正例数・行数)は **OOS を見る前に数値で凍結**する。

### 3.4 決定論・状態リセット

`CalibSplitFactory` は predictor を fold 間で使い回すため、`fit` 冒頭で学習状態を全消去
(校正器・閾値・各種カウント・sufficient フラグ・provenance)。`IsotonicRegression` は
`increasing=True` / `out_of_bounds="clip"` / `y_min=0` / `y_max=1` を明示。

### 3.5 CLI ルーティング(**実測で確認した罠**)

`_factory_from_spec` は `oof_power` しか認識せず、`fit_calibrator` は未知の method 名を
**黙って Platt にフォールバックする**(実測確認済み: `method='oof_isotonic'` → `platt`)。
つまり今のまま `pl_topk:oof_isotonic` を渡すと、**エラーも出さずに「Platt 校正の通常 holdout
arm」という別実験を測ってしまう**。

→ `oof_isotonic` の明示ルート + 別 recipe hash + **未知 calibration 名は raise**(これは
arm E とは独立に修正すべき既存バグ)。

## 4. 実行契約(事前登録)

- **窓**: 2019-01-01..2026-07-12(v2 gate-config の `eval_window` と一致)
- **gate**: `specs/073-eval-contract-correctness/gate-config.json` を**一切編集せず** hash
  `c3b33aff…` を `--gate-config-hash` で固定。閾値・subgroup・窓は変更しない
- **seed**: bootstrap seed は **20260713**(凍結 config 内の値。CLI `--seed` は config に
  上書きされる=以前の記述 20260712 は誤り)。model seed は別に 42
- **look は 1 回**。同一 arm の再実行は決定論再現(バイト一致確認)のみ許可。失敗したら
  E はこのコホートで閉じる。E2/E3 変種は新しい登録と新しいデータを要する
- **停止規則 / 採用基準(2026-07-25 ユーザー決定・実行前に固定)**:
  この窓での PASS は **「有望」止まりであって昇格根拠ではない**。PASS した場合の次段は
  **§7 の精度限定 prospective holdout(2026-07-13 以降の未使用レース)で CI 上限 <0 を
  確認すること**であり、それを経て初めて production 昇格を検討する。
  この窓で FAIL(REJECT / NO_DECISION)なら **E はこのコホートで閉じ**、prospective に進まない
- **過去 verdict は不変**: B の verdict、バグ下の旧 C/D artifact、修正後の C/D REJECT は
  append-only。旧 C/D は「バグにより無効」の supersession 注記を追加するのみで書換えない

### 4.0 実行前スモークの開示(2026-07-31・**事後追記**)

本番実行の前に、arm E の実行経路が壊れていないかを確かめる**機械的スモーク**を
2026-01-01..2026-03-31(858 レース・28 開催日・`--bootstrap-b 50`・**gate-config 未指定**)で
1 回走らせた(`out/085-armE-smoke.json`)。081 の `081-smoke-report.json` と同じ手順。

**開示すべき点**: この窓は事前登録窓 2019-01-01..2026-07-12 の**部分集合**であり、
「look は 1 回」に厳密には反する。スモークの出力(点推定 −0.013・CI ゼロ跨ぎ・
NO_DECISION=stat_guard_underpowered・`calib=True`)を実行者は見ている。

**それでも本番判定を有効とみなす根拠**: 閾値・窓・subgroup・seed・多重性の扱いは
すべて `073/gate-config.json`(hash `c3b33aff…`)に凍結されており、**スモークの結果を見て
動かせるパラメータが 1 つも無い**。スモークが与えたのは事前予想であって調整の余地ではない。
なおスモークは gate-config を渡していないため既定閾値で走っており(`gate_hash=44136fa355b3`
= 空 config)、本番のゲートとは別物である。

**この開示自体を契約に含める**: 以後、事前登録窓の部分集合で先にスモークする場合は
ここに記録する。記録しないスモークは禁止。

### 4.1 この窓の証拠としての格(**降格の明示**)

repo は 2008–2026 を **development evidence** と明文化している
([docs/plan/development-evidence.md](docs/plan/development-evidence.md))。`--confirmatory` は
「gate 設定の凍結」を意味し「未使用データ」を意味しない。

**E は C/D の結果(この窓)を見た後に着想された arm である。したがってこの窓での E の結果は
development evidence であり、confirmatory ではない。** 有効な family は A を参照とする
**B / C-D / E の 3 比較**。多重性の扱い:

- この窓での E は **kill filter**(悪い案を殺す)としてのみ使う。PASS しても「採用」ではない
- Holm 調整後の CI を **感度分析として併記**(主判定は事前登録の未調整ゲートのまま)
- **採用判定には未使用データが要る**(§7)

## 5. 製品化経路(勝った場合に何が要るか)

codex 3 の結論を採用: **(c) 標準 model_version の artifact 形状 + 074 型の evidence 規律**。
現状 arm E は**評価用ラッパーであって出荷可能なモデルではない**。

- `OofCalibratedPredictor` を pickle してはならない(session・共有行列・入れ子 predictor を
  巻き込む)。持続するのは **① full-history booster の `model.txt` ② OOF fit した isotonic を
  既存 `Calibrator` ラッパーに入れた `calibrator.pkl` ③ preprocessor ④ metadata**
- metadata に **`calibration_protocol="strict_past_oof_isotonic_v1"`・`booster_calib_frac=0.0`・
  OOF 分割 version / ブロック境界 / 件数 / hash・`oof_pred_from/through`・isotonic 閾値の
  checksum・paired report と gate-config の hash** を記録。
  **`calib_from/calib_through` を流用しない**(あれは連続 holdout 窓の意味)。
  `calibration_split_unit` も E には当てはめない(booster holdout の語彙)
- `FEATURE_VERSION` は **bump しない**(入力特徴は不変)。**新しい model_version を必ず採番**し、
  **candidate 固定**(自動 ACTIVE 化しない)
- **074/076/078 の manifest はそのまま使えない**(lgbm-063 に hard-pin・schema は two-gamma と
  stage-λ のみ)。OOF 生成/attestation の規律は流用し、E 用の evidence は別に作る

## 6. 想定される失敗モード(事前に明記・結果を見てから足さない)

- **OOF→final 転移ずれ**: 校正曲線は履歴の短い inner booster のスコア分布で学習され、
  full-history booster に適用される。isotonic は step 関数なので単一 γ より support ずれに敏感
  (C/D にも同じ構造があるが、E の方がリスクが大きい)
- **正規化後の周辺校正**: isotonic は per-horse 損失を最小化するが、適用後にレース内で
  再正規化するため、marginal calibration は保証されない
- **順位**: isotonic + clip + 正の定数除算は**順位を反転させない**が、plateau で同着(tie)を
  作りうる(厳密な狭義単調は保たれない)
- **field size 重み**: per-horse fit は多頭数レースを過大に重み付けする(race-equal の winner
  NLL と非対称)
- **primary の毀損**: 校正が直っても plateau で winner NLL / top2 / top3 が削られ、E が
  **別のゲートで落ちる**可能性

## 7. 未使用データでの確認(新しく判明した選択肢)

073 の prospective holdout は `DORMANT` だが、その **start_preconditions は ROI 仮説のための
もの**(pre-race odds capture)。**精度(winner NLL)仮説には発走前オッズは不要** — 将来の
レースと結果だけで足りる。

観測 CI 半幅 0.00218 @ 813 race-days からの外挿:

| 真の効果 | CI 上限 <0 に必要な race-days | JRA 実績(月 8–10 日)での期間 |
|---|---|---|
| −0.012(観測点推定) | ~27 | **約 3 か月** |
| −0.008 | ~61 | 約 7 か月 |
| −0.006 | ~108 | 約 12 か月 |

→ **精度限定の prospective holdout は現実的**。ECE は gate 上 CI を持たない点推定比較なので
小標本で不安定 = 主要エンドポイントは winner NLL、ECE は事前に別途規定が要る。

## 8. スコープ外(この feature に混ぜない)

閾値・窓・subgroup・seed の変更 / 過去 verdict の書換え / active モデルの切替・自動昇格 /
特徴スキーマ・値意味論・リーク境界の変更 / lgbm-063 の calibration manifest 流用 /
serving stage-discount・betting two-gamma の意味変更 / 074-076-078 の一括 activation /
過去データを prospective と称すること

## 9. 実施順序(codex 3 の推奨を採用)

1. **contract を凍結**(本 spec §3 の算法・artifact/provenance 形状・serving replay oracle)
2. **評価 arm E と evidence 経路だけを実装**(§3.5 の既存バグ修正を含む)+ テスト(§10)
3. **E を 1 回だけ実行**(この窓 = development evidence)
4. PASS した場合のみ、標準形状の candidate モデルを学習し **loaded-serving byte parity を証明**
5. 表示 top2/top3・betting 経路・prospective shadow を個別に確認
6. 明示的な昇格(単一 ACTIVE 不変条件 + rollback 記録)

## 10. 必須テスト(抜粋)

inner OOF の `max(train_date) < target_date`(同日複数レース含む)/ 通常レースで started 全馬・
正例ちょうど 1 / **同着で両馬が正例・レースを落とさない** / DNF・失格が負例 / 取消が行を作らない /
結果欠損で全 0 を捏造しない / **スコア空間パリティ(生 softmax で fit していることを極端値で証明)** /
適用後の Σ=1・順位非反転 / fold 間の状態リセット / 不十分 fold で identity + NO_DECISION /
`oof_power` と `oof_isotonic` の hash 差 + 未知 method で raise / 2 回実行で isotonic 閾値一致

## 11. 事前に潰しておく既存の穴(arm E とは独立)

- `fit_calibrator` が未知 method を黙って Platt にする(**実測確認済み**)→ raise に
- `paired-eval --confirmatory` が `--from/--to` を `assert_confirmatory` に渡していない
  → 窓の照合が効いていない([cli.py:1153](training/src/horseracing_training/cli.py:1153))
- gate-config の `bootstrap.alpha` が不活性(`paired_eval` は b と seed しか渡さない)

---

## 11. 判定(2026-07-31 実行・append-only)

artifact: `out/085-armE-verdict.json`(n_races 26,050 / n_eligible 26,006 / 813 race-days /
contract v2 / gate_hash `c3b33affec01` / race_id_set_hash `931d6699…`)

### 11.1 結果 = **ADOPT**(全ゲート PASS)

```
winner NLL diff = -0.012838   CI [-0.014835, -0.010920]   (uniform baseline 2.5956)
primary=True  stat_guard=True  recent=True  top_ni=True  calibration=True
```

**2×2 の 3 セルの対比(これが本 feature の結論)**:

| arm | booster | 校正器 | winner NLL diff | ECE(active→cand) | 判定 |
|---|---|---|---|---|---|
| B | 90/10 | isotonic holdout | −0.003813 [−0.00813, +0.00054] | 0.001895→0.002182 | NO_DECISION |
| C/D | full-history | OOF power(単一 γ) | −0.011879 [−0.01406, −0.00969] | 0.000803→**0.003010** | **REJECT**(calib) |
| **E** | **full-history** | **OOF isotonic** | **−0.012838 [−0.01484, −0.01092]** | 0.000785→**0.001043** | **ADOPT** |

**読み**: booster に直近 6 年を学習させるゲイン(−0.012〜−0.013)は校正器の族に依存しない実在の
効果。C/D が落ちたのは校正器側だけであり、単一 γ の power をノンパラメトリックな OOF isotonic に
差し替えると **ECE の劣化幅が +0.00221 → +0.00026(8.5 分の 1)** に縮み、非劣化幅 0.001 の内側に
収まる。順位ゲインは失われないどころか僅かに増える(−0.01188 → −0.01284)。
§6 で最大の懸念とした「OOF→final のスコア分布転移ずれで isotonic の step が壊れる」は**非発現**。

### 11.2 頑健性

- **ブロック幅感度**(診断・ゲートに AND しない): 2d/3d/4d/week のいずれでも CI 上限 ≤ −0.01083
- **critical subgroup 全 PASS**: `2026_only` [−0.01776, −0.00377] / `nk` [−0.00247, −0.00066] /
  `2026_nk` [−0.00231, −0.00029]。`canonical` [−0.00123, −0.00090] も PASS
- **多重性(§4.1 の感度分析)**: family = B / C-D / E の 3 比較。bootstrap replicate が artifact に
  永続化されていないため厳密な Holm 調整 CI は再計算不能。**正規近似**(implied SE 0.000999・
  α=0.05/3 → z=2.394)で **[−0.01523, −0.01045]**、ゼロを十分に外れる。
  *この 1 行は近似であって bootstrap 由来ではない* — 厳密値が要るなら replicate 永続化が先

### 11.3 **昇格しない**(§4 の停止規則をそのまま適用)

この窓は **development evidence** であり、E は C/D の結果を見た後に着想された arm である
(§4.1)。**PASS は「有望」止まりで昇格根拠ではない。** 次段は §7 の精度限定 prospective
holdout(2026-07-13 以降の未使用レース)で CI 上限 <0 を確認すること。

**現時点の未使用データ = 4 開催日 / 144 レース(2026-07-18〜07-26、結果は全件確定)。**
§7 の外挿では −0.012 の効果に必要なのは約 27 開催日 ≒ 3 か月。**現状はその約 15%** であり、
**いま prospective 判定を出すことはできない**。日次 ingest の蓄積待ちで、着手可能になるのは
おおむね 2026-10 以降。

### 11.4 この判定で閉じたこと / 開いたこと

**閉じた**: 「booster のデータ配分 × 校正器の族」の 2×2。4 セットすべてに判定がつき、
勝ちセルが 1 つ特定された。C/D の REJECT は「全期間 booster が駄目」ではなく
「power 校正が駄目」だったと確定した(旧 C/D verdict は書き換えず、この注記で supersede)。

**開いた**: 昇格経路(§5)。arm E は現状**評価用ラッパーであって出荷可能なモデルではない**
(`OofCalibratedPredictor` は pickle 不可)。prospective が通った後に §5 の標準 artifact 形状
(full-history booster の `model.txt` + OOF fit した isotonic を既存 `Calibrator` に入れたもの +
preprocessor + `calibration_protocol="strict_past_oof_isotonic_v1"` 等の provenance)で
新しい model_version を **candidate 固定**で採番し、loaded-serving byte parity を証明する。
