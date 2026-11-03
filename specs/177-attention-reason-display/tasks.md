# Tasks: 注目条件の該当理由の表示(177)

**Input**: Design documents from `/specs/177-attention-reason-display/`

**Prerequisites**: [plan.md](plan.md)・[spec.md](spec.md)・[research.md](research.md)(D1〜D12)・[data-model.md](data-model.md)・[contracts/api.md](contracts/api.md)・[contracts/display.md](contracts/display.md)・[quickstart.md](quickstart.md)

**Tests**: spec が要求する(FR-003 のビット一致・FR-004 の整合・FR-008/009 の表示規律・SC-002〜007)。テストは実装の **前** に書き、落ちることを見てから実装する。

**Organization**: user story 単位。ただし憲法 VI(UI は契約確定後)により **US2(API)→ US1(front)** の順に実装する。US3(数直線)は見送り可(spec)。

**開催中の制約**: 本 feature は DB・policy・稼働プロセスが毎ジョブ import する共通コード(scrape / ops / training / live)に触らない。api の反映は `scripts/stack.sh restart api` だけなので開催中でも実装・反映できる。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列可(別ファイル・未完了タスクに依存しない)
- **[Story]**: US1 / US2 / US3
- ファイルパスは repo ルートからの相対

---

## Phase 1: Setup(基準の保全)

**Purpose**: 書き換える前に、比較の基準を動かない形で残す。

- [X] T001 `eval/tests/unit/test_attention_rules.py` に 177 以前の `match_mask` 本体(`eval/src/horseracing_eval/attention_rules.py` L130〜L145・`_as_float_array` を含む)を **逐語コピー** した `_match_mask_138_reference(defn, *, ens_ev, single_ev, odds, days_since_last)` を置く(コピー元の git SHA をコメントに記録)。この時点で `_match_mask_138_reference` と現行 `match_mask` の一致テストを 1 本足して緑を確認する(基準の自己検証)。
- [X] T002 [P] `eval/tests/unit/test_attention_rules.py` に `definitions_sha256()` の **現在値を定数で固定** するテスト(`assert ar.definitions_sha256() == "<実行して得た 64 桁>"`・INV-R6)を足す。

**Checkpoint**: 基準(逐語コピー・ハッシュ値)が緑。

---

## Phase 2: Foundational(eval の部品判定・US1/US2 の両方が依存)

**Purpose**: `match_mask` を部品マスクの合成に書き換え、同じ helper から 1 馬の部品状態を作る(research D1・D2・data-model §1)。

### Tests(先に書く・T001 の基準に対して落ちる/通ることを見る)

- [X] T003 [P] `eval/tests/unit/test_attention_rules.py`: `component_states` の契約テスト(INV-R3/R4): 各条件で部品の種類と順序(S1/S2/S4 = ens_expected_return → win_odds → days_since_last、S3 = ens のみ)、None/NaN → `missing` かつ `value None`、境界(EV 1.20 → not_met・`np.nextafter(1.2, 2)` → met、odds 19.99/20.0/39.99/40.0、gap 13/14/112/112.5/113)、`Decimal` 入力、`uses_ensemble=False` の条件は単 seed の EV を使う。
- [X] T004 [P] `eval/tests/unit/test_attention_rules.py`: INV-R1「`match_mask[i]` ⇔ 全部品 met」を既存の格子(`test_scalar_and_vector_agree_on_a_grid` と同じ odds × gap × ev × ev)+ 乱数 20,000 行(seed 固定)で全条件に対して固定。
- [X] T005 [P] `eval/tests/unit/test_attention_rules.py`: INV-R2「新 `match_mask` == `_match_mask_138_reference`」を (a) 格子 (b) 乱数 20,000 行 (c) 各閾値とその隣の表現可能な float(`np.nextafter` の両側: 1.1 / 1.2 / 1.3 / 20.0 / 40.0 / 14 / 112)(d) スカラーとベクトルの混在・空配列・None・NaN・±inf・`Decimal` の各組合せで、値・shape・dtype(bool)の一致として固定。
- [X] T006 [P] `eval/tests/unit/test_attention_rules.py`: INV-R5「`chip_now == "matches"` ⇔ `component_states` 全部 met」を None / NaN / 境界 / S3 で固定し、既存 `test_chip_now` の期待(odds None → unknown・NaN は no_longer)が **変わらない** ことを併せて確認。INV-R7「`control=False` の条件はすべて `uses_ensemble=True`」を足す。

