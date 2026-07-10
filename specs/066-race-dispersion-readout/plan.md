# Implementation Plan: race dispersion & p/q divergence readout(荒れ度・意見差の読み計器)

**Branch**: `066-race-dispersion-readout` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/066-race-dispersion-readout/spec.md`

## Summary

既存の per-race モデル勝率 p(校正込み)と市場 vote-share q=(1/odds)/Σ(1/odds) を **race-level に要約**して、ユーザーが「買う/買わない」「人気馬/人気薄」を自分で判断するための **read-only 表示計器**を追加する。新しい予測エッジではなく既存 p/q の関数。

- **軸A(決着集中度=荒れる/荒れない)**: q由来の正規化エントロピーを5段バンド(堅い〜波乱含み)+ 生数値(本命勝率 max(q)・上位3頭累積)。校正済み p(048 two_gamma 経路)由来は q との差分のみ。q 欠損は unavailable(p フォールバックしない)。
- **軸B(p vs q 意見差=人気/穴の材料)**: race-level 中立サマリ + 既存 040 per-horse `divergence_band`(無改変)+ 全馬 p/q 展開の3層。
- **US3 診断(SECONDARY)**: 047 segment_edge 同型の walk-forward OOS でバンド別 realized chaos を CI 付き検証(採否ゲートにしない)。

技術方針: **スキーマ変更ゼロ・migration なし**。API は GET 純追加(021 selection / 040 divergence の隣に race-level オブジェクトを足す)。境界は凍結窓の5分位を artifact に記録(055/064 同型、logic_version/ファイル)。全表示派生値はモデル特徴・training に流入しない(behavioral leak-guard)。codex 設計レビュー済(7指摘全採用、[research.md](research.md))。

## Technical Context

**Language/Version**: Python 3.12(api/eval/training)、TypeScript + React + Vite(front)

**Primary Dependencies**: FastAPI + pydantic(api)、numpy(エントロピー/分位)、既存 `horseracing_probability`(009 joint / 010 q)、LightGBM(eval の予測器注入は training 側、eval は predictor-agnostic)、Vitest + RTL + MSW(front)

**Storage**: PostgreSQL 16(**read-only・スキーマ不変**)。境界 artifact は parquet/JSON ファイル + logic_version(DB 書込なし)

**Testing**: pytest + testcontainers(api/eval)、Vitest(front)、openapi drift-check

**Target Platform**: Linux server(api)、SPA(front)

**Project Type**: web(既存 api/ + front/ + eval/ + training/ に純追加)

**Performance Goals**: race-level 集計は read-time で O(頭数)。1レース描画に追加レイテンシ数 ms 未満(021/040 と同オーダー、per-horse 純関数の集約のみ)

**Constraints**: スキーマ変更ゼロ・migration なし・API GET-only・OpenAPI 純追加(drift-check 緑)・betting/training を api から import しない・全表示派生値を特徴/training に戻さない

**Scale/Scope**: api 数百行(selection.py 拡張 + 新 dispersion.py + router/queries 透過)、eval 1 モジュール(band diagnostic)、training CLI 1 サブコマンド(境界フィット + 診断)、front 2-3 コンポーネント

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: PASS。raceId/ID/ラベル契約に触れない(既存 races/predictions/odds を read するのみ)。q は既存 010 定義、p は既存 predictions。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS。全表示派生値(集中度・バンド・q集計・乖離サマリ)は **read-time 表示専用で、モデル入力特徴・training 経路に流入しない**。token 禁止(registry/materialized columns)+ import-graph ガード + **behavioral 不変テスト**(表示軸の計算を変えても model input features と decision-support 経路の選択 p がバイト不変)で機械固定。「全 odds 変更が全モデル不変」は主張しない(060 market-offset candidate があるため)、主張は「本 feature の新 display 集計が feature/training に入らない」に限定。市場 q は特徴化しない(p≠q)。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS(SECONDARY として)。US3 診断は eval walk-forward OOS 由来・独自指標を作らず 047 の realized 集計を流用。**バンドは採否ゲート・閾値調整に使わない**(047 SECONDARY 規律)。境界は結果非参照でフィット。
- [x] **IV. 確率整合性**: PASS。p・q は同一 canonical field(取消除外・再正規化=021 の canonical field をそのまま使用)。q 集計前に取消除外。Unknown(q欠損)を 0 で埋めず unavailable にする。
- [x] **V. 再現性・監査**: PASS。境界 artifact に metric/頭数バケット/フィット窓/as-of/version 記録。odds_as_of/odds_source を表示。q 集計は market-derived 表示として 015/021 の pseudo/source バッジ経路(pseudo_roi 流用禁止・真確率示唆禁止)。
- [x] **VI. feature 分割規律**: PASS。API 契約(OpenAPI 純追加)を front 実装前に確定。read-only 厳守(全 path GET のみのテスト)。スキーマ不変(migration head 変更なし)。
- [x] **品質ゲート**: PASS。codex:codex-rescue の設計レビュー取得済(7指摘全採用・[research.md](research.md) に両案差分と採用根拠を記録)。

**違反ゼロ**(Complexity Tracking 記入不要)。

## Project Structure

### Documentation (this feature)

```text
specs/066-race-dispersion-readout/
├── plan.md              # This file
├── research.md          # Phase 0: 設計判断 + codex レビュー突合
├── data-model.md        # Phase 1: read-time エンティティ(非永続)+ 境界 artifact
├── quickstart.md        # Phase 1: 実 DB E2E 検証手順
├── contracts/           # Phase 1: OpenAPI 純追加差分
└── tasks.md             # Phase 2(/speckit-tasks で生成、本コマンドでは作らない)
```

### Source Code (repository root)

```text
api/src/horseracing_api/
├── dispersion.py        # 新規: 軸A(集中度指標・バンド割当)+ 軸B(race-level 乖離サマリ)の read-time 純関数
├── selection.py         # 既存: market_win_probs/canonical_win_probs/divergence_band を再利用(divergence_band は無改変)
├── schemas.py           # 既存 predictions 応答に race_dispersion / race_divergence の nullable オブジェクト純追加
├── queries.py           # 既存: canonical_win_odds/run_predictions を透過利用
└── routers/…            # 既存 predictions router に純追加(新エンドポイント不要、predictions 応答に同梱)

