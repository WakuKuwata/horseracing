# Research: arm E 系モデルの OOF attestation 対応 (108)

すべて実コード・実 artifact で確認済み(2026-09-02)。推測で書いた項目はない。

## D1: payload 拡張と旧世代 digest の不変性

**Decision**: attestation payload に arm E 固有キーを足すが、**arm E のときだけ挿入**する
(legacy 構成では payload にキーが存在しない)。既存の「既定値なら hash payload から省く」
機構(`ModelRecipe.NEW_HASH_DEFAULT_OMISSIONS`・099 が用意し 101 が初使用)と同じ形。

**Rationale**: digest は `stable_hash(payload)` = payload dict 全体の canonical JSON hash
(`eval/hashing.py:25`)。キーを既定値つきで無条件に足すと **lgbm-063 の digest も変わり**、
生成済み manifest(`base_model_version=lgbm-063`・digest `d9f45bb0…`)と過去 verdict が
参照不能になる(FR-002/FR-009 違反)。条件付き挿入なら legacy の payload はバイト同一。

**Alternatives considered**: (a) payload を v2 化して legacy を v1 のまま読む二重形式 —
検証経路が 2 本になり「同じ契約の二重実装」(088/100/106 の反復欠陥)を招く。(b) digest 計算から
新キーを除外 — 記録した構成が digest に守られない = 改竄検出の穴。**両方とも不採用**。

**検証**: 実装前に lgbm-063 の現 digest を測って golden fixture に固定し、実装後の一致を assert。

## D2: 現状は「拒否」ではなく「捏造して通る」 — silent fail-open の封鎖(**2026-09-02 訂正**)

**当初の記述は誤りだった**。実測(`attestation_from_model_dir` を現行 active に実行)の結果:

```
RESULT: SUCCEEDED (not rejected)
  internal_calibration = {"method": "isotonic_strict_past_oof",
                          "calib_frac": 0.3, "calibration_split_unit": "race_count_v1"}
```

metadata の `calib_frac` は欠落・`split_unit` は null だが、`build_attestation` が
`DEFAULT_CALIB_FRAC`(0.3)と `LEGACY_CALIBRATION_SPLIT_UNIT`(race_count_v1)で**補完する**
(`legacy_attest.py:410-417`)。よって検証は通り、**arm E モデルの証明書が 70/30 旧世代モデルだと
主張する**。重み mask は payload に場所がないので**黙って落ちる**。

**Decision**: 欠落値の既定補完を arm E 経路で禁止し、必須項目が読めなければ型付きエラーにする。
検証は校正方式で 3 分岐(既知 legacy / arm E / **未知は拒否**)。
`_INTERNAL_CALIBRATION_FIELDS` は missing と unexpected の両方に使われる単一集合なので、
**許可フィールド集合そのものを方式別にする**(legacy 集合は現行のまま不変)。

**既知の校正方式名(実測)**: `artifacts/model_versions/` 全 11 件は `isotonic`(8 件・legacy)と
`isotonic_strict_past_oof`(3 件・arm E)の 2 値のみ。この 2 値を許可集合とし、他は拒否。

## D3: 出荷ビューと構成の分離(**2026-09-02 全面訂正**)

**当初の設計は出荷ビューを recipe と取り違えていた**。実測で判明した実際の構成
(`arm_e_register.py:160-173`):

```python
recipe = ModelRecipe(objective="pl_topk", calibration="isotonic", calib_frac=0.3, seed=42,
                     weight_mask_rate=0.5, weight_mask_seed=..., params=(("n_estimators", 900),))
predictor = OofCalibratedPredictor(recipe, n_oof_blocks=8, method="isotonic")
```

対して metadata が持つのは **出荷ビュー**(`to_servable()` が書く): `calibration` は
`isotonic_strict_past_oof`・`calib_frac` は欠落・`split_unit` は null。

**実測した重要な性質**:
- `ModelRecipe(calibration_split_unit=None)` は **構築不能**(`ValueError: unknown
  calibration_split_unit: None`)。当初設計の「payload の null から recipe を組む」は実装不能だった
- **`recipe.calib_frac` は arm E では挙動に効かない** — `_make_base` が `calib_frac=0.0` を
  ハードコードする(`calib_split.py:256`)。recipe hash にだけ効く
- **容量は `recipe.params` 経由**(`_RECIPE_FIELD_DISPOSITION["params"]="forward"`)。
  現行 active は `n_estimators=900`。記録しないと既定容量で走り**静かに別モデル**になる
  (`n_oof_blocks` と同型の失敗)
- **登録時の recipe hash はどこにも保存されていない**(metadata にも `model_versions` にも)

