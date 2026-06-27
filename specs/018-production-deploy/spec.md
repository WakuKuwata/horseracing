# Feature Specification: 本番デプロイ構成（コンテナ化 + single-origin reverse-proxy）

**Feature Branch**: `018-production-deploy`

**Created**: 2026-06-27

**Status**: Draft

**Input**: User description: "014(API) と 015(front) をコンテナ化し、nginx single-origin reverse-proxy + docker compose(postgres + migration one-shot + API + nginx)で実運用可能にする。read-only 維持・スキーマ変更なし・env 注入。"

## 概要

Feature 014（read-only API）と 015（read-only front SPA）は実装済みだが、運用構成は未整備（014 は CORS-free
前提、015 は Vite dev proxy 前提で**本番 reverse-proxy/CORS は deferred**）。本 feature はリポジトリ初の
**デプロイ/インフラ層**（新規 `deploy/`）として、API と front をコンテナ化し、**nginx single-origin
reverse-proxy**（静的 front 配信 + `/api/v1/*` を uvicorn に転送 → CORS 不要）と **docker compose**
（postgres + マイグレーション one-shot + API + nginx）で「`compose up` 一発で動く」状態を確立する。

ML・モデル・スキーマは一切変更しない。read-only 不変（write エンドポイント非露出）を維持し、マイグレーション
（write/DDL）は**起動時 one-shot の管理操作**として serving 経路から分離する。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - compose 一発起動（Priority: P1）🎯 MVP

運用者は `docker compose up` 一回で、postgres → マイグレーション → API → nginx が正しい順序で起動し、
ブラウザから単一オリジンで front と API にアクセスできる状態にしたい。

**Why this priority**: 「動く本番構成」が本 feature の中核価値。これ無しに他は意味を持たない。

**Independent Test**: クリーン環境で `docker compose up --wait` を実行し、(a) postgres healthy、(b) migrate が
exit 0、(c) API healthy、(d) nginx healthy の順に到達し、nginx 経由で front と `/api/v1/health` が応答する。

**Acceptance Scenarios**:

1. **Given** クリーンな環境と `.env`, **When** `docker compose up --wait` を実行, **Then** 全サービスが healthy/
   完了になり、起動順序が postgres(healthy) → migrate(成功 exit 0) → api(healthy) → nginx(healthy) で保証される。
2. **Given** マイグレーションが失敗する状態, **When** compose を起動, **Then** API は起動せず（fail-closed）、
   古いスキーマで serving が始まらない。
3. **Given** 全サービス起動済み, **When** ブラウザ/`curl` で `/`（front）と `/api/v1/health` にアクセス, **Then**
   front がロードされ、health が 200 を返す（単一オリジン、CORS 不要）。

---

### User Story 2 - single-origin routing（front/API 境界）（Priority: P1）

利用者は、同一オリジンで front を閲覧し、front が相対 `/api/v1/*` で API を叩けること、SPA の deep link が
動作することを期待する。

**Why this priority**: 015 の CORS deferred を解消する本 feature の主目的。routing 境界の誤りは UI 破壊に直結。

**Independent Test**: nginx 経由で (a) `/` と SPA deep link（例 `/races/<id>`）が index.html を返す、(b)
`/api/v1/races` が API データを返す、(c) 未知 `/api/...` が API の 404（index.html に化けない）を返す。

**Acceptance Scenarios**:

1. **Given** nginx 稼働, **When** `/api/v1/races` を取得, **Then** API のレスポンス（JSON）が返り、パスは保持
   される（`/api/v1/...` が upstream で書き換わらない）。
2. **Given** SPA, **When** `/races/<race_id>` に直接アクセス（deep link）, **Then** index.html が返り front が
   ルーティングを解決する（history fallback）。
3. **Given** 未知の API パス, **When** `/api/v1/does-not-exist` を取得, **Then** API の typed 404 が返り、
   index.html に**化けない**（`/api/` は SPA fallback の対象外）。
4. **Given** front から詳細ページ, **When** 実 DB データで描画, **Then** 予測/オッズ/推奨が表示され、疑似ラベル
   不変条件（015）が保たれる。

---

### User Story 3 - 再現性・read-only・権限分離（Priority: P2）

運用者は、構成が宣言的で再現可能（イメージ pin、lockfile 厳密）、serving が read-only（最小権限）、
マイグレーションのみが write 権限を持つことを保証したい。

