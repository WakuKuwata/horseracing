# Research: Probability Pipeline Activation & Parity (076)

Phase 0 の設計判断。各項目 **Decision / Rationale / Alternatives**。NEEDS CLARIFICATION は残さない。

## D0. codex second opinion(全採用)

**Decision**: spec フェーズで codex(gpt-5.6-sol, xhigh, 実コード読解)に 5 問(activation mode /
fixture-first / 必須 parity / path-threading / 見落とし制約)を出し、**full 総括を取得・全指摘を採用**。

**採用した blocker(実質的反対なし)**: (1) 明示 mode 化 + 「plumbing≠leak closure」明示、(2) fixture を
production loader が拒否(`artifact_scope`/`activation_eligible`)、(3) 全 entry path 結線、(4) 絶対パス
+ load-once、(5) 世代束縛は model_version 名だけでは不十分 → attestation 再計算照合(D3・当初案の 4-key
checksum は不能=C1 で是正)、(6) candidate model
選択(057)への対応、(7) 時間的妥当性(`target_date <= fit_through`)、(8) fail-closed を backfill 例外
隔離の前に + advisory lock、(9) digest を logic_version/冪等キーに、(10) checksum は authenticity でない。

**Rationale**: 校正リーク境界・parity・冪等はこのリポジトリの最大リスク面。codex は spec に反映済み
(FR-004/016–022, SC-009–012)。plan の判断はこの延長。

**Alternatives**: plan フェーズで再度 long codex run → 冗長(同一判断の再確認)。plan 固有の未決
(authenticity=署名 vs read-only)のみ D9 で確定。

**tasks/analyze フェーズで創発した軽微決定(監査記録・憲法 品質ゲートの carve-out)**: (i) **load-once の
分離**=ファイル I/O+verify は 1 回、時間照合のみ per-target_date(FR-018×FR-021 の両立、loader-contract
§0)。(ii) **collect-prospective は two_gamma のみ結線**(推薦 win 中心・stage_discount は表示専用で無関係、
T015)。(iii) **FR-019 の照合機構是正**=4-key checksum ではなく `attestation_from_model_dir` 再計算(analyze
C1 が実コードで substantiate)。いずれも新設計でなく既存制約への整合=「方針が固まった軽微な変更」に該当し
独立 codex は取らず、5 回の `/speckit.analyze` 反復で cross-artifact 収束を確認(HIGH 2→0)。

## D1. 単一共有 loader `load_calibration`(3 経路のドリフト防止)

**Decision**: `probability/calib_activation.py` に唯一の解釈点を置く:

```
load_calibration(manifest_path, *, active_model_version, active_model_dir, target_date, profile) -> Activation
Activation = { two_gamma: PCalibrator, stage_discount: StageDiscount, manifest_digest: str, mode: str, fit_through: date }
```
(正本は [contracts/loader-contract.md](contracts/loader-contract.md)。`active_model_dir` は世代 attestation
再計算に必須=D3、`fit_through` は per-day 時間判定に返す。)

`verify_manifest`(074)を通した後、世代/scope/時間を検証し、`full_precision_params.two_gamma` を
`PCalibrator(method="two_gamma", params={gamma_lo,gamma_hi,pivot})` に、`stage_lambdas{top2,top3}` を
`StageDiscount(lambda2=top2, lambda3=top3)` にマップして返す。fail-closed は全て例外。

**Rationale**: two-gamma と stage_lambdas の解釈(特に `{top2,top3}`→`lambda2/lambda3` の取り違え)を
1 箇所に集約。3 経路が同じ Activation を使うので `betting_two_gamma_parity` / `display_topk_parity` が
定義上成立する。`probability/` 配置=betting/serving/api/training/eval 全てが依存できる下流(循環回避)。

**Alternatives**: 各経路で個別に manifest を読む → マッピングのドリフト・取り違えリスク(却下)。

## D2. 明示 mode(`legacy-runtime` / `manifest-required`)

**Decision**: boolean opt-in ではなく enum。既定=`legacy-runtime`(現行 runtime fit・バイト同等)。
`manifest-required`=manifest 必須で、無効/欠如は**致命的**(runtime fit に fallback しない)。

**Rationale**: fixture-first では「manifest はあるが production 非適格」がありうる。boolean だと
「activation ON なのに黙って runtime fit」の silent leak を招く。mode 明示で fail-closed の意味を固定
(codex Q1)。

**Alternatives**: `identity-disabled`(外部校正なし)も codex が提案したが 076 では不要(identity は
manifest 内で表現=D6)→ 2 mode に絞る。将来追加は非破壊。

## D3. 世代束縛は model_version 名 + attestation 再計算照合(C1 是正)

