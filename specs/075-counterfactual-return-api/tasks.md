---
description: "Task list for feature 075 — Counterfactual Return API Terminology"
---

# Tasks: Counterfactual Return API Terminology

**Input**: Design documents from `/specs/075-counterfactual-return-api/`

**Prerequisites**: plan.md, spec.md, research.md (D1–D4), data-model.md, contracts/api-fields.md, quickstart.md

**Tests**: 含める(数値パリティ回帰=値不変の担保・drift-check・empirical 保護が本 feature の受け入れ条件)。

**Organization**: user story 単位。US1=P1・US2=P2・US3=P3。**依存順**: api(source of truth)→ OpenAPI 再生成 → front/admin 原子同期(並列)。

**制約**: **数値不変**(命名のみ)・DB スキーマ/migration なし・read-only(全 GET)・破壊的変更(後方互換なし)・api/front/admin/OpenAPI 原子同期。

**マルチエージェント codex(implement)**: Phase 2(api=source of truth)完了後、Phase 3/4 の **front と admin を 2 codex agent で並列**(独立ディレクトリ・同一 OpenAPI から型/fixtures/components を更新)。api と数値パリティ回帰は親が担当。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 数値パリティ oracle 取得: 改名前の api 応答値(既知 fixture / 固定レースの recommendations・shadow-log)を記録し、改名後に対応フィールド値が一致することを比較する基準にする(FR-007/SC-002)。`api/tests/` に baseline を固定。

---

## Phase 2: Foundational (api = source of truth・front/admin をブロック)

**Purpose**: api の応答モデル改名 = OpenAPI の source of truth。**⚠️ 完了まで front/admin を触らない**。

- [X] T002 `api/src/horseracing_api/backtest.py` の内部 dataclass(`WinRealized`/`FavoriteRealized`)の field を **中立 `gross_return`/`net_return`** に改名(`win_realized()` は shared core・ロジック不変)。`ShadowLogSummary.recovery_rate` と `by_month` の raw dict key `recovery` は**中立のまま可**(provenance 命名は応答モデル層=T003/T004 で付与・analyze A1)。**`recovery` は gross/net 中立規則の意図的例外**(集計値なので gross/net でない・analyze N1)。research D1。
- [X] T003 `api/src/horseracing_api/schemas.py` の応答モデルに **provenance 命名**を付与: win backtest=`counterfactual_snapshot_gross_return`/`net_return`+`valuation_basis="frozen_snapshot_odds"`、shadow-log=`counterfactual_snapshot_recovery_rate`+`valuation_basis`(**`n_scored` は追加しない**=`n_settled` が分母・analyze D1)、**`ShadowLogMonth.recovery`→`counterfactual_snapshot_recovery`**(by_month の owner・analyze I1/U1)、favorite=`current_odds_gross_return`/`net_return`+`valuation_basis="current_odds"`。**calibration `realized_rate`/`ci` は不変**。data-model §1–4。
- [X] T004 **両 router**(`api/src/horseracing_api/routers/recommendations.py`=favorite/win backtest、`api/src/horseracing_api/routers/shadow_log.py`=recovery/by_month)の dataclass→応答モデル マッピングを新フィールド名に更新(provenance を層で付与)。read-only 維持(analyze A1)。**⚠️ silent-null 罠(analyze I1)**: `shadow_log.py:52` の `ShadowLogMonth(**m)` splat は、応答 field を `counterfactual_snapshot_recovery` に改名しても内部 dict key が `recovery` のままだと pydantic が黙って無視し **null 化(値 regression・grep では検出不能)**する。→ **明示 keyword マッピングに変換**: `ShadowLogMonth(month=m["month"], n_settled=m["n_settled"], counterfactual_snapshot_recovery=m["recovery"])`(内部 dict key は中立 `recovery` のまま=応答層で provenance 名にマップ)。`extra="forbid"` を付けて splat の取りこぼしを構造的に防いでもよい。
- [X] T005 [P] `api/tests/` に **数値パリティ回帰**(T001 baseline と改名後値の一致・FR-007)+ **改名網羅**(win backtest/shadow-log/favorite 経路に `realized_return`/`realized_roi` が 0 件・SC-001)+ **FR-009 ガード**(exotic backtest フィールドは無改変)を追加。**⚠️ 値パリティは top-level だけでなく `by_month[].counterfactual_snapshot_recovery` の nested 値も baseline と一致することを assert(analyze I1・splat null 化を回帰で捕捉=grep では見えない)**。
- [X] T006 api の OpenAPI を再生成(pydantic 改名の反映)。api 全テスト緑を確認。

**Checkpoint**: api = 新契約の source of truth 確定。front/admin 同期に着手可能。

---

## Phase 3: User Story 1 - 反実仮想スナップショット収益の命名 (Priority: P1) 🎯 MVP

**Goal**: front/admin で backtest/shadow-log の収益が「反実仮想(判断時オッズ)」と分かる表記になり、`realized` 語が消える。

**Independent Test**: front/admin の応答型・fixtures・表示が `counterfactual_snapshot_*` に更新され、drift-check 緑。

### Implementation(front と admin は並列=codex マルチ)

