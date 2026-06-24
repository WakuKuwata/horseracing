# Implementation Plan: exotic EV 推奨と疑似ROIバックテスト

**Branch**: `011-exotic-ev-recommendation` | **Date**: 2026-06-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/011-exotic-ev-recommendation/spec.md`

## Summary

既存パッケージ `betting/`(`horseracing-betting`)を拡張し、`probability/`(009+010)に依存して、exotic 券種
(複勝/馬連/馬単/ワイド/三連複/三連単)の EV 推奨と疑似ROIバックテストを実装する。`EV(c)=P_model(c)×O_est(c)`:
P_model=009 を**モデル win 確率 p** に、O_est=010 を**市場 win オッズ q** に適用。両者を**同一 canonical 出走母集団**
(p と win オッズが両方有効な馬)で計算し、`EV≥閾値` の上位 K を `recommendations`(is_estimated_odds=true、
estimated_market_odds_used=O_est、pseudo_odds=1/P_model、pseudo_roi=EV−1)に保存。疑似ROIバックテスト(払戻=stake×O_est)
で券種別 baseline と比較。**二重疑似**(推定オッズ + PL 外挿)を明示。スキーマ変更なし。

codex の BLOCKER(p/q 母集団不一致・JSONB selection・券種別採点・複数当たり・二重疑似)を本 plan で機構解消する。

## Technical Context

**Language/Version**: Python 3.12（パッケージ/実行は `uv`）

**Primary Dependencies**: `horseracing-db` / `horseracing-features` / `horseracing-eval` / `horseracing-serving`
(既存、betting の依存)+ **`horseracing-probability`(新規依存、009 joint_probabilities + 010 estimate_market_odds)**。
numpy、SQLAlchemy 2.0

**Storage**: PostgreSQL 16(読: race_predictions / race_horses.odds / race_results、書: recommendations)。
バックテストはレポートを返す(大量の recommendations は永続化しない)。

**Testing**: pytest + testcontainers。合成データで p/q 母集団整合・EV/上位K・selection JSONB 安全・券種別的中
(順序/無順序/包含)・複勝/ワイド複数当たり・baseline・二重疑似明示・決定論を検証。実 DB スモーク。

**Target Platform**: Linux / macOS の手動 CLI 実行

**Project Type**: 既存 `betting/` パッケージへのモジュール追加(exotic_ev / exotic_recommend / exotic_roi /
exotic_strategies / exotic_backtest / cli 拡張)

**Performance Goals**: 009/010 と同じ O(N^3)。EV≥閾値 上位 K で行数を抑制(1レース1券種あたり最大 K)。

**Constraints**: P_model と O_est を同一母集団で。p≠q(混同禁止)。買い目決定は結果非参照。selection は JSONB 安全配列
(frozenset 非保存)。複勝/ワイドは複数当たりをベット単位。二重疑似明示。決定論。append-only。

**Scale/Scope**: exotic 6 券種(+単勝は 007)。EV=P_model×O_est。券種別 baseline 2 種。実 exotic オッズ取得・Kelly は将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: 既存 race_id / recommendations(BetType 各券種)/ prediction_runs を使う。新 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: P_model は**モデル p**(006/009、odds/results 非参照)、O_est は**市場 win
  オッズ**(010、p 非参照)。EV=P_model×O_est で **p と q を分離**。買い目決定はレース結果(着順)を一切参照しない
  (採点のみ結果使用)。オッズは予測モデル特徴ではない。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 疑似ROIバックテスト harness を実装し、券種別 baseline(最低 O_est/均等)と
  同一条件比較。**二重疑似**を明示。推奨ロジックの採否を評価で判断。**PASS(本原則の実装)**
- [x] **IV. 確率整合性**: P_model/O_est を**同一 canonical 母集団**(p と win オッズ両方有効、取消・除外除外)で計算し、
  009/010 の入力で再正規化。009/010 の整合性を継承。**PASS**
- [x] **V. 再現性・監査**: `recommendations` に model_version(prediction_run 経由)・logic_version(EV式/閾値/K/stake/
  控除率/q ソース/cap/母集団ポリシー/009/010 版)・estimated_market_odds_used・pseudo_odds・pseudo_roi・computed_at。
  **is_estimated_odds=true・market_odds_used=null・二重疑似明示**。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし。実 exotic オッズ取得・Kelly・bias 補正は将来に明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` の second opinion(p/q 母集団・JSONB selection・券種別採点・二重疑似)を取得・
  記録(下表)。BLOCKER を本 plan で解消。**PASS**

