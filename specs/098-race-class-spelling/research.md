# Research: race_class の表記統一(098)

事実はすべて実測(2026-08-22/23 の DB クエリ・`scripts/killtest_class_spelling.py`・codex-review.md)。

## D1: 正準綴り = JRA-VAN 綴り・変換表は `１勝/２勝/３勝` の 3 対応のみ

- **Decision**: `{"１勝": "1勝", "２勝": "2勝", "３勝": "3勝"}`。照合は NFKC キーではなく**明示表**
  (表に無い値は不変)。`オープン`(164 行)・`重賞`(12 行)は据え置き。
- **Rationale**: 15 年分の学習カテゴリ(`1勝` 5,936 レース等)と 4472796 の重賞修正(`Ｇ１` 全角)に
  整合。NFKC を正準にすると `Ｇ１→G1`・`ｵｰﾌﾟﾝ→オープン`(2,365 行)まで書き換わる。`オープン` は
  JRA-VAN 期の `ｵｰﾌﾟﾝ`(非リステッド)+`OP(L)`(リステッド)の混合なので `ｵｰﾌﾟﾝ` に寄せるのは意味変更
  (codex Q3)。再学習する以上、綴りの選択自体はモデルには無差別で、既存 artifact 互換だけが論点。
- **Alternatives**: NFKC 正準(却下・上記)/ `オープン→ｵｰﾌﾟﾝ` も含める(却下・混合)/ `重賞` を
  `Ｇ３` 等へ推定(却下・別名ではない)。

## D2: 変換は特徴層の「表現」として版に束ね、DB・取込は触らない

- **Decision**: 純関数モジュール `features/src/horseracing_features/race_class_canon.py`:
  `canonicalise(series) -> (series, audit)`(表で写像・表外値の値と件数を audit に返す)と
  `pseudo_split(series, race_dates, cutoff)`(逆写像を `race_date >= cutoff` の行にだけ適用・
  シミュレーション専用)。適用点は**ビルド後の後処理**: training は `build_training_matrix(representation=...)` に
  **明示引数**で渡された表現(training CLI は registry 定数 `RACE_CLASS_REPRESENTATION = "canonical-v1"`
  @ features-023 を明示して渡す・D12 Q5)を適用、serving は artifact の marker に従って `predict_race`
  の行に適用(同じ純関数・同じ生値 → バイト同一)。暗黙の既定値は持たない。
  `static_features.py`・`materialize`(静的列は非 materialize)・DB・取込は不変。
- **Rationale**: replay +0.029 は「モデルから見える意味変更」なので in-place の DB 書換は
  (i) 稼働モデルを即座に悪化させ (ii) ロールバックが逆 backfill になる(codex Q2)。特徴層なら
  旧 artifact は生表現・新 artifact は正準表現を同じ DB から得られ、切替/切戻しはモデルの昇格のみ。
  DB は供給元の綴りを provenance として保持し、出馬表再取得で値が「戻る」事故(重賞修正で発生)も
  構造的に起きない。
- **Alternatives**: 取込正規化+backfill(当初案・却下)/ `static_features` 内で常時正規化(却下・
  旧 artifact が生表現を受け取れず 017 型の暗転窓が生まれる)。

## D3: FEATURE_VERSION = features-021 → **features-023**(値変更 bump・017 型)+ compat pin

- **Decision**: 列集合は不変だが値が変わるので bump。**features-022 は 097 が使用後 REJECT で
  revert した焼却番号**(「1 つのラベルが 2 つの列集合を指さない」規律)→ 023。
  `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-023"] = {"features-021": "663fe86c7564…"}`
  (lgbm-094-cap900 の実測 `metadata.feature_hash`・本セッションで照合済み)。
- **Rationale**: 017 は値変更 bump で compat を**空**にした(全旧モデル fail-closed=昇格まで serving
  暗転)。本 feature は compat 経路(`exact=False`)が**生表現**を渡すので、旧版との共有列は
  バイト同一という compat の前提(registry 注記 (c))が**成立する**。したがって pin は正当。
  exact 経路(version 一致)だけが正準表現を受け取る。
- **検証**: `build_training_matrix(representation="raw")` == features-021 ビルド(全列 check_exact)
  を一度きり実測(058/061/091 同型)。

## D4: 語彙バインディング(feature_hash は列名しか見ない穴を塞ぐ)

