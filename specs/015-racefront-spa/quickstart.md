# Quickstart: RaceFront の検証

実装後に「API 起動 → 型生成 → dev 起動 → 画面/テスト検証」が動くことを確認する手順。

## 前提

- Feature 014（read-only API）が稼働可能（ローカル `horseracing` DB）。
- Node 20+ / pnpm。`front/` パッケージ（React + Vite + TypeScript）。

## API 起動（別ターミナル）

```bash
cd api && export DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
uv run uvicorn horseracing_api.app:app --port 8000
```

## フロント セットアップ / 型生成 / 起動

```bash
cd front && pnpm install
pnpm run gen:types          # 起動中 API の /openapi.json -> front/openapi.json + src/api/schema.d.ts
pnpm run dev                # Vite dev (proxy /api -> http://localhost:8000)
# ブラウザ: http://localhost:5173/  (一覧) -> レース行クリックで /races/:id (詳細)
```

期待:
- 一覧: 日付/開催で絞込・ページング（次/前・総件数）。空はゼロ件明示、エラーはメッセージ表示、読込はローディング。
- 詳細: 出走表 + 1着率/2着以内率/3着以内率 + 監査（run_id/model/logic/computed_at）。
- オッズ: 実（実ラベル）/ 推定（**推定（疑似）バッジ + as_of**）/ 実 exotic（coverage）を別セクションで区別。
- 推奨: `pseudo_roi`=**疑似ROI**、`double_pseudo`=**二重疑似** バッジ。
- null 値は `--`/`未提供`。

## テスト / ドリフト検知

```bash
cd front
pnpm run test               # vitest + RTL + MSW: full/空/エラー/null/ページング/疑似ラベル不変条件
pnpm run typecheck          # tsc --noEmit
pnpm run check:openapi      # コミット済み openapi.json から生成した型 == コミット済み型（ドリフト検知）
```

検証する受け入れ基準:

- **SC-001**: 一覧の絞込/ページング/詳細遷移、空/エラー/ローディングが区別表示。
- **SC-002**: 詳細で予測 + 監査情報（run_id/model/logic/時刻）が画面明示。
- **SC-003**: オッズが実/推定を区別、推定に疑似バッジ + as_of（混在なし）。
- **SC-004**: 推奨で pseudo_roi=疑似ROI、double_pseudo=二重疑似 バッジ。
- **SC-005（不変条件）**: 疑似値がラベル無しで描画される箇所ゼロ（テストで担保）。
- **SC-006**: null 数値が安全表示。OpenAPI 生成型がスナップショットと一致。
- **SC-007**: 書込を一切しない（閲覧専用）。API（014）無改変（CORS 無し、相対 + proxy）。

## 核心の考え方（誤読防止 / 契約先行）

疑似値（推定オッズ/疑似ROI/二重疑似）は**共通バッジを通してのみ**描画し、判別ユニオン型と不変条件テストで「ラベル無しの疑似値は
存在しない」を機構的に保証する（憲法 V）。型は 014 の OpenAPI から生成しスナップショットでドリフト検知（VI）。閲覧専用で API は
無改変、開発は Vite proxy で接続する。
