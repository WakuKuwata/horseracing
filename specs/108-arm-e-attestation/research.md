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

## D2: `internal_calibration` 検証の方式別分岐

**Decision**: 校正方式で分岐する。OOF 方式(`isotonic_strict_past_oof`)のときは
**`calib_frac == 0.0` を要求**し **`calibration_split_unit` は null 必須**、加えて
`n_oof_blocks`(正の整数)を必須とする。legacy 方式のときは現行の検証
(`0 < calib_frac < 1`・split_unit 非空)をそのまま維持する。

**Rationale**: 現行検証(`legacy_attest.py:311-322`)は arm E を構造的に弾く —
booster は何も holdout しないので `booster_calib_frac=0.0`、内部 split を使わないので
`calibration_split_unit=null`(`calib_split.py:477-479` が意図的に null を書いている:
「値を書くと 70/30 split モデルに見えてしまう」)。これは**緩和ではなく方式別の厳格化**で、
OOF 方式なのに split_unit が入っている payload は逆に拒否される(取り違え防止)。

**Alternatives considered**: `0 <= calib_frac < 1` に緩める — legacy モデルの
「holdout ゼロなのに legacy を名乗る」不正形を通してしまう(fail-open)。不採用。

## D3: 再構成の分岐と必須記録項目

**Decision**: 再構成も校正方式で分岐し、OOF 方式なら arm E factory(`CalibSplitFactory` 系)を
組む。attestation の必須項目に **`n_oof_blocks`** と **weight mask 設定(rate/seed)** を含める。

**Rationale**: **`n_oof_blocks` のコード既定は 3(`calib_split.py:144,570`)だが実モデルは 8**
(`calibration_protocol.n_oof_blocks=8`)。記録しなければ再構成は黙って別モデルになる。
mask は `ModelRecipe.weight_mask_rate/seed` が既に存在し(`recipe.py:65-66`)、
`weight_mask_spec()` が `MaskSpec(rate, seed, unit="race")` を組む(columns は features 層で
固定・unit はハードコード)ので、**rate と seed だけで mask は一意に決まる**。
`CalibSplitFactory.meta()` は既に `n_oof_blocks` を含む(`calib_split.py:587`)= factory 同一性の
一部として扱われている。

**この feature の実装量が小さい理由**: recipe 層は arm E を既に完全表現できている。
欠けているのは attestation 層の表現と分岐だけ。

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

**見積**: 19 fold × 9 = **171 回**。107 の confirmatory 実測(3 fold × 2 arm × 4 fit = 24 fit で
2,692 秒 ≈ 112 秒/fit)から **3〜5 時間**。旧世代(単純構成・19 fit)の実測 ~17 分とは桁が違う。

**Decision**: FR-018 の中断点を **1 fold の実測**として Phase 境界に置く。ETA が見積の
2 倍を超えたら本実行に進まず、実行方式(並列度・スレッド数)を見直してから再判断する。
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

## D8: codex 品質ゲート — unavailable、セルフレビューで代替(実質的な穴を 1 件検出)

**codex unavailable**: `codex exec` が本 feature で 2 回とも
`failed to initialize in-process app-server client: Operation not permitted (os error 1)` で起動不能
(`UV_CACHE_DIR` 回避を含む)。本日の別タスク(107 T003)では成功しているため**断続的な環境障害**。
規律どおり再試行は 1 回で打ち切り、復旧は別タスク化済み。代替のセルフレビューを 4 観点で実施:

### 観点 1: D1 の「条件付き挿入」に穴はないか

- **省略と欠落の区別**(実質的論点): arm E モデルで mask が「設定されていない」のと
  「記録し忘れた」のが payload 上で同じ「キー無し」になる。→ **対策を採用**: metadata に
  `weight_mask` キーが存在するのに rate/seed が読めない場合は型付きエラー(黙って省略しない)。
  T005/T006 に明示する
- **省略の悪用**: payload から arm E キーを削って legacy を名乗る改竄は、
  (a) `method` が OOF のままなら D2 の方式別検証が弾き、(b) `method` も書き換えるなら
  モデルディレクトリからの再計算照合(INV-A3)が弾く。**二重に守られている**
- **将来の保守性**: 省略軸を「arm E か否か」の 1 軸に限定し、新しい省略軸を増やさないことを
  contract に明記する

### 観点 2: D2 に fail-open の芽はないか — **穴を検出**

**検出**: 現行検証は `method` を `_nonempty_string` で見るだけで**値域を検証していない**
(`legacy_attest.py:312`)。方式別分岐を「OOF なら arm E 検証・それ以外は legacy 検証」と
書くと、**未知の method 文字列(将来の第 3 の方式・typo)が legacy 扱いで素通り**する。
これは `fit_calibrator` が未知 method を fail-closed にした 085 の教訓
(「typo が別の校正器を静かに学習し、実験が別物になった」)と同型。

**対策(採用)**: `method` の値域を既知の集合に限定し、**未知は型付きエラーで拒否**する。
分岐は「OOF → arm E 検証 / 既知 legacy → 現行検証 / それ以外 → 拒否」の 3 分岐にする。
T006 と contracts/attestation.md に反映済み。

### 観点 3: D5 の近道は本当に無いか

- **束の保存量を raw に変える**: 不採用。束の win は arm E の内部 isotonic 適用後 =
  two_gamma の入力そのもの。078 の verdict が見る「raw ECE」は two_gamma 適用前の意味であり、
  束の保存形式と整合している。raw に変えると activation の消費側と食い違う
- **fold を減らす**: 不採用(D6)。078 の実績が「窓が短いと verdict が変わる」ことを示している
- **結論**: 近道なし。中断点(T012)でコストを実測してから進むのが唯一の緩和

### 観点 4: 優先度の見立て

**低い**。精度も回収率も動かず、実体は表示 top2/top3 の校正正統化のみ。
正当化は「基盤が今後の全世代で恒久不活性になる」の一点に限られる。
この評価は spec 冒頭で開示済みであり、着手判断はユーザーが行った。
