# Implementation Plan: 人気-不人気バイアス補正（favorite-longshot bias correction）

**Branch**: `013-fl-bias-correction` | **Date**: 2026-06-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/013-fl-bias-correction/spec.md`

## Summary

010 の市場含意勝率 `q=(1/odds)/Σ(1/odds)`（投票シェア、人気-不人気バイアス含む）を、過去の (q, 実現勝敗) から校正し補正済み
`q'` を得る。**正準方式はべき乗 `q'_i ∝ q_i^γ`**（γ を**レース正規化後**の勝者尤度 MLE で学習）。`q'` を 009 エンジンに通すことで
010 の推定オッズ・011/012 の EV を補正（opt-in）。学習は train-only / walk-forward（対象レース開始より厳密前、結果リークなし）。
`q'` は市場由来でモデル p と別物（p≠q）、オッズ/q' は win モデル特徴に**一切使わない**。評価先行: **第一指標=勝率校正**
（NLL/Brier/ECE、人気帯別、採否ゲート）、**補助=012 乖離ハーネスの補正前後比較**（診断のみ）。スキーマ変更なし。

codex の CRITICAL（再正規化が marginal を変える / エンジンが再正規化する）を「正規化後 q' を学習・評価対象にする」「エンジン整合」
で機構解消する（下表）。既存 `probability/market_calibration.py`（q の NLL/Brier・同着除外）を再利用。

## Technical Context

**Language/Version**: Python 3.12（`uv`）

**Primary Dependencies**:
- `probability`（010 `market_odds` / `market_calibration` を拡張、009 engine を再利用）
- `betting`（011/012 の exotic_recommend/exotic_backtest/exotic_divergence が補正済み推定オッズを opt-in）
- **numpy のみ**（べき乗 γ = 自前 1 次元 MLE、新規依存なし）。pandas、SQLAlchemy 2.0。
  ※ isotonic/loglog は「正規化後 conditional-logit 目的での実装」が非自明なため**将来**（MVP は power のみ）。

**Storage**: PostgreSQL 16（読: race_horses.odds / race_results）。**スキーマ変更なし**。校正器は artifact + logic_version 相当メタに
記録（オッズのスナップショット履歴は作らない、憲法 V）。

**Testing**: pytest + testcontainers。合成データで 正規化後校正の学習/適用・単調性・Σ=1・walk-forward 厳密前・決定論・
リーク・ガード（q'/odds が win モデル特徴に入らない）・ECE 固定ビン・小サンプル帯・同着除外・乖離前後比較。実 DB スモーク。

**Target Platform**: Linux / macOS の手動 CLI 実行

**Project Type**: `probability` 拡張（校正器 + 補正経路 + 評価）+ `betting` の opt-in 配線

**Performance Goals**: γ MLE はレース集合上の 1 次元最適化（軽量）。評価は 010/012 と同等。

**Constraints**: 正規化後 q' を学習/評価対象に。エンジン整合（再正規化/clip 無作用）。walk-forward 厳密前 + race_id タイブレーク。
方式選択は学習窓内（選択リーク防止）。p≠q、オッズ/q' 非特徴量。採否ゲートは勝率校正。決定論。closing-odds 限界を明示。2007+。

**Scale/Scope**: 単勝市場の q 補正のみ。多出力モデル・Kelly・モデル p 側補正は将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: race_id 12 桁・2007+。既存 race_horses/race_results を使う。新 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 校正は **q（市場オッズ側）のみ**を入力、モデル p 非参照。`q'`/オッズを **win モデルの
  特徴量に一切使わない**（リーク・ガードテストで担保）。学習は **walk-forward 厳密前**（対象レース開始より前、race_id タイブレーク）、
  評価対象レースの結果を学習・方式選択に使わない。closing-odds の限界を明示し post-start odds を deployable EV に使わない。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: **第一指標=勝率校正**（NLL/Brier/ECE/信頼性、人気帯別）を採否ゲートに。補助=012 乖離
  （実 exotic は独自の偏りを持つため診断のみ）。baseline=補正なし（生 q）。**PASS（本原則の実装）**
- [x] **IV. 確率整合性**: `q'_i=g(q_i)/Σg(q_j)` でレース内 Σ=1、取消・除外・無効オッズ除外 + 残存再正規化、field_size は補正後出走
  集合から導出。009 の整合性を継承。**PASS**
- [x] **V. 再現性と監査**: 方式・γ・学習窓・サンプル数を logic_version 相当メタ + artifact に記録。**スキーマ変更なし**（オッズ履歴を
  作らない）。推定オッズ・評価は疑似評価明示。決定論。**PASS**
