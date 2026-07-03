# Feature Specification: 運用コンソール第1回 — admin 土台 + モデルレジストリ (Admin Console Foundation)

**Feature Branch**: `051-admin-console` / **Created**: 2026-07-03 / **Status**: Draft
**Input**: 管理ツールが存在しない(2026-07-03 実査: front は 100% エンドユーザー向け 4 ページ・管理系は CLI 30+ コマンド/ops 6 endpoint/api の calibration・importance 2 endpoint に分散・認証ゼロ)。「モデルが健康か・製品はどこまで埋まってるか・ジョブは通ったか・診断の結果」を見るには CLI か DB 直読しかない。独立した admin SPA で運用コンソールを段階構築する — 本 feature はその第 1 回(土台 + モデルレジストリ)。

## プログラム全体像(運用コンソール — 本セクションが後続 feature の共通参照点)

**アーキテクチャ(3 層・既存境界を壊さない)**
- **読み取り** → 既存 `api/`(read-only)に admin read endpoint を追加。014 の「読みは read-only DB セッション・全 path GET」不変を維持(021 calibration/040 importance と同経路)
- **書き込み** → 既存 `ops/`(write サービス・owner ロール・ジョブモデル・subprocess 境界)を拡張。新しい書き込み面は作らない
- **UI** → 新 `admin/` パッケージ(front と同スタック: React+Vite+TS・openapi 型生成+drift-check)。end-user front の read-only 思想に管理機能を混ぜない
- **認証** → 保留(単一オペレータ・ローカル起動のみ)。代替の構造ガード: admin は localhost 前提(公開しない)+ 危険操作(将来の adoption)は操作ガード+監査ログ
- **診断表示の規律(021 パターン)**: 重い診断はリクエスト内で計算しない — ops ジョブ等でオフライン計算 → 永続化 → UI は読むだけ

**ロードマップ(読み→書き / 既存データ→新計算 / 危険な書き込みは最後)**
1. **admin 土台 + モデルレジストリ(本 feature)** — 最小リスクの読みデータでアーキ全体を通す
2. データ被覆率 + ジョブ履歴 — 日付×レース充足マップ・ingestion_jobs 一覧(現状の盲点)
3. アクション — backfill/refresh/live refresh の UI 起動(ops ジョブ経由)
4. 診断永続化 + ビューア — 新テーブル(migration・VI 正当化)で 047 セグメント診断等を永続化 → 読むだけ表示
5. adoption 制御 — 唯一の active 保護・実行前確認・監査ログ。最後(最も危険)

## 本 feature のスコープ(第1回)
- **US1 (P1) モデル一覧 API**: `GET /api/v1/models` — model_versions 全行を「active 優先 → created_at DESC」で返す。各行 = model_version/model_family/feature_version/label_schema/adoption_status/created_at + metrics_summary からの抽出(win LogLoss/AUC/ECE/Brier、objective/calibration/train_through/n_model_rows/git_sha、adopted、calibration/importance 収録有無)。**read のみ・再計算しない**(021 規律)。metrics_summary 欠落キーは null(旧モデルは train_through 無し=050 以降のみ、これも正直に null 表示)。
- **US2 (P2) admin SPA + レジストリ画面**: 新 `admin/` パッケージ。(a) レジストリ一覧 — どれが active か一目・主要指標・train_through・feature_version、(b) モデル詳細 — 既存 `/models/{mv}/calibration`・`/importance` を表示(未収録 404 は「未収録」の typed 表示)+ adoption 理由(gate 判定の adopted/reasons)。front と同じ規律: 型は openapi 自動生成+committed snapshot+drift-check、nullable は中央 formatter、loading/typed-empty/typed-error の 3 状態。

## スコープ外(後続 feature)
被覆率・ジョブ履歴・アクション起動・診断ビューア・adoption 操作・認証。front(end-user)の変更ゼロ。

## Requirements
- **FR-001**: `GET /api/v1/models` は read-only(SELECT のみ・再計算/書込なし)。api の全 path GET 不変・betting/training 非 import 境界不変。
- **FR-002**: レスポンスの指標値は `metrics_summary` に永続化済みの値の転記のみ。欠落キーは null(0 埋め・推定禁止)。
- **FR-003**: 並びは deterministic(active 優先 → created_at DESC → model_version)。014 の select_prediction_run と同思想。
- **FR-004**: admin/ は独立パッケージ(front 非改変)。API 契約先行: openapi 再生成 → admin 側 snapshot+型生成+drift-check(015/VI 同型)。front 側の committed openapi.json・endpoint 期待リストも更新(純追加)。
- **FR-005**: admin は localhost 運用前提を README/起動設定に明記(公開しない)。認証は明示的に deferred と記録。
- **FR-006**: スキーマ変更なし・migration なし(必要データは model_versions に既在。train_through は 050 で記録開始済み=旧モデル null は正直に表示)。

## Success Criteria
- **SC-001**: `GET /api/v1/models` が実 DB で lgbm-042(active)を先頭に全モデルを返し、各行に LogLoss/AUC/adoption_status が入る。metrics 欠落モデルでも 500 にならず null。
- **SC-002**: admin SPA のレジストリ画面で「active がどれか・主要指標・feature_version・train_through」が一覧表示され、行クリックで詳細(calibration/importance/adoption 理由、未収録は「未収録」)が見える。
- **SC-003**: api 全 path GET 維持・境界テスト緑・openapi drift-check(front/admin 両方)緑。
- **SC-004**: api/admin スイート緑。front は openapi 契約更新以外 無変更。

## Assumptions
- 表示規律は 021 継承(事実表示・損益色/推奨語なし)。モデル指標は OOS eval 由来の永続値のみ。
- codex CLI セッション内 3 回起動失敗 → 見送り宣言・single-opinion(014/015/021/040 の確立済みパターンの組合せ)。

## Deferred
ロードマップ 2〜5(被覆率/ジョブ履歴/アクション/診断ビューア/adoption)・認証・admin の Docker/deploy 組込・モデル比較ビュー・metrics_summary の時系列化
