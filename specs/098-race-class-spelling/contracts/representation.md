# Contract: race_class の表現(representation)と artifact バインディング

## INV-R1 表現は版に束ねる

- features-023 以降の registry は `RACE_CLASS_REPRESENTATION = "canonical-v1"` を宣言し、
  `build_training_matrix` はビルド後に `canonicalise` を `race_class` に適用する。
- features-021 以前の表現は `"raw"`(変換なし)。
- 変換は純関数(`race_class_canon.canonicalise`)で、入力は `race_class` 列の値のみ。結果・オッズ・
  他馬・日付を読まない(憲法 II)。

## INV-R2 serving は artifact の marker に従う(exact/compat で分岐)

| 経路 | 条件 | 適用する表現 |
|---|---|---|
| exact | `feature_hash` 一致 ∧ `feature_version == FEATURE_VERSION` | artifact の `race_class_representation`(023 では必ず `canonical-v1`。欠落/不一致は fail-closed) |
| compat | `COMPATIBLE_PRIOR_FEATURE_VERSIONS[current][trained] == trained_hash` | **`raw` 強制**(旧ビルドと共有列バイト同一=compat の前提) |
| それ以外 | — | fail-closed(従来どおり) |

- **dispatch は artifact 主導・明示引数**(codex plan Q1/Q5): `build_training_matrix(representation=...)`・
  driver・`predict_race` はいずれも表現を**必須引数**で受け取り、registry の既定値を暗黙に参照しない。
  registry 定数は「この版で学習するときに渡す値」であって「読み込む側が推測する値」ではない。
  許される (trained_fv, current_fv, representation) の組は allowlist(021→023: raw / 023→023: canonical-v1)
  に限り、それ以外(features-023 以降の artifact での marker 欠落・未知値・未宣言ペア)はすべて fail-closed。
  features-021 以前の artifact は marker を持たないのが正常で、compat 経路で `raw` を強制する(欠落を
  fail-closed にするのは 023 以降のみ)。golden fixture(raw-021 / canonical-023 / 拒否組合せ)で固定する。
- **唯一の解決点**: training の `LightGBMPredictor(race_class_representation=None)` だけは registry 定数
  `RACE_CLASS_REPRESENTATION` へ解決してよい(既存の constructor 呼出 40 箇所を壊さないため)。解決値は
  `REPRESENTATIONS` に含まれることを assert し `fit_info_` に記録する。training CLI は registry 定数を
  明示して渡し、評価 driver と parity script は None を渡さない(driver は `is not None` を assert)。
  `build_training_matrix` と serving `predict_race` には既定値を置かない。
- compat で `raw` を強制するのは、compat の正当性が「共有列のバイト同一」に依存するため。
  artifact が `canonical-v1` を名乗りつつ compat 経路に来ることは無い(版が一致すれば exact)。
- 将来 features-024 が別の値変更を入れた場合、023 artifact を compat で serve できるかは
  **その時点で共有列バイト同一を再証明**しない限り pin しない(registry 注記の規律を継承)。

## INV-R3 語彙バインディング

- `save_model_version` は booster の `pandas_categorical` を順序つきで読み、
  `categorical_vocab`(原本)と `categorical_vocab_hash`(sha256)を metadata に書く。
- `load_serving_model` は読み込んだ booster から同じ手順で hash を再導出し、metadata と一致
  しなければ fail-closed。
- marker が `canonical-v1` の artifact は、`race_class` の語彙に `１勝/２勝/３勝` を**含まない**
  ことを assert(正準データで学習した証拠)。含む場合は「正準を名乗る分裂モデル」なので拒否。
- marker `raw` の artifact(旧版)はこの assert の対象外。
- 語彙 hash を持たない artifact(features-021 以前・`categorical_vocab_hash` 未記録)は、compat 経路かつ
  `raw` の場合に限り許容する。features-023 以降で hash 欠落は fail-closed。

## INV-R4 語彙外値の監査(拒否しない・**カテゴリ変換の前**に取る)

- serving は予測行の `race_class`(表現適用後・`astype("category")` の**前**)について、artifact
  語彙に無い値の mask を per-prediction で作り、件数・値・表現 marker・版を serving summary /
  logic_version 付随情報に出す。拒否はしない(正当な新カテゴリで serving を止めない)。
- **既知トークンの取りこぼし**(語彙にある値が正規化/型ドリフトで未知扱いになる)は事故なので、
  既知集合に対する未知率が閾値(事前登録・例 1%)を超えたら警告を出す。
- 注入テスト: 未知カテゴリを含む行でも予測が返り、監査が発火することを固定する(codex plan Q4)。
  LightGBM は未知値を missing 分岐へ黙って落とすため、この監査が唯一の可視化。

## INV-R5 ビルド監査

- `canonicalise` の audit(写像件数・表外の値と件数)を training のビルド summary(`TrainingMatrix.build_audit`
  → `fit_info_`/metrics_summary)に記録する(静的列は materialize されないので parquet 側には出ない)。表外に**新しい綴り**(例: 将来 `４勝` や `1勝クラス`)が現れたら監査で気づける。

## INV-R6 バイト同一の保証

- `build_training_matrix(representation="raw")` @ features-023 == features-021 ビルド(全列
  check_exact・check_dtype)を一度きり実測し、compat pin の根拠として記録する。
- `canonicalise` は `１勝/２勝/３勝` 以外の行を一切変えない(単体テスト: 表外値・NULL・既正準値の
  不変、冪等 `canonicalise(canonicalise(x)) == canonicalise(x)`)。

## INV-R7 カテゴリ化で NaN を生まない

- training/serving とも、`astype("category")` の前後で `race_class` の NaN 数が増えないことを
  assert する(pandas の category 変換は語彙外を NaN にする経路があり、表現の取り違えが
  黙って欠損に化ける。codex plan Q5)。増えたら fail-closed。