- [X] T007 [P] [US1] `front/` の OpenAPI snapshot 再生成(`front/openapi.json` を api 生成物に byte 一致)+ `schema.d.ts` 再生成(openapi-typescript)。
- [X] T008 [P] [US1] `front/src/tests/fixtures.ts` を新フィールド名に更新 + 表示を `counterfactual_snapshot_*` ラベル(「反実仮想(判断時オッズ)」)に更新: **`ShadowLogPanel`(`front/src/components/ShadowLogPanel.tsx`)** + **`WinBacktestSummary`(`front/src/components/RecommendationPanel.tsx` 内の inline function・standalone コンポーネントではない・analyze I3)**(+ .test)。
- [X] T009 [P] [US1] `admin/` の OpenAPI snapshot 再生成(`admin/openapi.json`)+ `schema.d.ts` 再生成(front と同一 api OpenAPI 由来・admin は API 全面を proxy するため drift-check 対象)。
- [X] T010 [P] [US1] **admin には backtest/shadow-log/favorite の表示コンポーネントが存在しない**(components=RefreshRangeButton/StateView、pages=Coverage/Diagnostics/Jobs/ModelDetail/ModelRegistry・analyze I2)→ **表示改名は不要**。admin は T009 の型/snapshot 再生成のみ + `admin/src/tests/fixtures.ts` に該当フィールドがあれば更新し、**admin build/test 緑**を確認。

**Checkpoint**: backtest/shadow-log の counterfactual 命名が api/front/admin で一貫。

---

## Phase 4: User Story 2 - favorite baseline を current_odds provenance に (Priority: P2)

**Goal**: favorite baseline が「現在オッズ基準」と分かり、snapshot と別 provenance で表示される。

**Independent Test**: favorite の応答が `current_odds_*`+`valuation_basis="current_odds"`、front/admin 表示が「現在オッズ基準」。

### Implementation

- [X] T011 [P] [US2] `front/` の favorite baseline 表示(`RecommendationPanel.tsx` の FavoriteBaseline セクション)を `current_odds_*` ラベル(「現在オッズ基準」)に更新(型/fixtures は T007/T008 で反映済み)(+ .test)。
- [X] T012 [US2] admin には favorite baseline の表示コンポーネントが**存在しない**(analyze I2)→ **表示改名は不要**。T009 の型/snapshot 再生成で契約整合済み・admin test 緑を確認(no-op 確認タスク)。

**Checkpoint**: favorite が current_odds provenance で snapshot と混同なく表示。

---

## Phase 5: User Story 3 - empirical realized_rate 保護 (Priority: P3)

**Goal**: calibration の realized_rate/ci が改名の巻き添えにならない。

**Independent Test**: calibration `realized_rate`/`realized_ci_low`/`realized_ci_high` が改名前後で同名・同値。

### Implementation

- [X] T013 [US3] `api/tests/` に回帰: calibration 応答の `realized_rate`/`realized_ci_low`/`realized_ci_high` が**同名・同値**で残ることを assert(FR-006/SC-005)。grep で backtest/shadow-log/favorite には realized_return/roi が無く calibration の realized_rate は残ることを固定。

---

## Phase 6: Polish & Cross-Cutting

- [X] T014 [P] drift-check: `front` と `admin` の `check:openapi`(committed snapshot == api 生成 == 型)が緑(SC-003)。
- [X] T015 [P] 全テスト緑: api(pytest)・front(vitest)・admin(vitest)。read-only 境界(全 GET)・DB migration 0 を確認(SC-006/SC-007)。
- [X] T016 quickstart.md の受け入れ手順(数値パリティ・改名網羅・empirical 保護・drift・不変)を通し、SC-001〜SC-007 を確認。

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup(1)**: T001 baseline oracle(先行)。
- **Foundational(2)**: api 改名+OpenAPI 再生成。**front/admin をブロック**。
- **US1(3)/US2(4)**: Foundational 後。**front と admin は並列**(独立ディレクトリ)。US2 は US1 の型/fixtures 再生成に乗る。
- **US3(5)**: 独立(api 回帰テストのみ)。いつでも可。
- **Polish(6)**: 全 US 後。

### Within
- api(T002→T003→T004)→ OpenAPI 再生成(T006)→ front/admin 同期。
- テストは各層で(api パリティ=T005・front/admin=各 .test・drift=T014)。

### Parallel(codex マルチ = implement)
- **api 確定後、front(T007/T008/T011)と admin(T009/T010/T012)を 2 codex agent で並列**(別ディレクトリ・別 package)。
- US3(T013)・Polish の drift/test は独立実行可。

---

## Implementation Strategy(MVP + codex マルチ)

### MVP(US1・api→front/admin counterfactual 改名)
1. Setup baseline(T001)
2. Foundational api 改名+OpenAPI(T002–T006)= source of truth
3. US1 front/admin 同期(T007–T010)= drift 緑・counterfactual 命名一貫
4. これで「誤称 realized→counterfactual」の中核が api/front/admin で成立

### codex マルチエージェント(implement)
- 親: api(T002–T006)+ 数値パリティ回帰(T005)+ US3 保護(T013)= 契約の source of truth と正しさを掌握。
- codex agent A: **front** の型再生成/fixtures/**表示コンポーネント**(T007/T008/T011)=WinBacktestSummary(RecommendationPanel 内 inline)+ ShadowLogPanel + FavoriteBaseline。
- codex agent B: **admin** の**型/snapshot 再生成のみ**(T009/T010/T012)=**admin に該当表示コンポーネントは無い**(analyze I2)ので型/snapshot 再生成 + drift + test 緑の確認だけ。軽量。
- 親が統合 + drift-check(T014)+ 全テスト(T015)。

---

## Notes
- 数値不変(命名のみ)・DB migration 0・read-only・破壊的変更(後方互換なし)。
- calibration realized_rate は意図的に残す(empirical)。exotic realized は対象外。
- codex は本セッションで repo skill に derail 履歴あり(design review)→ 実装(明確な機械的タスク)は codex が直接遂行しやすい(074 leaf modules の前例)。
