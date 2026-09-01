# Tasks: 実購入記録と三者比較 (106)

**Input**: [spec.md](spec.md) / [plan.md](plan.md) / [research.md](research.md) /
[data-model.md](data-model.md) / [contracts/](contracts/recording-and-comparison.md)

**組織**: user story 単位(各フェーズが独立に検証可能な増分)。US1(記録)→US2(比較)が MVP。
テストはこの repo の規約どおり必須(leak-guard・append-only・splat-null 値検証)。

## Phase 1: Setup — migration とモデル

- [ ] T001 migration 0017 を作成: `db/migrations/versions/0017_purchase_records.py` —
  data-model.md の列定義(kind 8 値・result_pending_at_record・pending_basis_at・
  client_request_id UNIQUE・payload_hash・presented_snapshot JSONB)+ **append-only トリガ**
  (UPDATE/DELETE 拒否・D6)+ TRUNCATE revoke(D9)。downgrade はトリガ/関数を除去し grant 復元
- [ ] T002 ORM モデル追加: `db/src/horseracing_db/models.py` に PurchaseRecord(列は 0017 と一致)
- [ ] T003 [P] migration head assert の波及更新(0016→0017): `grep -rl "0016" */tests` の
  head-assert テストを一括更新(040/054 前例)
- [ ] T004 append-only の実行時ロール検証テスト: `db/tests/integration/test_purchase_append_only.py`
  — **実行時の DB ロール**で UPDATE/DELETE/TRUNCATE が拒否されること・FK cascade が無いこと・
  downgrade→upgrade の往復(D9)

## Phase 2: Foundational — 境界と共有純関数

- [ ] T005 leak-guard: `features/tests/unit/test_purchase_leak_guard.py` — features パッケージの
  どの loader/SQL も purchase_records を参照しないことを import/AST 走査で固定(D7・INV-P5)
- [ ] T006 [P] exotic 的中判定の純関数: `api/src/horseracing_api/settlement.py` —
  011 規約(exacta/trifecta=順序・quinella/trio=集合・wide=上位3内包含・place=頭数規則)。
  selection は canonical 形(INV-P4)。betting を import しない(定義的二重実装・049 前例)
- [ ] T007 [P] settlement.py の単体テスト: `api/tests/unit/test_settlement.py` — 011 の数値例で
  券種ごとに固定(同着・返還・place の頭数境界 5-7/8+ を含む)
- [ ] T008 [P] 有効記録の畳み込み純関数: `api/src/horseracing_api/purchase_fold.py` —
  行系列 → EffectiveRecord(correction は置換・void は無効化・レース時系列順・INV-P2)+ 単体テスト
  `api/tests/unit/test_purchase_fold.py`

## Phase 3: US1 — 提示ベースの 1 操作記録 (P1)

**Goal**: 買い目画面から「そのまま/変更/見送り」を 1 操作で記録し一覧できる。
**Independent Test**: quickstart §1-2(記録 → GET 一覧に snapshot 凍結済みで現れる・UPDATE 拒否)。

- [ ] T009 [US1] ops 書き込み endpoint: `ops/src/horseracing_ops/routers/purchase.py` —
  POST /ops/v1/purchase-records。検証(race 存在・kind 列挙・INV-P4・correction 対象存在・
  run_id と race_id の整合=INV-P7)・result-pending 判定(D5: 観測事実として保存)・
  冪等(D8: 同一 id+同一 hash=200 リプレイ / 不一致=409)・typed 422。schemas は明示 DTO
- [ ] T010 [US1] ops endpoint の統合テスト: `ops/tests/integration/test_purchase_records.py` —
  正常系 3 種(as_presented/modified/skipped_presented)+ freeform + correction + 冪等リプレイ +
  409 + 422 各種 + result_pending_at_record の両値
- [ ] T011 [US1] api 読み出し: `api/src/horseracing_api/queries.py` + routers に
  GET /api/v1/purchase-records(有効記録+履歴・settled_estimated は is_estimated=true 必須)。
  openapi snapshot 再生成 + front drift-check 更新
- [ ] T012 [US1] api 読み出しテスト: `api/tests/integration/test_purchase_records_api.py` —
  **値まで assert**(splat-null 防止・075 の罠)・訂正履歴の復元・is_estimated の伝搬
- [ ] T013 [US1] front /ops proxy + opsClient: `front/vite.config.ts` に /ops proxy(053 の admin
  パターン)・`front/src/api/opsClient.ts`・ops-openapi snapshot を admin と byte 一致で追加
- [ ] T014 [US1] 記録 UI: `front/src/components/PurchaseActions.tsx` — BetSlip 直下に
  「そのまま購入/変更して購入/見送り」3 ボタン(見送りも同格・賭博助長導線なし)。
  変更時は金額編集+行の除外。presented_snapshot は**描画に使った提示をそのまま**送信
  (prediction_run_id 含む・INV-P7)。client_request_id 生成。エラー/成功の 3 状態表示