**Why this priority**: 憲法 V（再現性・監査）と II（read-only）をインフラ層で担保。codex 指摘の権限分離。

**Independent Test**: (a) API が使う DB ロールは SELECT のみ（write は失敗する）、(b) マイグレーションは別の
owner ロールで実行、(c) イメージは pin されたベース + 厳密 lockfile から再現ビルドできる、(d) build 後の
front が消費する OpenAPI が API 実体と一致する。

**Acceptance Scenarios**:

1. **Given** デプロイ済み, **When** API のDBロールで write を試行, **Then** 拒否される（serving は read-only、
   最小権限）。マイグレーションは別 owner ロールでのみ成功する。
2. **Given** 同一イメージ tag/digest と lockfile, **When** 再ビルド, **Then** 同一構成が再現する（ベースイメージ
   pin、`uv sync --frozen` / `pnpm install --frozen-lockfile`）。
3. **Given** 稼働中 API, **When** live `/openapi.json` と front の committed snapshot を比較, **Then** 一致する
   （015 の型同期が本番ビルドで壊れていない）。
4. **Given** デプロイ, **When** 構成を確認, **Then** 新規 write エンドポイントが露出していない（014 は read-only
   のまま）。

---

### Edge Cases

- **migrate 失敗**: API は起動しない（`condition: service_completed_successfully`）。`/health` は alembic head 到達も検証。
- **DB 未起動で API 起動**: postgres healthy（pg_isready）を待ってから migrate → api。
- **nginx routing 競合**: `/api/v1/` を `/` の SPA fallback より優先（`location ^~ /api/v1/`）、proxy はパス保持。
- **secret 漏洩**: `.env` は repo/イメージに含めない。`.env.example` のみ。`.dockerignore` で `.env`・node_modules・.venv 等を除外。
- **データ空**: fresh migration はスキーマのみ作成。予測/推奨が空なら front は 015 の 200-typed-empty 状態を表示（壊れない）。
- **データ永続化**: compose 内 postgres は named volume で永続化。本番は外部マネージド DB を推奨（compose postgres は single-host/staging 位置づけ）。
- **非 root / 最小権限**: コンテナは非 root 実行。API DB ロールは SELECT のみ。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: API のマルチステージ Dockerfile を提供 MUST。`uv sync --frozen`（lockfile 厳密）で依存解決し、
  **API の内部依存閉包（api, db, probability, eval）をビルドコンテキストに含める** MUST（probability→eval 依存）。
  非 root 実行、uvicorn で serving。
- **FR-002**: front の本番ビルド（`pnpm install --frozen-lockfile` → `pnpm build`）で静的アセットを生成 MUST。
  015 の committed `openapi.json` 型同期を維持する。
- **FR-003**: nginx が静的 front を配信しつつ `/api/v1/*` を uvicorn に reverse-proxy MUST。`location ^~ /api/v1/`
  を SPA fallback より優先し、**パスを保持**（`/api/v1/...` を書き換えない）。`/` 配下のみ history fallback
  （`try_files → index.html`）。`Host`/`X-Forwarded-For`/`X-Forwarded-Proto`/`X-Real-IP` を付与。
- **FR-004**: docker compose で postgres + migrate（one-shot）+ api + nginx を統合 MUST。起動順序を
  postgres(`service_healthy`, pg_isready) → migrate(`service_completed_successfully`) → api(`service_healthy`,
  /api/v1/health) → nginx の依存で保証する。
- **FR-005**: マイグレーションは `alembic upgrade head` を**起動前 one-shot**（`restart: "no"`）で実行 MUST。
  失敗時は API を起動させない（fail-closed）。冪等（head 到達済みなら no-op）。
- **FR-006**: API の `/api/v1/health` は接続性に加え **alembic_version == head** を検証 MUST（マイグレーション
  未適用での silent 起動を防ぐ）。
- **FR-007**: 設定は環境変数で注入 MUST（DATABASE_URL 等をイメージに焼き込まない）。`.env.example` を提供し、
  実 `.env`・シークレットは repo / イメージに含めない。`.dockerignore` で機密・不要物を除外。
- **FR-008**: API が使う DB ロールは **SELECT のみ（read-only、最小権限）** とし、マイグレーション用 owner
  ロールと分離 MUST（serving の read-only をアプリ実装依存でなく DB 権限で担保）。
