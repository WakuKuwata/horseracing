# Implementation Plan: 市場残差型・精度最優先モデル (market-residual accuracy model)

**Branch**: `060-market-residual-model` | **Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/060-market-residual-model/spec.md`

## Summary

今走単勝オッズ由来の市場確率 q(devig vote-share)の log を pl_topk(race-softmax)の offset として与え、特徴量側は「市場からの残差」だけを学習する精度最優先モデルを追加する。default(active)モデルは学習・serving・予測値ともバイト不変。offset は特徴列ではないため FEATURE_VERSION 不変(features-015)。事前登録ゲート(q 単体 baseline・lgbm-058-acc の両方を同一制限母集団で上回る + top2/top3 非悪化)を全通過した場合のみ `lgbm-060-mkt` を非 active(candidate)登録する。フル実装前に spike で go/no-go(FR-009)。設計判断の詳細は [research.md](research.md) D1–D10。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: LightGBM(custom objective)、pandas/numpy、SQLAlchemy 2.0(既存スタックのみ、新規依存なし)

**Storage**: PostgreSQL 16(スキーマ変更なし・migration なし。race_horses.odds の read のみ追加)

**Testing**: pytest + testcontainers(training/serving/eval 既存スイート拡張)

**Target Platform**: ローカル CLI(学習・評価)+ 既存 serving 経路

**Project Type**: 既存マルチパッケージへの結線(training 中心、serving を薄く拡張)

**Performance Goals**: フル 19-fold pl_topk 再学習は既存実測 ~20 分級(vectorized 経路)。offset 加算は O(n) でビルド時間に影響なし

**Constraints**: default モデル byte-parity(非交渉)/ FEATURE_VERSION 不変 / feature_hash・serving 互換ゲート不干渉 / fail-closed(オッズ欠損の黙った縮退禁止)

**Scale/Scope**: 学習母集団 ~95 万行・~6.5 万レース(オッズ完全カバーに制限後の件数は spike で実測)

## Constitution Check

- [x] **I. データ契約**: PASS — raceId/ID 契約・2007+ に変更なし。ラベル名不変。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS(手続き遵守)— 憲法 II は「市場オッズの特徴量化は別 spec でリーク防止・利用可能タイミング・評価方法を定義してから」と規定し、本 spec/plan がその定義(058 と同型の手続き)。(a) default モデルは市場情報を一切使わず不変、(b) 市場情報は対象レース自身の単勝オッズのみ・結果は不読(挙動型 guard D8)、(c) 利用可能タイミング=「オッズ後」で closing-leaning の限界を FR-008 で開示、(d) offset は特徴スナップショットに混入しない。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — 既存 walk-forward ハーネス + 既存市場 baseline(eval/baselines.py)を再利用。ゲートは事前登録(FR-004)、spike go/no-go も事前登録(D9)。
- [x] **IV. 確率整合性**: PASS — offset 加算後も既存の同一 postprocess(softmax→isotonic→clip→Σ=1→Harville)を通す(FR-002)。
- [x] **V. 再現性・監査**: PASS — logic_version に `mkt=logq` マーカー、metadata に market_offset 定義・オッズソース・限界を記録(FR-008)。
- [x] **VI. feature 分割規律**: PASS — スキーマ・migration・API/OpenAPI 変更なし。UI 変更なし(057 の available_models に自然に載る)。
- [x] **品質ゲート**: 実施 — 提案段階で codex 1 回(residual/offset 型の推奨を採用)、具体設計で codex 2 回目(下記「Codex second opinion」に採否記録)。

## Codex second opinion(具体設計レビュー)

**1 回目(提案段階)**: 「q は生 feature でなく offset(市場残差学習)が推奨」「isotonic が市場補正器化するリスク」「オッズ取得時点の固定」— 全て採用済み(spec の設計方針に反映)。

**2 回目(具体実装設計)**: 実施済み(codex は調査トレースを返し最終整形レポート前に終了 — トレース内の具体的発見を以下に採否記録)。

| codex の発見/指摘 | 採否 | 反映 |
|---|---|---|
| LightGBM 4.6.0 実測: custom fobj の preds は init_score を**含む**、`predict(raw_score=True)` は**含まない**。閉包方式(D1)は既存コード上妥当 | **採用(確認)** | D1 維持。init_score 方式でも成立するが、閉包方式の「全 offset 演算が自前コード」の利点で確定 |
| 既存 `eval/baselines.py` の MarketBaseline は**欠損オッズを floor 補完**しており 060 の fail-closed と非互換。q baseline は専用実装が必要 | **採用** | D5 修正: q baseline は既存クラス流用でなく、制限母集団上で q をそのまま assemble 経路に通す専用 baseline を実装(欠損補完なし=母集団制限で欠損は存在しない) |
| 評価ハーネスは predictor ごとに独立 evaluate で、共通レース集合フィルタを持たない。「同一 fold・同一レース集合」は明示構築が必要 | **採用** | D5 修正: オッズ完全カバーの race_id 集合を先に確定し、3 者(candidate/q baseline/058-acc)の evaluate に同一集合を渡す比較ドライバを実装 |
| 既存 `train-evaluate` はゲート通過→ACTIVE 保存の汎用ロジック。「通過しても非 active(candidate)」は新分岐 | **採用** | 060 用に「candidate 固定保存」経路を追加(058-acc 登録時の手順を機械化) |
| q の single source of truth は `probability.market_odds.market_implied_win_probs()` に統一すべき(軽い重複が複数箇所に既存) | **条件付き採用** | training→probability の import が境界テストで許容されるか確認し、可なら import。不可なら training/market_offset.py に純関数実装 + probability 実装との定義同一性テストで固定 |
| live 側の既存 odds guard は「recommendation のみ停止・prediction は続行」。060 モデルは prediction 自体を止める新 fail-closed 経路が必要 | **採用** | serving/live の typed skip は既存 guard の流用ではなく market_offset モデル専用の分岐として実装(D7 に明記) |
| `ServingModel.raw_predict` は PL 系で softmax 済み確率を返す型。offset はこの型を破る | **採用** | serving 側は raw(score)段階で offset を加算してから softmax する経路に整理(predictor/model_loader の責務分担を tasks で固定) |
| 既存 OOF TE は「自分のラベルを使わない」は担保だが fold 境界外の未来ラベル排除の厳密検証は未整備(既存の潜在課題・060 の相対比較には直接影響なし) | **保留(記録のみ)** | 060 スコープ外。別 feature 候補としてメモ |

残リスク: codex の最終整形レポートは未出力のため、上記はトレースからの抽出。設計否定の指摘は無く、D1–D10 の骨格は維持。

## Project Structure

### Documentation (this feature)

```text
specs/060-market-residual-model/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0(D1–D10)
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── market-offset.md # offset 定義・metadata 契約・監査マーカー
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
training/src/horseracing_training/
├── market_offset.py         # NEW: q/offset 純関数(devig, log-clip, レース単位 fail-closed 判定)
├── cond_logit.py            # pl_topk_objective に offsets 対応(閉包内 preds+offsets)
├── win_model.py             # WinModel.fit/predict に offsets 引数(None=既存バイト不変)
├── dataset.py               # TrainingMatrix.frame に補助列 mkt_odds(特徴列外)
├── predictor.py             # LightGBMPredictor(market_offset=True opt-in): fit/calib/predict で offset 構成
├── artifacts.py             # metadata に market_offset 記録
└── cli.py                   # train-evaluate / model-eval に --market-offset

serving/src/horseracing_serving/
├── model_loader.py          # metadata.market_offset 透過(無し=既存後方互換)
├── pipeline.py              # market_offset モデル時のみ対象レースのオッズ→q→offset、欠損=typed skip、lv に mkt=logq
└── predictor.py             # predict_race に optional offsets

eval/                        # 変更なし想定(市場 baseline・ハーネスは既存を再利用)
```

**Structure Decision**: training 中心の薄い結線。features パッケージは**不変**(offset は特徴列ではない=FEATURE_VERSION/feature_hash/materialization 不干渉)。db/api/front/admin/betting/ops 不変(スキーマ・API 契約変更なし)。

## Complexity Tracking

違反なし(スキーマ・API・特徴量スキーマすべて不変。市場オッズ利用は憲法 II の規定手続きに従う別 spec として本 feature 自体が該当)。