- [x] **VI. feature 分割規律**: スキーマ変更なし。補正は opt-in（生 q 経路は後方互換）。多出力モデル・Kelly・モデル p 補正は将来に
  明示分離。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` second opinion を取得・記録（下表）。CRITICAL/HIGH を機構解消。**PASS**

### Second Opinion 記録（codex:codex-rescue — spec/plan 段階）

| 重大度 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **CRITICAL** | per-horse `f(q_i)` 後の再正規化が校正済み marginal を変える | **正規化後 `q'=g(q)/Σg(q)` を学習・評価対象**に。正準=べき乗 `q^γ` を勝者尤度 MLE（R1） |
| **CRITICAL** | 009 が q' を再度 normalize/clip → 評価した q' と使う q' がズレうる | 我々の正規化/clip をエンジンと整合させ**渡す q' を無作用化**、評価はエンジン正規化後ベクトルで（R2） |
| HIGH | 「評価で方式を選ぶ」は選択リーク | 方式/γ 選択は**学習窓内**（MLE / nested walk-forward）、最終評価は選択に未使用（R3） |
| HIGH | walk-forward の同日タイ未定義 | **対象レース開始より厳密前** + race_id タイブレーク、日付 `<=` 禁止（R3） |
| HIGH | race_horses.odds は closing 寄りの恐れ | retrospective 研究と明示、operational は出走前オッズ、closing→朝の非転移を限界として開示（R4） |
| HIGH | market_odds に q' 注入口が無い | 補正経路を**新規 opt-in 追加**、生 q 経路は後方互換維持（R2） |
| HIGH | 無効オッズ除外で running set 変化、field_size 外部依存 | **補正後の出走集合から field_size 導出**（R2/IV） |
| HIGH | q-only は field 文脈を無視 | 正規化が field 文脈を内包（`/Σg`）。必要なら field_size 層別を評価（R1） |
| MED | 生 `1/odds` は控除/overround 込み | 出力は正規化 q ドメイン、生オッズは補助文脈のみ（R1） |
| MED | isotonic OOR でタイル平坦化 | 範囲ガード + 範囲外件数報告 + べき乗テールフォールバック（R5） |
| MED | 疎なテール帯で isotonic 不安定 | 最小サンプル・テール統合・nested walk-forward 比較（R5） |
| MED | ECE/信頼性が未定義 | 固定ビン・空ビン処理・clip・**正規化後 q' で計算**（R6） |
| MED | 人気帯が eval データで動く | 固定境界 / 学習窓エッジ、安定タイブレーク（R6） |
| MED | 実 exotic 乖離は偏ったターゲット | **採否ゲートは勝率校正**、乖離は診断のみ（R7） |
| MED | q' が特徴量に入らない保証 | **リーク・ガードテスト**（odds/q/q' を win モデル入力として拒否）（R8） |
| MED | スキーマ無変更で監査喪失リスク | 方式/窓/版を logic_version/artifact メタに保存（オッズ履歴は作らない）（R8/V） |
| LOW | 空/勝者なし評価が 0 ライク | 不足データは fail-fast / 不十分マーク（R6） |
| LOW | 同着が q 校正から脱落 | 同着は除外し件数明示（既存 market_calibration 同方針）（R6） |

最重要 TOP3: ①正規化後を校正対象に（marginal 破壊回避）②エンジン整合（評価=使用 q'）③選択/結果リーク（学習窓内選択・厳密前 walk-forward）。

## Project Structure

### Documentation (this feature)

```text
specs/013-fl-bias-correction/
├── plan.md
├── research.md          # R1 正規化後校正(べき乗) / R2 エンジン整合・注入口 / R3 リーク・選択 / R4 closing-odds / R5 方式・テール / R6 ECE・帯 / R7 採否ゲート / R8 監査・特徴量ガード
├── data-model.md        # FLCalibrator・CorrectedMarketProbs・校正/乖離レポート・不変条件
├── quickstart.md        # 学習 → 補正適用 → 校正/乖離評価 の検証手順
├── contracts/
│   ├── fl_calibrator.md   # fit/apply(正規化後 q')・方式・walk-forward の契約
│   └── fl_evaluation.md   # 勝率校正(NLL/Brier/ECE・帯) + 012 乖離前後比較 の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
probability/                                          # 010 を拡張
├── src/horseracing_probability/
│   ├── fl_bias.py            # 追加: power γ MLE(正規化後を学習)・fit_fl_calibrator(walk-forward 厳密前)・apply→q'。isotonic/loglog は将来
│   ├── market_odds.py        # 拡張: estimate_market_odds に補正経路(corrected q' 注入)を opt-in 追加、生 q 後方互換
│   ├── market_calibration.py # 拡張: q vs q' の NLL/Brier/ECE・信頼性曲線(人気帯別、正規化後 q')
│   └── cli.py                # 拡張: fl-fit / fl-evaluate
└── tests/                    # 正規化後校正・単調・Σ=1・厳密前 walk-forward・ECE 固定ビン・小帯・同着・決定論

betting/                                              # 011/012 の opt-in 配線
└── src/horseracing_betting/
    ├── exotic_recommend.py / exotic_backtest.py / exotic_divergence.py  # 補正済み推定オッズを opt-in(use_corrected_q)
    └── (リーク・ガードテスト: q'/odds が win モデル特徴に入らない)
```

**Structure Decision**: `probability` に校正器（`fl_bias.py`）と補正経路を追加し、010 の `market_odds`/`market_calibration` を拡張。
正準=べき乗（正規化後を勝者尤度で学習）。`betting`(011/012) は opt-in で補正済み推定オッズを使う。評価は既存 q 校正
（market_calibration）を q vs q' 比較に拡張し、012 乖離ハーネスを補助に再利用。採否は勝率校正で判断。

## Complexity Tracking

> Constitution Check 違反なし。スキーマ変更なし。記入不要。