**Decision**: `verify_manifest` の `base_model_version=="lgbm-063"` 一致に加え、loader は **resolved model
dir + `manifest.code_sha`** から 074 の `attestation_from_model_dir(dir, code_sha=...)` で attestation を
**再計算し `attestation_digest`(=`stable_hash(full payload)`)を `manifest.attestation_digest` と比較**する。
不一致は fail-closed。

**Rationale**: `save_model_version` は同名 model_version を上書きしうる(077 で是正予定)ため、名前一致
だけでは「その manifest がこの artifact 由来」を保証しない(codex Q5)。**074 の attestation digest は
full recipe payload の `stable_hash` であり 4-key checksum mapping では再構成できない**(analyze C1 が実
コード `legacy_attest.py:427/430` で substantiate)→ loader は 4-key を受けず **model dir を受けて 074 の
実関数で再計算**するのが正しい機構。registry 化(loader checksum enforcement 全般)は 077。

**Alternatives**: (a) 名前一致のみ(現 verify_manifest)→ 上書き耐性なし(却下)。(b) 4-key artifact
checksum mapping を渡して比較 → 074 の full-payload digest を再構成できず実装不能(却下、C1)。

## D4. 全 entry path 結線(betting/serving CLI + live + ops)

**Decision**: `--calib-manifest <abs-path>` + `--calib-mode {legacy-runtime,manifest-required}` を
betting recommend/backfill・serving predict/predict-backfill・`live refresh` の CLI に足し、
`live/orchestrate.py` は既存の `p_calibrator` 経路にそのまま流す。`ops/runner.py` は serving/recommend
subprocess の argv に `--calib-manifest` を伝播する。

**Rationale**: codex Q4=一部経路だけ結線すると leak が残る。`live/orchestrate` は既に `p_calibrator` を
`run_serving`/`generate_kelly_recommendations` に通しているので、manifest→Activation→(two_gamma,
stage_discount)を同じ param に注入するだけ。`ops` は ML 非 import 境界(subprocess)なので CLI flag 伝播。

**Alternatives**: betting/serving CLI だけ(spec 初稿)→ live/ops 経由の leak 残存(却下)。

## D5. 絶対パス解決 + load-once

**Decision**: manifest path は絶対パス/immutable artifact URI で受け、パッケージ横断で単一解決規則。
loader は 1 invocation につき 1 回だけ manifest を読み、全レースが同一 digest を使う。

**Rationale**: [[weights-uri-relative-path-ops-bug]] と同型=相対パスは ops(cwd=serving)から解決不能。
per-race 再読込は無駄 + digest 揺れリスク(codex Q4)。

**Alternatives**: 相対パス既定 → 既知バグ再発(却下)。

## D6. logic_version token + 冪等キー(digest 込み)

**Decision**: manifest 由来出力の `logic_version` に `;calib=<manifest_digest[:12]>;calibmode=manifest`
を追記。serving backfill(044)と betting recommend(043/045)の冪等キーに manifest digest を含める
(既存 `;sdisc=`/`;pcal=`/`;oddscap=`/`;prospective=` と同じ token 規律)。identity 校正も明示
(`;calib=<digest>` は付くが params が identity)。

**Rationale**: 別 manifest 再実行が「既存 run あり」で silent skip=leak 未是正のまま古い値が残るのを
防ぐ(codex Q4/Q5)。既存の logic_version フィルタ(betting cli の `.contains(...)`)と同じ機構で冪等・
監査・区別を一括担保。冪等は advisory lock + 厳密 logic-token 一致。

**Alternatives**: 丸めた γ/λ を logic_version に(現行)→ byte 再現不能・digest 非対応(却下)。

## D7. fixture-first + manifest schema v2(artifact_scope/activation_eligible)

**Decision**: `calib_manifest.py` の schema を v1→**v2** に additive 拡張し、`artifact_scope ∈
{fixture,production}` と `activation_eligible: bool` を追加。loader の `profile` が
`production` のとき **fixture/未 eligible を拒否**。テストは `build_manifest(..., artifact_scope=
"fixture")` で組んだ fixture を使い、loader は実 manifest を読む契約を守る。

**Rationale**: codex Q2=fixture が production loader に受理される false-parity trap を防ぐ。disk artifact
の JSON schema 拡張は DB schema-zero を破らない(migration 不要)。v2 bump で旧 manifest は明示的に非対応
(fail-closed)。**実 manifest 生成(stage-λ OOF fit・build_manifest の production caller・full OOF
job)は本 spec 外の blocking follow-up**。

**Alternatives**: loader に `allow_fixture` flag → manifest 自身に scope が無いと運用で取り違える(却下、
codex は artifact 側 metadata を推奨)。

## D8. 時間的妥当性(`target_date <= fit_through` 拒否)

