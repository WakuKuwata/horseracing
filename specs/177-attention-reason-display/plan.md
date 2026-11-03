# Implementation Plan: 注目条件の該当理由の表示

**Branch**: `177-attention-reason-display` | **Date**: 2026-11-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/177-attention-reason-display/spec.md`

**Status**: **実装完了 2026-11-03(未コミット)**。US1/US2 実装・US3 見送り。テスト eval 736 / api 472 / training picks 26 / 凍結 4 / front 457 / admin 46 緑。実 DB 3 開催日 28 頭で pick・最新行と一致(SC-003)。実画面確認済み。記録は [spec.md](spec.md) 末尾「実装結果」と [tasks.md](tasks.md)。以下は plan 時↓ plan 済み 2026-11-03(codex plan レビュー 11 件・採用 11・不採用 0=1 点のみ形を変えて採用。採否は [research.md](research.md) D12)。

## Summary

138 の注目条件チップを持つ馬の展開パネルに、「どの条件を、判断時点のどの値で満たしたか」を部品(期待回収率の閾値・単勝オッズ帯・前走からの間隔)ごとに並べる照合表と一文の要約を加える。材料は API が返す: eval の判定 `match_mask` を部品マスクの合成に書き換え(意味論・署名・呼び出し側は不変・177 以前の本体の逐語コピーとビット一致をテストで固定)、同じ helper から 1 馬の部品状態(met / not_met / missing)を作り、`AttentionHorse.reason` として純追加で返す。応答の整合は builder の検査と応答モデルの validator で強制し、矛盾は 500 にせず `unavailable` + WARNING ログで返す。front は適合を導き直さず、応答の構造(閾値・帯・日数・状態)から文を組む。138 の凍結統計は「検証の根拠」の `<details>`(初期 閉)に移し、内容は変えない。DB 変更なし・チップと強調と一覧は不変・利益は約束しない。

## Technical Context

**Language/Version**: Python 3.12(eval / api)、TypeScript 5 + React 18(front)。

**Primary Dependencies**: numpy(eval の判定)、FastAPI + pydantic v2(api・discriminated union と `model_validator`)、React + Vite + Vitest + Testing Library + MSW(front)、openapi-typescript 7.4.4(型生成)。新規依存なし。

**Storage**: PostgreSQL 16。**変更なし**(`attention_picks.days_since_last` は 138 の 0019 で保存済み)。migration 0 本。

**Testing**: pytest(eval 単体・api 単体 + OpenAPI 契約の integration)、Vitest(front・admin)、`check:openapi`(drift)。実画面の確認は quickstart §5。

**Target Platform**: ローカルの単一オペレータ環境(`scripts/stack.sh` の api / front)。

**Project Type**: 既存 monorepo の eval(純関数)+ api(応答の純追加)+ front(表示)の 3 パッケージ。admin は OpenAPI スナップショットの同期のみ。

**Performance Goals**: 馬ごとに 3 比較の追加。応答は 1 頭あたり約 400 バイト増。測定対象ではない(SC に性能項目なし)。

**Constraints**: `match_mask` の意味論・`definitions_sha256`・`RULE_DEFINITIONS`・pick 書込み・`chip_rule`/`chip_now`/強調レベル/日付一覧/一覧ページは不変。OpenAPI は純追加(既存 property の required/nullable 不変・path 不変)。front は閾値を定数に持たない。表示規律(禁止語・損益色なし・勝率非表示・推定バッジ・回収率の基準ラベル・チップに閾値なし)。

**Scale/Scope**: 1 レース最大 18 頭・チップ馬は通常 0〜3 頭。変更ファイルは eval 1 + テスト 1、api 2(`attention.py` / `schemas.py`)+ テスト 1、front 新規 1 + 変更 3(`AttentionPanel.tsx` / `lib/attention.ts` / `styles.css`)+ テスト 3 + fixture、OpenAPI スナップショット 4(front/admin の json と schema.d.ts)。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.1 に基づくゲート:

- [x] **I. データ契約**: ID・ラベル・期間に触らない。応答は既存の `race_id` / `horse_id` のまま。**N/A(PASS)**。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 表示専用。新項目(部品の適合・間隔・条件値)はどの特徴量にも戻らない。eval `attention_rules.py` の import whitelist と禁止トークン検査(pandas / sqlalchemy / db モデル名)は既存テストが継続して固定。間隔は pick の凍結値(描画時刻から再計算しない)。**PASS**。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: モデル・条件・集計に変更なし → walk-forward 評価は非該当。代わりに「新 `match_mask` == 177 以前の本体(逐語コピー)」のビット一致(格子・乱数・閾値の隣の float・None/NaN/inf/Decimal・shape/dtype)と「全部品 met ⇔ matches」をテストで固定し、training の pick 書込みテストと凍結スクリプトの経路を実装後に踏む(research D1)。**PASS(N/A の理由を記録)**。
- [x] **IV. 確率整合性**: 勝率・p̂ は表示しない(既存 `assertAttentionDiscipline` が `勝率|p̂` を拒む)。期待回収率の単位のみ。**PASS**。
- [x] **V. 再現性・監査**: 判断時点の値は pick の凍結値(計算時刻つき)、現在値は最新行(計算時刻つき)を明示。期待回収率は `PseudoValue` の推定バッジ。`rule_set_version` を reason に含める。整合の違反は `unavailable` + ログで見える(黙って落とさない)。**PASS**。
- [x] **VI. feature 分割規律**: API 契約(contracts/api.md・OpenAPI 純追加)→ front/admin スナップショット同期 → 画面、の順。migration なし。**PASS**。
- [x] **品質ゲート**: codex second opinion を spec 段階(設計相談・採用 6・不採用 0)と plan 段階(`codex:codex-rescue`・11 件)で取得。暫定案との差分と採否は [research.md](research.md) D12。最大の是正=部品の missing を `chip_now == unknown` に結び付けていた暫定案の撤回(BLOCKER)と、整合違反を 500 にしない `unavailable` 状態の追加(HIGH)。**PASS**。

**Post-design re-check(Phase 1 設計後)**: data-model の INV-R1〜R7 と V1〜V10、contracts/api.md の規則表、contracts/display.md の不変テストが上記ゲートを維持する。違反なし。Complexity Tracking は空。

## Project Structure

### Documentation (this feature)

```text
specs/177-attention-reason-display/
├── spec.md              # 2026-11-03
├── plan.md              # このファイル
├── research.md          # D1〜D12 + codex plan レビュー採否表
├── data-model.md        # eval の ComponentState・api の応答モデルと整合規則 V1〜V10・front の表示状態
├── quickstart.md        # SC-001〜007 の検証手順
├── checklists/requirements.md
├── contracts/
│   ├── api.md           # GET /races/{id}/attention の純追加(reason)・規則表
│   └── display.md       # 展開パネルの順序・照合表の行・要約の文型・不変テスト
└── tasks.md             # /speckit-tasks の出力(未作成)
```

### Source Code (repository root)

```text
eval/
├── src/horseracing_eval/attention_rules.py   # _ev_mask/_band_mask/_gap_mask(既存式の移動)・match_mask を合成に・
│                                             # ComponentState / component_states(新)。matches/applicable_rules/chip_now は不変
└── tests/unit/test_attention_rules.py        # _match_mask_138_reference(逐語コピー)との一致・component_states・INV-R1〜R7

