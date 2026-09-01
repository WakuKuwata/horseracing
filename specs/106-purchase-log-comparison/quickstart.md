# Quickstart: 実購入記録と三者比較 (106)

前提: ローカルスタック稼働(scripts/stack.sh)・migration 0017 適用済み。

## 1. 記録(US1)
買い目のあるレース画面で「そのまま購入」→ 201。
検証: `GET /api/v1/purchase-records?from=...` に行が現れ、presented_snapshot に
表示中だった買い目群が凍結されている。SC-001: 3 操作以内・5 秒以内。

## 2. 見送りと訂正(US1/FR-003)
同レースで「見送り」→ correction 行が積まれる(元の行は不変)。
検証: DB で UPDATE を試みる → トリガが拒否(INV-P1)。

## 3. 三者比較(US2)
確定レースを 2 件以上記録後、比較ページを開く。
検証: 3 本の累積が同一起点で並び、政策線=単勝のみ・非対称注記・税引前注記が常設(SC-002)。
手計算(公式配当 × 記録金額)と一致。

## 4. 事後入力の区別(US3)
確定済みレースで購入を記録 → recorded_pre_race=false・バッジ表示・集計切替が機能(SC-003)。

## 5. 推定精算(clarify Q2)
exotic_odds に配当が無いレースの的中 exotic を記録 → settled_estimated(二重疑似バッジ)で算入・
「うち推定精算 N 件」別掲。その後配当を取り込む → 自動で settled_real に置換。

## 6. 回帰(SC-006)
front 全テスト・api 全テスト・openapi drift-check 緑。features の leak-guard
(purchase_records 非参照)緑。