- **Decision**: artifact metadata に `race_class_representation`("raw" | "canonical-v1")と
  `categorical_vocab_hash`(booster の `pandas_categorical` を順序つきで JSON 化 → sha256)を記録。
  serving は読込時に booster から再導出して metadata と一致しなければ fail-closed、marker が
  `canonical-v1` なら語彙に分裂トークン(`１勝/２勝/３勝`)が**含まれない**ことを assert(構造的
  自己検査)。marker 欠落(旧 artifact)は "raw" と解釈し、かつ features-021 以前の版に限って許す
  (023 以降で marker 欠落は fail-closed)。
- **Rationale**: LightGBM のカテゴリコードは artifact ローカルで、未知値は missing 分岐へ**黙って**
  落ちる(codex Q3)。語彙 hash は「この artifact がどの語彙で学習したか」を束ね、表現 marker は
  「どの変換を適用すべきか」を束ねる。両方揃って初めて serving の適合判定になる。
- **監査(serving 時)**: 予測行の `race_class` 値のうち artifact 語彙に無い値の件数と値を
  serving summary に出す(拒否はしない=新しい正当なカテゴリで serving を止めない)。

## D5: primary = 擬似分裂シミュレーション(097 の driver を流用・DB マスク不要)

- **Decision**: カットオフ 3 本 `2019-01-01 / 2021-01-01 / 2023-01-01`、採点窓 `2020 / 2022 / 2024`
  (washout 1 年・互いに素・097 と同一)。**DB マスクは不要**: 分裂は DataFrame 変換なので、
  シミュレーション用の matrix(`canonical-v1`)を 1 回だけ構築し、アーム A=`pseudo_split` を
  `race_date >= cutoff` の行に当てたコピー、アーム B=その原本(=正準。シミュレーション窓の学習・採点行は
  JRA-VAN 期なので生データが既に正準綴りであり `canonicalise` は no-op。2025-10-11 以降の行だけが
  変換されるが、採点窓 ≤2024 の fold では学習に入らない)。実窓ガードは **raw 表現で別途 1 回**構築する
  (D6)。各測定の両アームは同一ビルドのコピーなのでアーム同一性は測定ごとに閉じる。paired_eval の
  candidate=B / active=A(負=正準が良い)。両アームとも 097 と同じレシピ(arm E OOF isotonic
  8 blocks・rounds 900・091 体重マスク・seed 42・num_threads 1)で fold 内再学習。
- **Rationale**: 実窓単独は MDE≈0.009 で検出力不足(ユーザー決定 2026-08-23・2 回確認)。washout
  1 年後の採点=アーム A のモデルは分裂綴りを**約 1 年分**学習済み=実際の 10 か月に近い。
  `race_class` はレース内定数で race-softmax では主効果が相殺され、効果は交互作用経由の小さな量
  =null-is-success と見込む。
- **アーム同一性(INV-A1..A5・097 の対称性/provenance 契約を置換)**: DB を触らないので symmetry
  snapshot は不要。代わりに (a) 両アームの frame は `race_class` を除く全列で check_exact 一致、(b)
  `race_class` の差異行は `race_date >= cutoff ∧ 値∈{1勝,2勝,3勝}` の行に限られ、その行数を記録、(c)
  両アームの `race_class` 列 hash を verdict.json に記録、(d) paired diff が全レースで 0 なら abort
  (097 run 1 の教訓=アームが同一)、(e) アーム A は原本のコピー(`frame is not`)で feature_cols 同一。
- **Alternatives**: 実窓主測定(却下・検出力)/ 2×2 レジーム指示子アーム(却下・計算 2 倍・採用しない
  指示子)/ `オープン` も分裂させる(却下・変換表に無いものは測らない=「配備する変換そのもの」を測る)。

## D6: 実窓ガード(2025-10-11〜)と transportability ゲート

- **実窓ガード(`guard_real_direction`)**: 実データの matrix を **raw 表現で別途 1 回**構築し、arm A=生
  (分裂のまま)・arm B=`canonicalise` 適用コピー。採点窓 2025-10-11〜DB 最新日、両アーム fold 内再学習。
  evidence-of-harm(標本 CI・margin +0.005): `ci_low > +0.005` なら FAIL。成立は要求しない。
- **transportability(FR-007a)**: (a) 各カットオフ単独の点推定が pooled と同符号、(b) leave-one-
  cutoff-out の pooled 点推定が同符号、(c) 実窓の CI が pooled の符号を**自信を持って否定しない**
  (pooled が負なら実窓 `ci_low ≤ 0`)。(c) を点推定の符号一致にしないのは、実窓の点推定は
  sd≈0.0045 で真の効果 −0.002 でも 1/3 の確率で正になり、ノイズで NO_DECISION を量産するため。
  spec FR-007a(c) はこの定義で書かれている(spec と本 research は同一定義・gate-config `transportability` に凍結)。