### Implementation

- [X] T007 `eval/src/horseracing_eval/attention_rules.py`: `_ev_mask(defn, ens_ev, single_ev)` / `_band_mask(defn, odds)` / `_gap_mask(defn, days_since_last)` を **既存の式の移動だけ** で作り(`_as_float_array`・`np.errstate(invalid="ignore")` の範囲・`odds_band is None` / `gap_days is None` のときは全 True)、`match_mask` を `np.logical_and.reduce` の合成に書き換える(出力 `np.asarray(mask, dtype=bool)` は維持・新しい正規化や型変換を足さない)。`matches` / `applicable_rules` / `chip_now` は不変。
- [X] T008 `eval/src/horseracing_eval/attention_rules.py`: `ComponentKind` / `ComponentStatus` / `@dataclass(frozen=True) ComponentState(kind, status, value)` と `component_states(defn, *, ens_ev, single_ev, odds, days_since_last) -> tuple[ComponentState, ...]`(1 要素の helper 呼び出し・定義順・条件に無い部品は含めない・None/NaN は `missing` かつ `value None`・それ以外は `float(value)` と met/not_met)。モジュール docstring の「Definitions」節に 177 の 1 段落を追記。import whitelist(`test_imports_are_whitelisted`)と禁止トークン(`test_no_leak_guard_tokens_in_source`)を守る(新 import なし)。
- [X] T009 `cd eval && uv run pytest tests/unit/test_attention_rules.py -q` 緑 → `cd training && uv run pytest tests/unit/test_attention_picks.py -q`(pick 書込みは無改修で緑)→ `cd scripts && uv run --project ../training pytest tests/test_freeze_rules_S1_S5.py -q`(凍結スクリプトの経路)→ `cd eval && uv run ruff check src tests`。

**Checkpoint**: eval 全テスト緑・`definitions_sha256` 不変・training / scripts の呼び出し経路が緑。

---

## Phase 3: User Story 2 - 応答が部品ごとの適合と間隔を返す(Priority: P1・契約先行)

**Goal**: `GET /api/v1/races/{race_id}/attention` の `AttentionHorse` に `reason` を純追加し、整合規則 V1〜V10 を builder と validator で強制する(contracts/api.md・data-model §2)。

**Independent Test**: 合成 pick と最新行から `build_attention` を回し、`reason` の部品が既存 `chip_now` と全ケースで整合し、既存項目が不変で、OpenAPI 差分が純追加。

### Tests(先に書く)

- [X] T010 [P] [US2] `api/tests/unit/test_attention.py`: `reason` の有無と値 — S1 チップ(ens 1.35・odds 30・gap 40)で `status=available`・`rule_id=S1`・部品 3 つの順序と条件値(1.2 / 20.0,40.0 / 14,112)・`judged` の値が `judged` snapshot と一致し全部 met・`current` の値が最新行と一致・間隔の `current.value == judged.value` かつ `current_source=pick_frozen`・`also_applicable == [S2(note_ev_gt 1.3), S3(None), S4(None)]`(対照 S5 なし・チップ S1 なし)。チップなし・S5 のみ・void 済み(取消)で `reason is None`。チップなしの馬の現在値が条件を満たしても `None` のまま(後の価格でチップは付かない)。
- [X] T011 [P] [US2] `api/tests/unit/test_attention.py`: 状態の分岐 — 現在 odds 45.0 → odds 部品 `not_met`・他 met・`chip_now=no_longer`;`current` None(出走馬変更 / オッズ欠落 / 表示版の行なし)→ 全部品 `current is None`・`chip_now=unknown`;S3 チップ(間隔 null の pick)→ 部品は EV の 1 つで `reason` あり;S1 pick の `days_since_last=None`(合成)→ 間隔部品 `missing`・総合 `no_longer`;`days_since_last=Decimal("112.5")` → `not_met`(切り捨てない);別 run の単 seed 行は `reason` に影響なし;不通過 / 保留のチップ(`_tallies(S1=failed)`)でも `reason` あり;`field_changed_after_pick=True` でも `reason` は判断時点の値のまま。
- [X] T012 [P] [US2] `api/tests/unit/test_attention.py`: 整合 — `AttentionHorse(...)` を直接組んだ矛盾(全部品 met で `no_longer` / `current` None で `matches` / `reason` ありで `chip_rule` None / `rule_id` 不一致 / 部品の順序違い / 条件値が定義と違う / `judged.value` が snapshot と違う / `also_applicable` にチップや S5)がそれぞれ `pydantic.ValidationError`。`check_reason_consistency` を monkeypatch で常に違反にすると `build_attention` は 200 相当の応答で `reason.status == "unavailable"`・`reason.reason == "inconsistent"` を返し、`caplog` に WARNING(race_id・horse_id・違反名)が出て、`chip_now` など既存項目は不変。
- [X] T013 [P] [US2] `api/tests/unit/test_attention.py`: 契約の純追加 — `AttentionHorse.model_json_schema()` の既存 property 名 12 個(`horse_id, horse_number, applicable, chip_rule, chip_stage, chip_s2, chip_now, judged, current, field_changed_after_pick, levels, stages, pick_status`)と required 集合が 138 時点の一覧(テスト内に列挙)と一致し、追加は `reason` のみ(required・nullable)。`EvSnapshot` / `AttentionDayItem` の property 集合が不変。

