# Data Model: race_class の表記統一(098)

スキーマ変更なし・migration なし。DB の `races.race_class` は供給元の綴りのまま(provenance)。
変わるのは**特徴層の表現**と **artifact metadata** だけ。

## 1. 正準綴り表(凍結・`race_class_canon.py`)

| 入力(netkeiba 期) | 出力(正準=JRA-VAN 綴り) | 根拠 |
|---|---|---|
| `１勝` | `1勝` | 同一クラス・学習 5,936 レース側へ |
| `２勝` | `2勝` | 同上 |
| `３勝` | `3勝` | 同上 |
| それ以外(`オープン`・`重賞`・`新馬`・`未勝利`・`Ｇ１`…・NULL) | **不変** | 表外は推測しない(FR-002)。`オープン` は `ｵｰﾌﾟﾝ`+`OP(L)` の混合 |

- 逆写像 `pseudo_split`: `1勝→１勝 / 2勝→２勝 / 3勝→３勝` を `race_date >= cutoff` の行にのみ適用
  (シミュレーション専用・本番経路から到達不能)。
- `canonicalise` の戻り値 `audit = {"mapped": {入力: 件数}, "out_of_table": {値: 件数}}`。

## 2. 表現(representation)と版

| 名称 | 値 | 意味 |
|---|---|---|
| `RACE_CLASS_REPRESENTATION`(registry 定数) | `"canonical-v1"` @ features-023 | この版のビルドが `race_class` に適用する変換 |
| features-021 以前 | `"raw"`(暗黙) | 変換なし=供給元の綴り |

- FEATURE_VERSION: `features-021` → **`features-023`**(022 は 097 の焼却番号)。列集合・列名は不変
  → `feature_hash` は不変(列名のみ)。値変更 bump(017 型)。
- `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-023"] = {"features-021": "663fe86c756428fca7411f23bb5f0a4eaa91926b067a0e0acc4a11d581da0f7a"}`
  (lgbm-094-cap900 の実測 hash)。compat 経路は生表現を渡すので共有列バイト同一の前提が成立。

## 3. Artifact metadata(追加キー・純加算)

| キー | 型 | 値 | 用途 |
|---|---|---|---|
| `race_class_representation` | str | `"canonical-v1"` / `"raw"` | serving がどの変換を適用するか(exact 経路=marker どおり・compat 経路=`raw` 強制・023 以降で欠落は fail-closed) |
| `categorical_vocab_hash` | str(sha256) | booster の `pandas_categorical`(順序つき・列順)を JSON 化した hash | 読込時に booster から再導出し不一致なら fail-closed |
| `categorical_vocab` | dict[col → list[str]] | 上の原本(`race_class` など全カテゴリ列) | 監査・語彙外値の検出・分裂トークン不在 assert |

`metrics_summary["training"]` にも `race_class_representation` を転記(DB だけで追跡できるように・050 同型)。

## 4. 評価アーム(driver 内の一時構造・永続化しない)

| アーム | シミュレーション(各カットオフ) | 実窓ガード(2025-10-11〜) |
|---|---|---|
| A(active 相当=分裂) | canonical-v1 matrix のコピーに `pseudo_split(cutoff)` | raw matrix(取込どおり分裂・別途 1 回構築) |
| B(candidate=正準) | canonical-v1 matrix の原本(シミュ窓の行は JRA-VAN 期=既に正準) | raw matrix のコピーに `canonicalise` |

matrix はシミュレーション用(canonical-v1)と実窓ガード用(raw)の 2 本を各 1 回構築し、各測定の両アームは
同一ビルドのコピー。不変条件(実行時 assert・contracts/adoption-gate.md **INV-A**。representation.md の
INV-R とは別系列):
- INV-A1: `race_class` 以外の全列は両アームで check_exact 一致
- INV-A2: `race_class` の差異行は規定の行(シミュ: `race_date >= cutoff ∧ 値∈{1勝,2勝,3勝}`/実窓: 値∈{１勝,２勝,３勝})に限り、差異行数 > 0
- INV-A3: paired diff が全レースで 0 ではない(097 run 1 の教訓)
- INV-A4: 両アームの `race_class` 列 hash と差異行数を verdict.json に記録
- INV-A5: アーム A は原本のコピー(`frame is not`)で `feature_cols`/`categorical_cols` が同一(drop 不使用)

## 5. verdict.json(主要キー)

```
artifact_kind: "counterfactual_spelling_simulation"
eligible_for_verdict: false            # evaluate_promotion は構造的に拒否
feature_adoption_eligible: true
evaluation_contract_version: "v4"
gate_config_hash, recipe hashes(両アーム), feature_version
simulation: {cutoffs, scored_windows, per_cutoff: [{window, n_races, n_days, point, race_class_hash_A, race_class_hash_B, n_rows_differing}], pooled: {point, ci, ci_inflated, gate, n_days, sufficient}}
guard_real_direction: {window_from, window_to, race_set_hash, point, ci_sample, three_way, evidence_of_harm}
transportability: {per_cutoff_sign_ok, loo_sign_ok, real_not_contradicting, ok}
diagnostics: {real_window_strata: {...}}        # 報告のみ・verdict に入れない
verdict: {"status", "adopt", "formula": "primary_pooled AND guard_real_direction AND transportable", "decision_reason"}   # RegimeReport 準拠(文字列禁止)・三値の優先順位は spec FR-007
```

## 6. リーク境界(不変)

`race_class` は出馬表公開時点で確定するレース属性(`PRE_ENTRY`)。変換は結果・オッズ・他馬の
情報を一切読まない純関数。as-of 集約・同日除外・自馬除外・`class_transition`(既に NFKC)は無変更。
