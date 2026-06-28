# Implementation Plan: 実 netkeiba パーサ (022)

**Branch**: `022-netkeiba-parser` | **Date**: 2026-06-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/022-netkeiba-parser/spec.md`

## Summary

scrape の唯一の実害スタブである parse 層（合成 HTML スキーマ前提）を、**実 netkeiba を解析する本物のパーサに置換**する。取得方式はハイブリッド（出走表・結果＝サーバ描画 HTML を静的取得して解析、単勝オッズ＝netkeiba 内部 JSON）。取得層 `HttpFetcher`・ID 解決 `idmap`・race_id 構築 `venues`・DB 書き込み `upsert` は本物として無改修で再利用し、parse↔upsert の境界 dataclass（`models.py`）も維持する。**DB スキーマ変更なし**。これにより live serving (019) と将来の「1日分実データ更新」が実 netkeiba で動作する土台を完成させる。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: httpx（既存 HttpFetcher）, BeautifulSoup + lxml（既存 parse）, 標準 `json`（odds JSON）。**新規依存なし**（headless 不採用）。

**Storage**: PostgreSQL 16（既存 `races`/`race_horses`/`race_results`/`horses`/`jockeys`/`trainers`/`id_mappings`/`ingestion_jobs`）。スキーマ変更なし。

**Testing**: pytest（unit=保存フィクスチャでネットワーク非依存、integration=testcontainers）。

**Target Platform**: ローカル/個人運用の CLI・pipeline（手動実行、憲法 技術制約）。

**Project Type**: ライブラリ/CLI（`scrape/` パッケージ）。

**Performance Goals**: netkeiba への取得は polite（robots/1秒間隔/cache）。パース性能は非クリティカル。

**Constraints**: リーク境界（odds/結果は特徴量にしない）、odds 最新値上書き（スナップショット禁止）、race_id 12桁/2007+、netkeiba エンティティは id_mappings 経由のみ、fail-close。netkeiba ToS/robots 遵守、個人利用前提。

**Scale/Scope**: P1 出走表 / P2 結果 / P3 単勝オッズ。exotic odds・RaceFront write・スキーマ変更は out of scope。

## Constitution Check

*GATE: Phase 0 前に通過必須。Phase 1 後に再確認。*

- [x] **I. データ契約**: race_id は `build_race_id`（12桁検証・2007+）を通過した場合のみ書き込み。netkeiba 馬/騎手/調教師 ID は `id_mappings` 経由のみ、未マップは surrogate `nk:`＋UNMAPPED。race_id→URL 構築はエンティティ guess-join ではない（research R6、codex 確認）。**+ URL の race_id と取得 HTML 本文内の race_id を照合し、不一致なら fail-close**（誤レース投入防止、codex 指摘）。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 本 feature は特徴量を追加・変更しない。netkeiba 由来の odds・結果は取り込み専用でモデル特徴量に再投入しない（leak-guard テスト維持、FR-009）。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: モデル/特徴量の変更なし＝walk-forward 採用ゲートの対象外。データ取得経路の修正であり、評価指標に影響しない。**N/A（理由明記）**
- [x] **IV. 確率整合性**: 確率出力ロジックに変更なし。**N/A**
- [x] **V. 再現性・監査**: odds は最新値上書き・`updated_at` のみ（スナップショット禁止、`upsert.update_odds` 既存挙動）。**odds JSON 取得は cache をバイパス（no-cache / TTL=0）し、cache 由来の古い odds を書かない**（codex 指摘＝V 抵触回避）。取り込みは `ingestion_jobs` に parser/logic version・件数・status・errors を記録。**PASS**
- [x] **VI. feature 分割規律**: 新 UI なし、新 API/DB 契約なし（既存テーブル・既存 dataclass を再利用）。RaceFront write は別 feature に分離。**PASS**
- [x] **品質ゲート**: 非自明な設計判断（取得方式ハイブリッド / 置換 vs 並存 / odds JSON 化 / parse↔upsert 契約維持）に `codex:codex-rescue` の second opinion を取得。指摘5件を実コードで裏取りし全件採用、差分と根拠を「Codex Second Opinion」節に記録。**PASS**

## Project Structure

### Documentation (this feature)

```text
specs/022-netkeiba-parser/
├── plan.md              # 本ファイル
├── research.md          # Phase 0（取得方式・置換・契約維持・技術選定）
├── data-model.md        # Phase 1（既存 dataclass + 取り込み先テーブル、スキーマ変更なし）
├── quickstart.md        # Phase 1（フィクスチャ取得→unit→integration→leak→e2e）
├── contracts/
│   └── parser-contract.md  # parse 関数シグネチャ + 実 markup→フィールド対応
└── tasks.md             # /speckit.tasks で生成（未）
```

### Source Code (repository root)

```text
scrape/src/horseracing_scrape/
├── fetch.py             # [無改修] HttpFetcher（robots/rate-limit/cache）
├── idmap.py             # [無改修] netkeiba→JRA-VAN id_mappings 解決
├── venues.py            # [無改修] build_race_id 維持
├── urls.py              # [新規] race_id→netkeiba URL 構築（entries/result HTML, odds JSON）
├── odds_adapter.py      # [新規] odds JSON 取得（no-cache）+ schema 必須キー検査 + 欠損 fail-close
├── models.py            # [無改修] parse↔upsert 境界 dataclass
├── upsert.py            # [小改修] update_odds に popularity 追加 / backfill_results に finish_time(str→Interval) 追加（既存カラム、スキーマ変更なし）
├── pipeline.py          # [小改修] odds 取得を odds_adapter 経由へ、capture-fixture 経路
├── cli.py               # [小改修] capture-fixture サブコマンド（1回限り取得）
└── parse/
    ├── _common.py       # [改修] 実 markup ヘルパへ更新（合成 data-* 撤去）/ 必須フィールドは strict parse→ParseError
    ├── entries.py       # [置換] 実 netkeiba 出走表 HTML 解析（本文 race_id 照合含む）
    ├── results.py       # [置換] 実 netkeiba 結果 HTML 解析
    ├── odds.py          # [置換] 実 netkeiba 単勝 odds JSON 解析
    └── exotic_odds.py   # [据え置き] 本 feature 対象外（次段。合成のまま残置を plan で明示）

