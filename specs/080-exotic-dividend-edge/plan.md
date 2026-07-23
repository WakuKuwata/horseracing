# Implementation Plan: Real Exotic Dividend Ingestion & Exotic Edge Measurement

**Branch**: `080-exotic-dividend-edge` | **Date**: 2026-07-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/080-exotic-dividend-edge/spec.md`

## Summary

WIN 市場でのモデル改善が ROI レバーとして枯れたことを最新最良モデル lgbm-065 で実証([[lgbm-065-roi-ceiling-confirmed]]:odds-cap 天井 ×0.816≈lgbm-061 ×0.818)。ROI>1.0 の唯一の残路 = WIN より非効率な **exotic 市場**で 009 joint EV が実配当を上回るか測ること。だが実 exotic 配当は 0 行=材料が無い。

**技術的アプローチ**: 既存の exotic 資産(`exotic_odds` テーブル/migration 0005・`upsert_exotic_odds`・`scrape-exotic-odds` CLI・`exotic-backtest`/`exotic-divergence` CLI)は配線済みで、欠けている 3 点だけを足す:
1. **parser の実 markup 対応**: `parse_exotic_odds` を fixture 形状(`table.exotic`/`data-bet-type`)から実 netkeiba result ページの払戻テーブル(`Payout_Detail_Table` 相当)対応へ書き換え。抽出ロジックのみ差し替え、出力契約 `ScrapedExoticOdds` は不変。
2. **日次 results 相乗り**: `scrape_results` が既に fetch(cache 済)している同一 result HTML から配当も抽出し `upsert_exotic_odds` へ。**追加 netkeiba リクエスト 0**・結果確定後のみ・例外隔離(result 保存を壊さない)。
3. **pre-registered edge 測定**: 実配当蓄積後に `exotic-divergence`(推定 vs 実)/`exotic-backtest`(009 joint EV vs 実配当)を、結果前に固定した pre-registration に沿って実行。実配当 n が最小未満は NO_DECISION。

スキーマ変更なし・migration 追加なし・API/front 不変。実配当はモデル特徴/校正に流入させない(リーク境界)。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: selectolax/bs4(HTML parse・既存 scrape スタック)、SQLAlchemy 2.0、既存 `probability`(009 joint)/`betting`(exotic-backtest/divergence)。新規依存なし。

**Storage**: PostgreSQL 16。`exotic_odds`(migration 0005、既存)。UNIQUE(race_id, bet_type, selection)。単一最新値・upsert 上書き。**新テーブル・列追加なし**。

**Testing**: pytest。parser は保存済み実 netkeiba result HTML fixture(network-free)。相乗り/冪等/例外隔離は testcontainers integration。leak-guard は features/serving のモデル予測 byte 不変。

**Target Platform**: Linux/macOS server(operator CLI + 日次 ops worker)。

**Project Type**: single-repo multi-package(`scrape`/`betting`/`eval` 変更、`db` は参照のみ)。

**Performance Goals**: 相乗り parse は既取得ページ上で完結(追加ネットワーク 0)。1 result ページ parse は数 ms オーダー(既存 parse_results と同等)。

**Constraints**: netkeiba polite(大量 backfill 回避=相乗りのみ)。憲法 II/V。実配当確定後のみ書込。scrape→betting/features 逆依存禁止(既存 import-graph 境界)。

**Scale/Scope**: 前向き収集=1 日あたり数十〜百数十レース(既存日次 result と同数)。歴史 backfill は netkeiba cache 分のみ機会利用。

## Constitution Check

*GATE: Phase 0 前に PASS。Phase 1 後に再確認。*

- [x] **I. データ契約**: race_id は既存 `race_id_from_html`(12桁)で解決、id_mappings 経由。exotic combo は **馬番(race-local)** キーで id-mapping 不要(parser docstring の既存前提)。ラベル体系変更なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: exotic 配当・オッズは特徴量にしない。source=netkeiba result ページ・timing=結果確定後・欠損=空返し。結果は edge 採点のみ。leak-guard テスト(配当変化でモデル予測 byte 不変)。scrape→betting/features 逆依存なし。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: edge 測定は券種/窓/baseline/最小 n/成功条件を結果前に pre-registration 文書へ固定。walk-forward/OOS で overfit を潰す。NO_DECISION 許容。edge scorer(exotic-backtest/divergence)は既存で predictor-agnostic。**PASS**
- [x] **IV. 確率整合性**: モデル p・win 予測は本 feature で不変(exotic は読み取り・採点のみ)。009 joint marginal 整合は既存不変式。**PASS(N/A change)**
- [x] **V. 再現性・監査**: exotic_odds は単一最新値・冪等 upsert・post-result 上書き(既存)。edge run は logic_version に控除率/窓/seed 記録・pre-registration は append-only。**PASS**
- [x] **VI. feature 分割規律**: migration 追加なし(0005 既存契約)・API/front 不変。parser は既存 model 契約保持。US3(edge 測定)は spec 内だが実行はデータ蓄積後(pre-registration を着手時固定)。**PASS**
- [~] **品質ゲート**: codex second opinion を 2 回 `codex exec` 試行 → repo AGENTS.md の「並走させる」指示で前置きのみ derail・本文出ず([[codex-env-recovery]])。再試行上限でセルフレビュー checklist 代替(下記 Complexity/research 記録)。**PARTIAL(codex unavailable, self-review 実施)**

## Project Structure

### Documentation (this feature)

```text
specs/080-exotic-dividend-edge/
├── plan.md              # This file
├── research.md          # Phase 0: parser markup 調査・edge 測定設計・codex 代替 self-review
├── data-model.md        # Phase 1: exotic_odds(既存)/ScrapedExoticOdds/pre-registration entity
├── quickstart.md        # Phase 1: fixture capture → parser → 相乗り → edge 測定 の検証手順
├── contracts/
│   ├── parser.md        # parse_exotic_odds 実 markup 契約(入出力・券種マップ・エッジ)
│   └── edge-eval.md     # exotic edge pre-registration + CLI 契約(NO_DECISION 規約)
└── tasks.md             # Phase 2(/speckit-tasks で生成)
```

### Source Code (repository root)

```text
scrape/src/horseracing_scrape/
├── parse/exotic_odds.py         # [REWRITE] fixture→実 Payout_Detail_Table markup
├── parse/_common.py             # [reuse] race_id_from_html / soup_of / to_float
├── pipeline.py                  # [EDIT] scrape_results に exotic 相乗り(確定後・例外隔離・追加fetch 0)
├── upsert.py                    # [reuse] upsert_exotic_odds(変更なし)/ _expected_count(place nuance は research)
└── cli.py                       # [reuse] capture-fixture --kind results / scrape-results

