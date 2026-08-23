# Implementation Plan: race_class の表記統一と再学習つき採否判定(098)

**Branch**: `098-race-class-spelling` | **Date**: 2026-08-23 | **Spec**: [spec.md](spec.md)
**Input**: spec.md + codex-review.md(spec 段階・採否確定)+ research.md(D1-D12)

## Summary

供給元切替(2025-10)以降、`race_class` の netkeiba 綴り(`１勝/２勝/３勝`)が JRA-VAN 綴り
(`1勝/2勝/3勝`)と別カテゴリとして共存し、2026 年は全レースの 51% が 10 か月分しか学習データの
無い側で分岐している。固定モデルの replay は「直すと悪化」(+0.0293 CI[+0.0254,+0.0333])=綴りが
切替後レジームの旗として学習されているため、**serving 書換では直らず、再学習つき paired 評価**
でしか採否を決められない。正規化は**特徴層の表現(canonical-v1)として FEATURE_VERSION(021→023)
に束ね**、DB・取込・backfill は触らない。旧 active は compat 経路で生表現のまま serve(暗転なし)。
採否は 097 型の**擬似分裂シミュレーション**(3 カットオフ・fold 内両アーム再学習・pooled・v4
標準ゲート)+実窓方向ガード+transportability ゲートで一度だけ判定する。null-is-success 型。

## Technical Context

- **変更パッケージ**: features(新モジュール `race_class_canon.py`・registry の FEATURE_VERSION/
  `RACE_CLASS_REPRESENTATION`/compat pin)/ training(`build_training_matrix` の表現適用・
  `artifacts` の語彙 hash+marker・評価 driver)/ serving(`model_loader` の表現/語彙検査・
  `predictor` の表現適用)/ eval(**無改修**=paired_eval・v4 ゲート・bootstrap をそのまま使う)/
  scrape・db・api・front・admin(**無改修**)
- **スキーマ/migration/API/OpenAPI**: 不変。DB の `race_class` は供給元綴りのまま(provenance)
- **FEATURE_VERSION**: features-021 → **features-023**(022 は 097 の焼却番号)。列集合不変=
  `feature_hash` 不変の**値変更 bump**(017 型)。compat pin = lgbm-094-cap900 の実測 hash
  `663fe86c7564…`(compat 経路は生表現を渡すので共有列バイト同一が成立=pin 正当)
- **評価**: 既存 `paired_eval` + v4 ゲート。新しいゲート実装は書かない。097 driver を流用し、
  DB マスクを DataFrame 変換(`pseudo_split` / `canonicalise`)に置換。**DB を触らないので
  symmetry/rollback は不要**、代わりに「アーム同一性」assert(INV-A1..A5・research D5)
- **性能**: matrix 構築 2 回(シミュレーション用 canonical-v1 1 回 + 実窓ガード用 raw 1 回。各測定の
  両アームは同一ビルドのコピー)+ arm E fit 6 本(3 カットオフ×2)+ 実窓 2 本 ≈ **2 時間**
  (097 実績比・D10)。実行時間は部品積み上げでなく実績比で見る
- **netkeiba リクエスト**: 0

## Constitution Check

- **I. データ契約**: 新 ID 結合なし。`races.race_class` の既存値のみ読む → PASS
- **II. リーク防止(非交渉)**: `race_class` は PRE_ENTRY のレース属性。変換は同列の値だけを読む
  純関数(結果・オッズ・他馬・日付を読まない)。as-of 機構・同日除外・自馬除外は不変。
  `pseudo_split` は評価 driver 専用で本番経路から到達不能 → PASS
- **III. 評価先行(非交渉)**: gate-config を実行前凍結・hash fail-closed・判定式単一・事後選別禁止・
  NO_DECISION 許容・固定モデルの replay は採否に使わない → PASS
- **IV. 確率整合性**: 確率導出層に触れない → PASS
- **V. 再現性と監査**: artifact に表現 marker+語彙 hash、verdict.json に両アームの `race_class`
  列 hash・差異行数・recipe hash・gate hash、serving summary に語彙外値の監査 → PASS
