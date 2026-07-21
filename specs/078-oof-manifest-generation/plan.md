# Feature 078 plan — OOF Calibration Manifest Generation

**Status**: Draft(codex 設計レビュー反映済み・段階分割）。詳細判断は research D1–D9。

## 技術方針
既存(実装済み)資産の上に**生成側**を足す。DB/migration/API 不変。manifest は disk artifact のみ。
- 再利用: OOF bundle(per-race win/top2/top3・content-addressed)/ `load_p_samples_from_oof` / `calibrate_oof`
  (two_gamma) / `fit_stage_discount`(golden-section λ2/λ3 core, eval)/ `attestation_from_model_dir` /
  `build_manifest`/`write_manifest`(schema v2, 076 で拡張)。
- 新規: stage-λ の OOF fit(raw win で・D1)/ prequential-eval と deployment-final-fit の分離(D2)/
  hardened gate(D3)/ frozen calibration-sample + result-snapshot artifact(D7)/ manifest **v3**(D9)/
  `generate-manifest` CLI / OOF-replay parity 検証(D8)。

## 段階(codex 推奨フロー準拠)
frozen sample artifact → prequential eval → policy decision → all-OOF final refit → candidate manifest →
replay parity + strong binding → 別 promotion record → prospective shadow。

### Phase 1 — US1: stage-λ の OOF-faithful 評価機構(P1・自己完結・fixture テスト可)
- `load_topk_samples_from_oof(session, bundle)`: OOF win + `_placed_finishers` で top-k サンプル。
  dead-heat 契約(D4): λ2=1着+2着一意 / λ3=1〜3着一意 / 2着同着は λ3 も無効。
- `calibrate_stage_oof(session, bundle, gate_config)`: **raw OOF win で**(D1)prequential λ2/λ3 fit
  ・strictly-later block・**atomic top2+top3 verdict**(D3: bootstrap CI・LogLoss/Brier 非悪化・worst-fold・
  実採点 sufficiency)。ECE label = 全 started 馬の multi-positive(D4)。append-only evaluation artifact。

### Phase 2 — US2: deployment final-fit + manifest 生成(P1)
- prequential-eval(verdict)と **deployment final-fit(全 eligible OOF 再 fit)**を分離(D2)。
- **frozen calibration-sample + result-snapshot artifact**(D7): calibration_sample_hash・result_snapshot_hash・
  reference checksum・gate-config hash・policy version。決定論(明示 sort・NaN/Inf 拒否・2-process byte 一致)。
- **manifest v3**(D9): 構造化 evaluation(両 stage verdict + params + fit_through/fit_race_set_hash/n_fit)+
  versioned eligibility policy を verifier が**再計算**(nonidentity⟺ADOPT・REJECT⟹identity・stage set/order)。
  consumer 別 pipeline(serving_raw stage / betting post-two-gamma)を記録(D1)。
- `generate-manifest` CLI: bundle load →(calibrate_oof + calibrate_stage_oof)→ deployment refit → attestation →
  build_manifest(v3, fit_through=max deployment-fit 日・D5)→ **manifest_digest-keyed content-addressed path**
  (`artifacts/oof/<bundle>/manifests/<manifest_digest>/`・create-only atomic・D7 の bundle_digest 固定バグ是正)。

### Phase 3 — US3: full OOF job + 検証(P2・operator)
- 実 lgbm-063 で `generate_oof_bundle`(数時間)→ `generate-manifest` → THE production manifest。
- **OOF-replay parity(pre-activation・D8)**: OOF win vector を production 純 apply へ replay=per-horse
  top2/top3 完全一致・win byte 不変・Σ≈2/3・単調性・identity byte parity。
- **別 promotion record**(append-only)で candidate→promoted を分離。
- 実 γ/λ で 076 gate 再走(full-precision parity・digest token・fail-closed・全 entry path)。
- prospective shadow(post-activation・065 基盤)は 078 スコープ外(運用蓄積後）。

## 憲法整合
II(OOF strict-past・raw fit で分布一致・派生値非還流)/III(事前登録フル gate・過去 verdict 不変・historical
backfill を OOS 証拠にしない)/IV(win 不変・Σ 整合)/V(frozen artifact・append-only・決定論・promotion 分離)/
VI(契約先行・DB schema 不変・manifest v3 は disk のみ)。

## スコープ外
076 activation 経路変更 / 077 registry(strong binding 全経路・save_model_version 上書き廃止)/ 新 model 学習・
昇格 / production 既定 ON 昇格(実 manifest 検証後の別 operator 判断)。

## codex レビュー
research.md D1–D9(gpt-5.6-sol・全採用）。