### Implementation

- [X] T014 [US2] `api/src/horseracing_api/schemas.py`: `ReasonValue` / `EvReasonComponent` / `OddsBandReasonComponent` / `GapReasonComponent` / `ReasonComponent`(`Field(discriminator="kind")`)/ `AlsoApplicable` / `AttentionReason(status="available")` / `AttentionReasonUnavailable(status="unavailable", reason="inconsistent")` / `AttentionReasonResponse`(`discriminator="status"`)を `AttentionHorse` の直前に追加(全部 `extra="forbid"`・docstring に値の由来)。`AttentionHorse` の末尾に `reason: AttentionReasonResponse | None`(既定値なし)。
- [X] T015 [US2] `api/src/horseracing_api/attention.py`: 純関数 `check_reason_consistency(*, chip_rule, chip_now, judged, current, applicable, reason) -> list[str]`(data-model §2 V1〜V10 を 1 規則 1 文字列で返す・`horseracing_eval.attention_rules` の `RULE_DEFINITIONS` / `RULE_SET_VERSION` を参照・DB なし)。`schemas.py` の `AttentionHorse` に `@model_validator(mode="after")` を足し、V1 と(`status="available"` のとき)V2〜V10 の違反を `ValueError` にする(schemas → attention の循環 import を避けるため、関数は `api/src/horseracing_api/attention_reason.py` に置き両方から import する)。
- [X] T016 [US2] `api/src/horseracing_api/attention.py`(実装時に移動: `attention_reason.py` は schemas から import されるので schemas のモデルを使う `build_reason` は循環を避けて attention.py に置いた・`note_ev_gt` は attention_reason.py): `build_reason(*, chip, pick, current, applicable) -> AttentionReason`: `ar.definition(chip)`(`uses_ensemble` でなければ作らない=assert)、`ar.component_states` を判断時点(pick の Decimal → float)と現在値(`current` の EV / odds + pick の間隔)で呼び、部品モデルへ写す(`current` None なら部品の `current` も None)。`also_applicable` は `applicable` から対照とチップを除き rank 順・`note_ev_gt` は `uses_ensemble`・`odds_band`・`gap_days` が等しい条件にだけ。
- [X] T017 [US2] `api/src/horseracing_api/attention.py::build_attention`: チップありの馬で `build_reason` → `check_reason_consistency` → 違反なしなら `reason`、違反ありなら `logger.warning("attention reason inconsistent race=%s horse=%s violations=%s", …)` + `AttentionReasonUnavailable`。`AttentionHorse(..., reason=reason)` に渡す(チップなしは None)。モジュール docstring の「Display sources」に 177 の段落を追記。`build_day_items` は不変。
- [X] T018 [US2] 既存テストの直接構築(`api/tests/unit/test_attention.py` 以外で `AttentionHorse(` を組む箇所があれば grep)に `reason` を明示的に足す。`cd api && uv run pytest tests/unit/test_attention.py tests/integration/test_openapi_contract.py -q` 緑・`uv run ruff check src tests`。`test_new_modules_are_inside_the_no_write_scan` の集合に `attention_reason.py` を加える。
- [X] T019 [US2] 契約の再生成: `scripts/stack.sh restart api`(または起動)→ `(cd front && bash scripts/gen-types.sh)` → `(cd admin && bash scripts/gen-types.sh)` → `git diff --numstat front/openapi.json`(削除行 0。実測: 削除 1 行=`required` 配列の末尾カンマの付け替えのみ=純追加)→ `cmp front/openapi.json admin/openapi.json` → `pnpm -C front check:openapi && pnpm -C admin check:openapi` → `pnpm -C admin test`(admin の表示は不変)。`front/src/api/types.ts` に `AttentionReason` / `AttentionReasonResponse` / `ReasonComponent` / `ReasonValue` / `AlsoApplicable` の re-export を足す。