- **VI. feature 分割規律**: 単一 spec・REJECT 時の revert 手順を事前定義・昇格は別段 → PASS
- **Codex ゲート**: spec 段階で 4 問(採用 5/不採用 2・codex-review.md)+ plan 段階で 5 問
  (採用 3/部分採用 1/不採用 1・research D12)。最大の指摘=表現 dispatch の可変既定値依存を
  明示引数+allowlist+golden fixture で排除 → PASS

## Project Structure

### Documentation (this feature)

    specs/098-race-class-spelling/
    ├── spec.md / plan.md / research.md / codex-review.md / codex-review-raw.md
    ├── data-model.md
    ├── contracts/
    │   ├── representation.md       (INV-R1..R7: 表現の版バインディング・語彙 hash・監査・NaN 不増)
    │   └── adoption-gate.md        (シミュレーション設計の凍結 + 判定式 + アーム同一性の契約 INV-A1..A5)
    ├── gate-config.json            (draft。実行前に凍結し gate-config.hash.txt を記録)
    └── quickstart.md

### Source Code

    features/src/horseracing_features/
    ├── race_class_canon.py             # 新規: canonicalise / pseudo_split / audit(純関数)
    ├── registry.py                     # FEATURE_VERSION=features-023・RACE_CLASS_REPRESENTATION・compat pin
    └── static_features.py              # 不変(生値を出す。変換はビルド後処理)
    features/tests/unit/test_race_class_canon.py        # 写像・表外不変・冪等・pseudo_split・audit
    features/tests/unit/test_registry_features023.py    # 版・表現定数・compat pin・022 不使用
    training/src/horseracing_training/
    ├── dataset.py                      # build_training_matrix(representation=必須引数)で canonicalise 適用
    ├── artifacts.py                    # metadata: race_class_representation・categorical_vocab(+hash)
    └── spelling_split.py               # 新規: アームのコピー生成+同一性 assert(driver とテストが import)
    training/tests/unit/test_spelling_split.py          # アーム同一性 assert の kill-test(差が無い/余計な差)
    serving/src/horseracing_serving/
    ├── model_loader.py                 # exact=marker どおり/compat=raw 強制・語彙 hash 再導出・分裂語彙拒否
    └── predictor.py                    # 行に表現を適用(純関数)+語彙外値の監査
    serving/tests/unit/test_model_loader_representation.py   # golden fixture 3 種(raw-021 / canonical-023 / 拒否)
    serving/tests/unit/test_predictor_representation.py      # 表現適用・語彙外注入・INV-R7・;rcr= マーカー
    scripts/098_spelling_split_gate.py  # driver(097 を流用・DB マスク→DataFrame 変換)
    scripts/parity_098.py               # raw@023 == 021 ビルド(全列 check_exact)の証跡

## 実装フェーズ

### Phase A: 表現と版(US2 の土台・US1 が使う変換)

1. `race_class_canon.py`: 3 対応の明示表・表外不変・冪等・`pseudo_split(cutoff)`・audit。
   単体テストは手計算 fixture(`１勝`→`1勝`、`オープン`/`重賞`/NULL 不変、冪等、逆写像の行限定)
2. registry: `FEATURE_VERSION="features-023"`・`RACE_CLASS_REPRESENTATION="canonical-v1"`・
   compat pin(021→`663fe86c…`)。022 を使わない理由をコメントに残す(焼却)
3. `build_training_matrix(representation=...)`: **必須引数**(既定値なし・codex plan Q5)。training CLI は
   registry 定数を明示して渡し、parity/driver は `"raw"`/`"canonical-v1"` を明示。`LightGBMPredictor` の
   `race_class_representation=None` は registry 定数へ解決する**唯一の許容点**(既存 40 箇所の constructor を
   壊さないため・INV-R2 に明記)で、解決値を `fit_info_` に記録し、driver/parity は None を渡さない(assert)。
   ビルド後に `canonicalise` を適用し audit を summary に載せる。カテゴリ化前後で NaN 数不変を assert(INV-R7)
4. `scripts/parity_098.py`: raw@023 == 021 ビルドの全列 check_exact(INV-R6)。正準では
   `race_class` のみ差異・差異行は 3 トークンの行に限る(SC-002/003)

**中断点**: parity が破れたら即中断(変換が他列に触れた=設計違反)。

### Phase B: serving の適合判定(US2・SC-004)

