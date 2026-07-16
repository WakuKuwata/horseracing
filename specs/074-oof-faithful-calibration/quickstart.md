# Quickstart: OOF-faithful Calibration Evidence

**Feature**: 074 | 目的: 校正リークを OOF-faithful に是正し、two-gamma/stage λ を OOF 上で測り直した evidence + immutable manifest を作る。**production は変えない**。詳細は [research.md](research.md) / [data-model.md](data-model.md) / [contracts/cli.md](contracts/cli.md) / [gate-config.json](gate-config.json)。

前提: ローカル Postgres(horseracing)・active=lgbm-063(073 で確定・freeze 済)。**計算コスト高**(fold 再学習)→ まず `--smoke` で小 fold、フルは長時間 operator job。

## 0. 前提確認 — ✅ 確認済み(2026-07-16)

- **active=lgbm-063(features-017)**・073 freeze oracle 存在(`specs/073-.../legacy-freeze-lgbm-063.json`、SC-006 で digest 一致=win byte 不変を機械確認済み)。
- 073 の calibrated-stage ECE(FR-007)は未完=074 が参照 fulfill する前提であることを確認。
- **feature-version gap = 解決設計済み(research D9)**: 現 `FEATURE_VERSION=features-018` だが lgbm-063=features-017。**069 が features-018=features-017+additive F02(共有列 byte 一致)を実証済み**なので、OOF fit を attestation の `ordered_feature_columns` に **`restrict_features`(inclusion・fail-closed)で制限**すれば byte-faithful に features-017 を再現できる(近似でなく厳密)。実装=T032(codex レビュー後)。統合テスト(T008–T011)は factory 注入で features-version 非依存にメカニズム検証済み。

## 1. legacy attestation(US2)

- lgbm-063 の完全 resolved recipe attestation を metadata.json + 073 freeze から構築。
- フィールド欠落/差異で OOF 再構築が fail-closed。

## 2. OOF bundle 生成(US1)

```
training oof-generate --base-model-version lgbm-063 --from 2008-01-01 --to 2026-07-12 \
  --num-threads 1 --out artifacts/oof --smoke   # まず smoke
```
期待:
- 全 OOF race で `max(train_date) < race_date`(SC-001)。
- 同日レースが fit に混入 0(SC-002、`race_date<target_date`)。
- 対象レース結果を変更 → 当該 OOF prediction 不変・result hash のみ変化(SC-003)。
- 別モデル/full-history latest run を DB 追加 → bundle digest 不変(SC-004)。
- 2 回生成で byte 一致(SC-005)。

## 3. OOF 校正再検証(US3)

```
training calibrate-oof --bundle artifacts/oof/<digest> \
  --gate-config specs/074-oof-faithful-calibration/gate-config.json --stage all --json out.json
```
期待:
- two-gamma/λ が prior OOF のみ prequential fit・**strictly-later OOF block** で ECE(fit sample では測らない、SC-007)。
- 048 verdict = ADOPT/REJECT/NO_DECISION(点推定不可)。transfer-check ミスマッチ=NO_DECISION。
- `evaluation_contract_version=v2` append-only artifact。073 過去 verdict 上書き 0(SC-009)。

## 4. manifest 検証(US4)

```
training verify-manifest --manifest artifacts/oof/<digest>/manifest.json
```
期待:
- 改竄/partial/未知 schema/世代不一致=拒否・同 payload=冪等成功・同 key 異内容=conflict(SC-008)。
- full 精度 γ/λ・fold race hash・OOF checksum・code SHA を含む。

## 5. parity(全 US 共通)

- lgbm-063 persisted **win** 16 頭 mismatch 0(SC-006、`model_internal_win_parity`)。
- serving/betting/API 挙動変更 0・既存 PredictionRun/Recommendation 変更 0(SC-010)。

## 受け入れ判定

| 検証 | SC |
|---|---|
| OOF strict-past 100% | SC-001 |
| 同日混入 0 | SC-002 |
| 結果変更で OOF 不変 | SC-003 |
| bundle digest 安定 | SC-004 |
| OOF 生成 byte 決定論 | SC-005 |
| win byte parity | SC-006 |
| ECE は strictly-later OOF block | SC-007 |
| manifest fail-closed/冪等 | SC-008 |
| 073 verdict 不変 | SC-009 |
| production 不変 | SC-010 |
