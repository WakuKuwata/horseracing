# Quickstart: race_class 表記統一(098)の検証手順

前提: ローカルスタック(`scripts/stack.sh status`)・`DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`。
netkeiba への新規リクエストは **0**(全工程 DB 読み取りのみ)。

## 0. 現状の確認(実装前の事実)

```bash
uv run --project training python -c "
from sqlalchemy import create_engine,text;import pandas as pd
e=create_engine('postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing')
with e.connect() as c: print(pd.read_sql(text(\"select race_class, count(*) n, min(race_date) d0 from races where race_class in ('1勝','１勝','2勝','２勝','3勝','３勝','オープン','ｵｰﾌﾟﾝ','OP(L)','重賞') group by 1 order by 1\"),c).to_string())"
```
期待: `１勝/２勝/３勝` は 2025-10-11 以降のみ、`1勝/2勝/3勝` は 2025-10-05 以前のみ。

## 1. 変換の単体検証(US2・INV-R6)

```bash
cd features && uv run pytest tests/unit/test_race_class_canon.py -q
cd training && uv run pytest tests/unit/test_spelling_split.py -q   # アーム同一性 assert の kill-test(INV-A1..A5)
```
期待: 3 対応の写像・表外(`オープン`/`重賞`/NULL)不変・冪等・`pseudo_split` がカットオフ以降の 3 トークンだけを逆写像・audit の件数。`assert_arm_identity` は差が無い/余計な差/許容外の行で fail。

## 2. 表現の版バインディングとバイト同一(SC-002/SC-003・INV-R6)

```bash
cd features && uv run pytest tests/unit/test_registry_features023.py -q
cd training && uv run python ../scripts/parity_098.py   # raw 表現 @023 == features-021 ビルド(全列 check_exact)
```
期待: features-023・`RACE_CLASS_REPRESENTATION="canonical-v1"`・compat pin(021→663fe86c…)。parity は全列一致、正準表現では `race_class` のみ差異で差異行は `１勝/２勝/３勝` の行に限る。

```bash
cd features && uv run python -m horseracing_features materialize   # T017: manifest の source_fingerprint が 021 と一致・feature_version=features-023
```
期待: 静的列は非 materialize なので parquet に `race_class` の表現 audit は出ない(audit は training ビルド summary=INV-R5)。

## 3. serving の適合判定(SC-004・INV-R2/R3)

```bash
cd serving && uv run pytest tests/unit/test_model_loader_representation.py tests/unit/test_predictor_representation.py -q
```
期待: compat(021)artifact は `raw`・exact(023)は `canonical-v1`・marker 欠落(023)は fail-closed・語彙 hash 不一致は fail-closed・`canonical-v1` を名乗る分裂語彙は拒否。predictor 側は語彙外値 `４勝` を注入しても予測が返り `audit.n_unknown==1`(INV-R4)・NaN 増加は `ServingError`(INV-R7)。

実 DB E2E(旧 active の予測バイト一致):
```bash
cd serving && DATABASE_URL=... uv run python -m horseracing_serving predict --race-id <確定済み race_id>
```
期待: 結線前の予測(永続化済み)と win_prob が全頭バイト一致・logic_version に `;reg=features-021;…;rcr=raw`・監査 `n_unknown==0`。

## 4. ゲートの凍結と smoke(FR-005・契約)

```bash
cd eval && uv run python -c "from horseracing_eval.decision import gate_config_hash; import json; print(gate_config_hash(json.load(open('../specs/098-race-class-spelling/gate-config.json'))))" > ../specs/098-race-class-spelling/gate-config.hash.txt   # 正本は harness の canonical hash(`_` キー除外)。ファイルの sha256 ではない(097 T001 と同じ)
cd training && uv run python ../scripts/098_spelling_split_gate.py --gate-config ../specs/098-race-class-spelling/gate-config.json --gate-config-hash $(cat ../specs/098-race-class-spelling/gate-config.hash.txt) --smoke --json ../out/098-smoke.json
```
期待: 配線・アーム同一性 assert(race_class 以外一致・差異行が規定の行のみ・diff 非ゼロ)・JSON 形のみ検証、効果数値は redact、kind="smoke"。

## 5. 本番判定(US1・約 2 時間)

```bash
cd training && nohup uv run python ../scripts/098_spelling_split_gate.py --gate-config ../specs/098-race-class-spelling/gate-config.json --gate-config-hash $(cat ../specs/098-race-class-spelling/gate-config.hash.txt) --json ../specs/098-race-class-spelling/verdict.json > ../logs/098-gate.log 2>&1 &
```
期待: `verdict.json` に pooled/guard/transportability の全成分と `verdict.status ∈ {ADOPT, REJECT, NO_DECISION}`、`artifact_kind="counterfactual_spelling_simulation"`、`eligible_for_verdict=false`。
昇格拒否の確認(`--apply` を付けない=dry-run。`--dry-run` というフラグは無い・`--model-version` は必須): `uv run python -m horseracing_training promote-model --model-version lgbm-094-cap900 --verdict ../specs/098-race-class-spelling/verdict.json` → `verdict_artifact_not_eligible`。

## 6. verdict 後(US2/FR-008/009)

- REJECT / NO_DECISION: bump・compat pin・marker 配線・serving 検査の結線を revert(モジュール+テスト+driver は保全)→ 手順 3 の E2E で active 予測バイト一致を再確認 → 結果を spec に転記。
- ADOPT: features-023 を確定 → 正準データで候補を学習・登録(`register-arm-e …`・tasks T029 の引数)→ **FR-014 の 1 件確認**: 候補の `metadata.json` に `race_class_representation="canonical-v1"`・`categorical_vocab["race_class"]` に `１勝/２勝/３勝` 無し、`load_serving_model` で語彙 hash の再導出一致(exact 経路)、同じレースで旧 active の予測が手順 3 とバイト一致 → 昇格は 3 点セットで別途。旧 active は compat(raw)で serve 継続(暗転なし)。

## 7. US3 リステッド調査(読み取りのみ・SC-007)

research D9 の賞金規則で切替後 `オープン` を分類し、リステッド相当 49 / 非リステッド 62 / 曖昧 16(障害 37 を除く
平地 127・2026-08-23 再実測。DB は動くので T026 が再実行して固定)を `evidence-listed.md` に記録。`オープン` の変換は別 feature で事前登録。