5. `artifacts.save_model_version`: booster の `pandas_categorical` → `categorical_vocab` +
   `categorical_vocab_hash`・`race_class_representation` を metadata と `metrics_summary.training` に
6. `model_loader`: (trained_fv, current_fv, representation) の allowlist(021→023: raw / 023→023:
   canonical-v1)以外は fail-closed、marker 欠落も fail-closed、語彙 hash 再導出一致、`canonical-v1` なら
   分裂トークン不在 assert。golden fixture(raw-021 / canonical-023 / 拒否組合せ)で固定(codex plan Q1)
7. `predictor.predict_race`: 行に表現を適用(同じ純関数・明示引数)+語彙外値の監査を**カテゴリ化の前**に
   取り、未知カテゴリを含む行でも予測が返る注入テストを置く(INV-R4・codex plan Q4)。戻り値は
   `(predictions, snapshots, explanations, audit)` の 4-tuple(089 の 3-tuple 化と同型・pipeline の 2 呼出と
   serving テストの unpack を更新)
8. 実 DB E2E: 旧 active(lgbm-094・021)が compat で読め予測バイト一致(SC-004)

### Phase C: 判定(US1)

9. gate-config.json を凍結(hash 記録・コミット)してから driver を書く。driver は 097 を流用:
   matrix を 1 回構築 → 各カットオフで A=`pseudo_split` コピー / B=原本 → アーム同一性 assert
   (race_class 以外 check_exact・差異行の限定・列 hash 記録・diff 非ゼロ)→ fold 内両アーム再学習
   → paired → pooled CI(seed inflate)→ 実窓ガード(raw 表現で別途 1 回構築・A=生 / B=`canonicalise`
   コピー)→ transportability(per-cutoff・LOO・実窓 CI 非矛盾)→ 実窓の層別診断(報告のみ)→ verdict JSON
   (三値の優先順位=実行不能→NO_DECISION / primary∧guard 不成立→REJECT / transportable 不成立→NO_DECISION / 全成立→ADOPT)
   (`counterfactual_spelling_simulation`・`eligible_for_verdict=False`)。出力規律: 全成分が揃うまで
   効果数値を出さない
10. smoke(事前登録外カットオフ 2016・redact)で配線と assert を検証してから本番 ≈2h
11. `promote-model --model-version lgbm-094-cap900 --verdict ...`(`--apply` 無し=dry-run)で `verdict_artifact_not_eligible` を確認

### Phase D: 後始末(US2 の verdict 分岐・US3)

12. REJECT/NO_DECISION → bump・compat pin・marker/語彙検査の結線を revert、モジュール+テスト+
    driver 非結線保全、active 予測バイト一致再確認、結果を spec へ転記
13. ADOPT → features-023 確定・正準データで候補を学習/登録・昇格は 3 点セットで別途(暗転なし)
14. US3: research D9 の賞金規則で `evidence-listed.md`(49/74/16)を記録。`オープン` の変換は別 feature

## 検証ゲート(要約)

| ゲート | 条件 | 破れたら |
|---|---|---|
| parity(raw@023 == 021) | 全列 check_exact | 即中断 |
| 正準の差異限定 | `race_class` のみ・3 トークン行のみ | 即中断 |
| serving 適合 | compat=raw・exact=marker・語彙 hash 一致・分裂語彙拒否 | fail-closed |
| アーム同一性 | race_class 以外一致・差異行限定・diff 非ゼロ | 即 fail |
| primary | pooled: point<−0.002 ∧ 総 CI 上限<0 | REJECT |
| guard_real_direction(実窓) | evidence-of-harm なし(標本 CI `ci_low ≤ +0.005`・raw ビルドの A/B) | REJECT |
| transportability | per-cutoff/LOO 同符号・実窓 CI 非矛盾(primary∧guard 成立時のみ verdict に効く) | NO_DECISION |
| 充足 | pooled 開催日数 ≥ 300 | NO_DECISION |

## Complexity Tracking

逸脱なし。新しいゲート実装・スキーマ変更・新規取得・DB 書換はいずれもゼロ。097 との差分は
「DB マスク → DataFrame 変換」と「表現の版バインディング+語彙 hash」の 2 点で、いずれも
既存機構(compat pin・preprocessor 検査・paired_eval)の上に乗る。
