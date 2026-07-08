# Implementation Plan: 本格スピード指数特徴 (speed figure features)

**Branch**: `061-speed-figure-features` | **Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/061-speed-figure-features/spec.md`

## Summary

(競馬場×トラック×距離正確値×馬場)セル別の as-of 基準タイム(strictly-before・同日除外の expanding 平均/分散)に対する過去走 z-score スピード指数を算出し、馬単位 as-of 集約 4 列(avg/best/recent3/last)を新群 speed_figure として追加(features-015→016)。新ソース列なし=source_fingerprint 不変・materialize-safe。serving は COMPATIBLE_PRIOR_FEATURE_VERSIONS に features-015 hash を追加ピン(features-014 ピン維持)して既存 3 モデルの byte-parity を守る(058 T013 第2回)。フル実装前に spike de-risk(binary 少数 fold → 微小なら pl_topk 追検証、059 教訓)。詳細は [research.md](research.md) D1–D8。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: pandas/numpy(既存スタックのみ、新規依存なし)

**Storage**: PostgreSQL 16(スキーマ変更なし・migration なし・新規 read 列なし)

**Testing**: pytest(features 中心、training/serving は回帰+E2E)

**Target Platform**: ローカル CLI(feature build・学習・評価)

**Project Type**: features パッケージ中心の特徴追加(020/023/059 と同型)

**Performance Goals**: セル統計は日次集計+cumsum(O(n log n))でビルド時間への影響は数秒級

**Constraints**: 既存列バイト不変(additive)/ materialized parity / 既存 3 モデルの serving byte-parity / NaN≠0

**Scale/Scope**: 95 万行 × 新 4 列。基準タイムセル ~数千(venue 10×track 2-3×距離 ~15×going 4)

## Constitution Check

- [x] **I. データ契約**: PASS — ID/ラベル契約に変更なし。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: PASS — 基準タイム(クロス馬統計)も個馬集約も strictly-before+同日除外(daily cumsum−当日、020 機構)。オッズ・今走結果不使用。grep 型 leak-guard 適用可+挙動型テスト(D5)。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: PASS — 事前登録ゲート(FR-008、020/023 同型)+ spike go/no-go(D7)。baseline=features-015(新群 drop)。
- [x] **IV. 確率整合性**: PASS — 特徴追加のみ、確率導出経路不変。標本不足は NaN(0 と区別)。
- [x] **V. 再現性・監査**: PASS — FEATURE_VERSION bump・決定論ビルド・parquet 再 materialize 手順明記。
- [x] **VI. feature 分割規律**: PASS — スキーマ/API/OpenAPI 不変。FEATURE_VERSION bump は新特徴群の追加として正当(030/056/059 前例)。
- [x] **品質ゲート**: 実施 — codex second opinion を具体設計(セル定義・標準化・クラス扱い・テスト)に対して取得(下記)。

## Codex second opinion(具体設計レビュー)

**1 回目(提案段階、2026-07-08 精度改善ブレスト)**: 「本格スピード指数は非市場系の本命候補。リスクはコース基準タイム・馬場差の全期間推定における静かなリーク、同日馬場差混入」→ 全て spec/design に反映済み(as-of 基準タイム必須・当日馬場差スコープ外)。

**2 回目(具体実装設計)**: 実施済み — 採否:

| codex の指摘 | 採否 | 反映 |
|---|---|---|
| min_samples=50 を実分布未確認で固定するな | **採用** | 実 DB 実測(585 セル・min_races=50 で 93.2% カバー)→ D1 で確定 |
| finisher 行プールは多頭数レースが基準に過重 → race-level 1 標本を検討 | **採用** | D1: race-level(レース finisher 平均を 1 標本)に変更。count=レース数となり「runner count だけの閾値管理」問題も同時解消 |
| クラス混合基準は「純粋な条件補正」でなく「能力寄り特徴」— spec に明記せよ | **採用** | D4 に明記。class 別補正レイヤーは deferred |
| clip±5 × 希少セル std 不安定 → best 列が鈍る副作用 | **一部採用** | race-level 標本+min_races=50 で std 安定性を先に確保。clip は維持し境界テストで固定 |
| 信頼度列 asof_spdfig_count を追加、worst/分散より優先 | **採用** | D3: 5 列構成に変更(count=有効 z の過去走数、履歴ゼロは 0.0) |
| 階層フォールバック(going→無し)を推奨 | **不採用(deferred)** | 実測カバレッジ 93.2% で NaN 正直路線の方が監査性が高い。カバレッジ不足が実害になったら別 feature |
| std 非依存の秒/100m 正規化を比較対象に | **採用(フォールバックレバーとして事前登録)** | D2/D7: spike 不発時に 1 回だけ試行 |
| pl_topk spike は「微小なら」でなく必須に(絶対軸にも 059 同型リスク) | **採用** | D7/contracts: 常時実施に強化 |
| 過去走 z 算出時も「その過去走当日の同セル他レース」を除外(cross-horse 統計は 023 より波及が広い) | **採用** | D5 の daily cumsum−当日 が保証。専用の合成データテストを tasks に追加 |
| features-014 と 015 の両方をピン | **採用(計画済み)** | D6 |
| STATIC_COLUMNS に誤登録禁止・build_asof_features 単一源 | **採用(計画済み)** | tasks のテストで機械固定 |
| テスト一覧(境界値・pool-end 非依存・additive・registry 整合・materialize parity) | **採用** | tasks に全て反映 |

実 DB セル分布は codex 環境から未検証だった → Claude 側で実測済み(上表 1 行目)。

## Project Structure

### Documentation (this feature)

```text
specs/061-speed-figure-features/
├── spec.md / plan.md / research.md (D1–D8)
├── data-model.md / quickstart.md
├── contracts/speed-figure.md
└── tasks.md (Phase 2)
```

### Source Code (repository root)

```text
features/src/horseracing_features/
├── speed_figure_features.py   # NEW: セル as-of 基準タイム + 過去走 z + 馬単位集約 4 列
├── materialize.py             # build_asof_features に speed_figure ブロック結線(単一 as-of 源)
└── registry.py                # FEATURE_VERSION 016 / FEATURE_GROUPS speed_figure /
                               # COMPATIBLE_PRIOR_FEATURE_VERSIONS に features-015 pin 追加

training/ serving/             # コード変更なし想定(FEATURE_VERSION 定数参照は registry 経由)
                               # 学習は既存 CLI、互換 E2E のみ
```

**Structure Decision**: features パッケージ内で完結(020/023/041/059 と同型)。loader 変更なし(新ソース列なし)。db/api/front/admin/betting/ops 不変。

## Complexity Tracking

違反なし。
