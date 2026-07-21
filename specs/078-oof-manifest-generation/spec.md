# Feature 078: OOF Calibration Manifest Generation (real manifest — leak closure)

**Status**: Draft (spec 起草・codex 設計レビュー並走中・plan 前)

## 概要

074 は OOF-faithful two_gamma_win 校正(`calibrate_oof`)と immutable manifest schema
(`build_manifest`/`write_manifest`, content-addressed)を作ったが、**`build_manifest` に production
caller が存在せず、manifest は一度も生成されていない**。stage-λ(top2/top3 stage discount)の OOF fit
も存在しない(runtime `fit_product_stage_discount` は `load_topk_samples`=最新永続予測=非OOS)。076 は
3 経路(betting two-gamma / serving stage-λ / api dispersion)を manifest から読むよう結線したが、
**FIXTURE manifest(合成 attestation)で検証**しただけで、実 γ/λ 値は未検証。

**078 は「実 production calibration manifest を生成する」= 074 が始めたリーク是正の完了段**。これで
初めて 076 の activation が本物の OOF-faithful 校正値で動く。**078 完了までは manifest モードを production
既定にしてはならない**(076 deploy note の time-boxed waiver を解除するのが 078)。

## 現状(調査確定)

- OOF bundle(`training/oof_generate.py::generate_oof_bundle`)は `expanding_folds`(first_valid_year=2008)
  で各 fold を strict-past fit(race_date < valid_year)→ valid 予測。per-race に **{win, top2, top3}** を
  content-addressed で保持(`foldfit.predict_over_folds` = fold ごと outer-train fresh fit・保存 booster
  不使用 = OOF)。着順は RaceResult(finish_order ∈ {1,2,3})。
- `load_p_samples_from_oof(session, bundle)` が bundle["predictions"] から (p_dict, winner) を返す。
- `calibrate_oof` は prequential_held_out(prior fold で fit・strictly-later block で ECE 評価)+
  three_way_verdict(ADOPT/REJECT/NO_DECISION)+ transfer_check(KS)を返し、`fit:{gamma_lo,gamma_hi,pivot}`
  を含む append-only evaluation artifact を出す。**stage-λ の同型関数は無い**。
- 実 lgbm-063 の OOF bundle は **未生成**(`artifacts/` に無し)。生成は fold-fit を全 fold 回すため高コスト。

## User Stories

### US1 — stage-λ の OOF-faithful fit (P1)
runtime の非OOS `load_topk_samples`/`fit_product_stage_discount` の OOF 版を作る。
- **`load_topk_samples_from_oof(session, bundle)`**: per-race `(race_id, race_date, p_dict, (id1|None,
  id2|None, id3|None))`。p_dict = `bundle["predictions"][race]["win"]`(OOF)、着順は RaceResult。
  dead-heat の stage は None(該当 stage のみ寄与しない=`calibrate_oof` の winner=None と同型)。
- **`calibrate_stage_oof(session, bundle, gate_config)`**: fold ごと prior OOF で λ2/λ3 を prequential
  fit・strictly-later OOF block で top2/top3 ECE 評価・tri-value verdict。**research D4=fit sample p を
  two_gamma 校正器に通してから λ を fit**(適用する分布で fit)。`stage_lambdas:{top2,top3}` + ece +
  verdict を含む append-only evaluation artifact を返す。

### US2 — manifest 生成 CLI (P1)
**`training generate-manifest`**: (a) OOF bundle を load(無ければ生成を促す/`--generate-bundle`)→
(b) `calibrate_oof`(two_gamma)+ `calibrate_stage_oof`(stage-λ)→ (c) `attestation_from_model_dir`
(lgbm-063 model dir + code_sha)→ (d) `build_manifest(full_precision_params={two_gamma, stage_lambdas},
fit_through=<下記 provenance>, artifact_scope="production", activation_eligible=<verdict 依存>,
evaluation={両 stage verdict})` → (e) `write_manifest` を content-addressed パスへ(create-only・atomic)。

