# Implementation Plan: Conditional-logit (race-softmax) 目的関数

**Branch**: `039-conditional-logit-objective` | **Date**: 2026-07-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/039-conditional-logit-objective/spec.md`

## Summary

win モデルの学習目的関数に **conditional-logit(レース内 softmax / Plackett-Luce top-1)** を追加する。各馬スコア s_i をレース内で softmax して `p_i = exp(s_i)/Σ_j exp(s_j)`、損失 `−log p_winner` を直接最適化する custom objective(LightGBM 4.x `params["objective"]=callable`)。1レース1勝の構造を学習に埋め込み、出力 softmax が自然に Σ=1(009 と整合)。**新特徴なし・スキーマ変更なし・FEATURE_VERSION 不変(features-011)**。036 と同型のモデリング変更で、変わるのは勾配計算のみ。de-risk spike で binary を全指標・全 3 fold で上回り済み。既定は binary(後方互換)、opt-in で cond_logit。18-fold OOS で binary(lgbm-036)を上回れば lgbm-039 として採用。

**技術アプローチの核心**: cond_logit は fit/predict 両方でレース group(race_id 配列)を要する(binary は不要)。fit は model 行をレース連続に整列し group sizes を custom objective の closure に渡す。予測は raw_score → group ごと softmax。serving は常に1レース単位なので softmax はそのレース内で自然に閉じる。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: LightGBM 4.x(custom objective = `params["objective"]=callable`、旧 `fobj` は不使用)、numpy、pandas、scikit-learn(isotonic)

**Storage**: PostgreSQL 16(read-only、スキーマ変更なし)。artifacts は `../artifacts/model_versions/lgbm-039/`(model.txt / calibrator.pkl / preprocessor.pkl / metadata.json)

**Testing**: pytest(training unit + serving)。合成データ(DB-free)で objective 単体、実 DB で 18-fold OOS

**Target Platform**: Linux/macOS server(CLI + serving)

**Project Type**: ML training/serving 拡張(training パッケージ中心、serving 少改修、eval は predictor-agnostic 維持)

**Performance Goals**: cond_logit の custom objective は per-boost で全 model 行を group ごと softmax(O(n))。18-fold OOS が binary と同オーダーで完走(数十分)

**Constraints**: 憲法 II リーク防止 / III 評価先行 / IV 確率整合 / V 再現性・監査。既定 binary は bit 後方互換。cond_logit の数値安定(softmax の max 減算)必須

**Scale/Scope**: 学習行 ~90万(2007-2025)、~65k レース。cond_logit の group = レース単位

## Constitution Check

Constitution v1.0.0 ゲート:

- [x] **I. データ契約**: raceId 12桁・2007+・id_mappings 経由・ラベル 1着率系。**本 feature は目的関数のみ変更、データ契約に一切触れない**(PASS)。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 目的関数変更は勾配計算のみ。データ経路(as-of 特徴・OOF TE・chronological fold・training-only encoder)は 036 と不変。cond_logit の group は race_id のみ依存で結果非参照。勝敗ラベルは損失計算のみに使用(特徴・group に非流入)。odds/今走結果は特徴にしない。leak-guard test で担保(PASS)。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 18-fold walk-forward OOS 採用ゲートを事前登録(spec SC-001/002)。閾値・fold ガードは 020/023/036 と同一。数値を見てから動かさない(PASS)。
- [x] **IV. 確率整合性**: cond_logit 出力 = レース内 win 確率で 009 win→joint にそのまま渡せる(むしろ binary+後付け正規化より構造整合)。0≤…≤1・レース内 Σ=1・取消除外再正規化・Unknown 維持は不変(PASS)。
- [x] **V. 再現性・監査**: objective を metrics_summary/metadata に記録。LightGBM deterministic/num_threads=1/seed 固定で同一データ再現。artifacts に model+calibrator+encoders(PASS)。
- [x] **VI. feature 分割規律**: UI/API 契約に触れない(serving 予測の供給元が変わるだけで 014/021 契約不変)。DB スキーマ不変(PASS)。
- [x] **品質ゲート**: 目的関数の校正統合・group 処理は非自明 → `codex:codex-rescue` で second opinion 取得(plan の Design Decisions に両案差分・採用根拠を記録)。

**Gate result: PASS**(スキーマ変更なし・新テーブルなし・リーク境界不変・feature 列不変)。

## Design Decisions(核心設計・codex second opinion 反映)

### D1: cond_logit custom objective
- `params["objective"] = fobj(preds, dataset) -> (grad, hess)`。closure に group sizes(model 行のレース連続整列に対応)を保持。
- 各 group で `v = preds − max(preds); p = softmax(v)`(数値安定)。`grad = p − y`、`hess = max(p*(1−p), 1e-6)`(multinomial 対角近似 = LightGBM の要求する2階近似)。
- 学習行整列: model_df を race_id で安定ソート → group sizes。`init_score`=0(boost_from_average 無効、softmax(0)=一様スタート)。

### D2: fit/predict の group 引き回し
- `WinModel.fit(X, y, *, categorical_cols, group_ids=None)`: cond_logit 時 group_ids(race_id, X 行順)必須。内部でソート+group sizes。
- `WinModel.predict(X, *, group_ids=None)`: cond_logit は `booster.predict(raw_score=True)` → group_ids ごと softmax。binary は現行(predict_proba[:,1]、group_ids 無視)。
- predictor.fit: model 行の race_id を group_ids として渡す。calib 行の予測も calib race_id を group_ids に(校正 fit 用)。

### D3: 校正統合(最大論点、codex 是正反映)
- cond_logit の calib raw 予測 = **レースごと**の softmax 確率(calib_df 複数レース → race_id group で **必ずレース単位に区切って** softmax。calib 全体で跨って softmax する事故を禁止)。
- **codex 是正(重要)**: softmax 出力は既に Σ=1 の条件付き確率。per-horse isotonic は各馬を独立に単調変換するため変換後は一般に Σ≠1 で、009 再正規化後は「厳密な isotonic 校正済み確率」でなく **校正風の再配分(heuristic)**。binary(独立確率→009 正規化)と校正の意味が異なる。
- **対応**: 採用評価で **(a) softmax-only(校正なし)vs (b) softmax→isotonic→009 再正規化** を 18-fold OOS で必ず両方測り、良い方を採る。(b) を採る場合は metadata に heuristic と明記。Σ=1 を保つ **temperature/power scaling(race-aware calibration)** は代替候補として research に記録(まず既存 isotonic infra で通るか確認、温度校正は deferred)。
- predict_race(1レース): softmax → (採用した校正)→ 009 で Σ=1 再正規化。**binary baseline との比較は両者とも最終 postprocess 後の確率で行う**(codex)。**採否は 18-fold win LogLoss(PRIMARY)+ ECE で機械判定**、winner-NLL は SECONDARY。

### D4: serving(codex 反映 — 経路一致が最重要)
- `model_loader` の S に objective 追加。`raw_predict(X)`: cond_logit は **softmax(booster.predict(raw_score=True)) over X(=1レース)**。binary は現行 predict_proba[:,1]。predict_race の後段(calibrator→009)不変。
- **codex 最大リスク**: predict の意味が objective で「独立馬確率」↔「レース内 softmax 確率」と変わる。**early stopping / OOS 評価 / calibration / serving の全経路で同一の postprocess(group softmax → 校正 → 009)を通す**こと。ズレると spike の改善が本番で再現しない。metadata に objective/postprocess/calibrator/renormalize を必ず保存。cond_logit は predict/calibration/eval の全入口で group(または single-race)入力を必須化。
- feature_hash は feature_cols(features-011)由来で不変。ただし **objective 変更は model_version(lgbm-039)/artifact に反映**(feature_hash でなく model_family/objective メタで区別、codex)。

### D5: エッジケース(codex 是正反映 — sum(y)=1 前提を守る)
- **sum(y) != 1 の group は損失定義(top-1)を壊す** → **学習・校正から除外**(実装は grad/hess=0 で中立化 = 勾配非寄与):
  - 勝ち馬不在(y 全 0、全 DNF/result 無し): 除外。
  - 同着(y 和>1): 稀。除外(or 勝ち k 頭に 1/k を割当て sum(y)=1 に正規化してから grad=p−y。まず除外を既定、1/k は代替)。
- 1頭立て: softmax 自明 p=1、学習信号ゼロ。学習は中立、**推論時の1頭立ては確率1の特別処理**。
- started/scratches フィルタは training と serving で一致させる(Σ=1 の母集団を揃える、codex)。sample_weight は本 feature では未使用(1頭1行、レース規模バイアス論点は deferred)。
- 評価 winner-NLL は「勝ち馬ちょうど1頭」レース限定(spike 同基準)。
- **row/race 同期(codex)**: TE 適用後に X/y/race_id を stable sort で同期させて group sizes を作る。race 単位 split で同一 race が model/calib や OOF fold 境界を跨がないことを assert。
- HPO×cond_logit: 本 feature では非対応(spec deferred)。hpo=True かつ cond_logit は既定 params にフォールバック or 明示エラー。

## Project Structure

### Documentation (this feature)

```text
specs/039-conditional-logit-objective/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── objective.md     # Phase 1
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
training/src/horseracing_training/
├── win_model.py         # objective 追加 + group 対応 fit/predict
├── cond_logit.py        # (新) softmax/grad/hess・group 整列ヘルパ(単体テスト対象)
├── predictor.py         # fit: group_ids 引き回し / calib 予測に group / objective パススルー
├── artifacts.py         # objective を metadata/preprocessor に記録
└── cli.py               # train-evaluate に --objective フラグ

