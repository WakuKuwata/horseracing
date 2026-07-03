# Specification Quality Checklist: データ被覆率 + ジョブ履歴

**Created**: 2026-07-03 / **Feature**: [spec.md](../spec.md)

## Content Quality
- [X] 051 プログラム全体像を参照(全体設計の再記述なし)
- [X] User value 明確(backfill の成果と穴・ジョブ成否が一目)

## Requirement Completeness
- [X] No [NEEDS CLARIFICATION]
- [X] FR/SC 対応・testable(範囲ガード・active 不在・フィルタ)
- [X] Scope bounded(read のみ・アクションは 3 回目)

## Feature Readiness
- [X] エッジ識別(active モデル不在・>400 日・未知フィルタ値=空)
- [X] 憲法整合(read-only 014 不変・VI 契約先行・グループ化 SQL=一定コスト)
- [X] codex 見送り宣言(3 回起動失敗)