scrape/tests/
├── fixtures/real/       # [新規] 実 netkeiba HTML/JSON フィクスチャ + manifest.json（URL/取得日/sha256/trim script）
├── unit/                # [更新] 実フィクスチャでパーサ検証 + fail-close（必須欠損/JSON必須キー欠損/race_id不一致）
└── integration/         # [更新] 取り込み e2e（canonical/surrogate, 騎手/調教師 idmap, popularity, finish_time, INSERT-only, odds 保護, odds no-cache, leak-guard E2E）
```

**Structure Decision**: 既存 `scrape/` パッケージ内で完結。変更面を parse 層（+ URL ヘルパ + odds 取得経路 + フィクスチャ）に閉じ込め、取得・ID 解決・書き込み・境界 dataclass は無改修で維持する（research R3）。

## Codex Second Opinion

`codex:codex-rescue` の plan レビュー結果と採用判断（憲法 品質ゲート）。総評は「条件付き採用 —『parse 内だけ差し替え』では閉じない」。指摘5件はいずれも実コードで裏取りし**採用**した。

| # | Codex 指摘 | 検証 | 採用判断 |
|---|---|---|---|
| 1 | parse を直しても `upsert.update_odds` が **popularity を捨て**、`backfill_results` が **finish_time を捨てる**ため DB に落ちない（VI） | ✅ 事実確認（`core.py` に `RaceHorse.popularity` L109・`RaceResult.finish_time` L130 が存在、upsert が書いていない） | **採用**: upsert を小改修しスコープに追加（既存カラム、スキーマ変更なし）。finish_time は str→Interval 変換（`ingest._parse_finish_time` 相当） |
| 2 | `_common.to_int/to_float` が不正値を `None` に潰す → 必須フィールドは strict parse で `ParseError` | ✅ 妥当 | **採用**: 馬番/horse_id/finish_order 等の必須は strict→ParseError |
| 3 | odds は `HttpFetcher` cache 由来の古い値を返し得る（V） | ✅ 妥当（cache に TTL なし） | **採用**: odds は `odds_adapter` で no-cache 取得、JSON 必須キー検査＋欠損 fail-close |
| 4 | 過度な trim は合成 fixture 化 → manifest（URL/取得日/sha256/trim script）を記録 | ✅ 妥当 | **採用**: `fixtures/real/manifest.json` を仕様化。テストは外部 HTTP 禁止（MockTransport / block-network） |
| 5 | URL の race_id と HTML 本文の race_id 照合なしは誤レース投入リスク（I 要注意） | ✅ 妥当 | **採用**: 本文 race_id 照合を fail-close invariant に追加 |

**追加で plan に反映した事項**:
- `ParserProfile(version, required_selectors, invariants)` 的な robustness 設計（必須セレクタ・不変条件: 馬番一意/horse_id 取得率/entry_status 閉世界/race_id 一致）を parse 層に持たせ、`parser_version` で版管理。
- **実 parser 化スコープは P1/P2/P3（entries/results/単勝odds）のみ**。`scrape-exotic-odds` は合成 parser のまま残置することを明記（将来担当者の混乱防止）。
- 不足テストを追加: popularity 更新 / finish_time 保存 / 騎手・調教師 idmap（UNMAPPED 含む）/ race_id 不一致→ParseError / odds no-cache / JSON 必須キー欠損 fail-close / scrape→`features.registry` の leak-guard E2E。
- `urls.py` / `odds_adapter.py` は新規ファイルだが依存方向は不変（憲法 VI 非抵触）。

**popularity の補足**: `RaceHorse.popularity` カラムは存在するため小改修で永続化可能（スキーマ変更不要）。codex が VI 違反として挙げた「落ちない」問題は upsert 小改修で解消する。

**不採用・保留**: なし（全件採用）。Codex 総評の「条件付き」条件はすべて上表で解消済み。

## Complexity Tracking

憲法違反なし。Complexity 逸脱なし（新依存・新サービス・スキーマ変更なし、変更面を parse 層に限定）。