- **verdict**: `primary_pooled AND guard_real_direction AND transportable`。三値の優先順位(spec FR-007):
  実行不能(充足未達を含む)→ NO_DECISION / `primary_pooled AND guard_real_direction` 不成立 → REJECT
  (transportability は参考値として記録するだけ)/ 両方成立かつ transportable 不成立 → NO_DECISION /
  全成立 → ADOPT。明確な REJECT を反転で NO_DECISION に変えない。

## D7: verdict artifact の隔離

- `artifact_kind = "counterfactual_spelling_simulation"`(097 の `counterfactual_supply_simulation`
  と並ぶ列採用用の種別)、`eligible_for_verdict = False` → `evaluate_promotion` が構造的に拒否。
  gate-config の `artifact_isolation.kinds` に追加。verdict の `verdict` は RegimeReport 準拠の
  オブジェクト(文字列にすると `normalize_verdict` が落ちる=097 analyze の教訓)。

## D8: verdict 後の分岐

- **REJECT / NO_DECISION**: bump(023)・compat pin・marker 配線・serving の語彙検査の**結線**を
  revert。`race_class_canon.py`+単体テスト+driver は非結線保全(062/070/090/097 同型)。active 予測
  バイト一致を検証。結果(replay +0.029 含む)を spec に転記。
- **ADOPT**: features-023 を確定(正準表現が既定)。旧 active(021)は compat で生表現のまま serve。
  正準データで候補を学習・登録(`register-arm-e`)し、昇格は「標準窓非劣化 + 本 verdict +
  prospective」の 3 点セットで別途(097 D9 同型)。昇格前後で serving 暗転は無い。

## D9: US3 リステッドは **賞金で大半が復元できる**(新規取得ゼロ)

- JRA-VAN 期(2023〜2025-10)で `OP(L)` と `ｵｰﾌﾟﾝ` の 1 着賞金は**面ごとに互いに素**:
  芝 OP(L)={1700,2000,2700,2800} / ｵｰﾌﾟﾝ={1400,1600,2300,2400}、ダ OP(L)={1900,2400} /
  ｵｰﾌﾟﾝ={1600,1800,2200}。切替後 `オープン` 164 のうち障害 37 を除く平地 **127** に当てると(2026-08-23
  再実測。plan 初稿の「障害除く 142・非リステッド 74」は障害の track_type 再ラベル前の値で内訳の和も合って
  いなかったため差し替え)、リステッド相当=芝 1700/2700/2800(5+17+10=32)+ダ 1900/2400(4+13=17)=**49**、
  非リステッド=芝 1400/1600/2300/2400(1+3+12+6=22)+ダ 1600/2200(2+38=40)=**62**、曖昧=芝 2100(14)+
  ダ 2000(2)=**16**(JRA-VAN 期に無い値)。49+62+16=127 で reconcile 済み。`race_name` に `(L)` は
  含まれない(0/164)。DB は動くので T026 が同じ規則を再実行して固定する。
- **Decision**: 本 feature では `オープン` を変換しない(D1)。US3 の成果物として上の対応表と
  曖昧 16 件を記録し、`オープン→ｵｰﾌﾟﾝ/OP(L)` は賞金規則を事前登録する**別 feature** とする。

## D5a: 分裂の開始点は名称変更の 2019-06-01(smoke で判明)

- `1勝/2勝/3勝` の名称は 2019-06-01 開始(それ以前は `500万/1000万/1600万`=変換表外で両アーム不変)。
  したがってカットオフ 2019-01-01 の擬似分裂は実質 2019-06-01 から始まり、2020 窓の washout は約
  7 か月(2021/2023 カットオフは 1 年)。estimand は「分裂綴りを 7〜12 か月学習した再学習モデル」。
- smoke(当初 2016/2017)は学習データに分裂行が無く INV-A3(両アーム同一)で abort した=assert が
  設計どおり働いた。smoke を 2020/2021(事前登録外・採点窓と互いに素・redact)に変更して再凍結。

## D5b: カットオフは 2021/2022/2023 に再事前登録(本番 1 回目の INV-A3 abort)

- 本番 run 1 回目は cutoff 2019-01-01 で INV-A3(両アームの予測が全レース同一)により abort。**効果の
  数値は一切出力されていない**(出力規律)ので覗き見ではない。
