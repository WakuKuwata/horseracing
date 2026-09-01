# Contracts: 記録と比較 (106)

## 書き込み (ops, :8001)

### POST /ops/v1/purchase-records → 201
Body: { race_id, kind, bets[], presented_snapshot?, corrects_record_id?, note? }
- サーバが付与: recorded_at, recorded_pre_race(result-pending 判定・D5), purchase_record_id
- 検証: race 存在 / kind 列挙 / bets の canonical 形(INV-P4) / correction は対象行の存在 /
  amount 正整数。失敗は typed 422(理由コードつき)
- **冪等(D8)**: client_request_id 必須(一意制約)+ payload hash。同一 id+同一内容の再送 =
  既存行をリプレイ(200)。同一 id+異なる内容 = 409 conflict。advisory lock は使わない
- prediction_run_id はクライアントが描画に使った値を必須送信(サーバは race_id 整合のみ検証)
- append-only: この endpoint 以外に書き込み手段を作らない(UPDATE/DELETE は DB トリガ拒否)

## 読み取り (api, :8000 — read-only 維持・GET のみ)

### GET /api/v1/purchase-records?from&to → 200
有効記録の一覧(履歴込み)。各行: kind・bets・精算状態(pending/settled_real/settled_estimated/
refunded)・recorded_pre_race・訂正履歴。**settled_estimated は is_estimated=true を必ず運ぶ**
(front の PseudoValue 経路・バッジ必須)

### GET /api/v1/purchase-comparison?from&to&scope={all|win_only}&include_post_hoc={true|false} → 200
{ as_of, series: {actual[], policy[], no_bet: 0}, cumulative: {...},
  coverage_rate: {overall, pre_ingestion, post_ingestion}, n_races,
  n_estimated_settlements, estimated_amount_yen, estimator_provenance,
  n_post_hoc, n_presentation_unavailable, notes[] }
- 系列の順序はレースの時系列。応答は明示 DTO で組み立て(splat-null 禁止・075 の罠)、
  値まで assert するテストを置く(形だけの検証は splat-null を素通しする)
- notes は常設文言のキー(反実仮想・税引前・非対称スコープ・記録率の基準)を含む
- 未確定レースは series に入らず pending として別掲

## OpenAPI / front
- api の追加は純追加(openapi snapshot 更新 + front drift-check 緑)
- front に /ops proxy 追加(admin の 053 パターン)。ops-openapi snapshot は admin と byte 一致
- 表示: 利益語・損益色・良い期間の既定表示 禁止(FR-010・既存 forbiddenPhrases 規律)