scrape/tests/
├── unit/test_parse_exotic_odds.py      # [REWRITE] 実 fixture ベース(現状は合成 fixture)
├── fixtures/real/                       # [ADD] 実 netkeiba result HTML(payoutテーブル含む)
└── integration/test_exotic_cli.py       # [EDIT] 相乗り・冪等・例外隔離・追加fetch 0

betting/src/horseracing_betting/
├── exotic_backtest.py           # [reuse/verify] 009 joint EV vs 実配当・NO_DECISION 追加余地
├── exotic_divergence.py         # [reuse/verify] 推定 vs 実
└── cli.py                       # [reuse] exotic-backtest / exotic-divergence

eval/ or specs artifact
└── exotic edge pre-registration # [ADD doc] 券種/窓/baseline/最小n/成功条件(結果前固定)
```

**Structure Decision**: 既存 multi-package を踏襲。変更は `scrape`(parser rewrite + pipeline 相乗り)に集中、`betting` の exotic-backtest/divergence は既存を実データで検証し NO_DECISION 規約を確認/追加、`db` は参照のみ(スキーマ不変)。UI/API 層は触らない。

## Phase 進行

- **Phase 0 (research.md)**: (1) 実 netkeiba result ページの払戻 markup 実測(T0 spike=実 fixture 1 枚捕獲)。(2) 券種ラベル日本語→canonical マップ・yen payout→odds 倍率変換・同着複数払戻・複勝 coverage nuance。(3) edge 測定の統計設計(最小 n・baseline・cluster-bootstrap・多重比較・前向き vs cache backfill)。(4) codex 代替 self-review の穴一覧。
- **Phase 1 (data-model/contracts/quickstart)**: 既存 exotic_odds/ScrapedExoticOdds を data-model に、parser I/O と edge pre-registration を contracts に、fixture→parser→相乗り→測定を quickstart に。
- **Phase 2 (tasks.md)**: `/speckit-tasks`。US1(parser rewrite+fixture test)→US2(pipeline 相乗り+integration)→US3(pre-registration doc+edge CLI 検証)。**T0 spike(実 fixture 捕獲・markup 確認)を最初のタスク**に置き、実 markup が想定と大きく違えば parser 設計を見直す。

## Complexity Tracking / Self-Review(codex unavailable)

codex 本文レビュー取得できずセルフレビューで代替。確認した穴と対応:

| 論点 | リスク | 対応 |
|---|---|---|
| ~~実 markup 未確認~~ **RESOLVED** | parser 書き換えが実物と乖離 | **T0 spike 実施済(2026-07-23)**: live result に `Payout_Detail_Table`×2 確認=相乗り追加req0 実証・実 markup を contracts/parser.md に確定・fixture `results_202602011206.html` 捕獲 |
| 相乗りの確定タイミング | 発走前ページから誤配当を書く | 結果確定シグナル(result 保存成立)を gate・未確定は空返し(FR-006) |
| silent-empty | markup 変更で黙って 0 行 | 期待券種数の下限チェックで異常検知(fail-loud) |
| place coverage nuance | `_expected_count(place)=n` だが実払戻は placed 頭数(2-3) | coverage_scope=partial になるだけで機能阻害なし。research に記録・別 issue |
| 小 n で偽の勝ち | 前向き初期に edge 誤主張 | NO_DECISION 規約(FR-009/SC-006)を edge scorer に確認/追加 |
| overfit | 過去当たり穴目を拾う | pre-registration + OOS/walk-forward(US3-AC3) |
| 控除率逆風 | exotic 25-27.5% を非効率が上回るか未知 | 正直な限界として spec 明記・null 結果も成功 |

**主要リスク**: ~~実 netkeiba payout markup の正確な構造~~ → **T0 spike で解消済(2026-07-23)**。live result に `Payout_Detail_Table`×2 を確認し実 markup を contracts/parser.md に確定・相乗り追加req0 実証。残る不確実性は同着行の実サンプル(後日捕獲 or 既知構造で合成)のみ。