api/
├── src/horseracing_api/schemas.py            # ReasonValue / Ev|OddsBand|Gap ReasonComponent / AlsoApplicable /
│                                             # AttentionReason / AttentionReasonUnavailable / AttentionHorse.reason + model_validator
├── src/horseracing_api/attention.py          # _reason(chip, pick, current, applicable) / check_reason_consistency / build_attention 結線
└── tests/unit/test_attention.py              # reason の有無・値・境界・unavailable・validator・schema の既存 property 不変

front/
├── openapi.json, src/api/schema.d.ts         # 再生成(gen-types.sh)
├── src/api/types.ts                          # AttentionReason / ReasonComponent 型の re-export
├── src/lib/attention.ts                      # criterionText / statusText / reasonSummaryText / alsoApplicableText / formatEvAgainstThreshold
├── src/components/AttentionReasonTable.tsx   # 新: 要約・照合表・「…にも該当」・値源の注記・unavailable の一文
├── src/components/AttentionPanel.tsx         # ChipPanel: 見出し → AttentionReasonTable → <details>検証の根拠</details> → 免責文
├── src/styles.css                            # .attn-reason / .attn-panel__evidence(色なし)
├── src/tests/fixtures.ts                     # attentionHorseFixture に reason
└── src/components/{AttentionReasonTable,AttentionPanel,HorseEntriesTable}.test.tsx, src/lib/attention.test.ts

admin/
└── openapi.json, src/api/schema.d.ts         # front とバイト一致で再生成(表示は変えない)
```

**Structure Decision**: 判定は eval、応答の組み立てと整合は api、文の組み立てと描画は front、の既存の責務分担をそのまま使う(138 と同じ)。training / live / ops / db / scrape は触らない。

## Complexity Tracking

Constitution Check に違反なし。記載事項なし。

## Phase 0 / Phase 1 の成果物

- Phase 0: [research.md](research.md)(D1〜D12・NEEDS CLARIFICATION 0・codex 採否表)。
- Phase 1: [data-model.md](data-model.md)、[contracts/api.md](contracts/api.md)、[contracts/display.md](contracts/display.md)、[quickstart.md](quickstart.md)。agent context(`CLAUDE.md` の SPECKIT 区間)は手動更新(agent-context フックは CLAUDE.md を壊すので実行しない)。

## 実装の順序(tasks の骨格)

| 段 | 内容 | 完了条件 |
|---|---|---|
| 1 eval | 逐語コピー `_match_mask_138_reference` をテストに置く → helper 抽出と `match_mask` の合成化 → `ComponentState` / `component_states` → INV-R1〜R7 のテスト | eval 全テスト緑・`definitions_sha256` 不変・training `test_attention_picks.py` 緑 |
| 2 api | schemas(新モデル・`AttentionHorse.reason`・validator)→ `check_reason_consistency` → `_reason` と `build_attention` 結線(unavailable + WARNING)→ テスト(quickstart §2 の a〜g) | api 全テスト緑・`test_openapi_contract.py` 緑 |
| 3 契約 | API 起動 → `front/scripts/gen-types.sh` と `admin/scripts/gen-types.sh` → `git diff --numstat front/openapi.json` 削除 0 → `cmp` → `check:openapi` | 2 スナップショット一致・drift 緑 |
| 4 front | `lib/attention.ts` の純関数 + 単体テスト → `AttentionReasonTable` → `ChipPanel` の並び替えと `<details>` → fixtures → テスト(quickstart §4)→ `HorseEntriesTable.test.tsx` のチップ検査強化 | front / admin 全テスト・typecheck・lint 緑 |
| 5 実画面 | quickstart §5(pick と最新行との一致・折りたたみの可視性・読み上げ) | SC-001 / SC-003 を spec に転記 |

開催中でも実装可(DB・policy・稼働プロセスが読む共通コードに触らない。api の反映は `scripts/stack.sh restart api` だけ)。
