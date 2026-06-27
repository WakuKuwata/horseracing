# Implementation Plan: ライブ serving（未開催レースの予測・推奨生成）

**Branch**: `019-live-serving` | **Date**: 2026-06-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/019-live-serving/spec.md`

## Summary

新規 `live/`（`horseracing-live`）を**結線層**として追加し、guard → scrape(008) → run_serving(006) →
recommend(011/016) → prospective ログ を実行する。**核心**: `run_serving` は既に as-of 特徴量（結果非参照・
同日除外・result-pending future race 安全）で予測するため、019 は新規予測ロジックを足さず既存の leak-safe
経路を再利用する＝リーク面を増やさない。pre-race オッズで 010 推定 → 011/016 推奨（estimated=double-pseudo、
使用オッズ値を保存）。結果が無いため eval は **p パリティ + リーク境界 + prospective ログ**。スキーマ変更なし。
codex top-3（fail-closed / 使用オッズ値保存 / p パリティ分離）+ cutoff 補正（post_time 多く null→race_date）を機構化。

## Technical Context

**Language/Version**: Python 3.12（uv）

**Primary Dependencies**: 新規 `horseracing-live` → `horseracing-scrape`(008 scrape_entries/scrape_odds) /
`horseracing-serving`(006 run_serving) / `horseracing-betting`(011 generate_exotic_recommendations / 016
generate_kelly_recommendations) / `horseracing-db`。SQLAlchemy 2.0。新規 ML/確率ロジックなし（既存再利用）。

**Storage**: PostgreSQL 16。読: races/race_horses/race_results/race_predictions。書: prediction_runs/
race_predictions（run_serving）、recommendations（011/016, append-only）、race_horses(entries+odds)/
ingestion_jobs（008）。**スキーマ変更なし**（head 0006）。

**Testing**: pytest + testcontainers。合成データで fail-closed ガード・予測（as-of/Unknown/Σ整合/リーク境界）・
推奨（estimated/使用オッズ保存/shadow）・p パリティ（live==retrospective）・決定論。scrape はネットワーク非依存
（合成 entries/odds を直接投入）。

**Target Platform**: 手動 CLI（自動 scheduler は deferred）。

**Project Type**: 新規結線パッケージ `live/`（orchestrate + guards + cli）。既存パッケージ無改変。

**Performance Goals**: 1 レース = scrape + run_serving(O(N^3) 009 含む) + 推奨。日付列挙は result-pending フィルタ。

**Constraints**: result-pending かつ valid id かつ entries 完全でなければ予測しない。odds 欠損で推奨しない。
features は結果非参照（II）。cutoff=race_date。使用オッズ値を保存（as_of 単独依存しない）。live Kelly は shadow。決定論。

**Scale/Scope**: 結線 + ガード + 評価ハーネス。発走時刻 cutoff・自動化・実資金・オッズ履歴は deferred。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: valid JRA-VAN 12桁のみ（008 規約）、netkeiba ID は id_mappings 経由、ラベル不変。
  新規 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 予測は run_serving の as-of 特徴量（結果非参照・同日除外）。cutoff=
  race_date 以降・他レース・結果を使わない。odds/stake はモデル特徴に戻さない。リーク境界テスト（結果変更で
  予測不変）。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 結果不在のため backtest 不能 → p パリティ（live==retrospective）+
  リーク境界 + prospective ログ（後日 007/011/016 backtest 投入）で代替。**PASS（代替を明示）**
- [x] **IV. 確率整合性**: run_serving が check_consistency 実施。新馬/unmapped は Unknown + 出走頭数に含め
  正規化を壊さない。009/010 の整合継承。**PASS**
- [x] **V. 再現性・監査**: prediction_run（model_version/feature 版/computed_at）+ recommendations（使用オッズ値/
  is_estimated_odds/logic_version/computed_at）。スナップショット履歴なしだが使用オッズ値で再現可。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし。結線層を新パッケージに分離（逆依存回避）。発走時刻 cutoff/
  自動化/実資金は将来に明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` second opinion を取得・記録（下表）。top-3 + cutoff 補正を機構化。**PASS**

### Second Opinion 記録（codex:codex-rescue — 設計レビュー）

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **A. cutoff/リーク** | 発走時刻不明は fail-closed、cutoff を統一 | races にpost_time 多く null→cutoff=race_date（004 継承、時刻粒度 deferred）。「未走」は result-pending で判定（R4/R2） |
| **B. pre-race odds** | 欠損/部分/as_of を必須化、欠損は推奨停止 | odds_present ガード、欠損→推奨 fail-closed・予測は可（R2/R5, FR-009） |
| **C. 008/ID** | entrants 完全性を前提、未マップ馬も頭数に含む | entries_complete ガード、Unknown+出走頭数に含む（R2/R4, FR-004/005） |
| **D. 評価** | 過去 pre-race odds 非保持→推奨の過去パリティ不可、p のみ | パリティは features+p、推奨は prospective のみ（R6, FR-012） |
| **E. 鮮度/再現** | as_of だけでなく使用 odds ベクトルを保存 | recommendations に使用オッズ値保存（R5, FR-008） |
| **F. 運用境界** | 発走後/結果存在を live で拒否 | result-pending ガードで拒否（R2/R7, FR-001） |
| **G. 追加** | live calibration は shadow-only から | live Kelly shadow（FR-016）、自動化/実資金 deferred |

最重要リスク TOP3: ①不完全/走行済みデータで予測 ②過去 odds 再現前提の誤評価 ③使用 odds 非保存で監査不能。
①=fail-closed ガード（result-pending/完全性）、②=p パリティのみ+prospective、③=使用オッズ値保存で対応。

## Project Structure

### Documentation (this feature)

```text
specs/019-live-serving/
├── plan.md / research.md (R1-R7) / data-model.md / quickstart.md
├── contracts/live_serve.md
├── checklists/requirements.md (16/16 PASS)
└── tasks.md  (/speckit-tasks で生成)
```

### Source Code (repository root)

```text
live/                              # 新規パッケージ horseracing-live
├── pyproject.toml                 # deps: scrape, serving, betting, db
└── src/horseracing_live/
    ├── __init__.py
    ├── guards.py                  # valid_race_id / result_pending / entries_complete / odds_present（R2）
    ├── orchestrate.py             # live_serve(): guard→scrape(008)→run_serving(006)→recommend(011/016)→report（R1/R3/R4/R5）
    └── cli.py                     # live-serve / list-pending
└── tests/{unit,integration}       # guards / 予測(as-of,leak) / 推奨(estimated,shadow) / p パリティ / 決定論
```

**Structure Decision**: 新規 `live/` 結線パッケージ。既存 scrape/serving/betting/probability/db は無改変
（再利用のみ）。スキーマ変更なし。

## Complexity Tracking

> Constitution 違反なし（スキーマ変更なし、既存 leak-safe 経路再利用、新パッケージは VI の責務分離に整合）。記入不要。
