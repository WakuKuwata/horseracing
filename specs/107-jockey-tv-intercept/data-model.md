# Data Model: 騎手の時変切片 confirmatory (107)

DB スキーマ変更なし・migration なし。以下はすべてファイル artifact(specs/107 配下)と
実行時のメモリ内構造。

## 騎手切片 b(実行時・fold ごと)

| 項目 | 定義 |
|---|---|
| 鍵 | `jockey_id`(features frame の値をそのまま。新規結合なし) |
| 母集団 | その fold の訓練末尾 730 日**以内**の内側 OOF 行(strict-past・arm E の OOF 分割) |
| 資格 | window 内 30 騎乗以上。未満・UNKNOWN は係数なし(=0・prior へ完全縮約) |
| 推定 | booster raw log-score を offset に固定した winner NLL + ridge(L-BFGS 1 回) |
| 縮約 | 経験ベイズ λ = 1/τ̂²(method-of-moments・訓練内のみ)→ クランプ [10, 500] |
| 正規化 | 露出加重で中心化(softmax でレース定数は消えるが監査のため) |
| 適用 | raw log-score + b → レース内 softmax → isotonic → assemble_predictions |

### 不変条件

- **INV-J1**: b の推定に評価窓(採点レース)のラベル・スコアを一切使わない
- **INV-J2**: isotonic の fit 母集団は**全 OOF 行**で両アーム同一(差は b の有無のみ)
- **INV-J3**: 候補と arm E の予測が全レースで一致したら abort(縮退・097 型)
- **INV-J4**: W/MIN_RIDES/クランプは gate-config の凍結値と driver 定数が一致
  (unit テストで spike モジュールの定数とも突き合わせ)
- **INV-J5**: 評価レースのロードは `start_date=FEATURE_POOL_START`(pre-2007 除外)
- **INV-J6**: 採点窓に 2025-01-01 以降のレースが 1 件も入らない(構造 assert)

## gate-config.json(凍結・実行前)

100 の v4 形式。キー: `evaluation_contract_version: "v4"` / `primary_metric: winner_nll` /
`min_effect_delta`(=100 導出値・`delta_derivation_ref` 必須)/ `seed_noise.sd_fold=0.001816`
/ `bootstrap {b:2000, seed:20260902, alpha:0.05}` / `eval_window {from:2022-01-01,
to:2024-12-31, min_eval_days:900}` / `critical_subgroups: []`(理由コメント付き)/
`arms`(A=arm E rounds900 seed42 / candidate=+jockey_tv_intercept W730)。
`_` 前置キーは hash から除外される(canonical hash 契約)。

## 証拠 artifact(evidence/)

- `preflight.json`: fold 別 window 行数・騎手数・λ(raw/クランプ後)・nk: 騎手 ID 件数・
  評価レース騎乗の b カバレッジ
- `paired-evidence.json`: 公式 `PairedEvidenceArtifact`(per-race 生値・append-only・
  点推定/CI をビット一致で再計算可能=100 US1 契約)
- `verdict.json`: 三値 decision・reason・gate-config hash・contract version・実行時刻

## verdict

`final_decision(gate, subgroups, n_days, cfg)` の三値が正本。ADOPT / REJECT /
NO_DECISION。事後の読み替え禁止。ADOPT でも active は書き換えない(候補登録+
標準昇格ゲートは US2)。
