# Implementation Plan: 過去走の市場評価(人気)as-of 特徴 — 精度最優先モデル(B1)

**Branch**: `058-market-history-features` | **Date**: 2026-07-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/058-market-history-features/spec.md`

## Summary

過去走の人気(=オッズ由来の市場ランク)を strictly-before + 同日除外で as-of 集約した特徴群 `past_market`(4 列)を追加し、公開情報のみの baseline に対しフル walk-forward OOS で採否判定(win 採用 + **top2/top3 非悪化 MUST**)。採用時は production 構成(pl_topk+TE+isotonic)で「精度最優先」モデルを再学習・**非 active 登録**し、057 の切替基盤で意思決定支援モデル(default active)と共存させる。default の意思決定支援モデルは past_market を含めない(p⊥q 維持、憲法 II)。

de-risk spike(3-fold 2021–2023、binary、**baseline=features-014=059 相対能力を含む真の現行** vs +past_market)で win/top2/top3 全改善(win −0.00028・top2 −0.00045・top3 −0.00042、3/3 fold、win ECE 改善)を確認。**採番齟齬是正済**: 058 worktree を現 main(ed4113f=059)へ載せ替え features-014→**015**。相対能力との重複でゲインは旧 spike(features-013 baseline: win −0.00035)の ~60-80% に縮小したが正を維持。本 plan はフル OOS ゲートと共存運用まで詰める。**特徴の配線は spike で実装済み**(loader/past_market_features.py/materialize/registry・features-015・125 列)。

**058 の設計上の核心**: past_market は **repo 初の「市場データを意図的に使う」特徴**。従来の leak-guard は「モジュールソースに odds/popularity トークン禁止」の grep 型だったが、本 feature ではモジュールが正当に popularity を読む。よって leak-guard を **挙動型に転換**:(a) 今走の人気を変えても特徴不変(strictly-before)、(b) 同日・未来不変、(c) 特徴名は禁止トークン非含有(asof_mkt_*=041 の late_gain 方式)で既存のグローバル名トークン検査を通過、(d) 今走の人気/オッズそのものは model_input_features に含まれない。grep 型ソース検査は本モジュールに**適用しない**(正当に市場データを使うため)。

## Technical Context

**Language/Version**: Python 3.12(features/eval/training)

**Primary Dependencies**: pandas/numpy(as-of 集約)、LightGBM(予測)、scikit-learn(校正)、SQLAlchemy(read)

**Storage**: PostgreSQL 16(read-only for features)。**スキーマ変更なし・migration なし**(model_versions.display_name/purpose は 057/migration 0011 で既存)。

**Testing**: pytest + testcontainers(features/eval/training)。leak-guard は合成 Frames(make_frames)で挙動不変を検証。

**Target Platform**: バッチ(feature-eval CLI・train-evaluate CLI)。

**Project Type**: ML 特徴追加(features)+ 採用ゲート(eval)+ モデル運用(training/057 基盤)。

**Performance Goals**: as-of 集約は既存 023 idiom(recent-N rolling + merge_asof)= O(n log n)。materialize bit パリティ維持。

**Constraints**: リーク境界(strictly-before/同日除外/今走非特徴)・FEATURE_VERSION features-015・materialize parity・default モデルは past_market 非含有(p⊥q)・009 win→joint 不変・Unknown=NaN。

**Scale/Scope**: 影響 = features(loader/新モジュール/materialize/registry・配線済)+ features/tests(leak-guard/parity)+ eval(top2/top3 併記の採用判定=既存 harness 流用)+ training(採用時 production 再学習)。UI/API/DB 契約変更なし(057 基盤を利用)。

## Constitution Check

*GATE: Phase 0 前に PASS 必須。Phase 1 後に再確認。*

- [x] **I. データ契約**: raceId/2007+ 契約は既存ローダ踏襲。集計キーは既存 horse_id。ラベル不変。**PASS**
- [~] **II. リーク防止 (NON-NEGOTIABLE)**: 市場データ(人気)を使うため**本 spec で定義**(憲法 II の別 spec 要請を満たす)。past_market は **strictly-before + 同日除外 + merge_asof(allow_exact_matches=False)**、**今走の人気/オッズは非特徴**(過去 as-of のみ)、**今走人気を変えても特徴不変**(挙動型 leak-guard test)。**default 意思決定支援モデルには past_market を含めない**(p⊥q、FR-009)。grep 型ソース検査は本モジュールに非適用(正当に市場データ使用)だが、グローバル名トークン検査(model_input_features に odds/popularity 名を禁止)は**通過**(asof_mkt_* 命名)。全特徴に source/timing(PRE_ENTRY)/missing(NULL)記載済。**PASS(本 spec が II の要件充足)**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: フル walk-forward OOS・baseline 比較・ECE。**事前登録ゲート**(下記 Pre-registered gate)を Phase 0 で固定し結果を見て動かさない。**top2/top3 非悪化を MUST** に追加(ユーザー目的)。**PASS**
- [x] **IV. 確率整合性**: 予測は既存 predictor 経路(009 win→joint)不変。past_market は特徴のみ。Unknown=NaN(0埋め禁止)。**PASS**
- [x] **V. 再現性・監査**: FEATURE_VERSION features-014→**015**。materialize parity(bit 一致)。**popularity は race_horses 全列ハッシュに自動包含**(source_fingerprint が list(columns) をハッシュ=backfill 検知 fail-closed 自動)。採用モデルは model_version/feature_version/logic_version 記録。**PASS**
- [x] **VI. feature 分割規律**: スキーマ/API/migration 変更なし(057 の切替基盤・model_versions メタを利用)。UI 契約は 057 済み。**PASS(契約変更なし)**
- [~] **品質ゲート(codex second opinion)**: features/eval/採用ゲート/リーク境界/FEATURE_VERSION に触る MUST-codex 案件。**codex unavailable**(環境未インストール・本セッション複数回失敗)→ single-opinion + 下記セルフレビュー checklist(CLAUDE.md fallback)。**代替実施・記録**

### Pre-registered adoption gate(結果を見る前に固定・憲法 III)

- **窓**: フル walk-forward。`first_valid_year = FIRST_VALID_YEAR`(harness 既定)、`end_date = 2024-12-31`(最新の完全年、19-fold 相当=056 と同型)。
- **設定**: feature-eval PRIMARY = binary + platt(020–056 と同一の確立ゲート設定)。baseline = features-014(past_market group drop)、candidate = features-015(全群)。seed=42。
- **PRIMARY(win)**: 平均 win LogLoss 改善 かつ 平均 win ECE 非悪化(ece_tol=1e-3)、fold guards = strict majority(n_win*2>n_folds)・worst_fold_ece_tol=2e-3・worst_fold_dll_tol=5e-3。
- **MUST(ユーザー目的、追加)**: **top2/top3 の平均 LogLoss 非悪化**(cand ≤ base、同一 harness の Harville 導出)。悪化なら不採用。
- **SECONDARY(診断・採否に使わない)**: market_edge(市場超過は採否バーでない)。
- **production 確認(採用後)**: 採用と判定されたら production 構成(pl_topk+TE(jockey/trainer)+isotonic)で `model-eval`/`train-evaluate` を回し、win/top2/top3 の production 上の寄与を確認してから精度最優先モデルを登録(binary spike の限界寄与過大評価=020 教訓の是正)。

### セルフレビュー checklist(codex 代替)

| 観点 | リスク | 対応 |
|---|---|---|
| リーク(最重要) | 今走人気が特徴に混入 | strictly-before + 同日除外 + merge_asof(allow_exact_matches=False)。挙動型 leak-guard(今走/同日/未来の人気を変えても特徴不変)+ 過去人気を変えると特徴が変わる positive test。 |
| leak-guard の型転換 | 従来の grep 型を機械適用すると本モジュールで誤検知 | 本モジュールは grep 型ソース検査の対象外(正当に popularity 使用)。グローバル名トークン検査(model_input_features)は asof_mkt_* 命名で通過を確認。 |
| parity | FEATURE_VERSION/新列で materialize bit 不一致 | build_asof_features 単一源に結線済。実 DB parity(bit 一致)を検証。popularity は fingerprint 自動包含。 |
| default 汚染 | 意思決定支援モデル(default)に past_market が入り p⊥q 破壊 | default モデルは past_market group を drop して学習/serving(FR-009)。default 予測不変を確認(SC-005)。 |
| ゲート後付け調整 | 結果を見て閾値変更 | Pre-registered gate を plan に固定。binary spike の数値は de-risk のみで採否に使わない。 |
| 過学習/市場回帰 | p が市場のコピーに寄る | 精度最優先モデルは非 active・別用途。default は独立維持。market_edge は SECONDARY で監視。 |
| production 乖離 | binary spike が production 寄与を過大評価(020) | 採用後に production(pl_topk+TE)で再確認してから登録。 |

## Project Structure

### Documentation (this feature)

```text
specs/058-market-history-features/
├── plan.md              # This file
├── research.md          # Phase 0: 非自明判断(leak-guard 型転換・ゲート事前登録・共存)
├── data-model.md        # Phase 1: past_market 4 列・registry/fingerprint
├── quickstart.md        # Phase 1: feature-eval + parity + production 再学習の手順
├── contracts/           # (API 契約変更なし → 省略 or note)
└── tasks.md             # /speckit-tasks で生成
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── loader.py                 # (配線済) race_horses.popularity ロード追加
├── past_market_features.py   # (配線済・新) build_past_market_features — 4 列 as-of
├── materialize.py            # (配線済) build_asof_features に結線
└── registry.py               # (配線済) 4 列登録 + group past_market + FEATURE_VERSION 015

