# Research: 複数モデル切り替え基盤

Phase 0。非自明な設計判断とその根拠。codex unavailable(環境未インストール・本セッション 2 回失敗)→ single-opinion。既存 feature(014/021/040/044/051/053)の確立パターンとの整合で代替検証。

## D1: セレクタの母集団をどう front に渡すか

**Decision**: 予測応答(`PredictionResponse`)に `available_models: list[AvailableModel]` を純追加。各要素 = `{model_version, display_name, purpose, adoption_status, is_selected}`。このレースに永続化済み run を持つモデルだけを列挙。

**Rationale**:
- 1 リクエストでセレクタを描画でき、レース詳細を開いた時点で選択肢が揃う(追加ラウンドトリップ不要)。
- run を持つモデルだけ提示 → 選んだ瞬間 404 になる無駄が構造的に無い(FR-007 の「未生成」状態は defensive/直接 API 用に残す)。
- read-only・純追加(既存フィールド不変)で契約フォークなし。

**Alternatives considered**:
- 別 GET `/races/{id}/models`: エンドポイント増・ラウンドトリップ増。予測応答に同梱の方が凝集。
- `/api/v1/models`(全モデル)を front から呼ぶ: run の有無を無視し全モデルを並べ 404 スパム。レース非依存で「このレースで選べる」を表現できない。

## D2: 用途メタの保存先

**Decision**: `model_versions` に nullable 列 `display_name TEXT` / `purpose TEXT` を追加(migration 0011)。

**Rationale**:
- 用途はメトリクスでなくモデルの素性。`metrics_summary` JSONB は 021 で「eval 指標の転記のみ」と規律化されており、そこに識別メタを混ぜない。
- 既存 `label_schema`(= `win_top2_top3` のラベル体系)と概念が別。名前衝突を避け `display_name`/`purpose` とする。
- migration は 0008(explanation)/0009(diagnostic_runs)/0010(raw features)と同型の軽微な nullable 追加。憲法 VI 正当化済。
- 既存行は遡及書換せず null のまま(040 importance / 050 train_through と同じ「以後 populate」方針)。

**Alternatives considered**:
- `metrics_summary` JSONB に格納(migration 回避): 021 規律違反 + 用途とメトリクスの混線。head-assert 波及は避けられるが概念的コストが上回る。
- `model_version` の命名規約で用途表現(例 `accuracy-first-001`): 技術 ID が artifact/logic_version/prediction_run FK に参照され、リネームは広域破壊(FR-001 違反)。

## D3: 用途メタの書込経路

**Decision**: 書込は CLI(training/registry 層に `set-model-label --model-version --display-name --purpose`)。API/admin は読むだけ。

**Rationale**:
- 014 read-only 不変(全 path GET)を守る。用途設定は低頻度のオペレータ操作で CLI が自然。
- admin 書込は 053 の ops subprocess 経由が必要=低頻度メタ設定に過剰なインフラ。

**Alternatives considered**:
- admin から `POST /ops/.../model-label`: read-only 思想に穴。将来 adoption 制御(ロードマップ 5)で認証込みの書込面を設計する時にまとめる方が筋。
- 直接 SQL: 監査/再現性が弱い。CLI なら logic を 1 箇所に集約でき将来 admin 化も容易。

## D4: run 選択の model_version 化

**Decision**: `select_prediction_run(session, race_id, model_version: str | None = None)`。`model_version` 指定時は active-first の case 式を外し、そのモデルの run を `computed_at DESC → prediction_run_id DESC` で選択。未指定時は現行(active-first)完全維持。run 不在は `None` を返し router が typed 404。

**Rationale**:
- 決定規則(computed_at DESC → run_id DESC)を採用モデル選択と一貫。指定時は「active 条件だけ外す」最小差分。
- 未指定パスは 1 文字も挙動が変わらない(後方互換=SC-002)。

**Alternatives considered**:
- 新関数を分離: 重複ロジック。既存関数に任意引数の方が DRY で回帰面が小さい。
- 指定モデルの run 不在時に active フォールバック: 見ているモデルの誤認(FR-005 違反)。却下。

## D5: 後方互換と `available_models` 追加の整合

**懸念**: `available_models` 追加で model 未指定応答のバイトが変わり SC-002「100% 同一」と矛盾しないか。

**判断**: SC-002 の本質は「未指定時の **run 選択と各馬確率** が不変」であること。`available_models` は追加メタフィールドで、既存フィールド値は不変。pydantic 応答へのフィールド追加は既存コンシューマ(front は型再生成)を壊さない。応答全体を厳密一致で比較する型テストのみ新フィールドを追記する。→ spec の SC-002 は「run/確率の不変 + 既存フィールド不変」と解釈し、追加メタは互換の範囲。

## セルフレビュー総括(codex 代替)

- リーク境界: 変更なし(特徴量非関与)。leak-guard テスト影響なし。
- 契約: 純追加のみ、drift-check byte 一致維持。
- migration: head 0010→0011、head-assert テスト更新を tasks に明示。
- 残リスク: 用途メタ書込 CLI のテスト(冪等/上書き)を忘れずに。ModelSelector の「未生成」状態を loading/empty/error と別 testid で固定。
