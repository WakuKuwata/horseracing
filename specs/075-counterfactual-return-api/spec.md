# Feature Specification: Counterfactual Return API Terminology

**Feature Branch**: `075-counterfactual-return-api`

**Created**: 2026-07-16

**Status**: Draft

**Input**: 073 で予約された公開契約の破壊的変更(命名のみ・値不変)。backtest/shadow-log が `realized_return`/`realized_roi`/`recovery_rate` と呼ぶ値は、実は**凍結 `market_odds_used`(判断時オッズの snapshot)**由来で closing の実現値ではない=誤称。これを `counterfactual_snapshot_*` に改名し provenance を明示する。favorite baseline は current odds 由来なので別ラベル。calibration の `realized_rate`(実際の勝率)は真に empirical なので改名しない。

---

## 概要 (Why)

049/065 の backtest・shadow-log は「買ったつもり」を**判断時に凍結したオッズ**(`market_odds_used`)で精算する。[api/backtest.py](../../api/src/horseracing_api/backtest.py) 自身が「FROZEN `market_odds_used` を使い、current `race_horses.odds` は読まない(それは closing になる)」と明記している。つまりこの値は**反実仮想スナップショット収益**(counterfactual snapshot return)であって、closing での実現 ROI ではない。にもかかわらず API/front が `realized_return`/`realized_roi`/`recovery_rate` と呼ぶため、閲覧者が「実際に儲かった実現値」と誤読しうる(憲法 V: pseudo/counterfactual は明示ラベルが必要)。

073 の redesign proposal でも「判断時オッズ由来の指標を `realized ROI` と呼ぶのをやめ `counterfactual_snapshot_return` に降格・改名する」と決めており、074 codex レビューが具体名(gross/net/recovery + provenance)を確定した。本 feature はその**公開 API 契約の原子的 migration**を実施する。

**重要な区別**(この feature の肝):
- **改名対象(FROZEN snapshot 由来)**: 単勝 backtest / shadow-log の `realized_return`/`realized_roi`/`recovery_rate`。
- **別ラベル(current odds 由来)**: favorite baseline の `realized_*` は current `race_horses.odds` 由来 → `current_odds` provenance に分離(snapshot と混同させない)。
- **改名しない(真に empirical)**: calibration reliability の `realized_rate`/`realized_ci_low`/`realized_ci_high` は**実際の勝率**(結果から観測した頻度)であり、正しく realized。触らない。

**値は一切変えない**(命名・provenance ラベルのみ)。精算ロジック・数値・read-only 境界・DB スキーマは不変。API/front/admin/OpenAPI を**原子的に**変更し drift-check を緑に保つ。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 反実仮想スナップショット収益を正しく命名する (Priority: P1)

閲覧者(front/admin ユーザー)が単勝 backtest / shadow-log の収益指標を見るとき、それが「判断時オッズで計算した反実仮想値」だと名前から分かり、closing の実現値と誤読しない。

**Why this priority**: 誤称の是正が本 feature の核。憲法 V(counterfactual は明示ラベル)を満たす。単独で価値を成す。

**Independent Test**: API 応答と front/admin 表示で、FROZEN `market_odds_used` 由来の収益が `counterfactual_snapshot_gross_return`/`counterfactual_snapshot_net_return`/`counterfactual_snapshot_recovery_rate` として返り、`valuation_basis="frozen_snapshot_odds"` が付き、`realized_return`/`realized_roi` の名が backtest/shadow-log 経路から消えていることを検証できる。数値は改名前と一致する。

**Acceptance Scenarios**:

1. **Given** 単勝 backtest が凍結オッズで精算する現状、**When** API 応答を見る、**Then** `realized_return`→`counterfactual_snapshot_gross_return`(hit なら odds 倍・miss なら 0)、`realized_roi`→`counterfactual_snapshot_net_return`(gross−1)に改名され、`valuation_basis="frozen_snapshot_odds"` が付与される。**数値は不変**。
2. **Given** shadow-log サマリの `recovery_rate`、**When** API 応答を見る、**Then** `counterfactual_snapshot_recovery_rate` に改名され、`valuation_basis="frozen_snapshot_odds"` が付き、分母は既存 `n_settled` のまま(`n_scored` は追加しない=冗長)。数値不変。
3. **Given** front/admin の表示、**When** 画面を見る、**Then** ラベルが「反実仮想(判断時オッズ)」と分かる表記になり、`realized`(実現)という語が backtest/shadow-log の表示から外れる。
4. **Given** OpenAPI 契約、**When** 生成型と snapshot を確認する、**Then** api の OpenAPI・front/admin の committed snapshot・生成 TS 型が原子的に更新され drift-check が緑。