**Checkpoint**: api 全テスト緑・OpenAPI 純追加・front/admin スナップショット一致・drift 緑。

---

## Phase 4: User Story 1 - 展開パネルで該当理由を読む(Priority: P1)🎯 MVP(表示)

**Goal**: `ChipPanel` の冒頭に要約・照合表・「…にも該当」・値源の注記を出し、138 の統計を `<details>`「検証の根拠」に移す(contracts/display.md)。

**Independent Test**: fixture の `reason` から照合表の値・適合・要約文が応答どおりに出て、チップ・見出し・バッジに閾値の数字が無く、折りたたみを開いた状態で 138 の規律と全開示が残る。

### Tests(先に書く)

- [X] T020 [P] [US1] `front/src/tests/fixtures.ts`: `attentionReasonFixture(overrides)`(S1・3 部品・contracts/api.md の例の値: judged 1.31 / 32.5 / 41、current 1.246 / 30.8 / 41、`also_applicable` S2(1.3)/S3/S4)と `attentionHorseFixture` の `reason` 既定(`judged`/`current` snapshot の EV・odds と同じ値にする)、`attentionNoChipHorseFixture` は `reason: null`。
- [X] T021 [P] [US1] `front/src/lib/attention.test.ts`: 純関数のテスト — `criterionText`(3 種の文・閾値は応答の値から・`ev_gt 1.3` → 「130% 超」)、`statusText`(met/not_met/missing/current null の 4 語)、`reasonSummaryText`(contracts/display.md §3 の 4 文型・n=1 の S3 文・満たさない部品名の列挙・missing は「値なし」)、`alsoApplicableText`(`note_ev_gt` あり→「S2(期待回収率 130% 超)にも該当」・無し→「S3・S4 にも該当(表示する条件は最上位の S1)」・閉じた段階のタグ・空なら null)、`formatEvAgainstThreshold`(1.246/1.2 → 「124.6%」・1.2004/1.2 → 「120.04%」・1.20004/1.2 → 「120.004%」・ちょうど 1.2/1.2 は 3 桁まで増やしても同じなので「120.000%」で打ち切り=適合の語「満たさない」が状態を言う)。文に `ATTENTION_SCOPE` の語が無い。
- [X] T022 [P] [US1] `front/src/components/AttentionReasonTable.test.tsx`: spec US1 シナリオ 1〜8 — (1) 3 行・判断時点 131.0%(推定)/32.5 倍/41 日すべて「満たす」・現在値 124.6%(推定)/30.8 倍/41 日(判断時点で固定)すべて「満たす」・要約文型 1・`th scope="col"` 3 つと `th scope="row"` 3 つ・記号 `aria-hidden`・`data-status` (2) 現在 45.0 倍 → odds 行だけ「満たさない」・要約文型 2 (3) `current` null → 全行「最新の計算なし」・要約文型 3 (3b) 間隔 `missing` → 「値なし」(「最新の計算なし」ではない) (4) S3 → 1 行 (5) S1+S2 → 「S2(期待回収率 130% 超)にも該当」 (6) S1 不通過・S3 チップ → 「S1(300 点不通過)・S4 にも該当(表示する条件は S3)」 (7) `reason.status="unavailable"` → 表と要約なし・`attention-reason-unavailable` の一文 (8) `assertPseudoLabelCoverage`(EV 2 値)・`assertAttentionDiscipline`・`assertRoiBasisLabels` 相当の `N%` 包含(`criterion` / `expected_return` の `data-kind`)・損益色なし。
- [X] T023 [P] [US1] `front/src/components/AttentionPanel.test.tsx`: (a) パネルの順序 = 見出し → `attention-reason-summary` → `attention-reason-table` → `attention-reason-also` → 値源の注記 / `attention-field-changed` → `attention-evidence` → `attention-panel-disclaimer` (b) `<details>` が初期状態で `open` を持たず、`userEvent.click(summary)` で `open` が付き、中に 138 の `dt` が同じ並びで全部ある (c) 開いた状態で既存の 23 テストの期待(過去検証・判断時点の見込み・前向き・価格ずれ・鮮度・免責文・`assertRoiBasisCoverage` 非空)がそのまま通る(`panel()` ヘルパが summary を開く) (d) 見出し・バッジ・副チップの text と `aria-label` に `\d\s*(%|倍|日)` が無く、照合表にはある (e) 「期待回収率」行は単 seed の 1 行に縮む(`attention-judged` / `attention-current` の期待文字列を更新) (f) S5 のみ・取消・チップなしで `attention-reason-table` が無い(既存テストに assertion 追加)。
- [X] T024 [P] [US1] `front/src/components/HorseEntriesTable.test.tsx`: 既存「チップに `%|倍|超` が無い」を `\d\s*(%|倍|日|超)` に広げ、`attention-chips` 配下の全要素の text・`aria-label`・`aria-labelledby` の参照先・`title` を検査。展開パネル(`attention-panel`)の照合表には閾値がある(正の確認)。SC-007 の既存テスト(`scoped display discipline …`)は `attention-evidence` を開いてから当てる。