**Decision**: attestation は (a) 出荷ビューを**出荷ビューとして**記録し、
(b) arm E の**構成**を別ブロックで記録する(プロトコル名・`n_oof_blocks`・mask rate/seed・
解決済みパラメータ)。再構成は構成ブロックから
`ModelRecipe(calibration="isotonic", calib_frac=<既定・挙動に不影響>, split_unit=<既定>,
params=<容量>, weight_mask_*)` + `CalibSplitFactory(method="isotonic", n_oof_blocks=N)` を組む。
**保証は挙動的一致まで**(登録 hash が無いため識別子照合は原理的に不可能)。

## D3a: 既存の再構成入口は旧世代を強制する(**新規**)

**実測**: `oof-generate` が使う `factory_from_attestation` は `enforce_legacy=True` 既定で
`base_model_version == "lgbm-063"` を強制する → 現行 active では
`AttestationError: legacy base_model_version differs from expectation` で**例外**。

**Decision**: `oof_generate.generate_oof_bundle` を非 legacy 対応にする
(`general_factory_from_attestation` へ切り替え、期待 model/feature version を**必須引数**で
受けて fail-closed にする)。`cli.py` の既定値(`--base-model-version lgbm-063` /
`--active-dir .../lgbm-063`)と help も現行世代を指せるようにする。
**plan の「変更は legacy_attest.py に集中」は誤りだった** — `oof_generate.py` と `cli.py` も変更対象。

## D3b: 共有消費者への影響(**新規**)

attestation の消費者は本 feature だけではない: `ev_weight_run`(079)と
`segment_accuracy_run`(082)。**082 は現行 active に対してこの経路を使う**。

**実測した 082 の現状**: attestation 生成は成功し捏造値を返す →
`general_factory_from_attestation` も成功し `AttestedRecipeFactory`(plain・**mask は None に
落ちる**)を返す → だが `recipe.calibration = "isotonic_strict_past_oof"` は
`CALIBRATION_METHODS = ("platt","isotonic","none","identity")` に無いため、
**学習時に `fit_calibrator` が fail-closed で落ちる**。

→ **静かに誤った読み出しが出回ることはないが、082 は現行世代で使用不能**。
これは本 feature が発見した既存欠陥で、どの artifact にも記録がなかった。
本 feature の回帰対象に 079/082 の両経路を含める。

## D4: `calibrator_degenerate: true` の矛盾 — 原因判明・報告バグ

**事実**: lgbm-094-cap900 の metadata は `calibrator_degenerate: true` だが、
`calibrator_params` には実データ由来の isotonic 写像(x/y の breakpoint 配列)が入っている。

**原因**: `CalibSplitFactory.to_servable()`(`calib_split.py:473-501`)が
`info = dict(base.fit_info_)` で **base predictor の fit_info を継承**し、
`calibration`・`calibration_split_unit`・`calib_from`・`calib_through`・`n_calib_rows`・
`calibration_protocol` は上書きするが **`calibrator_degenerate` は上書きしない**。base は
`calibration="none"` で作られる(= identity)ので、その `True` が残留する。

**真の値は False であることが構造的に確定**: 同じ `to_servable()` が
「refusing to ship a degenerate (identity) OOF isotonic」で **identity 校正器を fail-closed で
拒否する**(`calib_split.py:470-471`)。よって出荷されたモデルの校正器は必ず非退化。

**Decision**: (a) attestation は**権威ある場所だけを読む**(`calibration_protocol` と
`calibrator_params`)。top-level の `calibrator_degenerate` を信頼しない。
(b) 報告バグ自体は 1 行(`to_servable` で `info["calibrator_degenerate"] = False` を明示)で
是正する — spec の edge case が「記録側の欠陥であれば本 feature の範囲で是正する」と定めている。
**既存モデルの metadata.json は書き換えない**(過去 artifact は不変・次回学習から正しくなる)。

## D5: OOF 束は校正後の予測を保存する → コストと中断点

**事実**: `oof_generate.py:89-93` は `predictor.predict_race(...)` の返り値
(**校正後**の win/top2/top3)をそのまま保存する。よって fold ごとの fit は arm E の
内部 OOF isotonic(8 ブロック)を含まねばならず、**1 outer fold あたり 1+8=9 回の booster 学習**。

**見積(算術を是正)**: 19 fold × 9 = **171 回**。107 の confirmatory 実測
(3 fold × 2 arm × 4 fit = 24 fit で 2,692 秒 ≈ 112 秒/fit)から
**171 × 112 秒 = 5.32 時間**。arm E は後年の fold ほど学習データが増えて高価になるため、
保守的には **5〜8 時間**。旧世代(単純構成・19 fit)の実測 ~17 分とは桁が違う。
(当初の「3〜5 時間」は自身の数値と矛盾していた・analyze U5)