- [ ] T015 [US1] PurchaseActions テスト: `front/src/components/PurchaseActions.test.tsx` —
  3 操作・snapshot 送信内容・提示ゼロ時は skipped_presented でなく no_recommendation・
  提示取得失敗時は presentation_unavailable(codex Q2 の 3 区分)
- [ ] T016 [US1] 記録一覧: `front/src/pages/PurchaseListPage.tsx` + ルート — 有効記録+履歴・
  「結果取込前/後に記録」バッジ(D5 文言)+ 恒久注記・訂正/取消操作(新行の追加として)+ テスト

## Phase 4: US2 — 三者比較 (P1)

**Goal**: 同一起点の累積収支 3 本(実購入/政策 win のみ/賭けない=0)を注記つきで表示。
**Independent Test**: quickstart §3・§5(手計算一致・推定精算の別掲・as_of)。

- [ ] T017 [US2] 比較の導出: `api/src/horseracing_api/purchase_comparison.py` —
  レース時系列で畳み込み(D3)・実購入=全券種(win=win_realized 流用・exotic=T006・配当欠落は
  010 推定+マーク)・政策=snapshot 内 win×counterfactual_snapshot・no_bet=0・as_of・
  推定器出所・coverage_rate {overall, pre_ingestion, post_ingestion}(codex Q5)
- [ ] T018 [US2] GET /api/v1/purchase-comparison ルート + openapi 純追加 + drift-check
- [ ] T019 [US2] 比較の統合テスト: `api/tests/integration/test_purchase_comparison.py` —
  quickstart §3/§5 の数値固定(公式配当×記録金額の手計算一致・見送りレースで政策線のみ動く・
  未確定は pending 別掲・推定→実配当の自動置換・scope=win_only の対称ビュー・
  include_post_hoc 切替)
- [ ] T020 [US2] 比較ページ: `front/src/pages/PurchaseComparisonPage.tsx` + ルート —
  3 本の累積・常設注記(反実仮想/税引前/券種非対称/記録率の基準)・「うち推定精算 N 件・M 円」
  別掲(二重疑似バッジ)・単勝のみビュー切替・事後入力切替。**利益語・損益色・ソート禁止**
  (forbiddenPhrases 規律)+ テスト(禁止語・バッジ coverage・値表示)

## Phase 5: US3 — 記録の正直さ (P2)

**Goal**: 記録率(全開催レース分母)・事後入力・訂正の可視化。
**Independent Test**: quickstart §4。

- [ ] T021 [US3] 記録率と内訳を比較応答に(T017 に含めた coverage の front 表示):
  比較ページに記録率(overall + 取込前/後の分離)+「未記録があると実購入の線は実態より
  良く見えうる」+「全開催レース基準のため低く出るのは正常」注記
- [ ] T022 [US3] 事後入力の全画面区別: 一覧・比較の内訳で「結果取込後に記録」を視覚区別し、
  集計切替(既定=含む・件数明示)の E2E テスト(front)

## Phase 6: US4 — 自由記録 (P3)

- [ ] T023 [US4] freeform フォーム: `front/src/components/FreeformPurchaseForm.tsx` —
  レース・券種・selection(canonical 形へ正規化)・金額。presented_snapshot=null・
  政策線は「このレースの政策買い目が無い場合は動かない」明示 + テスト
- [ ] T024 [US4] freeform の精算経路テスト(api): 買い目提示の無いレースの freeform が
  実購入線にだけ算入されること

## Phase 7: Polish

- [ ] T025 実 DB E2E(quickstart 全節): 記録→一覧→比較→訂正→推定精算→配当取込後の自動置換を
  ローカルスタックで通し、SC-001..006 を確認
- [ ] T026 [P] 全スイート回帰: db/ops/api/front + openapi drift(front・admin 両方)+
  forbiddenPhrases + leak-guard
- [ ] T027 [P] spec.md に実装結果を転記・memory 更新(product ロードマップ #4 出荷)

## Dependencies

```
Phase 1 (T001-T004) → Phase 2 (T005-T008) → US1 (T009-T016) → US2 (T017-T020)
                                                    ↘ US3 (T021-T022) は US2 の後
                                                    ↘ US4 (T023-T024) は US1 の後(US2 と並列可)
Polish (T025-T027) は全 US 後
```

- MVP = Phase 1-4(US1+US2)。US3/US4 は独立増分
- 並列機会: T003/T006/T007/T008(異ファイル)・T023-T024 と T021-T022・T026/T027

## Implementation Strategy

MVP first: migration+境界(1-2)→記録(3)→比較(4)で「記録して比較が見える」を最短で成立させ、
正直さの可視化(5)と自由記録(6)を独立増分として積む。実装は codex 並列(ops/api/front の
3 ストリーム)+ 親が配線・E2E(087/089 の運用パターン)。
