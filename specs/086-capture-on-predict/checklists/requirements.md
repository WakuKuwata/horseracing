# Specification Quality Checklist: 予測実行時の荒れ度スナップショット捕捉

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-27
**Feature**: [spec.md](../spec.md)

## Content Quality

- [X] No implementation details (languages, frameworks, APIs) — **文書化した例外あり(下記)**
- [X] Focused on user value and business needs
- [X] Written for non-technical stakeholders
- [X] All mandatory sections completed

## Requirement Completeness

- [X] No [NEEDS CLARIFICATION] markers remain
- [X] Requirements are testable and unambiguous
- [X] Success criteria are measurable
- [X] Success criteria are technology-agnostic (no implementation details) — **文書化した例外あり(下記)**
- [X] All acceptance scenarios are defined
- [X] Edge cases are identified
- [X] Scope is clearly bounded
- [X] Dependencies and assumptions identified

## Feature Readiness

- [X] All functional requirements have clear acceptance criteria
- [X] User scenarios cover primary flows
- [X] Feature meets measurable outcomes defined in Success Criteria
- [X] No implementation details leak into specification

## 検証で直した点(2 周目・全 28 箇所を修正して再検証で全項目 PASS)

1 周目で以下が不合格だったため spec を修正した:

- **実装詳細の混入**: FR に `fetch_win_odds` / `pg_advisory_xact_lock` / `subprocess` /
  `ingestion_jobs.summary` といった実装識別子が入っていた
  → 「取得」「排他制御」「ジョブ実行層」「構造化してジョブに残す」という**振る舞いの記述**に置換。
  実コードの所在は「背景・目的」の確認結果テーブルに事実として残し、要件からは分離した。
- **成功基準が技術的**: 「advisory lock を取得の前に移動」等が SC に入っていた
  → 「同時捕捉で取得 1 回・有効行 1 行に収束する」という**観測可能な結果**に置換。
- **テスト不能な要件**: 「politeness を担保する」が曖昧だった
  → FR-016..019 に分解(拒否・制限応答の後は再試行しない / プロセス跨ぎで制限 /
  内側の試行 budget < 外側の打ち切り / 打ち切りは結果不明として記録)。
- **User Scenarios 側にも実装語が残っていた**(`status='active'` / `typed skip` /
  `void_reason` / `capture_trigger` / `prospective-report` / `403/429` / `fail-closed` /
  `canonical field` 等)→ 受入シナリオと成功基準も含め計 28 箇所を振る舞い記述に統一。
  検証で「Requirements だけ直して満足しない」ことを確認できたのが 2 周目の収穫。

## Notes

- 主 horizon の**具体値**(下限・上限の秒数)は意図的に spec で固定していない。
  これは事前登録の対象であり plan 段階で決めて artifact に凍結する(Assumptions に明記)。
  spec は「窓を必須にする・fail-closed にする・窓内の最初が勝つ」という**規則**を固定する。
- migration の要否・テーブル列名・ジョブ実行の仕組みは plan の領分として意図的に書いていない。


## 文書化した例外(「実装詳細なし」を意図的に外した箇所)

2 周目の一斉除去の後も、以下は**意図して具体名を残している**。
抽象化すると要件が検証不能になるか、084 が実際に壊れた欠陥を指し示せなくなるため。

| 箇所 | 残した具体名 | 残す理由 |
|---|---|---|
| FR-001b1 / FR-004a | `--allow-outside-horizon` / `--min-seconds-to-post` | **既定の挙動が変わる**破壊的変更で、操作者は名前を知らないと回避できない。名前が要件の一部 |
| SC-010 例外 2 / 9 | `recaptured` / `late_scratch` / `field_changed` / `field_changed_after_capture` / `no_snapshot` | **永続化される値と公開契約の値**。抽象化すると「どの値が消えてどの値が増えるか」という不変条件そのものが言えなくなる |
| SC-010 例外 7 | `chaos_bands.py` の行番号 | 「構造的に到達しない」という主張の**根拠の所在**。これが無いと将来の読み手が検証できない |

いずれも 086 が**既存の出荷済みコードを是正する** feature であることに由来する。
新規機能なら不要な精度である。
