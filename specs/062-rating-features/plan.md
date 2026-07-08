# Implementation Plan: as-of レーティング特徴 (Elo / Bradley-Terry rating features)

**Branch**: `062-rating-features` | **Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/062-rating-features/spec.md`

## Summary

対戦結果から Elo 多者ペアワイズで逐次更新した as-of レーティング(レース朝スナップショット)の馬単位派生列 5 群を追加(features-016→017)。既存能力特徴が無視する「相手の質」を織り込む。逐次状態のため materialize 安全性(決定論・pool-end 非依存・bit parity)が最大リスクで US2/FR-004 を P1 に格上げ。新ソース列なし=source_fingerprint 不変。serving は COMPATIBLE_PRIOR_FEATURE_VERSIONS に features-016(lgbm-061)+features-015 をピン。フル実装前に spike de-risk(レーティング正しさ+materialize 決定性 → binary+pl_topk、061 の重複リスク教訓)。詳細は [research.md](research.md) D1–D8。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: pandas/numpy(既存スタックのみ、新規依存なし)

**Storage**: PostgreSQL 16(スキーマ変更なし・migration なし・新規 read 列なし)

**Testing**: pytest(features 中心、training/serving は回帰+E2E)

**Project Type**: features パッケージ中心の特徴追加(059/061 と同型 + 逐次状態)

**Performance Goals**: 1 パス Elo 更新は O(Σ n_i²)(レース内ペア)だが n≤18 で軽量。materialize ビルド時間への影響は数秒〜十数秒

**Constraints**: 既存列バイト不変 / materialize 決定論・pool-end 非依存 / 既存 3 モデルの serving byte-parity / NaN≠0 / OOS ハイパラ調整禁止

**Scale/Scope**: 95 万行 × 新 5 列。レーティング状態 = 全馬(数十万頭)

## Constitution Check

- [x] **I. データ契約**: PASS — ID/ラベル契約に変更なし。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — レーティングは strictly-before の結果のみで更新・朝スナップショットで同日除外(D3)。オッズ不使用。grep 型 leak-guard 適用可+挙動型テスト(D8/INV-R1)。**逐次状態の pool-end 非依存(INV-R2)を専用テストで固定**。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — 事前登録ゲート(FR-010、020/023/061 同型)+ spike go/no-go(D8)。ハイパラは train 期間内固定・OOS 調整禁止。
- [x] **IV. 確率整合性**: PASS — 特徴追加のみ。初出走は固定初期値(事実)、starts で信頼度分離(NaN/0 と区別)。
- [x] **V. 再現性・監査**: PASS — FEATURE_VERSION bump・決定論ビルド(INV-R3)・再 materialize 手順明記。
- [x] **VI. feature 分割規律**: PASS — スキーマ/API/OpenAPI 不変。FEATURE_VERSION bump は新群追加として正当。
- [x] **品質ゲート**: 実施 — codex second opinion を具体設計(更新式・日単位凍結・pool-end 非依存・二重相対化・テスト)に取得(下記)。

## Codex second opinion(具体設計レビュー)

**1 回目(提案段階、2026-07-08 精度改善ブレスト)**: 「Elo/BT は非市場系で有望。距離/芝ダ/馬場/クラス別分割か shrink 設計が重要。リスクは同日結果混入・更新順・fold 外情報でのレーティング再推定」→ spec/design に反映(同日=日単位凍結・総合レーティングから開始・ハイパラ固定)。

**2 回目(具体実装設計)**: 実施済み。12 指摘**全採用**(不採用ゼロ)。主要:

| codex 指摘 | 採否 | 反映 |
|---|---|---|
| #1 PARITY: 窓ロードが履歴途中開始だと full-history と不一致 | **採用(実コードで回避確認)** | `build_feature_matrix` は 2007 から下限なしロード(窓は上限のみ)→ checkpoint 不要。**下限窓ロード禁止の回帰テスト**追加(research D2) |
| #2 同日除外は whole-day-batched 更新必須 | **採用** | D3 日単位凍結(朝スナップショット→日末一括更新) |
| #3 同日 2 走馬は両スタート同一朝値・対称寄与 | **採用** | D3 に明記+専用フィクスチャ |
| #4 Elo pairwise K/(m−1) が pragmatic baseline(PL は cleaner だが重い) | **採用** | D1(PL は deferred) |
| #5 K=24 は妥当だが未検証・後知恵調整するな | **採用** | D6 固定・OOS 調整禁止 |
| #6 margin は距離/馬場ノイズ→別 OOS variant | **採用(deferred)** | D1 |
| #7 DNF/DQ 除外・tie=0.5・行順タイブレーク禁止 | **採用** | D4 |
| #8 派生列(delta/max/starts)も朝スナップショットから(naive shift 禁止) | **採用** | D5 |
| #9 vs_field は 059 と冗長の可能性大だが無害→pl_topk ablation | **採用** | D5(初版含め ablation 判定) |
| #10 2007 コールドスタートは非リーク・starts で明示 | **採用** | D9 |
| #11 float 決定論(stable sort/固定 dtype/非並列 reduce) | **採用** | D2 |
| #12 K 分母は除外後有効頭数 m(raw starter でない) | **採用** | D1/D4 |

残リスク: 逐次状態の pool-end 非依存が最重要 → 専用テスト群(research 末尾)で機械固定してから spike。

## Project Structure

### Documentation (this feature)

```text
specs/062-rating-features/
├── spec.md / plan.md / research.md (D1–D8)
├── data-model.md / quickstart.md
├── contracts/rating.md
└── tasks.md (Phase 2)
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── rating_features.py         # NEW: Elo 多者更新(1 パス・日単位凍結)+ 馬単位派生 5 列
├── materialize.py             # build_asof_features に rating ブロック結線(単一 as-of 源)
└── registry.py                # FEATURE_VERSION 017 / FEATURE_GROUPS rating /
                               # COMPATIBLE_PRIOR_FEATURE_VERSIONS に 016+015 pin

training/ serving/             # コード変更なし想定(FEATURE_VERSION 定数は registry 経由)
                               # 学習は既存 CLI、互換 E2E のみ
```

**Structure Decision**: features パッケージ内で完結(059/061 同型)。逐次状態は rating_features.py に閉じ込め、build_asof_features は 1 箇所結線。loader/db/api/front/admin/betting/ops 不変。

## Complexity Tracking

違反なし(逐次状態は features 内部実装で、materialize 契約=決定論・pool-end 非依存で吸収)。