### Implementation

- [X] T025 [US1] `front/src/lib/attention.ts`: `criterionText(component)` / `statusText(value: ReasonValue | null)` / `STATUS_GLYPH`(○ × —)/ `reasonSummaryText(reason, chipRule, judgedAtText)` / `alsoApplicableText(reason, stages, chipRule)` / `formatEvAgainstThreshold(value, evGt)` / `COMPONENT_NAMES`(期待回収率 / 単勝オッズ帯 / 前走からの間隔)を純関数で追加。閾値・帯・日数の定数は持たない(すべて引数の構造から)。
- [X] T026 [US1] `front/src/components/AttentionReasonTable.tsx`(新): props `{ reason: AttentionReasonResponse; chip: RuleId; stages; judgedAt: string | null; fieldChanged: boolean }`。`status="unavailable"` なら一文のみ。available なら要約 `<p data-testid="attention-reason-summary">` → `<table className="attn-reason" data-testid="attention-reason-table">`(`<caption>` 視覚的に隠す・`th scope`・行ごとに `<td data-status>` に `<span aria-hidden>記号</span> 語`・EV は既存 `EvValue` を export して再利用・odds `formatNum(v,1)` 倍・間隔は Decimal 由来の値をそのまま `formatNum`・現在値の間隔に「(判断時点で固定)」・`current` null は「最新の計算なし」)→ `<p data-testid="attention-reason-also">`(対照の「対照 S5 にも該当」も併記)→ 値源の注記と `attention-field-changed`。
- [X] T027 [US1] `front/src/components/AttentionPanel.tsx::ChipPanel`: 見出しの直後に `AttentionReasonTable`、既存 `<dl>` を `<details className="attn-panel__evidence" data-testid="attention-evidence"><summary>検証の根拠(過去検証・判断時点の見込み・前向き検証・価格ずれ試験・価格鮮度)</summary>…</details>` で包む(`open` 属性なし)。「期待回収率」行は `EvLine` を単 seed のみの 1 行(「単 seed 判断時点 128.0% 推定・現在値 126.0% 推定」/ 現在値なしは既存文)に縮め、値源の注記と `attention-field-changed` は `AttentionReasonTable` へ移す。`chipNowText` は照合表の要約に吸収されるので削除(`CHIP_NOW_LABELS` はチップ側で継続使用)。`EvValue` を export。モジュール docstring を更新。
- [X] T028 [P] [US1] `front/src/styles.css`: `.attn-reason`(表・`th` 左寄せ・`td[data-status]` は色なし・モバイルで横スクロール)、`.attn-panel__evidence > summary`(既存 `attn-panel__note` と同じトーン・カーソル pointer)、`.attn-reason__glyph`(等幅)。損益色クラスは使わない。
- [X] T029 [US1] `pnpm -C front test -- AttentionReasonTable AttentionPanel HorseEntriesTable attention` → `pnpm -C front test` → `pnpm -C front typecheck && pnpm -C front lint && pnpm -C front check:openapi`。既存 138 テストの変更は「期待文字列の位置(折りたたみの中)」と「期待回収率行の縮小」に限ることを diff で確認。