serving/src/horseracing_serving/
├── model_loader.py      # objective フィールド + raw_predict の cond_logit 分岐
└── predictor.py         # (後段不変、raw_predict 経由で cond_logit 対応)

training/tests/            # cond_logit 単体・後方互換・校正統合
serving/tests/             # cond_logit serving 予測・feature_hash 整合
```

eval は predictor-agnostic を維持(winner-NLL/top1 診断は harness に任意追加、training 非依存)。

## 実装フェーズ概要

1. **P1 core**: cond_logit.py(softmax/grad/hess/group)+ WinModel objective 対応 + 単体テスト。
2. **P1 predictor 統合**: fit/predict の group 引き回し + 校正統合 + 後方互換テスト(binary bit 不変)。
3. **P1 leak/確率**: leak-guard(group 結果非参照・今走変更で不変)+ 009 不変。
4. **P2 serving**: model_loader/predictor の cond_logit 対応 + feature_hash 整合。
5. **P1 採用判定**: cli --objective + 18-fold OOS → AdoptionReport → 採否 → lgbm-039 昇格 or ブランチ保全。
6. **Polish**: lint/test 緑・CLAUDE.md 更新・codex 反映確認。

## Complexity Tracking

スキーマ変更なし・新テーブルなし・新依存なし。既存 infra への objective 分岐追加のみ。複雑度の増分は cond_logit の group 引き回し(fit/predict/serving で race_id を通す)に限定。
