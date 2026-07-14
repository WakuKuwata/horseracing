# Quickstart: F02 + subgroup ゲート拡張

**Feature**: 069 | **Date**: 2026-07-13

end-to-end 検証手順。詳細は [data-model.md](data-model.md) / [contracts/cli.md](contracts/cli.md) / [gate-config.json](gate-config.json) 参照。

## 前提

- ローカル Postgres([local-db-setup]: `docker-postgres-1`、port 15432、DB `horseracing`、`DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`)。
- 現 active = lgbm-063(features-017)。オッズ coverage 99.6%。067 repair 実質適用済み。

## SC-002: features-018 の byte-parity + lgbm-063 compat

```
# F02 込み features-018 を build し、共有128列が features-017 と byte 一致することを確認
uv run --project features pytest features/tests -k "past_odds_additive or shared_column_parity"
# lgbm-063(features-017)が features-018 registry 下で compat-load・予測 byte 一致
uv run --project serving pytest serving/tests -k "compat_load or feature_hash_pin"
```

**期待**: 共有128列 check_exact/check_dtype 一致・lgbm-063 が compat pin(`300b28a9…`)で serve・予測 byte 完全一致。

## SC-003: F02 leak-guard + materialize parity

```
uv run --project features pytest features/tests -k "pm_core_strength and (leak or parity or missing)"
```

**期待**: 今走・同日・未来のオッズ変更で F02 不変、過去オッズ変更で変化。materialized/in-memory bit-parity 一致。0観測で NaN + has_obs=0。

## SC-001 + SC-004: subgroup ゲート + F02 採否

```
# F02 は 068 paired-eval 経路で採否(candidate=features-018全群 vs active=F02群drop、両者accuracy-first)
uv run --project training training paired-eval \
  --candidate <features-018 full recipe> --active <features-018 minus-F02 recipe> \
  --subgroups --gate-config specs/069-past-odds-features/gate-config.json \
  --from 2019-01-01 --to 2026-07-12 --num-threads 1 --json /tmp/f02_eval.json
  # --from/--to は gate-config.eval_window に事前登録した凍結窓と一致させる(III, analyze C1)
```

**期待**: winner NLL 差 + race-level(2026_only/2026_field_has_nk)/ horse-level(canonical/nk/2026_nk/coverage帯)ごとの三値ガード付き CI + top2/top3 + 校正。critical(2026/nk/2026_nk)intersection-union で採否。少標本 subgroup は NO_DECISION。

## SC-005: coverage 監査

```
uv run --project training training coverage-audit \
  --from 2024-01-01 --to 2026-07-12 --json /tmp/pm_coverage.json
```

**期待**: 年×ID source(canonical/nk:)×coverage 帯の 1/3/5走 coverage、overround 分布・境界値率・popularity と q-rank 不一致率。2026 nk: 馬の過去市場 coverage が数値で出る。

## SC-006: 契約不変

```
git diff --stat HEAD -- '*.sql' 'db/migrations/**' 'api/**' 'front/openapi.json' && echo "no schema/api change"
# default 意思決定支援モデルに F02 が入らない
uv run --project training pytest training/tests -k "default_model_drops_market_history"
```

**期待**: スキーマ/API/OpenAPI/migration 差分なし。default モデルは market-history 群を drop(p⊥q)。

## テストの要点(合成データ)

- q/s: 共通オッズ倍率不変・Σq=1・一様で s=0・単調(支持↑で s↑)。
- 1頭でもオッズ無効なら race 全体の q を作らない(complete-field)。
- recent-K=有効観測・trend(直近3単回帰)・sd5(ddof=1)の手計算 golden・境界(2観測未満 NaN)。
- 行順・race 内馬順を変えても F02 完全一致(決定論)。
- subgroup 割当が結果ラベル変更で不変(属性のみ)。
- subgroup CI が seed 決定論・少 subgroup で NO_DECISION。