### Second Opinion 記録(codex:codex-rescue — spec/plan 段階)

| 論点 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **p/q 母集団不一致** | **BLOCKER**: 009 は与キーを正規化、010 は無効オッズを落とす。母集団不一致で EV が別母集団の積になる | 同一 canonical 母集団(p と win オッズ両方有効)で 009/010、片方欠損は除外+再正規化 or スキップ監査(R1, FR-002) |
| **selection JSONB** | **BLOCKER**: frozenset を永続化しない。順序券種=順序付き配列、無順序=整列配列、単一=単一馬 | selection シリアライザを実装(R2, FR-005) |
| **疑似ROI 採点** | **BLOCKER**: 既存 roi は単勝1頭専用。券種別的中(順序/無順序/包含)が必要。**複勝/ワイドの複数当たりはベット単位** | exotic 専用採点を新設(R3, FR-007/008) |
| 二重疑似 | EV・ROI は二重疑似(推定オッズ + PL 外挿)。is_estimated_odds=true, market_odds_used=null, ラベル明示 | 採用(R4, FR-010) |
| 組み合わせ爆発 | EV≥閾値 を `(-EV, 決定論キー)` で整列し上位 K。最大行数=Σ min(K, 有効数) | 採用(R5, FR-003) |
| baseline | 券種別 最低 O_est(市場最有力)/ 均等(決定論シード)。同一レース・可用性・stake・K。成功=baseline 超え | 採用(R6, FR-009) |
| リーク/決定論 | 買い目は p + win オッズ + entry_status のみ、結果は採点まで非参照、同一 canonical ID、安定タイブレーク | 採用(R7, FR-004/012) |

最重要リスク TOP3: ①p/q 母集団不一致(別母集団の積)②exotic 採点(複数当たり/包含)③二重疑似の不明示。
①は canonical 母集団、②は券種別採点、③は二重疑似ラベルで対応。

## Project Structure

### Documentation (this feature)

```text
specs/011-exotic-ev-recommendation/
├── plan.md
├── research.md          # p/q 母集団・selection JSONB・券種別採点・複数当たり・二重疑似・baseline・選択
├── data-model.md        # CanonicalField・ExoticBet・selection 形・採点規則・不変条件
├── quickstart.md        # 推奨生成 → バックテスト → 監査・二重疑似確認手順
├── contracts/
│   ├── exotic_recommend.md  # EV 計算・selection・generate_exotic_recommendations の契約
│   └── exotic_backtest.md   # 券種別採点 / baseline / 疑似ROI の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
betting/                                   # 既存 horseracing-betting を拡張(probability 依存追加)
├── pyproject.toml                         # + horseracing-probability (path)
├── src/horseracing_betting/
│   ├── ev.py / recommend.py / roi.py / strategies.py / backtest.py  # (007 既存、単勝)
│   ├── exotic_ev.py                       # 追加: canonical 母集団 → P_model(009) + O_est(010) → EV → 上位K
│   ├── exotic_selection.py                # 追加: 券種別 selection の JSONB 安全シリアライズ + 的中判定
│   ├── exotic_recommend.py                # 追加: generate_exotic_recommendations → recommendations
│   ├── exotic_roi.py                      # 追加: 券種別採点(複数当たり)+ RoiReport(二重疑似)
│   ├── exotic_strategies.py               # 追加: EVStrategy / 最低O_est / 均等(券種別)
│   ├── exotic_backtest.py                 # 追加: 期間バックテスト(in-memory 予測+オッズ→EV→採点)
│   └── cli.py                             # 拡張: exotic-recommend / exotic-backtest
└── tests/
    ├── unit/                              # p/q 母集団・EV/上位K・selection・券種別的中・複数当たり・baseline・決定論
    └── integration/                       # 実 DB で推奨生成→保存→監査、バックテスト→baseline 比較
```

**Structure Decision**: exotic EV は 007 の EV/推奨/バックテスト枠組みの拡張であり、`betting/` にモジュール追加する。
確率/オッズは `probability/`(009 joint + 010 estimate)を再利用するため betting に probability 依存を追加。`exotic_selection`
で JSONB 安全 selection と券種別的中判定を一元化(009/010 のキー型 → 配列)。単勝(007)とは別経路で混入を防ぐ。

## Complexity Tracking

> Constitution Check に違反なし。記入不要。
