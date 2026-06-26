# Contract: UI コンポーネント / 画面（誤読防止ラベル規約）

閲覧専用。疑似値は単一バッジ経路でのみ描画。日本語文言。

## ページ

### RaceListPage（`/`）
- フィルタ: 日付（date）、開催（venue）。ページング: page/page_size（最大 200）/total/has_next。
- `RaceTable`: 各行 race_id/race_date/venue/race_number → 詳細リンク `/races/:raceId`。
- 状態: Loading / Empty（該当ゼロ）/ Error（型付き）を別表示。

### RaceDetailPage（`/races/:raceId`）
- `PredictionTable` + `RunAudit` + `OddsPanel` + `RecommendationPanel`。
- race 無し（404）→ エラー状態。各セクションは独立に Loading/Empty/Error。

## コンポーネント契約

### PseudoBadge / SourceBadge（唯一の疑似/実 描画経路）
- `<SourceBadge source="real"|"estimated">` … 実=実ラベル、estimated=「推定」。
- `<PseudoBadge label="推定（疑似）"|"疑似ROI"|"二重疑似">` … 疑似値の近傍に必ず付与。
- **規約**: 推定オッズ・pseudo_odds・pseudo_roi・double_pseudo を表示する箇所は**必ず**これらを通す（生数値直書き禁止）。

### PredictionTable
- per-horse 馬番/馬/状態/1着率/2着以内率/3着以内率。値は `formatNum(_, "prob")`（null → `--`）。

### RunAudit
- prediction_run_id / model_version / logic_version / computed_at を**可視**（ツールチップのみ不可）。

### OddsPanel（実/推定 非混在）
- `win`（real, updated_at）/ `estimated`（estimated, `PseudoBadge=推定（疑似）` + as_of）/ `real_exotic`（real, coverage_scope +
  updated_at）を**別セクション**。混在禁止。値は `formatNum(_, "odds")`。

### RecommendationPanel
- 各行 bet_type/selection、`pseudo_roi`→`PseudoBadge=疑似ROI`、`double_pseudo=true`→`PseudoBadge=二重疑似`、監査
  （logic_version/computed_at/prediction_run_id）。is_estimated_odds で実/推定使用を区別。

### StateView
- kind = loading | empty | error。error は `{status, code, detail}` を防御的に表示。

## 不変条件（テスト対象）
- 疑似値がラベル無しで描画される箇所ゼロ（FR-006/SC-005）。
- 実/推定が同一行/列に混在しない（FR-004）。
- null 数値が `--`/`未提供`（FR-008）。
- 3 状態（loading/empty/error）が別表示（FR-007）。