- **FR-009**: 各サービスに healthcheck を定義 MUST（postgres=pg_isready、API=/api/v1/health、nginx=静的 200）。
- **FR-010**: 新規 write エンドポイントを一切露出してはならない MUST（014 は read-only のまま）。DB スキーマ変更
  を行ってはならない（既存 0006 が head）。
- **FR-011**: ベースイメージと依存を pin MUST（release はベースイメージ digest 固定、local は tag 許容を明記）。
  構成は宣言的（compose）でバージョン管理し、イメージ tag/digest + git SHA ラベルで再現する。
- **FR-012**: compose 内 postgres は named volume で永続化 MUST。本番は外部マネージド DB を推奨と明記（compose
  postgres の適用範囲・バックアップ前提をドキュメント化）。
- **FR-013**: 受け入れは CI なしで再現可能な手順 MUST: `docker compose config` 検証 → `docker compose build`
  → `docker compose up --wait` → migrate exit 0 → nginx 経由 `/api/v1/health` 200 / `/` 200 / SPA deep link /
  未知 `/api/...` 404 / live OpenAPI と snapshot 一致。
- **FR-014**: 運用手順（up/down、migrate one-shot、ログ確認、env 設定、本番 DB 注意）を日本語でドキュメント化 MUST。

### Key Entities *(include if feature involves data)*

- **デプロイ構成（compose プロジェクト）**: サービス（postgres / migrate / api / nginx）、依存・healthcheck・
  起動順序、named volume、env_file、イメージ tag。
- **API イメージ**: 内部依存閉包（api, db, probability, eval）+ pin ベース + 厳密 lockfile。非 root。
- **front イメージ（nginx）**: 静的ビルド成果物 + nginx 設定（reverse-proxy + history fallback）。
- **DB ロール**: serving 用（SELECT のみ）/ migration 用（owner）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: クリーン環境で `docker compose up --wait` が成功し、postgres→migrate(exit 0)→api→nginx の順で
  全サービスが healthy/完了に到達する。
- **SC-002**: nginx 経由で `/api/v1/health` が 200、`/api/v1/races` が実データ（≥1 行が存在する DB で）を返す。
- **SC-003**: front が単一オリジンで描画され、SPA deep link（`/races/<id>` 直アクセス）が動作し、未知 `/api/...`
  が API の 404（index.html に化けない）を返す。
- **SC-004**: マイグレーション失敗時、API が起動しない（fail-closed、100% の頻度で）。
- **SC-005**: `/api/v1/health` が alembic head 未到達を検出して unhealthy を返す（migration 未適用での起動を防ぐ）。
- **SC-006**: API の DB ロールで write を試みると拒否される（read-only 最小権限）。新規 write エンドポイントは 0。
- **SC-007**: 同一イメージ tag/digest + lockfile から再ビルドして同一構成が再現する。live `/openapi.json` が
  front の committed snapshot と一致する。
- **SC-008**: DB スキーマ変更が 0（head は 0006 のまま）。`.env`・シークレットが repo / イメージに含まれない
  （`.dockerignore` / `.gitignore` で除外）。

## Assumptions

- **依存閉包**: API イメージは api/db/probability/eval を含める（probability→eval→db）。features/serving/training
  は API serving に不要なため含めない。migrate は db（alembic）を含むイメージで実行。
- **single-host**: 初インフラ層は単一ホストの docker compose を対象。k8s/Helm・マルチノードは deferred。
- **本番 DB**: compose 内 postgres は local/staging。本番は外部マネージド DB（DATABASE_URL で接続）を推奨。
- **権限分離**: serving=SELECT のみ、migration=owner。最小 hardening（非 root、`.dockerignore`、静的 asset
  cache、access/error log）を含める。
- **再現性**: release はベースイメージ digest 固定、local は tag 許容。`uv sync --frozen` / `pnpm install --frozen-lockfile`。
- **read-only**: 014 は GET のみ。migration は起動前 one-shot の管理操作（read-only の明示的例外）。
- **TLS**: 本番 TLS 終端（証明書）は最小では reverse-proxy の前段 or deferred。`X-Forwarded-Proto` は将来 TLS の
  ために付与。
- **deferred**: Kubernetes/Helm、クラウド固有、TLS 自動化、オートスケール、CI/CD、シークレットマネージャ、
  観測スタック（metrics/tracing/集約ログ）、DB backup 自動化、ライブ serving（未来レース）。
