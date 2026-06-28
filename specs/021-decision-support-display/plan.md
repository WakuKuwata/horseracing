# Implementation Plan: 意思決定支援の表示強化 (Decision-Support Display)

**Branch**: `021-decision-support-display` | **Date**: 2026-06-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/021-decision-support-display/spec.md`

## Summary

read-only の意思決定支援（014 API + 015 front）を、市場超過を目的とせず「正直で正確な情報提示」へ強化する。(US1) 各馬のモデル p と市場 q を**同一 canonical field**上で併記し乖離を中立提示、(US2) walk-forward OOS 由来の校正（reliability）を可視化、(US3) リーク安全な「データ裏付け（条件カバレッジ）」を併記。スキーマ変更なし（q は既存 win オッズから算出、reliability は既存 `model_versions.metrics_summary` JSONB に OOS bins を追記して read）。014 は read-only 厳守、front 型は openapi 自動生成 + drift-check。

## Technical Context

**Language/Version**: Python 3.12（api/eval/training/probability）、TypeScript + React 18 + Vite（front）

**Primary Dependencies**: FastAPI/pydantic（api、既存 014）、horseracing-probability（009 joint / 010 `market_implied_win_probs`）、numpy（eval reliability）、openapi-typescript / Vitest / React Testing Library / MSW（front、既存 015）

**Storage**: PostgreSQL 16（read-only アクセス）。新規テーブルなし。`model_versions.metrics_summary`（既存 JSONB）に walk-forward OOS reliability bins を追記して再利用。

**Testing**: pytest + testcontainers（api/eval）、Vitest + RTL + MSW（front）。leak-guard test、契約 drift-check test、pseudo ラベル invariant test。

**Target Platform**: Linux server（API, uvicorn）+ ブラウザ SPA（front）

**Project Type**: web（API backend + SPA front）+ eval ライブラリ拡張

**Performance Goals**: API read-only エンドポイント p95 < 200ms（reliability は事前計算値の read、q は O(頭数) の純計算）

**Constraints**: read-only 厳守（新規書き込み経路なし）、スキーマ変更なし、リーク境界（表示派生値をモデル特徴に戻さない）、p≠q を端から端まで分離

**Scale/Scope**: 既存データ（2007–2024, 62k races）。新規 API フィールド/エンドポイント 数個 + front コンポーネント 3 系統。

## Constitution Check

Constitution v1.0.0 ゲート:

- [x] **I. データ契約**: `raceId` 12桁・2007+ は既存契約を踏襲。新規 ID 結合なし。ラベルは内部 win/top2/top3、表示は日本語。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: q（市場由来）・reliability（過去結果由来の診断）・データ裏付け（事前情報のみ）はすべて**表示専用の一方向出力**で、モデル学習特徴に戻さない（leak-guard test, FR-019）。データ裏付け指標は対象レースより前の情報のみ・結果/オッズ不使用（FR-010）。市場オッズは引き続きモデル特徴にしない。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: reliability は eval の walk-forward OOS 計算に基づき（in-sample 不可, FR-006a）、独自指標を作らない。データ裏付け指標は過去 OOS データで「裏付け弱→校正悪化」の妥当性を確認してから採用（不定なら US3 defer, FR-012）。**PASS**
- [x] **IV. 確率整合性**: p は 009 canonical field（スクラッチ除外＋再正規化）。q を**同一 canonical field**で算出し p−q を整合させる（母集団不一致時は乖離表示抑制, FR-001a）。win→joint は 009 を維持。**PASS**
- [x] **V. 再現性・監査**: model_version/logic_version/computed_at/as_of、オッズ source（確定/事前推定）、EV の控除率と出典、reliability の model_version/期間/件数/出典を画面表示（FR-015/018, FR-007）。pseudo（q/q'/推定オッズ/pseudo-ROI/recomputed 校正）は単一 PseudoBadge 経路でラベル（FR-014）。**PASS**
- [x] **VI. feature 分割規律**: 新表示データは先に 014 API 契約（フィールド/nullability/source/監査/警告セマンティクス）を確定してから front 実装。front 型は committed openapi から自動生成 + drift-check（FR-013）。014 は read-only 厳守（新規書き込みなし）。**PASS**
- [x] **品質ゲート**: 新規 spec として `codex:codex-rescue` の second opinion 実施済み。10 リスク＋抜け表示を spec/plan に反映（spec「codex レビュー所見」、research R1–R10）。本 plan の非自明判断（reliability の OOS 出所、US3 指標）も下記に根拠記録。**PASS**

スキーマ変更なし・憲法違反なし → Complexity Tracking 不要。

## 主要設計判断（codex second opinion 反映）

1. **US1: p と q を predictions エンドポイントに co-locate**（odds でなく predictions に q を追加）。理由: p の canonical field（`canonical_win_probs`）はこのエンドポイントで確定するため、同じ population で q を算出すれば母集団不一致（codex R1, IV）を構造的に防げる。q は `market_implied_win_probs`（010）を canonical field の win オッズに適用し再正規化。生 q のみ（FL 補正 q'(013) の併記は deferred、足す場合は別フィールド+ラベル, R4）。p−q は front 側で 2 フィールドから算出し中立提示（profit 言語/色/ソート禁止, R3）。
2. **US2: reliability は walk-forward OOS を `metrics_summary`(既存 JSONB) に事前永続化 → API は read のみ**。理由: 永続化済み serving 予測は過去レースに対し in-sample 楽観（codex R2）。eval harness が walk-forward OOS で算出する reliability bins（予測平均/実現勝率/件数, ECE）を adoption 時に `metrics_summary` へ追記し、API はそれを read（スキーマ変更なし、API は学習を走らせない、model_version スコープ R8）。少数 bin は件数表示＋抑制（R5）。
3. **US3: 「データ裏付け（条件カバレッジ）」に限定**（汎用「信頼度」を不採用, codex R6/verdict）。指標案: 馬の過去出走数（Unknown=新馬→裏付け弱）+ field_size をベースにした粗いカテゴリ（弱/中/強）。リーク安全（事前情報のみ）。**採用条件**: 過去 OOS データで「裏付け弱い群は校正/誤差が悪い」と確認できること。確認できなければ US3 を defer（spec FR-012 で明記済）。

## Project Structure

### Documentation (this feature)

```text
specs/021-decision-support-display/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── api.md
└── tasks.md             # /speckit-tasks で生成
```

### Source Code (repository root)

```text
api/src/horseracing_api/
├── routers/predictions.py   # US1: HorsePrediction に market_win_prob(q) + data_backing 追加
├── routers/calibration.py   # US2: NEW /models/{model_version}/calibration（reliability read）
├── schemas.py               # q / reliability / data_backing の pydantic スキーマ
├── selection.py             # canonical field で p と q を同一母集団算出するヘルパ
└── queries.py               # model_versions.metrics_summary 読取

