# Implementation Plan: 実 exotic オッズ取込と疑似→実 ROI 化

**Branch**: `012-exotic-odds-ingest` | **Date**: 2026-06-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/012-exotic-odds-ingest/spec.md`

## Summary

008 の polite netkeiba 基盤を再利用し、実 exotic 配当オッズ(複勝/馬連/馬単/ワイド/三連複/三連単)を取得・パースして
**新テーブル `exotic_odds`**(コア/取込/予測契約 0001–0004 以降で初の新テーブル、006–011 はスキーマ変更なし + Alembic
マイグレーション)に格納する。`selection` は 011 と**同一の
JSONB 安全配列**(`to_selection`、canonical horse_number)でキー一致。`exotic_odds` は (race_id, bet_type, selection) ごとに
**単一最新値 + updated_at**(憲法 V、`race_horses.odds` と同方針、スナップショット履歴なし)。betting(011)を拡張し、実 exotic
オッズがあれば `market_odds_used=実値 / is_estimated_odds=false / 実 ROI`、無ければ 011 推定(二重疑似)にフォールバック
(selection 完全一致で行単位区別)。評価先行: 推定 O_est vs 実オッズの乖離(カバレッジ率・符号付き log 比・中央値/MAE/P90)を
券種別・レース単位で計測。exotic オッズは**モデル特徴に一切しない**(リーク境界、憲法 II)。

codex の BLOCKER(selection 突合・配線・冪等)を機構解消し、codex の `odds_phase` 提案は**憲法 V に反するため不採用**(単一最新値
で代替)した点を本 plan に記録する。

## Technical Context

**Language/Version**: Python 3.12(`uv`)

**Primary Dependencies**:
- `db`(`exotic_odds` モデル + Alembic マイグレーション追加)
- `scrape`(`horseracing-scrape`、008 の fetch/idmap/upsert/pipeline/parse を再利用・拡張)
- `betting`(`horseracing-betting`、011 の exotic_ev/exotic_recommend/exotic_roi/exotic_backtest に実オッズ配線)
- `probability`(010/011 の estimate_market_odds を乖離評価の baseline に)
- httpx + selectolax/bs4、SQLAlchemy 2.0、Alembic

**Storage**: PostgreSQL 16。新テーブル `exotic_odds`(read/write)。読: race_horses/race_results/race_predictions/recommendations。
書: exotic_odds / recommendations(実オッズ配線)。

**Testing**: pytest + testcontainers。HTML fixture(ネットワーク非依存)でパーサ、合成データで selection 突合・実/推定フォール
バック・実 ROI 採点・乖離評価・冪等・リーク境界・決定論。実 DB スモーク。

**Target Platform**: Linux / macOS の手動 CLI 実行

**Project Type**: `db`(スキーマ)+ `scrape`(取込)+ `betting`(配線/評価)の複数パッケージ拡張

**Performance Goals**: 取得は 008 の polite 規律。三連単/三連複グリッドは `coverage_scope` で full/partial、取込は期間/レース駆動。

**Constraints**: exotic オッズはモデル特徴にしない。単一最新値 + updated_at(履歴なし、憲法 V)。selection は 011 と同一配列で完全
一致。実/推定は行単位区別。netkeiba ID は id_mappings 経由のみ。冪等 + ingestion_jobs 監査。決定論。2007+。

**Scale/Scope**: 6 券種。新テーブル 1。完全グリッド保証・Kelly・bias 補正・運用 UI は将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: race_id は JRA-VAN 12 桁(future は有効 ID のみ書込)。netkeiba ID は **id_mappings 経由のみ**(guess-join
  禁止)。bet_type は exotic 6 券種語彙(win 除外、recommendations は 7 券種を保持)。`exotic_odds` は既存 ID 契約に従う。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: exotic オッズは**市場データでモデル特徴に一切しない**(win オッズと同一)。買い目決定は
  オッズ + p のみ、結果(着順)は採点のみ。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 推定 O_est(010/011)vs 実 exotic オッズの乖離評価ハーネス(カバレッジ率/log 比/MAE/P90)を
  実装。実 ROI と疑似 ROI をラベル分離。**PASS(本原則の実装)**
- [x] **IV. 確率整合性**: 確率は 011/009 の canonical 母集団・正規化を継承(本フィーチャーで再定義しない)。取消・除外は 011 規律で
  void/skip。exotic オッズは確率ではなく市場価格。**PASS(継承)**
- [x] **V. 再現性と監査**: **オッズはスナップショット履歴を持たず最新値で上書き + updated_at のみ**(`race_horses.odds` と同方針)。
  決定時オッズは `recommendations.market_odds_used` にスナップショット(model_version/logic_version/computed_at は既存)。実オッズ=
  実評価、推定=疑似評価でラベル分離。ingestion_jobs 監査。**PASS**(下記 codex `odds_phase` 不採用の根拠を参照)
- [x] **VI. feature 分割規律**: **コア/取込/予測契約(0001–0004)以降で初の新テーブル追加**(`exotic_odds`、006–011 はスキーマ変更
  なし)。bet_type は exotic 6 券種(win 除外)。既存テーブルに置き場が無く、後付け作り直しを避けるため最小契約で新設。破壊的変更
  なし。完全カバレッジ保証・Kelly・bias 補正・UI は将来に明示分離。**PASS(正当化を記録)**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion を取得・記録(下表)。BLOCKER を機構解消、`odds_phase` 提案は憲法 V 優先で
  不採用。**PASS**

### Second Opinion 記録(codex:codex-rescue — spec/plan 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **selection 突合** | **BLOCKER**: `5` vs `[5]`、順序/整列差で JSONB 等価 join が外れる。同一シリアライザ必須 | 011 の `to_selection`/canonical horse_number で生成した同一配列、`UNIQUE(race_id, bet_type, selection)` 複合 B-tree(R1) |
| **事前 vs 確定オッズ** | **BLOCKER**: `odds_phase`/`is_final` をスキーマ可視化、prerace は NOT EXISTS results のみ、final は保護 | **不採用(憲法 V 優先)**: スナップショット履歴禁止 → 単一最新値 + updated_at。exotic は netkeiba 単独源で JRA-VAN 保護対象なし。決定時オッズは recommendations にスナップショット(R2) |
| **冪等/監査** | **BLOCKER**: `ingestion_jobs.job_type`(event_type ではない)、UNIQUE で dedup、partial + summary | job_type='exotic_odds'、UNIQUE 上書き、status=partial、summary(期待/観測/欠損)(R3) |
| **実/推定フォールバック配線** | **BLOCKER**: canonical 母集団/to_selection をバイパスすると母集団ズレ・キー不一致。推奨後取消は void/skip | 必ず canonical_field/to_selection 経由、行単位で実/推定区別、推奨後取消は void(R4) |
| 三連単グリッド量 | RISK: 完全グリッド未検証。coverage_scope、期待件数テスト、欠損は推定フォールバック | coverage_scope(full/partial)、完全は期待件数テストで証明、欠損は推定(R5) |
| 乖離評価設計 | RISK: 生の相対誤差や実/疑似混在ラベルは誤誘導 | カバレッジ率・符号付き log(実/推定)・中央値/MAE/P90、推定=baseline、ラベル分離(R6) |
| 憲法ゲート | RISK: spec/plan/tasks を先に書きゲート内在化 | 本 plan で II/III/V/VI を内在化、exotic オッズ非特徴量(R7) |

**codex 提案からの意図的逸脱**: `odds_phase`(2 行スナップショット)は憲法 V「オッズはスナップショット履歴を保存せず最新値で
上書き」に反するため不採用。単一最新値 + updated_at(既存 `race_horses.odds` パターン)で代替し、決定時値は recommendations の
監査列にスナップショットする。これにより V を満たしつつ「事前=推奨用 / 確定後=実払戻」を実現する。

## Project Structure

### Documentation (this feature)

```text
specs/012-exotic-odds-ingest/
├── plan.md
├── research.md          # R1 selection 突合 / R2 単一最新値(V) / R3 冪等監査 / R4 配線 / R5 coverage / R6 乖離評価 / R7 リーク
├── data-model.md        # exotic_odds スキーマ・selection 形・上書き規律・不変条件・乖離レポート
├── quickstart.md        # 取込 → 実 ROI 推奨/バックテスト → 乖離レポート の検証手順
├── contracts/
│   ├── exotic_odds_ingest.md   # parse / upsert(最新値上書き)/ ingestion_jobs 監査の契約
│   └── real_roi_wiring.md      # 実/推定フォールバック配線 + 乖離評価の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
db/
├── src/horseracing_db/models/market.py        # 追加: ExoticOdds モデル(TimestampMixin → updated_at)
├── src/horseracing_db/enums.py                # 追加: CoverageScope(full/partial)
├── src/horseracing_db/constraints.py          # 追加: EXOTIC_BET_TYPE CHECK 等
└── migrations/versions/0005_exotic_odds.py    # 追加: exotic_odds テーブル + UNIQUE(race_id,bet_type,selection)

