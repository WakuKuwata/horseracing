# Implementation Plan: オッズ上限つき買い目 policy + 正直な意思決定支援表示

**Branch**: `064-odds-cap-betting-policy` | **Date**: 2026-07-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/064-odds-cap-betting-policy/spec.md`

## Summary

現行の win 買い目ロジック(`betting/ev.py::select_ev_bets`: EV=p×odds≥1.0 の全 started 馬買い)は真OOS(2008–2026 walk-forward・902,710頭)で realized 回収 ×0.721。bet の約半分が 51 倍超の大穴帯に集中し ×0.634 で全体を押し下げる(047/048 の tail 過信)。**唯一頑健なレバー=オッズ上限フィルタ(odds<21 で ×0.818・19/19 年で現行超え)**。本 feature は (1) `select_ev_bets`/`generate_recommendations` に **win 用 odds-cap(上限のみ・cap=21・選定段フィルタ)** を選定母集団を絞る形で追加(EV閾値・renorm・Kelly は不変、cap 無効時バイト同等)、(2) production 構成(pl_topk)walk-forward で現行 policy vs cap policy を比較する採用ゲート、(3) rec panel を「正直な意思決定支援」に(回収<1・no-bet/本命ベタ基準・odds帯別回収・中立注記・skip 理由)。**スキーマ変更なし**(cap は `logic_version` に記録)。既定は opt-in、ゲート合格後に cap ON へ切替。

## Technical Context

**Language/Version**: Python 3.12(betting/eval/api)、TypeScript + React + Vite(front)

**Primary Dependencies**: betting(既存 ev/recommend/roi/strategies)、eval(market_edge/splits/predictor 経由の walk-forward)、training(LightGBMPredictor: pl_topk+isotonic+TE)、api(FastAPI read-only)、front(Vitest+MSW)

**Storage**: PostgreSQL 既存。**migration なし**(`recommendations` 既存列 + `logic_version` に cap 記録)。

**Testing**: pytest + testcontainers(betting/eval/api)、Vitest+RTL+MSW(front)。leak-guard・byte-parity・walk-forward・契約 drift-check。

**Target Platform**: ローカル/staging(既存 deploy)。CLI + read-only API + SPA。

**Project Type**: 既存マルチパッケージ(betting/eval/api/front)への薄い結線。

**Performance Goals**: cap は O(n) フィルタ=推奨生成レイテンシ不変。採用ゲートは walk-forward(pl_topk は fold 毎数分、19 fold=長時間ジョブ、nohup+監視)。

**Constraints**: cap 無効時バイト同等(後方互換)。cap/オッズはモデル特徴に流入しない(II)。selection は results を読まない。closing-oracle バイアス残存(過去オッズ closing 寄り・購入時点非保持=019)。

**Scale/Scope**: win のみ。exotic 対象外。cap=21 上限のみ事前登録。

## Constitution Check

*GATE: Phase 0 前に PASS。Phase 1 後に再確認。*

- [x] **I. データ契約**: raceId/ID/ラベル契約に変更なし(新データ源なし)。PASS。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: cap 値・オッズ・q・realized ベースラインは買い目 policy と表示にのみ使用、モデル特徴に戻さない(leak-guard test)。selection は race_results を読まない(cap 判定はオッズのみ)。p≠q 分離維持(cap は odds 側、p は不変)。PASS。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: cap=21 を事前登録、production 構成 walk-forward OOS で現行 policy と比較、採否バーは「回収率改善 かつ fold 安定」(ROI>1 単独不可)。cap 値は評価結果を見てから動かさない(selection leak 回避)。ゲート合格後に既定 ON。PASS。
- [x] **IV. 確率整合性**: cap は選定段フィルタ。cap 除外馬も確率分母に残す(odds-missing と同じ扱い=勝ちうるが賭けない)→ 009 導出・p 再正規化を変えない。win_prob バイト不変。PASS。
- [x] **V. 再現性・監査**: cap 種別/値/policy を `logic_version` に記録、stake=fraction×bankroll 再現可。pseudo/real 分離、realized は ResultBadge(既存)。PASS。
- [x] **VI. feature 分割規律**: スキーマ変更なし。表示に本命ベタ基準の per-race データが要る場合のみ OpenAPI 純追加で契約先行(front snapshot+drift-check 同期)。UI は API 契約確定後。PASS。
- [x] **品質ゲート**: codex second opinion を plan 段で並走(betting+eval+採用ゲート=高リスク)。両案差分と採否を research.md に記録。PASS。

**Gate result: PASS(スキーマ変更なし・新リーク面ゼロ・後方互換バイト同等が担保)。**

## Project Structure

### Documentation (this feature)

```text
specs/064-odds-cap-betting-policy/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0: 設計判断(cap 配置/バイト同等/ゲート harness/表示基準)
├── data-model.md        # Phase 1: スキーマ不変・logic_version 文法・エンティティ
├── contracts/           # Phase 1: betting API/CLI・eval ゲートレポート・(必要なら)api 追加
│   ├── betting.md
│   ├── eval-gate.md
│   └── display.md
├── quickstart.md        # Phase 1: 検証手順(バイト同等・ゲート・表示)
└── checklists/requirements.md
```

### Source (touched)

```text
betting/src/horseracing_betting/
├── ev.py                # select_ev_bets(..., odds_cap=None) 追加(cap 除外は分母に残す)
├── recommend.py         # generate_recommendations(..., win_odds_cap=None) 透過 + logic_version
├── strategies.py        # OddsCappedEVStrategy 追加(採用ゲート用・Favorite/Uniform は既存)
├── roi.py               # score_backtest 既存流用(指標は proposal-doc 準拠)
└── cli.py               # recommend-serve/backfill に --win-odds-cap(既定 None=現行)
eval/src/horseracing_eval/
└── policy_gate.py       # (新) 純 scorer: OOS rows × strategy 比較レポート(現行 vs cap)
training/.../cli.py       # policy-gate-eval CLI = walk-forward driver(production predictor 注入)→ eval scorer
api/src/horseracing_api/
├── backtest.py          # favorite_realized 等の read-time 純関数追加(betting 非 import)
├── schemas.py           # win_policy_status(skip 理由区別)を純追加
├── queries.py/router.py # 本命ベタ基準の per-race realized + policy status を純追加
front/src/components/
└── RecommendationPanel.tsx  # no-bet/本命ベタ基準・odds帯別・中立注記・skip 理由(policy status 由来)
```

## Phase 0 / Phase 1

- **Phase 0 → research.md**: 主要設計判断を確定(cap を独立引数にする理由 / cap 無効バイト同等の担保点 / 分母保持の是非 / 採用ゲート harness の落とし穴 / 表示基準の read-time 算出可否 / closing-oracle 明示)。codex second opinion を反映。
- **Phase 1 → data-model.md, contracts/, quickstart.md**: スキーマ不変の確認、logic_version 文法、betting/eval/表示の契約、検証手順。

## Complexity Tracking

- スキーマ変更なし・新テーブルなし。cap は既存選定関数への 1 引数追加(選定段のフィルタ、sizing/確率と直交)。
- 最大コストは採用ゲートの production pl_topk walk-forward(長時間ジョブ)= 検証は proxy 済のため「忠実性の最終確認」に限定。
- 表示は既存 read-time 純関数(api/backtest.py)+ front 派生。本命ベタ基準のみ小さな read-only API 追加の可能性(契約先行)。
