# Data Model: 意思決定支援の表示強化 (021)

**スキーマ変更なし**。新規テーブル/カラムなし。既存の永続データを read し、表示用の派生値を API レスポンスに追加するのみ。下記は「API が返す/算出する論理エンティティ」。

## 1. 市場推定 win 確率 q（per horse, 算出値）
- **source**: `race_horses.odds`（win オッズ、既存）。
- **算出**: canonical field（スクラッチ除外＋有効オッズ）の win オッズに `market_implied_win_probs`(010) → その field 上で Σ=1 に再正規化。
- **fields**: `market_win_prob: float | null`（有効オッズなしは null=未提供、0 補完しない）。
- **不変条件**: p と**同一 canonical field**で算出（R1, 憲法 IV）。`Σ market_win_prob ≈ 1`（その field 上）。
- **ラベル**: 「市場推定（vote-share）・FL bias 含む・真の確率でもモデル p でもない」（pseudo 扱い）。
- **leak**: モデル特徴に流用しない（R9）。

## 2. モデル win 確率 p（per horse, 既存）
- 既存 `HorsePrediction.win`（009 canonical field の正規化済み win）。q と別フィールドで保持（p≠q）。

## 3. p−q 乖離（表示のみ, front 算出）
- front が `win`(p) と `market_win_prob`(q) から算出。**中立提示**（利益示唆語/損益色/ソート禁止, R3）。母集団不一致や q=null の馬は乖離を出さない。

## 4. 校正サマリ reliability（per model_version, 事前永続→read）
- **source**: eval walk-forward OOS 算出 → `model_versions.metrics_summary`(既存 JSONB) に追記。
- **構造**:
  - `bins: [{ pred_lo, pred_hi, pred_mean, realized_rate, realized_ci_low, realized_ci_high, count, suppressed }]`（等幅 bin、件数必須、realized_rate に Wilson 信頼区間＝FR-006b の不確実性, analyze U1）
  - `ece: float`、`n_total: int`、`oos: true`、`source: "walk_forward_oos"`
  - audit: `model_version`、`valid_years`（OOS 期間）
- **不変条件**: OOS のみ（in-sample 不可, R2）。少数件 bin は抑制/統合（R5）。model_version スコープ（R8）。
- **leak**: 結果由来の診断であり read 専用、特徴に流用しない（R9）。

## 5. データ裏付け data_backing（per horse/prediction, リーク安全）
- **source**: 事前レース情報のみ（馬の過去出走数＝recent_form の Unknown 有無、field_size）。結果/オッズ/表示派生値 不使用（R6, 憲法 II）。
- **fields**: `data_backing: "weak" | "medium" | "strong" | null`（粗いカテゴリ）。
- **採用条件**: 過去 OOS で「weak 群は校正/誤差が悪い」と確認できる場合のみ採用。確認不可なら本フィールド/US3 を defer（FR-012）。
- **ラベル**: 「データ裏付けであり的中確信ではない」明示。

## 6. 監査・前提メタ（表示, 既存+追加）
- 既存: `prediction_run_id`/`model_version`/`logic_version`/`computed_at`。
- 追加表示: オッズ `as_of`・source（確定/事前推定）、「市場 q がモデル p より上手い（020）」注記。（EV/控除率 表示は 021 スコープ外＝将来 EV を出す時に FR-018 を適用, analyze G1）

## エンティティ関係
- 1 race → N 出走馬。各馬に p（既存）と q（新, 同一 field）と data_backing（新, 任意）。
- reliability は race ではなく model_version に紐づく（レース横断の診断）。
- すべて read-only。書き込みなし、スキーマ不変（head=0006 維持）。