eval/src/horseracing_eval/
├── dispersion_bands.py  # 新規: 凍結窓5分位の境界フィット + walk-forward OOS realized-chaos 診断(segment_edge 同型)
└── segment_edge.py      # 既存: expanding_folds/収集機構を参照(改変しない)

training/src/horseracing_training/
└── cli.py               # 新規サブコマンド dispersion-bands(境界フィット出力 + 診断、--persist は diagnostic_runs 流用は deferred)

front/src/
├── components/
│   ├── RaceDispersionPanel.tsx   # 新規: 軸A(5段バンド + 生数値 + p差分 + pseudo/source バッジ + unavailable 状態)
│   ├── RaceDivergenceSummary.tsx # 新規: 軸B 一言サマリ(中立文言)
│   └── HorseEntriesTable.tsx     # 既存: 040 divergence バッジ + p/q 展開(軽微結線のみ、既存挙動不変)
├── lib/dispersionLabels.ts       # 新規: バンド enum→表示ラベルの単一対応表
└── api/schema.d.ts               # 自動生成(openapi 純追加を反映、drift-check 緑)

features/tests/unit/
└── test_feature066_leak_guard.py # 新規: 表示軸トークンが registry/materialized_columns に出現しない token-ban テスト(read のみ・features コードは変更しない)
```

**Structure Decision**: 既存 web 構成(api/eval/training/front)への**純追加**。新パッケージ・新テーブルなし。読み計算は api の新 `dispersion.py`(021/040 と同じ read-time 純関数層)、診断は eval の新 `dispersion_bands.py`(047 predictor-agnostic 収集を参照)、境界フィット/診断駆動は training CLI。front は新パネル 2 点 + 既存表の軽微結線。

## Complexity Tracking

> 違反なし。記入不要。
