# CLI Contract: F02 + subgroup ゲート拡張

**Feature**: 069 | **Date**: 2026-07-13

既存 CLI の拡張のみ。API・OpenAPI・DB 不変。

## `training paired-eval`（068 拡張）

068 の paired-eval に **subgroup 出力 + subgroup ガード**を加算(後方互換)。

```
training paired-eval \
  --candidate <recipe|model_version> --active <recipe|model_version|"db-active"> \
  [--subgroups]                 # 追加: race-level(2026_only/2026_field_has_nk)winner NLL 差 +
                                #        horse-level(canonical/nk/2026_nk/coverage帯)started-all 差の CI
  [--gate-config <path>]        # subgroup 閾値(margin ε・critical 集合)を含む事前登録
  ... (068 の既存フラグは不変)
```

**契約**:
- `--subgroups` 指定時、PairedReport に `race_subgroups` / `horse_subgroups` / `subgroup_guard`(data-model §4)を加算。既存の primary/stat/recent/top/calibration ガードは不変(FR-005)。
- subgroup 割当は eval 内で注入属性(race_date.year・horse_id `nk:` prefix・フィールドの nk: 有無・厳密前観測数)のみ(結果非参照、training 非依存)。
- **critical subgroup(`2026_only`・`nk`・`2026_nk`)を intersection-union(全 PASS)で守る。三値判定**: PASS=CI 上限<margin ε / FAIL=CI 下限>ε / NO_DECISION=跨ぐ(非否決だが十分条件でもない)。adopted は critical 全 PASS 必須(FR-002/003, codex C2/C3)。
- 未指定時は 068 と byte 同等(後方互換)。

## F02 採否は `paired-eval` に一本化（feature-eval は使わない、codex C5）

現行 `training feature-eval` は旧 020 binary LogLoss/ECE gate で winner NLL/bootstrap ではない。F02 は **068 `paired-eval` 経路**で評価する。

```
training paired-eval \
  --candidate <features-018 全群 recipe>  \
  --active    <features-018 minus-F02 recipe>  \  # baseline = F02 群のみ drop(058 rank は残す)
  --subgroups --gate-config specs/069-past-odds-features/gate-config.json \
  --from 2019-01-01 --to 2026-07-12 --num-threads 1 --json <out>
```

**契約**:
- 両 arm とも accuracy-first(pl_topk + TE、features-018)。candidate は F02 込み、active は `drop_features` で `pm_core_strength` 群のみ drop(058 rank は両者に残す=帰属分離、FR-008/FR-013)。
- default 意思決定支援モデルは対象外(market-history 群を全 drop、F02 を入れない、FR-012)。
- 採否 = winner NLL 改善 + **subgroup 三値ガード(critical `2026_only`/`nk`/`2026_nk` が intersection-union で全 PASS)** + top2/top3 non-inferiority + 校正非劣化(FR-013)。OOS 後に列選別しない。

## `features materialize` / coverage 監査

```
features materialize             # F02 込み features-018 を build(025 同型・bit-parity)
training coverage-audit --from --to [--json]   # 年×source×coverage帯の 1/3/5走 coverage + provenance 品質(SC-005)
```

**契約**:
- materialize は F02 を build_asof の single as-of 源に結線(025 同型・source_fingerprint 拡張・stale fail-closed)。
- coverage-audit は read-only、特徴に流入しない(監査のみ、D7)。

## 非対象（明示）

- API・OpenAPI・front・admin・DB migration: 変更なし。
- default 意思決定支援モデルへの F02 組込: 対象外(p⊥q・provenance 前提の別判断)。
- provenance 列(source/observed_at/finality)追加: 別 spec。
