# Implementation Plan: 騎手の時変切片 — confirmatory 測定と採否

**Branch**: `main`(measurement feature・scripts 完結・結線差分ゼロ) | **Date**: 2026-09-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/107-jockey-tv-intercept/spec.md`

## Summary

screening 3 段(オラクル −0.0039 → 静的 b −0.0020 → 時変 b **−0.003281 CI[−0.00602,
−0.00029] 生存**)で絞り込んだ「年 1 回更新・730 日窓の騎手部分プーリング切片」を、
選択に使っていない 2022-2024 窓で v4 契約の confirmatory として一度だけ測定し決着させる。
実装は**公式 `paired_eval` に候補 factory を渡す薄い driver** のみ — ゲート・CI・seed 膨張・
証拠 artifact は eval パッケージの正規機構を使い、二重実装を作らない(088/100/106 の教訓)。
verdict=ADOPT なら training/serving への本実装(US2)、REJECT/NO_DECISION なら保全と
設計族閉鎖(US3)。**null も成功**(生存マージン +0.00028 < sd_fold 0.0018)。

## Technical Context

**Language/Version**: Python 3.12(uv 管理・training/eval パッケージの既存環境)

**Primary Dependencies**: horseracing_eval(paired_eval / decision / evidence /
delta_provenance / bootstrap)・horseracing_training(CalibSplitFactory /
OofCalibratedPredictor / fit_calibrator / predictor)・LightGBM・scipy(L-BFGS)

**Storage**: PostgreSQL 16(読み取りのみ)+ materialized parquet
(features-021・data_through 2026-08-23・2022-24 窓を完全被覆)。**書き込みゼロ・
migration なし**

**Testing**: pytest(driver の構造テスト=定数一致・窓 assert・縮退 abort)+
実行そのものが SC-001..003 の検証

**Target Platform**: ローカル macOS(nohup バックグラウンド・推定 1h)

**Project Type**: 測定 feature(scripts/ + specs/ 完結。US2 は ADOPT 条件付きで
training/serving に波及)

**Performance Goals**: 実測ベース見積: spike は 2 outer fold ×3 アームで 53 分 →
confirmatory は 3 outer fold ×2 アーム ≈ 60 分(095 の教訓=実績比で見積もる)

**Constraints**: 評価窓に 2025-01-01 以降を含めない(構造 assert)・
`load_eval_races(start_date=FEATURE_POOL_START)` 必須・凍結定数の事後変更禁止

**Scale/Scope**: 採点 約 10,300 レース / 約 960 開催日(2022-2024)・騎手 約 170 人/fold

## Constitution Check

- [x] **I. データ契約**: raceId 12 桁・既存 loader のみ・ID 結合なし(騎手 ID は
  frame の `jockey_id` をそのまま鍵に使う=新規結合ゼロ)。pre-2007 は
  `FEATURE_POOL_START` で構造的に除外(FR-008)
- [x] **II. リーク防止**: b は strict-past の内側 OOF 行のみから推定(arm E の OOF 機構を
  そのまま使用)・評価窓のラベルは判定のみ・λ は訓練内経験ベイズ(評価窓を見ない)。
  b/isotonic のラベル二重使用は既知の限界として文書化(FR-016・評価窓は非汚染)
- [x] **III. 評価先行**: 本 feature 自体が評価。v4 契約・gate-config 凍結・δ provenance・
  証拠 artifact・walk-forward。ECE は v4 ゲート組込(非劣化)
- [x] **IV. 確率整合性**: 候補は raw score へ外部加算後にレース内 softmax → isotonic →
  `assemble_predictions`(arm E と同一の正規化経路)= Σ=1 維持。009 導出は不変
- [x] **V. 再現性・監査**: gate-config hash・証拠 artifact(ビット一致再計算)・
  実行前監査 JSON・verdict.json。測定は何も persist しない(prediction_runs 非汚染)
- [x] **VI. feature 分割規律**: UI なし・スキーマ/API 不変・migration なし。US2(ADOPT 時)
  の serving 統合は verdict 後に tasks の条件付き分岐で設計を固定してから着手
- [x] **品質ゲート**: **codex unavailable**(CLI 初期化エラー ×2・復旧別タスク化)。
  代替 = spike 段階の設計レビュー反映済み+research D10 のセルフレビュー checklist

## Project Structure

### Documentation (this feature)

```text
specs/107-jockey-tv-intercept/
├── spec.md
├── plan.md              # this file
├── research.md          # D1-D10
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── confirmatory.md  # driver の入出力・fail-closed・verdict 契約
│   └── adoption.md      # ADOPT 分岐の artifact/serving 契約(概略・verdict 後に確定)
├── gate-config.json     # 凍結(hash は quickstart に記録)
├── evidence/            # 実行後: 監査 JSON・証拠 artifact・verdict.json
└── tasks.md
```

### Source Code (repository root)

```text
scripts/
├── jockey_timevarying_spike.py     # 既存(screening・変更しない・保全)
└── jockey_tv_confirmatory.py       # 新規: 薄い driver
    #   1. gate-config 読込 → assert_confirmatory(cfg, hash, window) + assert_delta_provenance
    #   2. 窓 assert(to < 2025-01-01)・load_eval_races(start_date=FEATURE_POOL_START)
    #   3. 実行前監査(nk: 件数・b カバレッジ)→ evidence/preflight.json
    #   4. paired_eval(候補 factory, CalibSplitFactory, gate_config=cfg,
    #                  first_valid_year=2022, subgroups=True[開示のみ])
    #   5. final_decision → verdict.json / 証拠 artifact(append-only)
    # 候補 factory = spike と同一定義(W=730/MIN_RIDES=30/λ clamp[10,500])。
    # 定数一致は unit テストで spike モジュールと突き合わせて固定

eval/ training/ serving/ features/ db/ api/ ops/ front/ admin/   # 測定段階は差分ゼロ
# US2(ADOPT 時のみ): training/src/horseracing_training/jockey_intercept.py +
#   recipe フィールド(既定 None・NEW_HASH_DEFAULT_OMISSIONS)+ serving 外部加算 +
#   artifact 凍結(b/λ/window メタデータ)— 詳細は verdict 後に確定
```

**Structure Decision**: 測定は scripts/ 完結(102/104 同型)。判定機構は eval の正規
関数のみを呼ぶ(再実装ゼロ)。ADOPT 分岐だけが training/serving に波及し、それは
tasks の条件付きフェーズに隔離する。

## 主要設計判断(research 参照)

| # | 決定 | 根拠 |
|---|---|---|
| D1 | 評価窓 2022-2024(3 outer fold)・2025+ 構造排除 | 選択リーク排除・日付だけの窓規則 |
| D2 | 公式 paired_eval + final_decision に factory 直渡し | 二重実装の禁止(088/100/106) |
| D3 | アーム A vs 候補(W=730 凍結)・B は走らせない | B は診断であって estimand でない |
| D4 | v4 config・δ=100 導出参照・critical_subgroups=[] | 偽 NO_DECISION 回避・死角は開示+昇格ゲート |
| D7 | 単一 seed 42 + sd_fold 膨張(v4 標準) | seed 増しで CI を縮める操作の禁止(100 R9) |
| D10 | codex unavailable → セルフレビュー代替 | CLI 初期化エラー ×2・復旧別タスク |

## Complexity Tracking

なし(Constitution Check 全 PASS・migration なし・スキーマ/API 不変)。
