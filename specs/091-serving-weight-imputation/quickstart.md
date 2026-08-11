# Quickstart: 馬体重欠損時の serving 入力是正

**Feature**: 091 | **Date**: 2026-08-10

実装が正しく動いていることを端から端まで確認する手順。数値の期待値は本 feature の測定で確定するため、ここでは**構造的に必ず成り立つべきこと**を検証する。

## 前提

```bash
scripts/stack.sh postgres
```

`DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`

## 0. 現状の再確認(実装前のベースライン)

kill-test を再実行し、spec 背景の数値が再現することを確かめる。

```bash
uv run --project serving python specs/091-serving-weight-imputation/evidence/build_cache.py
```

```bash
uv run --project serving python specs/091-serving-weight-imputation/evidence/killtest2.py
```

期待: `C−B` が −0.0123 前後、CI がゼロを跨がない。パリティ確認は `parity2.py`(全アーム PASS)。

## 1. 特徴列の検証(Phase A)

```bash
uv run --project features pytest features/tests/unit/test_weight_history_features.py -q
```

確認すること:

- 手計算 fixture で `prev_weight` が期待値になる
- 同日の走が供給元にならない(INV-W1)
- 取消行・体重 NULL 行・範囲外行が供給元にならない(INV-W2)
- 同一馬同日に複数候補があれば NaN
- 供給元が無ければ NaN(0 埋めしない)

```bash
uv run --project features pytest features/tests/unit/test_weight_leak.py -q
```

確認すること: 結果・当日オッズ・同日他レースを変えても `prev_weight` が変化しない(INV-W3)。

## 2. パリティと fingerprint(Phase A)

```bash
uv run --project features pytest features/tests/integration/test_features020_parity.py -q
```

確認すること(**FEATURE_VERSION を上げる前に**、列を足しただけの build を T001 の基準と比べる):

- 既存 137 列が T001 で採取した `features-018` 基準とバイト一致(`check_exact=True, check_dtype=True`)— INV-W7
- `source_fingerprint` が変化しない — INV-W8
- 過去出走のある行で `prev_weight` が非欠損 — INV-W4(**破れたら D1 の列縮小の前提が崩れた合図**)

## 3. mask 純関数(Phase B)

```bash
uv run --project features pytest features/tests/unit/test_weight_mask.py -q
```

確認すること:

- `spec=None` が入力とバイト同一を返す(INV-W5)
- mask がレース単位。1 レース内で mask 状態が混在しない(INV-W6)
- 同一 `(race_id, seed, rate)` で 2 回実行して同一結果
- `prev_weight` は決して mask されない
- 対象列が入力に無ければ fail-closed(黙って何もしない挙動を禁止)

## 4. 再 materialize

現在の parquet は既に stale(fingerprint 不一致で fail-closed 状態)なので 1 回作り直す。

```bash
uv run --project features features materialize
```

## 5. serving 互換の実 DB E2E(Phase D0)

`features-020` の下で現行 active の予測がバイト不変であることを確認する(INV-W9)。

```bash
uv run --project serving serving predict --race-id 202504040301 --model-version lgbm-064-f02acc
```

確認すること: 全頭の勝率が bump 前の値と**1 ビットも違わない**。compat 経路を通ったことが `logic_version` から読めること。

## 6. outcome-blind 受入(Phase D0・本評価に進む前の中断点)

直近 3 fold を回し、**効果を見ない項目だけ**を確認する。

```bash
uv run --project training training paired-eval --candidate <candidate-recipe> --active lgbm-064-f02acc --weight-regime both --subgroups --acceptance-recent-folds 3
```

確認すること(すべて効果非依存):

- 両アームの mask 適用レース集合と件数が一致している
- `prev_weight` のカバレッジが想定どおり
- 指標がすべて有限(NaN / inf が出ていない)
- artifact と gate-config の provenance が一致している

**winner NLL の大小で継続可否を判断してはならない。** ここで使う直近 fold は最終評価窓の内側にあるため、効果を見て継続判断すると選択リークになる。artifact には `artifact_kind="acceptance"` / `eligible_for_verdict=false` が刻まれ、verdict loader が機械的に弾く。

## 7. 本評価(Phase D)

gate-config の `eval_window.to` を実行時点の最新確定レース日に確定し(既定値は暫定)、**そのうえで** canonical hash を計算して凍結してから confirmatory で回す。

```bash
uv run --project training training paired-eval --candidate <candidate-recipe> --active lgbm-064-f02acc --confirmatory --gate-config specs/091-serving-weight-imputation/gate-config.json --gate-config-hash <frozen-hash> --from 2021-01-01 --to <凍結時に確定した終端> --weight-regime both --subgroups
```

確認すること:

- `serving_regime` と `full_info_regime` の両方が出る
- 校正前 winner NLL が診断として出る
- `verdict` が `serving_regime.gate.adopted AND full_info_guard AND **serving_regime**.subgroups.subgroup_guard` から一意に決まり、レポートが**単一真偽値 `verdict.adopt`** を出力している(読み手が 3 つのパスを自分で AND しない)
- gate-config を 1 文字でも変えると `assert_confirmatory` が落ちる(凍結が効いている)

## 8. registry 是正の値不変確認(Phase E・採否と独立)

```bash
uv run --project features pytest features/tests/ -q -k timing
```

確認すること: `carried_weight_ratio` の宣言を `post_weight` に直しても、特徴量の値・列名・列順・`feature_hash` が変わらない(INV-W10)。registry 全体の監査で、宣言より遅い入力に依存する列が他に無いこと。

## 9. 全体スイート

```bash
uv run --project features pytest features/tests -q
```

```bash
uv run --project training pytest training/tests -q
```

```bash
uv run --project eval pytest eval/tests -q
```

```bash
uv run --project serving pytest serving/tests -q
```

`ruff` もクリーンであること。**スキーマ・migration・API・OpenAPI に差分が無いこと**を `git diff --stat` で確認する(SC-007)。