**Decision**: FR-018 の中断点を **1 fold の実測**として Phase 境界に置く。
**外挿規則を事前に固定する**: 最終年 1 fold(`--first-valid-year 2026`)を測り、
`ETA = 実測 × 19 × 0.6`(後年 fold が最も高価なので係数で割り引く)。
**ETA > 12 時間なら本実行に進まない**。
**近道(束が保存する量を raw に変える・fold を減らす)は採らない** — 前者は 076 の activation が
消費する量と食い違い、後者は D6 に抵触する。

## D6: 評価窓は 078 と同じ全史(2008-2026)

**Decision**: 窓・判定基準は 078/074 の凍結設定をそのまま流用する。

**Rationale**: 078 の実績が示すとおり、窓が短いと verdict が変わる(2 fold のとき
`no_held_out_stage_evidence` で NO_DECISION → 全史 18 fold で決定的 verdict)。
窓を選び直すことは「結果に合わせた窓選択」であり FR-007(探索・調整の禁止)に抵触する。
**同じ手続き・新しいモデル**が原則。

## D7: 実行コスト以外の前提確認(実測済み)

| 項目 | 実測値 | 含意 |
|---|---|---|
| `n_oof_blocks`(active) | 8 | 既定 3 と異なる=必須記録 |
| weight mask(active) | rate=0.5・seed=20260810・unit=race・3 列 | recipe の 2 フィールドで一意 |
| `git_sha`(active metadata) | **null** | attestation の `code_sha` は引数で渡す設計なので致命的でない。ただし manifest の production scope は clean tree を要求するので実行時に確認が要る |
| `fold_boundaries`(active) | `[]` | arm E は outer fold を持たない(全史 booster)。attestation には不要 |
| 旧世代 manifest | `d9f45bb0…`(scope=production・eligible=True・fit_through 2026-07-18) | 不変であることを検証する対象 |

## D8: codex 設計レビュー — 取得成功(採用 6 / 部分採用 1 / 不採用 1)

本 feature の 1 回目は初期化エラーだったが、analyze の指摘を受けた 2 回目
(是正案そのもののレビュー)で**取得に成功**した。指摘と採否:

### 採用

1. **`calib_frac` は除外せず `requested` / `effective` を分けて記録する**(私の案「既定値を使う」より良い)。
   `requested=0.3`(recipe 宣言値)・`effective=0.0`(実際に booster が使った値)・
   「この protocol version では非挙動項目」を区別する。**将来 `calib_frac` が効くようになったとき、
   protocol version の更新を強制することで検知できる**。挙動同値性の比較は非挙動項目を除いた
   別規則で行う
2. **protocol version で意味を固定する**。同じ version のまま意味が変わることを拒否する
   (現行の `strict_past_oof_isotonic_v1` を必須項目とし、未知 version は拒否)
3. **保証境界を明記する**: digest が保証するのは payload の改竄検出であり、
   **内容が登録時の真実だったことまでは遡及証明できない**。FR-003 の限界記述をこの表現に是正
4. **相互整合検証**: 出荷ビューが OOF を名乗るのに構成ブロックが無い / 重複する主張が矛盾する /
   未知の組合せ は**すべて拒否**。edge case でなく FR に格上げする
5. **出荷ビューで欠落を 0.0 に正規化しない**。欠落は欠落として記録し、実効値は別に持つ
6. **legacy と arm E の検証経路を完全分離**する。arm E を legacy として解釈できないようにしつつ、
   legacy の payload bytes・digest・検証規則は一切変更しない

### 部分採用

7. **OOF 分割規則・行順・mask アルゴリズム・依存実装が protocol で固定されているか不明**
   → 再生成差の入口になる。実際には `day_block_partition` と `MaskSpec` がコード側で固定され、
   payload には `code_sha` が既にあるため部分的に担保される。**完全な固定ではないことを
   限界として記録**し、protocol version + code_sha の組で縛る旨を contract に書く

### 不採用

8. **再生成予測と出荷済みモデルの予測照合による強い保証** — codex 自身が
   「データ・実行条件の再現コストが高く、比較可能性も提示事実からは確定できない」と留保。
   本 feature の目的(基盤の活性化)に対してコストが見合わない。**限界として記録**するに留める

### 前回(1 回目)のセルフレビューで検出した穴

codex 取得前に実施したセルフレビューで、**未知の校正方式名が legacy 扱いで素通りする
fail-open** を検出済み(085 と同型)。codex の指摘 6(経路の完全分離)と同じ方向であり、
3 分岐(既知 legacy / arm E / 未知は拒否)として既に反映済み。
