# Feature 085: Arm E — full-history booster + strict-past OOF isotonic

**状態**: Draft(設計のみ・未実装・未事前登録)
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
