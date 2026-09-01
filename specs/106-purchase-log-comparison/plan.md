# Implementation Plan: 実購入記録と三者比較 (106)

**Branch**: `main 直行(表示+記録の追加・モデル/評価に不干渉)` | **Date**: 2026-08-31 |
**Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

## Summary

利用者の実購入を 1 操作で記録(append-only・提示スナップショット凍結)し、読み取り時計算で
「実購入(全券種・実現) / cap 政策の反実仮想(単勝のみ・凍結オッズ) / 賭けない(0)」の三者比較を
表示する。書き込みは ops(同期 POST)・読みは api(GET 純追加・read-only 維持)・front に記録 UI と
比較ページ。2026-08-31 確定方針のガードレール 6(実際の賭け行動で判定)の実装。

## Technical Context

- **触るパッケージ**: db(migration 0017・新テーブル 1)/ ops(POST 1 本)/ api(GET 2 本)/
  front(記録 UI・一覧・比較ページ・/ops proxy)
- **触らないもの**: features / training / eval / serving / betting / probability の全ロジック、
  FEATURE_VERSION、既存テーブル、既存 API 応答(純追加のみ)
- 精算の部品: win=既存 `api/backtest.py::win_realized` 流用。exotic 的中規約(011)は api 内の
  純関数として実装(definitional 二重実装・049 前例)。推定オッズ=probability(010)
- 設計判断の詳細は research.md D1〜D7

## Constitution Check

| 原則 | 判定 | 根拠 |
|---|---|---|
| I. データ契約 | PASS | race_id は既存 FK。新テーブルは行動記録で ID 体系に触れない |
| II. リーク防止 | PASS | purchase_records を features が読まないことを leak-guard テストで固定(D7)。予測・買い目生成・精算ロジック不変(FR-011) |
| III. 評価先行 | N/A→PASS | モデル・評価に触れない。比較は表示であり採否ゲートに使わない |
| IV. 確率整合性 | PASS | 確率の導出・表示に変更なし |
| V. 再現性と監査 | PASS | append-only(DB トリガ)・提示スナップショット凍結・訂正履歴・counterfactual/pseudo の明示(075 命名・二重疑似バッジ) |
| VI. スキーマ/契約 | **正当化要** | 新テーブル 1(migration 0017)。正当化: 行動記録は既存のどのテーブルの意味論にも属さず、append-only 強制(トリガ)という独自の契約を持つ。api は read-only 維持(GET 純追加)・書き込みは ops(053 前例)。OpenAPI は両サービスとも純追加+snapshot 同期 |

## Project Structure

```
db/        migrations/0017_purchase_records.py(新テーブル+append-only トリガ)・models
ops/       routers/purchase.py(POST・検証・result-pending 判定)・schemas
api/       queries/purchase.py・settlement.py(exotic 的中純関数)・comparison.py・routers
front/     PurchaseActions(BetSlip 内 3 ボタン+差分編集)・PurchaseList・ComparisonPage・opsClient
```

## codex 設計レビュー

結果: **採用 1 / 修正 4 / 不採用 0**(全件反映済み・詳細は research.md D2/D3/D5/D8/D9)。

| 論点 | 判定 | 反映 |
|---|---|---|
| ops 同期 POST | 採用 | ジョブ機構・advisory lock は使わず、一意制約+単一 Tx(D8) |
| スナップショット凍結 | 修正 | schema 版の凍結・correction は複製せず参照・見送り 3 区分(unavailable≠ゼロ)・run_id はクライアント送信値(D2) |
| 読み取り時計算 | 修正 | as_of で restatement を正当化・系列順序=レース時系列・単一 DB スナップショット・推定器出所の併記(D3) |
| result-pending 判定 | 修正 | 「発走前」でなく観測事実 `result_pending_at_record`+根拠時刻を保存。UI は「結果取込前/後に記録」+恒久注記(D5) |
| repo 固有の罠 | 修正 | 明示 DTO+値まで assert(splat-null)・全 openapi snapshot の byte 比較・payload hash 冪等・**実行時ロールでの append-only 検証**(TRUNCATE revoke・cascade 無し・downgrade の復元)(D8/D9) |

## Complexity Tracking

- 新規に増える公開面: ops POST 1・api GET 2・front ページ 2 — いずれも既存パターンの写像
  (053 proxy / 014 read-only / 087 カード UI)
- 最大の独自性 = 提示スナップショットの凍結(D2)と append-only トリガ(D6)。どちらも前例あり
  (075 の counterfactual snapshot / 084 の chaos_readouts)