---

### User Story 2 - favorite baseline を current_odds provenance に分離する (Priority: P2)

閲覧者が favorite baseline(人気馬ベタ買い基準)を見るとき、それが**現在のオッズ**由来で、凍結スナップショットとは別 provenance だと分かる。

**Why this priority**: favorite baseline は current `race_horses.odds` 由来(判断時 snapshot でない)。US1 の snapshot ラベルと混同させないため別ラベルが要る。US1 の後でよい。

**Independent Test**: favorite baseline の収益が `current_odds` provenance(例 `current_odds_gross_return`/`current_odds_net_return` + `valuation_basis="current_odds"`)として返り、snapshot ラベルと区別されることを検証できる。数値不変。

**Acceptance Scenarios**:

1. **Given** favorite baseline が current odds で計算される現状、**When** API 応答を見る、**Then** その収益が `current_odds` provenance ラベルを持ち、`counterfactual_snapshot_*`(凍結由来)とは別名で返る。数値不変。
2. **Given** front/admin の favorite baseline 表示、**When** 画面を見る、**Then** 「現在オッズ基準」と分かる表記になる。

---

### User Story 3 - empirical な realized_rate を改名から守る (Priority: P3)

研究者/閲覧者が calibration reliability を見るとき、`realized_rate`(実際の勝率)は**真に観測された実現値**なので名前が保たれ、counterfactual 改名の巻き添えにならない。

**Why this priority**: 改名の過剰適用を防ぐ回帰ガード。empirical と counterfactual を取り違えない。

**Independent Test**: calibration の `realized_rate`/`realized_ci_low`/`realized_ci_high` が改名前後で**同名・同値**であることを検証できる。

**Acceptance Scenarios**:

1. **Given** calibration reliability bin、**When** API 応答を見る、**Then** `realized_rate`/`realized_ci_low`/`realized_ci_high` は**改名されず**同名で残る(実際の勝率=empirical realized)。
2. **Given** 改名 migration、**When** grep で `realized` を探す、**Then** backtest/shadow-log/favorite 経路には `realized_return`/`realized_roi` が残らないが、calibration の `realized_rate`/`realized_ci_*` は意図的に残る。

---

### Edge Cases

- 未精算レース(settled=false)は全 counterfactual フィールドが null(改名後も null 意味論不変)。
- exotic(推定オッズ double-pseudo)の backtest 値も同じ改名規則を受けるか(単勝 win のみか)を明確化。**win のみ改名対象**(exotic realized は 049 で別扱い・本 feature は win backtest/shadow-log に限定)。
- 旧フィールド名で読む既存クライアントは壊れる(**破壊的変更**)。本 feature は原子 migration であり後方互換フィールドは残さない(073 の realized 誤称を確実に排除するため)。

## Requirements *(mandatory)*

### Functional Requirements

**改名(US1)**

- **FR-001**: 単勝 backtest の `realized_return`(hit で odds 倍・miss で 0)を `counterfactual_snapshot_gross_return` に、`realized_roi`(return−1)を `counterfactual_snapshot_net_return` に改名しなければならない。**数値は不変**。
- **FR-002**: shadow-log サマリの `recovery_rate` を `counterfactual_snapshot_recovery_rate` に、`by_month[].recovery`(ShadowLogMonth 応答モデルの field)を `by_month[].counterfactual_snapshot_recovery` に改名し、`valuation_basis="frozen_snapshot_odds"` を明示しなければならない。数値不変。**注(analyze D1)**: recovery の分母は既存 `n_settled` であり、これを保持する(`n_scored` は `n_settled` と同義=冗長なので追加しない)。`n_settled`/`n_hit`/`hit_rate` は結果由来の集計として保持。
- **FR-003**: 改名は api の schema・backtest・routers、front/admin の生成 TS 型・fixtures・表示コンポーネント(RecommendationPanel/ShadowLogPanel/BacktestSummary 等)、OpenAPI(api 生成 + front/admin committed snapshot)を**原子的に**更新し、drift-check を緑に保たなければならない。
- **FR-004**: front/admin の表示ラベルは「反実仮想(判断時オッズ)」と分かる表記にし、backtest/shadow-log の表示から `realized`(実現)語を外さなければならない。