**Checkpoint**: front 全テスト緑・US1 シナリオ 1〜10 が fixture で再現。

---

## Phase 5: User Story 3 - オッズ帯の数直線(Priority: P3・見送り可)

**Goal**: 照合表の odds 行に 20〜40 の帯と 2 点(判断時点・現在値)の小さな数直線を添える。適合は応答の値(描画座標から判定しない)。

**Independent Test**: 32.5 と 45.0 が帯の内外に置かれ、ラベルは「判断時点」「現在値」、S3 では出ない。

- [ ] T030 [P] [US3] `front/src/components/AttentionReasonTable.test.tsx`: 数直線 — `lo`/`hi` の目盛(20 は含む・40 は含まない の注記)・2 点の `aria-label`「判断時点 32.5 倍」「現在値 45.0 倍」・S3(odds 部品なし)で描かれない・`current` null で現在値の点なし・色クラスなし。
- [ ] T031 [US3] `front/src/components/AttentionReasonTable.tsx`: odds 行の下に inline SVG(`role="img"`・`aria-label` に 2 点の値・帯の端は応答の `lo`/`hi`・点の位置は `clamp((v−lo)/(hi−lo))` の表示専用計算・値が帯の外は端に置き「帯の外」と文で添える)。適合の語は T026 のまま。

**Checkpoint**: 初版では見送り(2026-11-03・spec US3 に記録)。T030/T031 は未着手のまま残す。

---

## Phase 6: Polish & 実画面・記録

**実測(2026-11-03)**: SC-003 = 3 開催日(11/01・10/12・10/18)の全チップ馬 28 頭(S3 14・S4 12・S1 2)で判断時点=pick 行・現在値=最新 ens15 行と一致、`chip_now` と部品の整合も全件成立。実画面 = 202608041111 の 9 番(S1・判断時点のみ該当)で照合表 3 行・要約・「S2(期待回収率 130% 超)にも該当。S3・S4 にも該当(表示する条件は S1)。対照 S5 にも該当。」・折りたたみ初期 閉(スクリーンショットで中身が見えない)→ summary クリックで 138 の 7 行が出る・チップとアクセシブルネームに閾値なし。VoiceOver は未実施(記号は `aria-hidden`・語は DOM に常在、構造で担保)。回帰: eval 736 / api 472 / training picks 26 / 凍結スクリプト 4 / front 457 / admin 46 緑。

- [X] T032 実画面(quickstart §5): `scripts/stack.sh restart api`・front を起動し、2026-10 以降の開催日でチップのある馬を展開 → 照合表の判断時点の値が `attention_picks` の pick 行と、現在値が `market_ev_predictions(mev-ens15-v1)` の最新行と一致することを psql で確認(SC-003)→ 「検証の根拠」が閉じていて中身が見えない・開くと 138 の統計が見える(可視性は実ブラウザのみ)→ VoiceOver で適合の語が 1 回・記号が読まれない → チップに閾値の数字なし(SC-005)→ 現在値なしのレースがあれば全行「最新の計算なし」。結果と確認日を `spec.md` の SC-001/003/005 に転記。
- [X] T033 [P] 回帰(quickstart §6): `cd eval && uv run pytest -q`・`cd api && uv run pytest -q`・`cd training && uv run pytest tests/unit/test_attention_picks.py -q`・`cd scripts && uv run --project ../training pytest tests/test_freeze_rules_S1_S5.py -q`・`pnpm -C front test && pnpm -C admin test`。件数を `plan.md` の Status に記録。
- [X] T034 [P] `specs/177-attention-reason-display/spec.md` の Status を「実装完了」に、US3 の採否、SC-001〜007 の実測を転記。`plan.md` の Status を更新。
- [X] T035 `CLAUDE.md` の SPECKIT 区間の冒頭(177 の段落)を「実装完了・テスト件数・US3 の採否」に更新(agent-context フックは使わない・Edit で手動)。
- [X] T036 コミット(`git add` は 177 のファイルと変更した src/test/snapshot を **個別に指定**・`git add -A` 禁止): `feat(177): 注目条件の該当理由を展開パネルに表示` — eval の合成化と `component_states`、api の `reason` 純追加と整合検査、front の照合表と折りたたみ、OpenAPI スナップショット、spec/plan/tasks。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1(基準)**: 依存なし。T001 が先、T002 は並列。
- **Phase 2(eval)**: T001 の基準に依存。T003〜T006(テスト)は並列、T007 → T008 → T009 の順。**US1 / US2 の両方をブロック**。
- **Phase 3(US2 api)**: Phase 2 完了後。T010〜T013(テスト)並列 → T014 → T015 → T016 → T017 → T018 → T019(契約の再生成は api 緑の後)。
- **Phase 4(US1 front)**: **T019(型の再生成)に依存**(憲法 VI・front は生成型を使う)。T020〜T024(テスト・fixture)並列 → T025 → T026 → T027(T028 は並列)→ T029。
- **Phase 5(US3)**: Phase 4 完了後・見送り可。
- **Phase 6**: Phase 4(と実装した場合 Phase 5)完了後。T033 / T034 は並列、T035 → T036 は最後。

