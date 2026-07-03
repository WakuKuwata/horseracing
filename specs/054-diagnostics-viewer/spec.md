# Feature Specification: 運用コンソール第4回 — 診断永続化 + ビューア (Diagnostics Persistence & Viewer)

**Feature Branch**: `054-diagnostics-viewer` / **Created**: 2026-07-03 / **Status**: Draft
**Input**: 運用コンソール プログラム第 4 回(全体像は `specs/051-admin-console/spec.md` 参照)。セグメント診断(047)は CLI 実行で**結果がどこにも永続化されず実行ごとに消える** — 「本命帯は市場を信用・中穴帯がモデルの土俵」という運用指針の根拠が画面で見られない。021 の規律(**オフライン計算 → 永続化 → UI は読むだけ**、リクエスト内で再計算しない)に載せるための永続化テーブルが必要 = **migration 0009(040 以来のスキーマ変更、憲法 VI 正当化)**。

## 設計判断(スコープの境界)
- **計算トリガは CLI のみ**(`training segment-diagnostic --persist`)。ops ジョブ化は deferred — 047 は fold 毎再学習の walk-forward で数十分級、ops worker の stale 回復(RUNNING>600s で再キュー)と衝突する長時間ジョブは現ジョブモデルに載せない(載せるなら heartbeat 設計が先)。
- **payload は JSONB**(metrics_summary 前例)。行構造は 047 の SegmentRow をそのまま転記(独自指標を作らない=憲法 III)。
- **append-only**(履歴が残る)・読みは kind ごとの最新 1 件(computed_at DESC)。

## スコープ
- **US1 (P1) 永続化**: 新テーブル `diagnostic_runs`(id/kind/date_from/date_to/logic_version/payload JSONB/computed_at、index (kind, computed_at DESC))= migration 0009。eval に `save_diagnostic_run`(047 report → payload 変換込み)、training CLI `segment-diagnostic --persist` で書き込み(表示出力は不変)。logic_version に軸定義バージョン・窓・seed を記録(V)。
- **US2 (P2) 読み出し + ビューア**: `GET /api/v1/diagnostics/segment-edge` — 最新 run の転記(未永続化は typed 404 `diagnostic_unavailable`)。admin に**診断ページ**: 軸ごとのテーブル(n/勝率/LL(p)/LL(q)/gap/mean p/q)+ **computed_at・評価窓・logic_version を常時表示**(鮮度が見える)+ 047 の但し書き常時(「SECONDARY・採否ゲート/買いシグナルではない」)。**gap でのソート禁止・損益色禁止**(047/021 規律)。未永続化時は CLI 実行手順を案内。

## Requirements
- **FR-001**: migration 0009 は `diagnostic_runs` 新設のみ(既存テーブル不変)。db/features/live の migration head assert を 0009 に更新(040 前例)。
- **FR-002**: payload は 047 の計算結果の転記のみ(api/admin で再計算・派生指標の追加をしない)。rows + n_horses + note を保存。
- **FR-003**: 永続化は append-only(上書きしない)。読みは deterministic(kind → computed_at DESC → id)。
- **FR-004**: `diagnostic_runs` はモデル特徴に流入しない(憲法 II — features は本テーブルを import/読まない。市場 q・結果由来の診断値が特徴に戻る経路を作らない)。
- **FR-005**: admin 表示は事実記述のみ: gap ソート禁止・損益/推奨色禁止・SECONDARY 但し書き常時・odds/市場語の中立性(047/021 継承)。
- **FR-006**: OpenAPI 契約先行(純追加・front 期待リスト+front/admin snapshot 同期・drift 緑)。

## Success Criteria
- **SC-001**: `training segment-diagnostic --from … --persist` で diagnostic_runs に 1 行追記され、CLI 表示は従来と同一。
- **SC-002**: API が最新 run を返し(2 回 persist → 新しい方)、未永続化 DB では typed 404。
- **SC-003**: admin 診断ページで軸別テーブル・鮮度(computed_at/窓)・但し書きが表示され、未永続化時は案内が出る。
- **SC-004**: 実 DB E2E: 短窓(2024+)で --persist 実行 → API → 表示値が CLI 出力と一致。db/eval/training/api/admin/front スイート緑・migration head=0009。

## Assumptions
- フル 2021+ の再実行はオペレータの長時間 CLI 作業(E2E は短窓で経路を証明)。047 の公表所見(2021+ n=181,341)は将来の --persist 再実行で画面に載る。
- codex CLI 本セッション 3 回起動失敗 → 見送り宣言・single-opinion(021 規律 + 047 出力 + 012/016 migration 前例の組合せ)。

## Deferred
ロードマップ 5(adoption 制御+認証)・診断の ops ジョブ化(heartbeat 設計が前提)・定期実行・複数 run の時系列比較ビュー・market_edge 等 他診断の kind 追加(テーブルは汎用設計済み)