- 機構: `1勝/2勝/3勝` は 2019-06-01 開始。cutoff 2019-01-01 ではアーム A の学習データ中の勝クラス行が
  **全て**分裂綴り(正準綴りと共存しない)。カテゴリ名を全体で一対一に付け替えたデータ分割は同一なので
  LightGBM は同一の木を学習し、予測が完全一致する。「分裂のコスト」は**両綴りが学習データに共存する**
  ときにしか発生しない(実世界=正準 6.3 年+分裂 10 か月)。
- 置換: cutoffs 2021-01-01 / 2022-01-01 / 2023-01-01、採点窓 2022 / 2023 / 2024(互いに素・envelope
  2020-2024 内・washout 1 年)。共存は 1.5 / 2.5 / 3.5 年。pooled n_days ≈ 320 ≥ 300。
- smoke(cutoff 2020/採点 2021・共存 7 か月)が 3,425/3,447 レースで差を示したことと整合。
- 教訓: カテゴリの「分裂」を人工的に再現するときは、学習データに**両方の綴りが共存する**ことを
  事前登録時に確認する(INV-A3 が無ければ 0.000000 の verdict を出していた=097 run 1 の同型)。

## D10: 実行時間の見積(実績比)

- 097 実績: 1 カットオフ ≈ ビルド 1 回 + arm E fit 2 本(650-700s/本)≈ 35-40 分。本 feature は
  DB マスクが無いので**シミュレーション用ビルドは 1 回**(3 カットオフ共通・canonical-v1)→ 3 カットオフ
  ≈ 6 fit ≈ 70-80 分 + 実窓ガード(raw ビルド 1 回 ≈ 5 分 + 2 fit ≈ 25 分)+ canonical ビルド ≈ 5 分
  → **約 2 時間**(097 の 4.5h より短い)。

## D11: 正直な限界

- シミュレーションは「綴り分裂そのもののコスト」を測り、実切替に伴うレジーム差(nk: 馬・欠損・
  脚質定義)は再現しない。実窓の効果量推定は検出力不足のまま(反実仮想として報告)。
- 単一 seed(42)。seed 分散は v4 の inflate で畳む。
- 期待効果は小さい(交互作用経由)。REJECT/NO_DECISION は衛生債務を残すが、現状の「レジームの旗」
  モデルは機能しているので安全側。

## D12: codex plan レビュー(`codex exec` 直叩き 1 回・成功・5 問)

生出力: codex-review-plan-raw.md。採否:

| # | 指摘 | 採否 | 反映先 |
|---|---|---|---|
| 1 | compat の表現選択を registry の既定値から選ぶと、marker 欠落・将来の 023→024 fallback で黙って誤表現になる。artifact 主導+明示 allowlist+golden test | **採用** | INV-R2 を「artifact 主導・ペア単位 allowlist・marker 欠落は fail-closed」に強化、golden fixture(raw-021 / canonical-023 / 拒否組合せ)を Phase B に追加 |
| 2 | シミュレーションは「3 対応変換の限界因果効果」であり供給元移行の総効果ではない。実窓で他列を固定し race_class だけ変える(=既に設計どおり)+ リステッド歴/供給元で層別 | **部分採用** | 実窓ガードは既に race_class のみ差異。層別は**報告のみ**(実窓は MDE≈0.009 で層別ゲートは検出力ゼロ)=gate-config `diagnostics.real_window_strata` |
| 3 | washout 窓に加えて「移行期窓(固定モデルに分裂綴りを当てる)」を事前登録 | **不採用** | 配備は再学習とセットで、旧モデルは compat で生表現を受け続ける(INV-R2)ため、この衝撃は本番経路で発生しない。実世界版の replay(+0.029)は採否外の証拠として記録済み(gate-config `evidence`) |
| 4 | 語彙 hash は artifact の整合性しか担保せず、既知値が正規化/順序/型ドリフトで黙って未知になる。カテゴリ変換**前**に per-prediction の unknown mask を出し、未知値でも予測が続く注入テストを置く | **採用** | INV-R4 を「変換前に取る・既知トークンの取りこぼし率で警告・注入テスト必須」に強化 |
| 5 | 最大の実務リスク=表現 dispatch が可変の既定値に依存し、実験の raw アームまで汚染する。全境界で明示引数・正確なトークン値の assert・カテゴリ化で生じた NaN の拒否 | **採用** | `build_training_matrix(representation=...)` を**必須引数**(既定値なし)に、driver は両アームに明示指定、INV-R7(カテゴリ化前後で NaN 数が増えない)を追加 |
