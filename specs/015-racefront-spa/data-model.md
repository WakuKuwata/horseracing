# Data Model: RaceFront

フロントは永続データを持たない。以下は **API 生成型 + ビュー/コンポーネントのモデル**と**判別ユニオン**・状態・不変条件。型は
`src/api/schema.d.ts`（openapi-typescript 生成、014 契約由来）から取得し、表示モデルへ射影する。

## 1. API 生成型（消費・コミット）

- `front/openapi.json` … 014 `/openapi.json` のスナップショット（契約）。
- `src/api/schema.d.ts` … `openapi-typescript` 生成型（コミット）。`components.schemas` に RaceSummary/RaceDetail/PredictionResponse/
  OddsResponse/RecommendationResponse/ErrorBody/Page など。
- ドリフト検知: スナップショットから生成した型 == コミット型（テスト）。

## 2. 状態（全フェッチ共通）

| 状態 | 表現 |
|---|---|
| Loading | `<StateView kind="loading">` |
| Empty | 200 typed-empty（run=null / 配列空）→ `<StateView kind="empty" message=…>` |
| Error | 型付き `{status, code, detail}` → `<StateView kind="error" code detail>` |
| Ready | データ描画 |

- 空とエラーと読込を**別表示**（空白固定にしない）。

## 3. 判別ユニオン（誤読防止の核）

- **OddsRow（表示）**:
  - real: `{ kind: "real", odds: number|null, updatedAt? }` → `<SourceBadge source="real">`
  - estimated: `{ kind: "estimated", odds: number|null, asOf }` → `<SourceBadge source="estimated"><PseudoBadge label="推定（疑似）">`
  - real_exotic: `{ kind: "real", betType, selection:number[], odds, coverageScope, updatedAt }`
- **RecommendationRow（表示）**: `is_estimated_odds`/`double_pseudo` を判別に。`pseudo_roi` は **必ず `<PseudoBadge label="疑似ROI">`**、
  `double_pseudo` は **`<PseudoBadge label="二重疑似">`**。
- **不変条件**: 疑似値（推定オッズ/pseudo_odds/pseudo_roi）は `<PseudoBadge>` を**通してのみ**描画（型・コンポーネントで強制、テストで担保）。

## 4. ビュー/コンポーネント

| 名前 | 役割 |
|---|---|
| `RaceListPage` | フィルタ（日付/開催）+ `Pagination`（page/page_size/total/has_next）+ `RaceTable`（→ 詳細リンク） |
| `RaceDetailPage` | `PredictionTable` + `RunAudit` + `OddsPanel` + `RecommendationPanel` |
| `PredictionTable` | per-horse 1着率/2着以内率/3着以内率（`formatNum` で null 安全） |
| `RunAudit` | prediction_run_id/model_version/logic_version/computed_at（画面明示） |
| `OddsPanel` | win(real)/estimated(疑似+as_of)/real_exotic(coverage+updatedAt) を**別セクション** |
| `RecommendationPanel` | 疑似ROI/二重疑似バッジ + 監査 |
| `PseudoBadge`/`SourceBadge` | 疑似/二重疑似/推定/実の唯一の描画経路 |
| `StateView` | Loading/Empty/Error の 3 状態 |

## 5. 表示ユーティリティ

- `formatNum(x: number | null, kind: "prob"|"odds"|"roi")` → null は `--`/`未提供`、prob は %、odds は ×、roi は符号付き。
- ラベル文言（日本語）: 1着率/2着以内率/3着以内率・推定（疑似）・疑似ROI・二重疑似（推定オッズ + PL 外挿）・実。

## 6. 不変条件まとめ

1. 閲覧専用: 書込/フォーム送信 UI を持たない（II）。
2. 疑似値は `<PseudoBadge>` 経由のみ（型・コンポーネント・テストで強制、V/R1）。
3. 実/推定は別フィールド・別セクションで非混在（V/R1）。
4. 監査（run/model/logic/computed_at/as_of）を画面明示（V/R6）。
5. loading/empty/error の 3 状態を別表示、エラー本体を防御的にパース（R4）。
6. null 数値は `formatNum` で安全表示（R5）。
7. ページングは一覧のみ（R4）。型は OpenAPI スナップショットと一致（VI/R2）。API は相対 + proxy で無改変（R3）。
