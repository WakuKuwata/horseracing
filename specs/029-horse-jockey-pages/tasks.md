---
description: "Task list for feature 029 — 馬・騎手プロフィールページ"
---

# Tasks: 馬・騎手プロフィールページ

**Input**: Design documents from `/specs/029-horse-jockey-pages/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/horse-jockey-api.yaml, quickstart.md

**Tests**: 含める。憲法 III（評価先行）＋ quickstart の「不変条件テスト（必須）」により、契約・集計正当性・read-only 不変・leak-guard・リンク化規則・front 状態のテストを生成する。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列実行可（別ファイル・依存なし）
- **[Story]**: US1（馬）/ US2（騎手）
- パスは plan.md の構造（既存 `api/` への read 追加 ＋ `front/` 画面追加）に準拠。スキーマ変更なし。

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 `api/src/horseracing_api/routers/horses.py` と `routers/jockeys.py` の骨組み（空 `APIRouter`）を作成し、`app.py` で `include_router(horses, jockeys, prefix=API_PREFIX)`。
- [X] T002 [P] `front/src/router.tsx` に `/horses/:horseId`・`/jockeys/:jockeyId` ルートを追加（ページは後続タスクで実装）。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 両ストーリーが依存する共有スキーマと境界確認

**⚠️ CRITICAL**: このフェーズ完了まで US1/US2 のエンドポイント実装は着手不可

- [X] T003 `api/src/horseracing_api/schemas.py` に `HorseProfile`・`HorseHistoryRow`・`JockeyProfile`・`JockeyHistoryRow` を `contracts/horse-jockey-api.yaml` どおり追加（履歴は既存ジェネリック `Page[T]` を流用）。率は `float | None`、avg_finish も nullable。
- [X] T004 [P] `api/tests/integration/test_readonly_invariant.py`・`tests/unit/test_no_write_boundary.py` を実行し、新ルーター追加後も 014 が全 GET・行数不変・`features`/`training` 非 import を満たすことを確認（境界の回帰なし）。

**Checkpoint**: 共有スキーマと read-only 境界が確認でき、各ストーリー着手可能

---

## Phase 3: User Story 1 - 馬プロフィール (Priority: P1) 🎯 MVP

**Goal**: レース詳細の馬名から `/horses/{id}` に遷移し、基本＋血統＋通算成績＋レース別履歴を表示

**Independent Test**: 任意レース詳細で馬名クリック→馬ページ遷移→基本/血統名/通算成績(出走数基準の率・完走平均着順)/履歴(新しい順・ページング)が表示される

### Tests for User Story 1

- [X] T005 [P] [US1] `api/tests/integration/test_horses_api.py`：`GET /api/v1/horses/{id}` が 200(HorseProfile)/404(未存在)、`GET /api/v1/horses/{id}/history` が 200(Page)・ページング（page/page_size 上限）・404。（馬 ID は固定フォーマットなし→422 は設けない）
- [X] T006 [P] [US1] `api/tests/integration/test_horse_stats.py`：seed（複数走＋取消＋中止を含む馬）で 出走数=started のみ・勝率/連対率/複勝率=分子(finished&finish_order)÷出走数・平均着順=完走のみ、starts=0 は率 null（Unknown と 0 区別）を検証。

### Implementation for User Story 1

- [X] T007 [US1] `api/src/horseracing_api/queries.py` に `get_horse`(存在確認)・`horse_career_stats`(単発 GROUP BY 集約・母数規則 D2)・`horse_history`(race join, `race_date DESC` 安定順, paged) を実装（N+1 なし）。
- [X] T008 [US1] `api/src/horseracing_api/routers/horses.py`：`GET /horses/{id}`(HorseProfile, 未存在 404) と `GET /horses/{id}/history`(Page[HorseHistoryRow], page/page_size) を実装。
- [X] T009 [US1] `front/openapi.json` を更新中の 014 から再生成・コミットし `front/src/api/schema.d.ts` を `openapi-typescript` で再生成、`scripts/check-openapi.sh` の drift-check を通す（馬 endpoint 反映）。
- [X] T010 [P] [US1] `front/src/api/queries.ts` に `useHorseProfile`・`useHorseHistory`（既存 `useQuery<T, ErrorInfo>`＋`unwrap` パターン）を追加、`types.ts` に型 re-export。
- [X] T011 [US1] `front/src/pages/HorseDetailPage.tsx`：基本＋血統(名前)＋通算成績＋履歴を `QueryStateView`(loading/empty/typed-error) と `formatNum`/`formatPct` で表示。「確定実績/過去成績」と明記しモデル予測と分離。
- [X] T012 [US1] `front/src/components/HorseEntriesTable.tsx`：**馬名を `<Link to=/horses/{horse_id}>` 化**（canonical のみ。`nk:` surrogate/null は非リンク）。既存ソート/取消表示を壊さない。
- [X] T013 [P] [US1] `front/src/pages/HorseDetailPage.test.tsx`（Vitest+MSW）：プロフィール/履歴描画・実績ゼロの空状態・nullable 率の `--` 表示・馬名リンク化規則（nk: 非リンク）を検証。

**Checkpoint**: US1 単独で「馬ページ閲覧」が成立（MVP）

---

## Phase 4: User Story 2 - 騎手プロフィール (Priority: P2)

**Goal**: レース詳細の騎手名から `/jockeys/{id}` に遷移し、騎乗成績＋騎乗履歴を表示

**Independent Test**: 騎手名クリック→騎手ページ遷移→騎乗数/勝率/連対率/複勝率/平均着順＋騎乗履歴が表示。ID 欠損/surrogate は非リンク

### Tests for User Story 2

- [X] T014 [P] [US2] `api/tests/integration/test_jockeys_api.py`：`GET /api/v1/jockeys/{id}` 200/404、`/jockeys/{id}/history` 200(Page)・ページング。
- [X] T015 [P] [US2] `api/tests/integration/test_jockey_stats.py`：seed で 騎乗数(started)・率(finished÷騎乗数)・平均着順(完走のみ) を母数規則どおり検証。
- [X] T016 [P] [US2] `api/tests/integration/test_race_horses_jockey_id.py`：`GET /races/{id}` の `HorseEntry` に `jockey_id`/`trainer_id` が含まれることを検証（リンク用契約）。

### Implementation for User Story 2

- [X] T017 [US2] 契約 additive(backend): `api/src/horseracing_api/schemas.py` の `HorseEntry` に `jockey_id: str | None`・`trainer_id: str | None` を追加し、`queries.race_horses` の select に両 ID を追加。（front 型再生成は jockey endpoint 実装後に T020 でまとめて行う）
- [X] T018 [US2] `api/src/horseracing_api/queries.py` に `get_jockey`・`jockey_career_stats`(jockey_id 起点 GROUP BY)・`jockey_history`(騎乗馬名 join, paged) を実装。
- [X] T019 [US2] `api/src/horseracing_api/routers/jockeys.py`：`GET /jockeys/{id}`(JockeyProfile, 404) と `GET /jockeys/{id}/history`(Page[JockeyHistoryRow]) を実装。
- [X] T020 [US2] `front/openapi.json`＋`schema.d.ts` を 014 から再生成（HorseEntry の jockey_id/trainer_id 追加＋jockey endpoints を反映）し `scripts/check-openapi.sh` の drift-check を通す。続けて `front/src/api/queries.ts` に `useJockeyProfile`・`useJockeyHistory` を追加、`types.ts` re-export。
- [X] T021 [US2] `front/src/pages/JockeyDetailPage.tsx`：基本＋騎乗成績＋騎乗履歴を `QueryStateView`/`formatNum` で表示。
- [X] T022 [US2] `front/src/components/HorseEntriesTable.tsx`：**騎手名を `<Link to=/jockeys/{jockey_id}>` 化**（`jockey_id` 解決行のみ。`nk:`/null は非リンク）。
- [X] T023 [P] [US2] `front/src/pages/JockeyDetailPage.test.tsx` ＋ `HorseEntriesTable` の騎手リンク化テスト（Vitest+MSW）。

**Checkpoint**: US1・US2 が各々独立に機能

---

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T024 [P] `api/tests/integration/` に集計エッジテスト（同着 finish_order・全取消馬・surrogate horse/jockey・履歴ページ境界）。
- [X] T025 [P] `front`：nullable 数値の `formatNum`→`--` 表示と 3 状態（loading/empty/typed-error）を横断確認するテスト。
- [X] T026 `front/scripts/check-openapi.sh`（014 drift-check）が馬/騎手 endpoint・`HorseEntry` 追加後も in sync であることを CI/テストで確認。
- [X] T027 `quickstart.md` の end-to-end を実 DB（`horseracing@15432`）でスモーク（レース詳細→馬/騎手リンク→プロフィール、curl で 4 endpoint）。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 即着手可
- **Foundational (Phase 2)**: Setup 後。共有スキーマ・境界確認。**両ストーリーをブロック**
- **US1 (Phase 3)**: Foundational 後。MVP・他ストーリー非依存
- **US2 (Phase 4)**: Foundational 後。`HorseEntry` の jockey_id additive(T017) を含む。US1 と独立にテスト可（HorseEntriesTable は US1 で馬リンク、US2 で騎手リンクを別タスクで足す＝同ファイルなので順次）
- **Polish (Phase 5)**: 対象ストーリー完了後

### Within Each User Story

- テストを先に書き FAIL 確認 → queries(集約) → routers(endpoint) → 型再生成 → front hooks/page/link
- `HorseEntriesTable.tsx` は US1(T012 馬リンク)→US2(T022 騎手リンク)で同一ファイルを順次編集（並列にしない）

### Parallel Opportunities

- Setup の T002(front route) は T001 と並行可
- Foundational の T004(境界確認) は T003 と並行可
- 各ストーリーのテスト群（T005/T006, T014/T015/T016）は並行可
- front の別ファイル hooks/page test（T010/T013, T023）は並行可。T020(型再生成→hooks)は共有生成ファイルを触るため順次
- Foundational 後は US1/US2 を別担当で並行可（HorseEntriesTable の編集だけ順次調整）

---

## Parallel Example: User Story 1

```bash
Task: "T005 contract test in api/tests/integration/test_horses_api.py"
Task: "T006 stats test in api/tests/integration/test_horse_stats.py"
Task: "T010 hooks in front/src/api/queries.ts"
Task: "T013 page test in front/src/pages/HorseDetailPage.test.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1（馬ページ）→ **STOP & VALIDATE**（レース詳細→馬名→馬プロフィール）→ デモ可

### Incremental Delivery

1. Setup + Foundational → 土台
2. US1（馬プロフィール）→ 独立検証 → MVP デモ
3. US2（騎手プロフィール）→ 独立検証 → デモ
4. Polish（エッジ集計・drift-check・実 DB スモーク）

---

## Notes

- 新 DB スキーマ変更なし（既存テーブルの read 集約）。契約変更は `HorseEntry` への nullable 追加のみ（additive）
- read-only 不変（全 GET・app_ro・features/training 非 import）を維持。表示値はモデル特徴量に流さない（II）
- 事実集計（実績）とモデル予測（p/q）を型/hook/コンポーネントで分離（製品目的・II）
- 母数規則: 出走数=started、率の分子=finished&finish_order、平均着順=完走のみ。starts=0 は率 null（Unknown と 0 区別）
- リンク化は canonical ID のみ、`nk:` surrogate/null は非リンク
- 設計の非自明点は codex second opinion 済み（research.md D1〜D6）。実装中の新分岐は再度 second opinion
