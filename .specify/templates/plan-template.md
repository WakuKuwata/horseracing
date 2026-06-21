# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]

**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]

**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]

**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]

**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]

**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]

**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]

**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]

**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート (各項目 PASS / N/A を明記):

- [ ] **I. データ契約**: `raceId` 12桁固定、2007年以降のみ、JRA-VAN/netkeiba ID は `id_mappings` 経由でのみ結合、ラベルは `1着率`/`2着以内率`/`3着以内率`。
- [ ] **II. リーク防止 (NON-NEGOTIABLE)**: 全特徴量に source・利用可能タイミング・欠損処理を記載。結果後情報を予測特徴量に使わない。累積統計は対象レース前/out-of-fold。walk-forward 境界日を特徴量計算に適用。
- [ ] **III. 評価先行 (NON-NEGOTIABLE)**: model/feature 変更は walk-forward out-of-sample 評価。採用は baseline 比較 + ECE。評価ハーネスを学習より先に用意。
- [ ] **IV. 確率整合性**: `0≤1着率≤2着以内率≤3着以内率≤1`、レース内合計 ≈1/2/3、取消・除外は除外して再正規化、Unknown と 0 を区別。
- [ ] **V. 再現性・監査**: 予測・推奨保存時に model_version・特徴量定義版・使用オッズ・疑似ROI ロジック版・計算時刻を保持。オッズは上書き、推定オッズは「疑似評価」と明示。
- [ ] **VI. feature 分割規律**: UI は API/DB 契約確定後に着手。P0 未決を含む場合は spec/plan で方式固定。予測・推奨系テーブルの最小契約を初期 DB に含める。
- [ ] **品質ゲート**: 非自明な設計判断は `codex:codex-rescue` / `/codex:rescue` の second opinion を取り、両案差分と採用根拠を plan.md/tasks.md に記録。

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