### User Story Dependencies

- **US2(API)**: Phase 2 の `component_states` に依存。他の story に依存しない。単独で OpenAPI・api テスト・admin テストまで完結する。
- **US1(front)**: US2 の生成型(`schema.d.ts`)に依存。MSW fixture で API なしにテストできる。
- **US3**: US1 の `AttentionReasonTable` に依存。見送り可。

### Parallel Opportunities

- Phase 2: T003 / T004 / T005 / T006 を同時に書く(同じテストファイルだが別の test 関数=1 人なら順に、複数なら別ブロック)。
- Phase 3: T010〜T013 を同時に書く。T014(schemas)と T016(attention_reason.py)は別ファイルだが T015 の関数配置に依存するので T014 → T015 → T016 の順。
- Phase 4: T020 / T021 / T022 / T023 / T024 は別ファイルで並列。T028(CSS)は T026/T027 と並列。
- Phase 6: T033 と T034 は並列。

---

## Parallel Example: Phase 4(US1)

```bash
# テストと fixture を同時に:
Task: "T020 front/src/tests/fixtures.ts に attentionReasonFixture"
Task: "T021 front/src/lib/attention.test.ts の純関数テスト"
Task: "T022 front/src/components/AttentionReasonTable.test.tsx"
Task: "T023 front/src/components/AttentionPanel.test.tsx の順序・details・閾値なし"
Task: "T024 front/src/components/HorseEntriesTable.test.tsx のチップ検査強化"

# 実装(T025 → T026 → T027 の順・T028 は並列):
Task: "T028 front/src/styles.css"
```

---

## Implementation Strategy

### MVP(US2 + US1)

1. Phase 1 → Phase 2(eval・基準とのビット一致)。
2. Phase 3(US2・API と契約)→ `scripts/stack.sh restart api` で応答を実 DB で見る(reason の値 = pick の値)。
3. Phase 4(US1・front)→ 実画面(T032)。
4. **STOP**: SC-001〜007 を確認。US3 は見送りでよい。

### 開催中の実装

- 全フェーズとも DB・policy・ops / scrape / training / live の稼働コードに触らない。api の反映は `restart api` のみ、front は dev server の HMR。
- Phase 2 の `attention_rules.py` は training の pick 書込み(ops の subprocess が毎ジョブ import)からも読まれる。**挙動不変**(T005/T009 で担保)なので開催中に反映しても pick は変わらないが、反映は api 再起動と同じタイミングで 1 回にまとめる。

---

## Notes

- codex tasks レビューは取らない: tasks は plan(codex plan レビュー 11 件を反映済み)の機械的な分解で、新しい設計判断を含まない。実装中に plan から外れる判断が出たら `codex:codex-rescue` を呼ぶ。
- 逐語コピー `_match_mask_138_reference` は 177 以降もテストに残す(将来 `match_mask` を触る変更の恒久ガード)。
- `reason` の `unavailable` は実装のバグの表示。実 DB で 1 件でも出たら WARNING ログの違反名から原因を追う(黙って出続けさせない)。
- `git add -A` 禁止(並行セッションの変更を巻き込んだ前例)。
