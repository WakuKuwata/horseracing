# Data Model: Counterfactual Return API Terminology

**Feature**: 075 | **スキーマ変更**: なし(DB 不変)。API 応答モデルのフィールド**改名のみ・値不変**。

## 改名対応表(値は全て不変)

### 1. 単勝 backtest(FROZEN market_odds_used 由来 → counterfactual snapshot)

| 旧(削除) | 新 | 定義(不変) |
|---|---|---|
| `realized_return` | `counterfactual_snapshot_gross_return` | hit=odds 倍・miss=0(払戻倍率) |
| `realized_roi` | `counterfactual_snapshot_net_return` | gross−1(純益倍率) |
| — | `valuation_basis` | `"frozen_snapshot_odds"`(新規・provenance 明示) |

### 2. shadow-log サマリ(FROZEN 由来)

| 旧 | 新 |
|---|---|
| `recovery_rate` | `counterfactual_snapshot_recovery_rate`(Σ gross_return / n_settled・不変) |
| `by_month[].recovery`(**ShadowLogMonth 応答モデルの field**=T003 が owner) | `by_month[].counterfactual_snapshot_recovery` |
| — | `valuation_basis="frozen_snapshot_odds"`(新規) |
| `n_settled`/`n_hit`/`hit_rate` | **保持**(結果由来の集計・不変・recovery の分母は `n_settled`) |

**analyze D1**: `n_scored` は `n_settled` と同義=冗長なので**追加しない**(`n_settled` を分母として保持)。
**analyze I1/U1**: `by_month[].recovery` の provenance 命名は **ShadowLogMonth pydantic 応答モデル層**(schemas.py)で行う=中立コア規則と矛盾しない(内部 `ShadowLogSummary.by_month` の raw dict key は下記 §internal のとおり中立のまま可・応答モデルへのマップ時に provenance 名を付与)。owner は **T003**。

### 3. favorite baseline(current race_horses.odds 由来 → current_odds provenance)

| 旧 | 新 |
|---|---|
| `realized_return` | `current_odds_gross_return` |
| `realized_roi` | `current_odds_net_return` |
| — | `valuation_basis="current_odds"`(新規) |

### 4. calibration reliability(empirical=改名しない)

| フィールド | 対応 |
|---|---|
| `realized_rate` | **不変**(実際の勝率=empirical realized) |
| `realized_ci_low` / `realized_ci_high` | **不変**(Wilson CI) |

## 内部 dataclass(api/backtest.py・中立命名)

`win_realized()` は shared core なので内部 dataclass の field は provenance 非依存の中立名にする:

| dataclass | 旧 field | 新 field(中立) |
|---|---|---|
| `WinRealized` | `realized_return`/`realized_roi` | `gross_return`/`net_return` |
| `FavoriteRealized` | `realized_return`/`realized_roi` | `gross_return`/`net_return` |
| `ShadowLogSummary` | `recovery_rate` | 内部は中立のまま可。`by_month` の raw dict key(`recovery`)も**内部は中立**=provenance 名は ShadowLogMonth 応答モデル層で付与(I1 解消) |

provenance ラベル(`counterfactual_snapshot_*` / `current_odds_*` / `valuation_basis`)は **routers が dataclass→応答モデルへマップする層**で付与。

## front/admin(生成物 + 表示)

| 対象 | 変更 |
|---|---|
| `front/src/api/schema.d.ts`・`admin/src/api/schema.d.ts` | openapi-typescript 再生成(新フィールド名) |
| `front/openapi.json`・`admin/openapi.json` | api 生成 OpenAPI と byte 一致(drift-check) |
| `*/tests/fixtures.ts` | 新フィールド名 |
| **front** `ShadowLogPanel.tsx` + `RecommendationPanel.tsx`(内 `WinBacktestSummary` inline function・I3・+ FavoriteBaseline セクション)(+ .test) | 表示ラベル=「反実仮想(判断時オッズ)」「現在オッズ基準」 |
| **admin** | 表示コンポーネント無し(analyze I2)=型/snapshot 再生成のみ |

## 不変条件

- 全対応 field の**数値が改名前後で一致**(数値パリティ回帰・FR-007)。
- DB migration ゼロ・read-only(全 GET)・win_realized ロジック不変。
- calibration realized_rate/ci は同名・同値(FR-006)。