features/tests/unit/
├── test_past_market_leak.py  # 新: 挙動型 leak-guard(今走/同日/未来不変 + 過去 positive + 名前)
└── (materialize parity 既存テストが features-015/新列で緑になることを確認)

eval/ (変更なし想定 — 既存 harness が top2/top3 を計算; 採否スクリプトは feature-eval CLI + top2/top3 読み)
training/ (採用時のみ: model-eval/train-evaluate を production 構成で実行し精度最優先モデル登録)
```

**Structure Decision**: features への特徴追加(配線済)+ 新 leak-guard テスト。API/DB/UI 契約変更なし(057 基盤利用)。採用判定は既存 feature-eval + harness の top2/top3 併記。採用時のモデル登録・共存は 057 の set-model-label + predict-backfill を運用手順として使う。

## 非自明な設計判断(research.md 詳細)

1. **leak-guard の型転換(grep→挙動)**: 058 は正当に市場データを使う初の特徴。ソーストークン grep を本モジュールに適用せず、strictly-before の挙動不変 + クリーン命名 + 今走非特徴で守る。
2. **default モデルは past_market 非含有**(p⊥q 維持、FR-009): past_market は精度最優先モデル専用。default は drop_features で past_market を落として学習/serving。
3. **採用ゲートに top2/top3 非悪化 MUST を追加**: ユーザー目的が 1・2・3着のため。既存 harness が Harville 導出で計算済み=読み取りのみ。
4. **binary spike の限界寄与を鵜呑みにしない**: 採用後に production(pl_topk+TE)で再確認してから登録(020 教訓)。
5. **精度最優先モデルは非 active・共存**(057 FR-009): eval 合格 ≠ 自動昇格。default は意思決定支援のまま。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 市場データを特徴化(憲法 II の別 spec) | 過去市場評価は着順単独に無い履歴情報で top2/top3 を上げる(spike 実証) | 公開情報のみでは市場評価の履歴を取れない。憲法 II の要請どおり本 spec でリーク/タイミング/評価を定義=正当化 |
| FEATURE_VERSION bump(features-015) | 新特徴群でモデル入力が変わる | 版を据え置くと採用済みモデルの feature_hash と不整合。020–056 と同型の bump |