### US3 — full OOF job(実 manifest 産出・operator) (P2)
実 lgbm-063 で `generate_oof_bundle`(数時間)→ `generate-manifest`。THE production manifest を産出し、
076 activation を実 γ/λ で再検証(win byte-parity・stage-λ が serving top2/top3 を実際に校正)。

## ⚠️ codex 設計レビュー後の是正(research D1–D9 参照)
初期設計に **2 つの実バグ**が判明し是正:
- **D1**: stage-λ を two_gamma 後 p で fit する案は**誤り**(serving は raw p に λ を適用)→ **raw OOF win で fit**。
- **D2**: prequential 評価 params を production params に流用しない → **eval と deployment final-fit を分離**。
加えて: **D3** eligibility は事前登録フル gate(bootstrap CI・LogLoss/Brier・worst-fold)に強化 / **D9** manifest
**v3** + eligibility を pure policy 化し verifier 再計算 / **D7** 決定論に frozen sample+result snapshot 必須 /
**D5** fit_through=deployment final-fit 日 / **D6** verdict matrix。**→ 078 は初期見積りより大幅に大きい多段作業**。

## 設計核 / 制約

- **リーク境界(憲法 II)**: stage-λ サンプルは bundle の OOF win 予測 + RaceResult 着順のみ。strictly-past・
  同日除外・dead-heat 分離・対象レース非参照。top2/top3 は複数馬 stage なので winner 単独ロジックと非対称
  → per-stage の valid-label 定義を厳密化(codex A で精査)。
- **fit_through provenance(最重要・codex B)**: 出荷 params が prequential で fit された最後の race_date に
  fit_through を固定 → activation はそれ以降(OOF が校正を fit していない=真に未見)のレースにのみ適用可。
  これで 076 レビューの「fit_through 自己申告=選択リーク」懸念を構造的に解消。**[OPEN: 出荷 params を
  全 fold で再 fit するか prequential last_params にするか — 全 fold 再 fit なら fit_through=bundle 被覆末]**。
- **activation_eligible ポリシー(codex C)**: two_gamma verdict × stage-λ verdict の組合せで決定。
  **[OPEN: REJECT stage-λ は λ=1 identity で載せる vs manifest eligible=False。two_gamma ADOPT ×
  stage-λ REJECT の混在扱い]**。
- **決定論・content-addressing(憲法 V)**: 同一 bundle + 同一 code_sha → 同一 manifest_digest。
  golden-section/grid の決定論・full 精度(丸めない)。
- **fit 順序(D4/engine 整合)**: stage-λ は two_gamma 校正後 p で fit。manifest には両生 params を載せ、
  activation 時の適用は engine の two_gamma→stage 順。
- **スキーマ不変(憲法 VI)**: DB/migration/API 不変。manifest は disk artifact + 既存 build_manifest schema
  v2(076 で拡張済)。新テーブルなし。

## スコープ外
- 076 の activation 経路変更(078 は生成のみ・076 は読込側で完成)。
- 077 registry(save_model_version 上書き廃止 + loader checksum enforcement)。
- 新 model 学習・昇格・active 書換(078 は既存 lgbm-063 の校正を OOF で正しく測るだけ)。
- production 既定 ON への昇格(78 で real manifest を検証後、別途 operator 判断)。

## 憲法整合
- II(OOF strict-past・リーク境界・派生値非還流)/III(OOS 事前登録ゲート・過去 verdict 不変)/
  IV(Σ 整合・win 不変)/V(content-addressed・append-only・決定論監査)/VI(契約先行・スキーマ不変)。

## Open Questions(codex 設計レビューで解決予定)
1. 出荷 params = prequential last_params か 全 fold 再 fit か(→ fit_through 意味論)。
2. activation_eligible の verdict 組合せポリシー(REJECT stage-λ の扱い)。
3. stage-λ の per-stage valid-label 定義(top2/top3 の complete-field 要件・dead-heat)。
4. two_gamma と stage-λ の eval 母集団整合(同一 fold 分割・同一制限母集団)。
5. 実 manifest 生成後の 076 再検証項目(stage-λ の production pl_topk 確認要否)。

## codex 設計レビュー
`scratchpad/codex_078_out.txt`(gpt-5.6-sol・並走中)。指摘は research.md に採否記録。