scrape/                                         # 008 を再利用・拡張
├── src/horseracing_scrape/parse/exotic_odds.py # 追加: 6 券種 exotic オッズパーサ(fixture テスト)
├── src/horseracing_scrape/upsert.py            # 拡張: upsert_exotic_odds(最新値上書き + dedup)
├── src/horseracing_scrape/pipeline.py          # 拡張: scrape_exotic_odds(ingestion_jobs 監査)
└── src/horseracing_scrape/cli.py               # 拡張: scrape-exotic-odds

betting/                                         # 011 を拡張(db に依存済み)
├── src/horseracing_betting/exotic_market.py     # 追加: load_real_exotic_odds(race) → {(bet_type,selection)->odds}
├── src/horseracing_betting/exotic_recommend.py  # 拡張: 実オッズ優先 / 推定フォールバック(行単位区別)
├── src/horseracing_betting/exotic_roi.py        # 拡張: 実払戻(実オッズ)/ 疑似払戻ラベル、推奨後取消 void
├── src/horseracing_betting/exotic_backtest.py   # 拡張: 実 ROI / 疑似 ROI 分離
├── src/horseracing_betting/exotic_divergence.py # 追加: 推定 O_est vs 実 の乖離評価(カバレッジ/log 比/MAE/P90)
└── src/horseracing_betting/cli.py               # 拡張: exotic-divergence
```

**Structure Decision**: スキーマ(`db`)→ 取込(`scrape`、008 再利用)→ 配線/評価(`betting`、011 拡張)の 3 層。`exotic_odds` は
`race_horses.odds` と同型の単一最新値テーブルで憲法 V を満たす。selection 突合は 011 の `to_selection` を**唯一の正準化経路**として
共有し、実オッズと推奨/推定のキー一致を保証する。

## Complexity Tracking

> Constitution Check 違反なし。スキーマ変更(`exotic_odds`)は憲法 VI で正当化済み(既存テーブルに置き場が無く最小契約で新設、
> 破壊的変更なし)。記入不要。
