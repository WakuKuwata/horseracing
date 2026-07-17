# Research: Counterfactual Return API Terminology

**Feature**: 075 | **Date**: 2026-07-16

## D1. provenance は「呼び出し側のオッズ」で決まる → 応答モデル層で命名

**Decision**: `api/backtest.py:win_realized()`([backtest.py:36](../../api/src/horseracing_api/backtest.py))は shadow-log(frozen `market_odds_used`)と favorite(current `race_horses.odds`)の両方が使う **odds 非依存コア**。内部 dataclass(`WinRealized`/`FavoriteRealized`/`ShadowLogSummary`)の field を **中立の `gross_return`/`net_return`** に改名し、**provenance 命名は API 応答モデル(`schemas.py`)層**で付ける:
- shadow-log / win backtest → `counterfactual_snapshot_gross_return`/`net_return`/`recovery_rate` + `valuation_basis="frozen_snapshot_odds"`
- favorite baseline → `current_odds_gross_return`/`net_return` + `valuation_basis="current_odds"`

**Rationale**: win_realized は共有コアなので、そこに provenance を焼き込むと二重命名になる。provenance は「どのオッズで精算したか」=呼び出し文脈で決まるので、応答モデル層で付けるのが正しい層。backtest.py:124-125 が「shadow-log は FROZEN market_odds_used、current odds は読まない」と明記=snapshot provenance が確定している。

**Alternatives**: (a) win_realized を frozen/current で二分岐 → 重複。(b) 内部も counterfactual_snapshot_* にする → favorite(current)で誤称。→ 中立コア + 応答層 provenance。

## D2. gross/net の定義(値不変)

**Decision**: `realized_return`(hit=odds 倍・miss=0)= **gross_return**(元返し込みの払戻倍率)。`realized_roi`(return−1)= **net_return**(純損益倍率)。値は現状と完全一致(命名のみ)。`recovery_rate`(Σ return / n_settled)= `*_recovery_rate`。**`n_scored` は追加しない**(`n_settled` と同義=冗長・analyze D1)。既存 `n_settled`/`n_hit`/`hit_rate` は保持。

**Rationale**: 074 codex レビューの gross/net/recovery 命名を踏襲。gross=払戻倍率・net=純益倍率で会計的に明確。

## D3. calibration realized_rate は改名しない(empirical 保護)

**Decision**: `CalibrationBin.realized_rate`/`realized_ci_low`/`realized_ci_high`([schemas.py](../../api/src/horseracing_api/schemas.py))は**改名しない**。これは reliability の**実際の勝率**(結果から観測した頻度)であり真に realized。改名は backtest/shadow-log/favorite 経路に限定し、grep で calibration の realized_rate/ci が意図的に残ることを回帰で固定。

**Rationale**: counterfactual(オッズ由来の反実仮想)と empirical(結果由来の実現頻度)は別概念。過剰改名は意味を壊す。

## D4. codex unavailable → セルフレビュー checklist

**Decision**: 本セッションで codex に 073/074 設計レビューを投げたが 2 回とも repo の AGENTS.md/speckit skill に derail し結論を出さず(**codex unavailable**)。075 は数値・ロジック変更なしの純命名 migration で命名は 073 proposal + 074 codex レビューで確定済み。→ 実装前セルフレビュー checklist で代替:

- **値不変**: 改名前後で全対応 field の数値一致(数値パリティ回帰)。win_realized のロジックは触らない。✓
- **provenance 正しさ**: shadow-log=frozen(snapshot)、favorite=current。backtest.py:124-125 の現状と一致。✓
- **empirical 保護**: calibration realized_rate/ci 不変(回帰 assert)。✓
- **原子同期**: openapi 再生成 → 型再生成 → fixtures/components → drift-check 緑。順序を tasks で固定。✓
- **read-only/schema-zero**: GET のみ・DB migration ゼロ。✓
- **破壊的変更の明示**: 後方互換フィールドを残さない(073 誤称の確実排除)。既存クライアント=front/admin は同 repo なので原子更新で追随。✓

**Rationale**: 低リスク・命名確定済み。codex 再々試行は derail 履歴からコスト過大。

## 未解決

なし。命名は確定・値不変・層設計(中立コア + 応答層 provenance)確定。