eval/src/horseracing_eval/
└── harness.py               # US2: walk-forward OOS reliability bins を EvalResult/summary に出力

training/src/horseracing_training/
└── artifacts.py / cli.py    # adoption 時に reliability bins を metrics_summary へ永続化（既存経路に追記）

front/src/
├── components/PQCompare.tsx        # US1: p/q 併記（中立提示・PseudoBadge）
├── components/CalibrationChart.tsx # US2: reliability 曲線
├── components/DataBackingBadge.tsx # US3: データ裏付けバッジ
└── (openapi.json 再生成 + 生成型 + drift-check)

tests:
api/tests/   # q 母集団一致・read-only・reliability read・404/empty・leak 不在
eval/tests/  # reliability bins 算出（OOS, 件数, 少数 bin 抑制）
front/tests/ # 併記表示・pseudo ラベル invariant・中立提示(色/ソートなし)・型 drift
```

**Structure Decision**: 既存の web 構成（api backend + front SPA）+ eval ライブラリ拡張。新規パッケージ・新規テーブルなし。014 は read-only のまま US1 フィールド追加と US2 read エンドポイント追加。eval/training は reliability bins の算出・永続化のみ（学習ロジック不変）。

## Complexity Tracking

> 憲法違反なし・スキーマ変更なしのため記載不要。
