# Contract: 098 採否ゲート(実行前凍結)

## 事前登録(gate-config.json に凍結・hash fail-closed)

- **カットオフ**: 2019-01-01 / 2021-01-01 / 2023-01-01(097 と同一・固定)
- **分裂の定義(シミュレーション)**: `race_date >= cutoff` の行の `race_class` に逆写像
  `1勝→１勝 / 2勝→２勝 / 3勝→３勝` を適用(アーム A)。DB は触らない(DataFrame 変換)。
  `オープン`・`重賞` など表外は変換しない=**配備する変換そのもの**を測る
- **採点窓**: [cutoff+1y, cutoff+2y) = 2020 / 2022 / 2024(互いに素・washout 1 年)。washout の意味:
  アーム A のモデルが分裂綴りを約 1 年分学習した状態=実際の切替後 10 か月に近い
- **アーム**: candidate = B(正準=無変換)/ active = A(分裂コピー)。paired 差は B−A(負=正準が良い)。
  レシピは両方 arm E(OOF isotonic 8 blocks)+ rounds 900 + 091 体重マスク + seed 42、
  fold 内で再学習(保存モデル流用禁止)
- **primary**: 3 窓 pooled の paired race-level winner NLL 差。v4 標準式
  `point < −0.002 AND seed 分散込み CI 上限 < 0`(sd_fold=0.001816・n_folds=3・b=2000)
- **guard_real_direction(実窓方向一致)**: 実データ 2025-10-11〜DB 最新日で、matrix を raw 表現で別途
  1 回構築し、A=生(分裂のまま)/ B=`canonicalise` 適用コピー、両アーム fold 内再学習。標本 CI の
  evidence-of-harm: `ci_low > +0.005` なら FAIL
- **transportability**(FR-007a・research D6):
  (a) 各カットオフの点推定が pooled と同符号 (b) leave-one-cutoff-out pooled の点推定が同符号
  (c) 実窓 CI が pooled の符号を自信を持って否定しない(pooled<0 なら実窓 `ci_low ≤ 0`)。
  いずれか不成立 → NO_DECISION
- **実窓の層別診断(報告のみ・ゲートではない)**: 実窓の paired 差を「前走にリステッド/オープン歴あり
  vs なし」「nk: サロゲート馬の割合(レース単位・0 / <50% / ≥50% の 3 層)」で層別して報告する。実窓は MDE≈0.009 で層別は
  検出力ゼロのため verdict に入れない(codex plan Q2 の部分採用)
- **充足**: pooled n_days ≥ 300(097 実測 323 日)。未満は NO_DECISION
- **付随成分**(凍結): recent_guard non_inferiority 3/5y margin 0.005 max_date 2024-12-31 /
  top2・top3 非劣性 0.0005 / ECE 幅 0.001・緊急 0.05 / pooling 規則は 097 と同一

## verdict(正本は単一式)

    verdict = primary_pooled AND guard_real_direction AND transportable
    ADOPT / REJECT。NO_DECISION は「評価実行不能(充足未達を含む)」または「transportability 不成立」のときのみ

- 三値の優先順位(spec FR-007・凍結): (1) 実行不能・充足未達 → NO_DECISION (2) `primary_pooled AND
  guard_real_direction` が不成立 → REJECT(transportability は参考値として記録するだけ・verdict を変えない)
  (3) 両方成立かつ `transportable` 不成立 → NO_DECISION (4) 全成立 → ADOPT

- 出力規律: primary・guard・transportability の全成分を計算し終えるまで効果数値を出さない
- 個別カットオフの事後選別禁止(068 C2)。結果は「反実仮想頑健性」として報告
- ADOPT は**表現(features-023)の採用**であってモデル昇格ではない(昇格 = 標準窓非劣化 +
  本 verdict + prospective の 3 点セットで別途・097 D9 同型)

## アーム同一性の契約(INV-A1..A5・実行時 assert・097 の対称性/provenance を置換)

- **INV-A1**: 両アームの frame は `race_class` を除く全列で check_exact 一致
- **INV-A2**: `race_class` の差異行は規定の行に限る(シミュ: `race_date >= cutoff ∧ 値∈{1勝,2勝,3勝}`/
  実窓: `値∈{１勝,２勝,３勝}`)かつ差異行数 > 0
- **INV-A3**: paired diff が全レースで 0.0 なら abort(アームが同一=比較不成立)
- **INV-A4**: 差異行数と両アームの `race_class` 列 hash を verdict.json に記録
- **INV-A5**: matrix は測定ごとに同一セッションから 1 回だけ構築(シミュレーション用 canonical-v1・実窓
  ガード用 raw の計 2 本・`use_materialized=False` を assert)し、変換側のアームはそのコピー。
  **コピーであること**(`is not`)と `_scope_columns` 後の feature_cols/categorical_cols が同一集合
  (drop は使わない)で「差は綴りだけ」を機構で担保
- 実装は `training/spelling_split.py::assert_arm_identity`(INV-A1/A2/A4/A5)と driver(INV-A3)

## artifact の隔離

- `artifact_kind = "counterfactual_spelling_simulation"`・`eligible_for_verdict = False`・
  `feature_adoption_eligible = True`。gate-config `artifact_isolation.kinds` に追加
- `verdict` は RegimeReport 準拠のオブジェクト(文字列禁止)
- smoke: 事前登録外のカットオフ(2016-01-01・採点 2017)・効果数値は redact・kind="smoke"