**provenance 分離(US2)**

- **FR-005**: favorite baseline(current `race_horses.odds` 由来)の収益を `current_odds` provenance ラベル(例 `current_odds_gross_return`/`current_odds_net_return` + `valuation_basis="current_odds"`)に分離し、`counterfactual_snapshot_*`(凍結由来)と別名にしなければならない。数値不変。

**empirical 保護(US3)**

- **FR-006**: calibration reliability の `realized_rate`/`realized_ci_low`/`realized_ci_high` は改名してはならない(真に観測された勝率=empirical realized)。改名前後で同名・同値でなければならない。

**不変条件(共通)**

- **FR-007**: 精算・ROI 計算の**数値**を変更してはならない(命名・provenance ラベルのみの変更)。改名前後で全 counterfactual/current フィールドの値が一致しなければならない。
- **FR-008**: DB スキーマ・migration を変更してはならない。read-only 境界(全 GET・api は betting/serving を書かない)を保たなければならない。
- **FR-009**: 本 feature は win backtest/shadow-log に限定し、exotic realized(推定オッズ・049 別扱い)には触れてはならない。

### Key Entities

- **counterfactual snapshot 収益**: 凍結 `market_odds_used` 由来の per-unit gross_return(hit=odds/miss=0)・net_return(gross−1)・recovery_rate。`valuation_basis="frozen_snapshot_odds"`。分母は既存 `n_settled`。win backtest / shadow-log で使用。
- **current_odds 収益**: current `race_horses.odds` 由来の favorite baseline gross/net_return。`valuation_basis="current_odds"`。
- **empirical realized(不変)**: calibration reliability の `realized_rate` + Wilson CI。結果から観測した勝率。改名しない。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: **凍結 snapshot 経路**(win backtest + shadow-log)の API 応答・front/admin 表示に `realized_return`/`realized_roi` の名が **0 件**(全て `counterfactual_snapshot_*` に改名)。favorite baseline は別 provenance の `current_odds_*`(SC-004)であり本 SC の対象外(counterfactual にはしない)。
- **SC-002**: 改名前後で対応フィールドの**数値が 100% 一致**(命名のみ変更・値不変)。
- **SC-003**: OpenAPI drift-check(api 生成 == front/admin committed snapshot・生成 TS 同期)が**緑**。
- **SC-004**: favorite baseline が `current_odds` provenance で返り、`counterfactual_snapshot_*` と別名(混同 0)。
- **SC-005**: calibration の `realized_rate`/`realized_ci_*` が改名前後で**同名・同値**(empirical 保護)。
- **SC-006**: DB migration 追加 **0**・read-only 境界維持(全 GET・書き込み経路追加 0)。
- **SC-007**: api/front/admin の全テストが緑・drift-check 緑。

## Assumptions

- 073 の redesign proposal + 074 codex レビューで確定した命名(gross/net/recovery + valuation_basis + current_odds provenance)を正とする。
- backtest/shadow-log の値は既に FROZEN `market_odds_used` 由来(closing でない)= counterfactual snapshot という現状認識(backtest.py:124-125 が明記)を前提とする。
- **破壊的変更**(後方互換フィールドを残さない):073 の `realized` 誤称を確実に排除するため、旧名は削除する。既存クライアントは新名に追随する必要がある。
- 値の不変は「改名前 branch の応答」と「改名後の応答」を同一入力で比較する回帰で担保する(数値パリティ)。

## 依存・スコープ外

- **スコープ外**: 精算ロジックの数値変更・DB スキーマ変更・ROI 台帳(将来 feature・憲法 V 改定前提)・exotic realized の改名・発走前オッズ取得。
- **憲法**: V(pseudo/counterfactual を明示ラベル・provenance 区別)/ VI(契約先行・OpenAPI drift-check・front 型同期)。read-only 境界(II の派生値非還流は該当なし=表示のみ)。
