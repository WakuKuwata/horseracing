# Implementation Plan: 本番デプロイ構成（コンテナ化 + single-origin reverse-proxy）

**Branch**: `018-production-deploy` | **Date**: 2026-06-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/018-production-deploy/spec.md`

## Summary

リポジトリ初のデプロイ/インフラ層（新規 `deploy/`）。014(API)/015(front) をコンテナ化し、**nginx
single-origin reverse-proxy**（静的 front + `/api/v1/*`→uvicorn、CORS 不要＝015 の deferred 解消）と
**docker compose**（postgres + migration one-shot + api + nginx）で `compose up` 一発起動を確立する。
API イメージは依存閉包 **api/db/probability/eval**（context=repo root、`uv sync --frozen`）。**fail-closed**:
migrate を `service_completed_successfully` で API 起動条件にし、`/api/v1/health` が **alembic head 同期**を
read-only 検証。**DB 権限分離**: serving=app_ro(SELECT のみ)、migration=owner。スキーマ変更なし・read-only
維持・シークレット非焼き込み。codex の top-3（fail-closed / 権限分離 / 依存閉包+OpenAPI 同期）を機構解消。

## Technical Context

**Language/Version**: Python 3.12（API, uv）/ Node 22 + pnpm 10.33（front）/ nginx alpine / postgres 16

**Primary Dependencies**: Docker + docker compose v2。API 依存閉包 = `horseracing-api`/`-db`/`-probability`/
`-eval`（確認済み closure）。alembic（db/migrations、head=0006）。アプリ変更は `/api/v1/health` の read-only
head 検証のみ（alembic ScriptDirectory）。

**Storage**: PostgreSQL 16（compose 内 = local/staging、named volume 永続化；本番は外部マネージド DB を
DATABASE_URL で）。スキーマ変更なし。

**Testing**: api 単体（health head-check ロジック）+ no-write guard（014 既存）+ `docker compose config` 検証 +
quickstart の compose smoke（CI なし再現）。

**Target Platform**: 単一ホスト docker compose（Linux/macOS）。

**Project Type**: インフラ（`deploy/`）+ API health の最小拡張。新フロント・新サービスコードなし。

**Performance Goals**: 起動が数十秒で healthy。serving レイテンシは 014 と同等（nginx 経由のオーバーヘッドのみ）。

**Constraints**: read-only 維持（app_ro=SELECT のみ、health も SELECT のみ、migration は起動前 one-shot の
write 例外）。スキーマ変更 0・新規 write エンドポイント 0。シークレットは repo/イメージ非格納。再現性
（pin イメージ + frozen lockfile + 宣言的 compose）。

**Scale/Scope**: 単一ホスト。k8s/TLS自動化/CI/CD/観測/backup自動化/ライブserving は deferred。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート（インフラ feature のため II/V/VI が中心、ML 系 III/IV は N/A）:

- [x] **I. データ契約**: race_id/スキーマ無改変。既存契約をそのまま配信。**PASS**
- [x] **II. リーク防止**: API は read-only（app_ro=SELECT のみ、health も SELECT）。モデル特徴に影響する変更
  なし。migration は起動前 one-shot の write 例外として serving 経路から分離。**PASS**
- [N/A] **III. 評価先行**: モデル/特徴量変更なし。インフラの「評価」は compose smoke（quickstart）で代替。
- [N/A] **IV. 確率整合性**: 確率ロジック変更なし。
- [x] **V. 再現性・監査**: pin ベースイメージ + frozen lockfile + 宣言的 compose + git SHA label でイメージ
  再現。health が alembic head 同期を検証。シークレット非格納。**PASS**
- [x] **VI. feature 分割規律**: API/DB 契約（014/015）確定後に着手。スキーマ変更なし。k8s/TLS/CI 等を将来に
  明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` second opinion を取得・記録（下表）。top-3 を機構解消。**PASS**

### Second Opinion 記録（codex:codex-rescue — 設計レビュー）

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **A. 起動順序** | migrate を `service_completed_successfully` で API 起動条件に。health で alembic head も検証（古いスキーマ silent 起動防止） | R4/R5、FR-004/005/006 |
| **B. routing** | `^~ /api/v1/` を fallback より優先・パス保持・X-Forwarded-*。SPA fallback は `/` 配下のみ | R3、FR-003 |
| **C. ビルド再現性/依存** | API イメージに eval 必須（probability→eval）。base pin・frozen lockfile・OpenAPI 同期確認 | R1/R7、FR-001/011、quickstart |
| **D. 設定/シークレット** | env 注入・.env 非コミット・compose postgres は local/staging（本番は外部 DB）・永続化 | R4/R7、FR-007/012 |
| **E. read-only/権限** | serving=read-only role / migration=owner role に分離（DB 権限で担保） | R6、FR-008 |
| **F. 受け入れ** | compose up smoke + nginx 経由 health/front/deep link/未知404/OpenAPI 一致 | quickstart、FR-013 |
| **G. 最小 hardening** | `.dockerignore`・非 root・asset cache・明示 deferred | R7、FR-007/011 |

最重要リスク TOP3: ①migrate 失敗で古いスキーマ silent 起動 ②read-only がアプリ実装依存 ③依存コピー漏れ
（eval）で再現性破綻。①=service_completed_successfully + health head 検証、②=app_ro 権限分離、③=閉包コピー
+ OpenAPI 同期確認で対応。

## Project Structure

### Documentation (this feature)

```text
specs/018-production-deploy/
├── plan.md / research.md (R1-R7) / data-model.md / quickstart.md
├── contracts/deploy_compose.md   # compose/nginx/role/health 契約
├── checklists/requirements.md    # 16/16 PASS
└── tasks.md                      # /speckit-tasks で生成
```

### Source Code (repository root)

```text
deploy/
├── docker-compose.yml          # db + migrate + api + nginx（依存/healthcheck/順序）
├── api.Dockerfile              # multi-stage、context=repo root、閉包 api/db/probability/eval、非 root
├── front.Dockerfile            # multi-stage、context=front/、vite build → nginx
├── nginx.conf                  # ^~ /api/v1/ proxy（パス保持）+ SPA fallback + /healthz
├── scripts/grant_ro.sql        # app_ro 冪等作成 + SELECT 付与
├── .env.example                # DATABASE_URL(app_ro)/DATABASE_URL_OWNER/POSTGRES_*
└── README.md                   # 運用手順（up/down/migrate/log/本番DB注意）

.dockerignore                   # repo root（.git/.venv/node_modules/__pycache__/dist/specs/.env*）
front/.dockerignore             # front context 用

api/src/horseracing_api/
├── app.py / routers/...        # health の read-only head 検証拡張（唯一のアプリ変更）
api/tests/                      # health head-check 単体 + no-write guard
```

**Structure Decision**: 新規 `deploy/` に集約。アプリ変更は API health の read-only 拡張のみ。スキーマ・モデル・
front コードは無改変。

## Complexity Tracking

> Constitution 違反なし（スキーマ変更なし、read-only 維持、既存原則の枠内）。記入不要。