**Decision**: manifest に `fit_through`(校正 fit の最終日)を持たせ(074 evaluation/attestation 由来)、
loader は対象レース日が `<= fit_through` の manifest を拒否する。

**Rationale**: 校正は strictly-past でなければリーク(憲法 II)。manifest 由来でも適用対象が fit 窓に
入っていれば非OOS(codex Q3)。

**Alternatives**: 検証なし → 過去日に対する非OOS 適用を見逃す(却下)。

## D9. artifact authenticity(plan 固有の未決 → read-only 運用で確定)

**Decision**: checksum は改竄検知であって authenticity ではない(攻撃者が payload+digest 両方を書換可)。
076 は **artifact ディレクトリを read-only 運用**(deploy 手順)で担保し、**署名は導入しない**。署名/
本格 registry は 077 に送る。

**Rationale**: 現状の脅威モデルはローカル単一オペレータ(admin-console-program の localhost 前提)。署名
基盤は過大。read-only + content-addressed digest で実運用上十分(codex Q5 も「脅威範囲に含めるなら」と
条件付き)。

**Alternatives**: 署名導入 → 鍵管理コスト・077 と重複(却下、今は不要)。

## D11. loader 配置と generation-binding(実装時判明・codex 確認)

**問題(実装時)**: 当初 loader-contract は `load_calibration` が `training.calib_manifest.verify_manifest`
と `training.legacy_attest.attestation_from_model_dir` を直接使い、`active_model_dir` から attestation を
再計算する設計だった。しかし **training→probability の依存が既存**(training/market_offset.py 等が
probability を import)+ uv workspace で probability の env に training が入らない → **probability の loader
が training を import すると循環かつ実行時 ModuleNotFoundError**。さらに **api は training を import しない**
(read-only 軽量境界)ので dispersion 経路も training 依存不可。codex(gpt-5.6-sol)も「現行 plan は実依存
グラフと不整合」と確認。

**Decision**:
1. **manifest schema/verify を probability に移設**(`probability/calib_manifest.py`)。この module は training
   固有依存を持たず `horseracing_eval.hashing.stable_hash` のみ → 移設は挙動保存。training は re-export shim
   で後方互換(training→probability は許可方向、074 テスト無改修で緑)。loader は同一 package の verify を使う。
2. **generation-binding(attestation 再計算)は injected verifier 化**。loader signature は
   `load_calibration(manifest_path, *, active_model_version, target_date=None, profile=production,
   attestation_verifier=None)`。`attestation_verifier(manifest)` は不一致で `ActivationError` を raise。
   - **training を持つ経路(serving/live/dispersion-pcal)**: `training/calib_binding.py::
     model_dir_attestation_verifier(model_dir)` が `attestation_from_model_dir(dir, code_sha=manifest.code_sha)`
     で再計算・digest 照合する verifier を注入=**strong binding**(FR-019 完全)。
   - **training を持たない経路(betting/api)**: `attestation_verifier=None` → **name(base_model_version)+
     content-addressed digest(改竄検知)+ scope + temporal** の binding に留める。save_model_version 上書き
     に対する strong binding の全経路化は **077 registry** に送る(FR-019 自身が「registry 化は 077」と明記)。

**Rationale**: FR-006(単一 loader)を保ちつつ依存グラフを尊重。attestation 再計算は本質的に training 固有
(ModelRecipe 再構築)なので、それを持つ経路だけが strong binding を注入するのが最小侵襲。betting/api の
strong binding 欠如は「save_model_version 上書きが production で起きうる」= 077 が是正する前提条件が整うまで
は content-addressed immutable artifact(read-only 運用 D9)で実運用上十分。

**Alternatives**: (a) loader を training を見える package に移す→ betting/api→training を強制し api の read-only
境界を破る(却下)。(b) betting/api を training 依存に→ LightGBM stack を軽量経路に持ち込む(却下)。

**docs 反映**: loader-contract の signature を `active_model_dir` から `attestation_verifier` に是正。FR-019
は「training-having caller が strong binding を注入・betting/api は name+content-address(strong は 077)」に
staging。

## D10. dispersion は manifest 直読(派生 JSON 廃止)+ 選択 run の model

**Decision**: US3 は API が `load_calibration(...).two_gamma` を**直接 consume**し、`dispersion-pcal` の
派生 JSON 生成経路は使わない(CLI は残すが manifest 直読を既定)。比較対象は **API が選択した
prediction run の model**(057 candidate 選択)であり、単に active model ではない。

**Rationale**: codex Q4=US3 が「直接読込」と「弱い派生 JSON 生成」の 2 設計を混在。直接 consume が
clean(派生 artifact に provenance/verify を再実装せずに済む)。model 照合は FR-020。

**Alternatives**: 派生 pcal JSON を manifest から再生成 → provenance 二重管理(却下)。
