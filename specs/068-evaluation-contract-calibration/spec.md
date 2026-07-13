# Feature Specification: 評価契約の是正 + 校正分割の見直し

**Feature Branch**: `068-evaluation-contract-calibration`

**Created**: 2026-07-12

**Status**: Draft

**Input**: [モデル予測精度向上 提案書](../../docs/plan/model-accuracy-improvement-proposal.md) Phase 0 + Phase 1

## 背景・目的

現行の採用判定には2つの構造的欠陥がある。

1. **評価契約の欠陥**（Phase 0）: (a) trainingはstarted全馬（DNF・失格をwin=0）で学習するのに、evaluationはfinished馬だけを採点している（母集団不一致）。(b) `train-evaluate` は候補を現DBで再評価するが、baselineは `model_versions.metrics_summary` の保存値を読むため、backfill・materialize・母集団変更があると同一race集合でのpaired比較にならない。(c) 020/023/039 で繰り返した「worst-fold ECE blip で機械ゲートがFalse・meanは改善」というゲート摩擦、および060の「expanding-window初期foldアーティファクトで毛差FAIL」を、統計的信頼区間と直近窓ガードで解消できていない。

2. **校正分割の非効率**（Phase 1）: `DEFAULT_CALIB_FRAC = 0.3`（[calibration.py:22](../../training/src/horseracing_training/calibration.py)）により、各walk-forward foldの学習期間を古い70%のmodel-fitと**最新30%**のcalibration-fitに分割している。LightGBM本体はmodel-fit側だけを学習し、**最も新しい277,841行を学習していない**（現active lgbm-062: model-fit 673,561 / calib 277,841）。この最新期間こそserving対象に近い分布であり、boosterに返せれば追加データ・新特徴なしで精度が上がりうる。

本featureは、上記2点を「新しい物差し（Phase 0）を作り、それで校正分割実験（Phase 1）の限界効果を測る」順で解決する。**特徴量・目的関数・seedは固定**し、評価方法と校正・学習データ配分だけを変える。

**スコープ外**（提案書の後続Phase）: pl_topk対応HPO（Phase 2）、過去オッズ特徴（Phase 3）、スピード指数v2（Phase 4）、恒久欠損bundleの撤去判断（Phase -1）。これらは本featureが確立する評価契約を前提に、別specで行う。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 評価契約の是正（Priority: P1）

モデル研究者として、0.0001級の改善を誤採用せず、以後の全実験を同じ物差しで比較したい。そのために評価を「finished-only・非paired・単一点推定」から「started-all母集団・race-level winner NLLをPRIMARYとするpaired比較・block bootstrap信頼区間」へ是正する。

**Why this priority**: これが無ければPhase 1以降の全実験の合否が信用できない。物差しの是正は他のすべての前提であり、単独で価値がある（既存モデル同士の再評価だけでも「本当にlgbm-062はlgbm-061に勝っていたか」をserving母集団で検証できる）。

**Independent Test**: 既存の2モデル（例 lgbm-062 と lgbm-061）を同一DB・同一race集合・同一fold境界でpaired評価し、race単位の損失差とその95%信頼区間、winner NLL・started-all LogLoss/Brier・finished-only（互換）を1レポートに出せることで検証できる。

**Acceptance Scenarios**:

1. **Given** 候補とactiveの**ModelRecipe**（objective/calibration/features/seed/TE/calib_frac）、**When** paired評価を実行、**Then** 両者を**各outer foldで再fit**し（保存artifactは全履歴fitのserving modelでありwalk-forward適用はin-sample=使わない、codex C1）、同一DB/source fingerprint・同一materialized manifest・同一race_id集合・同一fold境界・同一評価コードversionでouter-validを一度だけ予測し、race単位のpaired loss差を出す。baselineの保存値は読まない。
2. **Given** race-softmaxモデルの予測、**When** PRIMARY指標を算出、**Then** race-level winner NLL（1レース1標本の `-log(p_winner)`）を主指標とし、started-all LogLoss/Brier（DNF・失格を0として含む）とfinished-only（過去互換）を併記する。
3. **Given** 各fold・全馬のpaired loss差、**When** 信頼区間を算出、**Then** moving-block bootstrap（block = 開催日）で95%信頼区間を出す（i.i.d.シャッフル禁止・seed記録）。
4. **Given** paired差レポート、**When** 期間別に集計、**Then** 全期間と直近期間（3年/5年）を分けて報告する。
5. **Given** 予測の校正、**When** ECEを算出、**Then** 固定10等幅ECEに加え、equal-mass ECE・確率帯別・頭数別校正を報告する。
6. **Given** uniform baseline、**When** 採用判定、**Then** uniformはsanity checkとして残すが、active昇格の比較対象にはしない。

---

### User Story 2 - 校正分割と全履歴学習の比較（Priority: P2）

モデル研究者として、最新30%をLightGBM本体の学習から外している構造を見直し、booster学習量（とりわけ直近期間）を増やせるかを、US1の評価契約の下で測りたい。

**Why this priority**: 追加データ・新特徴なしで直近学習データを増やせるため費用対効果が最も高い。ただしUS1の物差しが前提なのでP2。

**Independent Test**: 特徴量・目的関数・seedを固定した同一スナップショット上で、下表A〜Dの限界効果を直近foldで比較し、勝ち候補をフルwalk-forwardへ進め、現activeとpaired評価できることで検証する。

| ID | Booster学習 | 校正データ | 校正方式 |
|---|---|---|---|
| A | 古い70% | 最新30% | isotonic（現行） |
| B | 古い90% | 最新10% | isotonic |
| C | 全履歴refit | 時系列OOF予測 | temperature |
| D | 全履歴refit | 時系列OOF予測 | race-normalized power |

**Acceptance Scenarios**:

1. **Given** 固定スナップショット、**When** A〜Dを実行、**Then** 特徴量・目的関数・seedが4条件で同一であり、校正と学習データ配分だけが異なる。
2. **Given** C/DのOOF校正、**When** OOFモデルと全履歴refitモデルのraw score分布を比較、**Then** 校正パラメータの移植可能性をtrain内validで確認し、分布ミスマッチで悪化する場合はBへフォールバックする。
3. **Given** 校正方式の選択、**When** 各外側foldで方式を決定、**Then** 外側validを見ず各foldのtrain内だけで方式（identity/isotonic+race-norm/temperature/race-normalized power/two-gamma）を選ぶ。
4. **Given** 校正方式選択・transfer-check・計算量screening、**When** de-risk、**Then** これらは各outer foldの**inner-validのみ**で行い（outer-validを見ない、codex C2）、screeningに使ったfoldを最終判定CIに含めない（独立confirmation window）。同じfoldでの勝者選択と最終判定の二重使用は憲法III違反。
5. **Given** 校正後の確率、**When** race-softmaxモデルへ適用、**Then** 順位を壊さずレース内合計1を保つ（既存のisotonic+race normalizationと整合、IV）。

---

### User Story 3 - 学習期間provenanceの記録（Priority: P3）

運用者として、どのboosterがどの期間・行数を実際に学習したかを再現可能に記録したい。現行の `train_through` は全training frameの最大日であり、boosterが実際に学習した最終日とは限らない。

**Why this priority**: A〜D実験の帰属と再現に必要だが、US1/US2の判定ロジック自体は動く。憲法Vの監査強化。

**Independent Test**: 学習後、metadataに `model_fit_through` / `calib_from` / `calib_through` / `n_model_rows` / `n_calib_rows` が記録され、`train_through` と `model_fit_through` が校正分割時に異なる値になることを確認する。

**Acceptance Scenarios**:

1. **Given** 校正分割ありの学習、**When** metadataを書く、**Then** `model_fit_through`（booster実学習の最終日）と `calib_from`/`calib_through`（校正データ期間）を個別に記録する。
2. **Given** 全履歴refit（C/D）、**When** metadataを書く、**Then** `model_fit_through` がcalib期間を含む全履歴の最終日になる。
3. **Given** 既存モデル行、**When** 遡及、**Then** 既存行は遡及書換せず（040 importance / 050 train_through 同型）、次回学習からpopulateする。

### Edge Cases

- 校正slice が退化（単一class / 行数不足）した場合、既存の identity-with-clip フォールバック（[calibration.py](../../training/src/horseracing_training/calibration.py)）を維持する。
- 直近3年/5年に該当raceが存在しないfoldでは、直近ガードを「該当期間なし」として報告し、非劣化判定から除外する（黙って合格にしない）。
- winner NLL は「1レースに勝者がちょうど1頭」の前提。同着（dead heat）・勝者不在（全馬DNF）・結果未確定レースは winner NLL の母集団から除外し、除外件数を surface する。
- started-all Brier/LogLoss で「取消（cancel）」馬はそもそもstartedでないため母集団に含めない（started の定義は既存 eval/dataset の entry_status に従う）。
- 「部分取込（partial ingest）」レース＝出走馬の一部行や結果が欠ける取込不完全なレースは、winner-NLL-eligible（勝者ちょうど1頭）の判定に必要な情報が揃わないため winner NLL 母集団から除外し件数を surface する。started-all では entry_status が確定している行のみ母集団に含める（欠損行は含めない）。
- block bootstrap で開催日数が極端に少ない直近窓では、CIが広くなることを許容し、点推定だけで採否しない。

## Requirements *(mandatory)*

### Functional Requirements

**評価契約（US1）**

- **FR-001**: システムは race-level winner NLL（1レース1標本の `-log(p_winner)`）をPRIMARY指標として算出しなければならない。
- **FR-002**: システムは started-all LogLoss/Brier（DNF・失格をwin=0として含む）を算出し、finished-only指標を過去互換として併記しなければならない（started-all は診断報告であり、分布ゲートの primary は winner NLL＝started-all はゲート条件ではない、analyze N1）。
- **FR-003**: システムは候補とactiveを**ModelRecipeから各outer foldで再fit**し（保存artifactを過去raceに適用しない=in-sample回避、codex C1）、同一DB/source fingerprint・同一materialized manifest・同一race_id集合（race_id hash一致）・同一fold境界・同一評価コードversionで**同時評価**し、race単位のpaired loss差を出さなければならない。baselineを `metrics_summary` の保存値から読んではならない。race集合はmodel-blindに先に固定し、片側の予測欠落はrace除外でなくcontract failure（fail-closed）とする（codex C8）。
- **FR-004**: システムは開催日単位の**moving-block bootstrap**（1開催日=1ブロック、B回 resample、95% percentile CI）でpaired差の信頼区間を算出しなければならない（i.i.d.シャッフル禁止・serial order保存・seed記録、D2）。
- **FR-005**: システムは全期間と直近期間（3年および5年）を分けて指標を報告しなければならない。
- **FR-006**: システムは固定10等幅ECEに加え、equal-mass ECE・確率帯別校正・頭数別校正を報告しなければならない。
- **FR-007**: uniform baselineはsanity checkとしてのみ算出し、active昇格の比較対象にしてはならない。

**採用ゲート（US1が定義、US2/後続specが使用）**

- **FR-008**: 採用ゲートは次を満たすとき合格とする。(a) PRIMARY: candidateのwinner NLLがactiveより小さい。(b) 統計ガード: paired差の95%信頼区間上限が0未満。(c) 直近ガード: 直近3年**かつ**5年の**両窓で非悪化**（どちらか一方でも悪化したら不合格＝保守的 AND、analyze C2 で確定。060 の直近regime悪化を確実に捕捉）。(d) top2/top3: 事前固定したnon-inferiority幅以内。(e) 校正: **mean-ECE が active 比 non-inferiority幅以内**（worst-fold ECE 単発 blip では否決しない＝020/023/039 で本feature が消そうとしている摩擦源。worst-fold は監査報告のみ・ゲート条件にしない）。絶対ECE 0.05は非常停止用の上限に格下げする。幅は T001 gate-config に OOS 前固定。
- **FR-009**: ゲートの閾値・non-inferiority幅・bootstrap seed・fold境界は、OOS結果を見る前に固定しなければならない（憲法III）。

**校正分割（US2）**

- **FR-010**: システムは A（70/30 isotonic 現行）・B（90/10 isotonic）・C（全履歴refit + OOF temperature）・D（全履歴refit + OOF race-normalized power）を、特徴量・目的関数・seedを固定した同一スナップショット上で比較できなければならない。
- **FR-011**: C/Dでは、OOFモデルと全履歴refitモデルのraw score分布の移植可能性をtrain内validで確認し、悪化する場合はBへフォールバックしなければならない。
- **FR-012**: 校正方式の選択は各外側foldのtrain内だけで行い、外側validを見てはならない（035/036 selection-leak前例）。**FR-014 の inner-valid screening 規律の下位明確化**であり、実装は T027 の1経路で両者を満たす（analyze D1）。
- **FR-013**: 校正後の確率はレース内で順位を壊さず合計1を保たなければならない（憲法IV、009整合）。
- **FR-014**: 校正方式選択・transfer-check・screeningは各outer foldの**inner-valid**で行い、screeningに使ったfoldを最終判定CIに含めてはならない（独立confirmation window、codex C2）。A–D screening の go/no-go 基準（inner-valid winner NLL のマージン・NO_DECISION の CI 扱い）も採用ゲートと同様に **OOS前に gate-config へ事前登録**する（analyze U1, III）。
- **FR-014a**: C/Dの校正フィット用OOF予測は**expanding strict-past**（各行 `max(train_date) < prediction_date`）で生成しなければならない（現行 `oof_target_encode` の held-fold 方式を流用しない、codex C6）。
- **FR-014b**: model-fit / calib の時系列分割は**開催日（race_date）単位**で行い、同一開催日が両側に跨ってはならない（bootstrap 単位と整合、codex C4）。A（現行70/30再現）検証のためrace数ベース分割はテスト専用に残す。

**provenance記録（US3）**

- **FR-015**: 学習は `model_fit_through` / `calib_from` / `calib_through` / `n_model_rows` / `n_calib_rows` を metadata に記録しなければならない。既存モデル行は遡及書換しない。

**境界（全体）**

- **FR-016**: 評価で算出した指標・reliability・paired差・bootstrap CIは、モデル特徴に一切戻してはならない（憲法II leak boundary・leak-guard test）。
- **FR-017**: 本featureはDBスキーマを変更せず、APIを変更せず（read-only 014不変）、migrationを追加しない。metadataは既存 `metrics_summary` JSONB内で完結する。
- **FR-018**: 本featureはFEATURE_VERSION・feature_schema（列名/列順/特徴コード）を変更しない。**model artifact の bit-parity は要求しない**（A/B/C/Dは calib_frac/学習配分を変えるため booster と TE encoder 母集団が変わるのは実験の意図。`feature_hash` は列名のみのhashで値の同一性を証明しない、codex C5）。hash契約は6種に分離する（data-model §3・research D7 C5 と一致）: `feature_schema_hash`/`raw_matrix_content_hash`（全arm同一）、`model_race_set_hash`/`calib_race_set_hash`（arm別の race 分割）、`transformed_matrix_hash`/`model_artifact_hash`（arm別・within-arm再実行で `num_threads=1` 時一致）。
- **FR-019**: 068対象のModelRecipeは **`market_offset=false` を fail-closed で要求**しなければならない（`market_offset=true` は対象race自身のoddsを読むため、提案書§2.2の境界違反、codex C3）。
- **FR-020**: D（race-normalized power）採用時の二重校正方針は **「新p分布で refit」に固定**する（analyze U1 で確定）。すなわち D の校正は serving 段の win-p に適用し、betting product 経路の後段校正（046/048 `_fit_product_p_calibrator`）は**既に D 校正済みの永続化予測に対して strictly-before で refit する**（現行 046 の動的フィット挙動そのまま＝スタックせず自己補正、evaluation p == serving p）。049 harville stage discount（top2/3 表示）は別目的のため対象外。**本feature では方針確定のみ**（betting 側実装は D 採用後の別 spec）。これは decision-only の要件であり、本feature内に検証タスクはなく方針記述の存在で充足する（analyze Cov1）。codex C7。

### Key Entities *(include if feature involves data)*

- **PairedEvalReport**: 候補とactiveのpaired評価結果。属性: race_id集合hash、fold境界、winner NLL（候補/active/差）、started-all LogLoss/Brier、finished-only（互換）、期間別（全/直近3年/5年）、equal-mass/確率帯別/頭数別ECE、block bootstrap CI（seed込み）、ゲート判定（各条件の合否と理由）。特徴には流入しない。
- **CalibrationSplitExperiment**: A〜Dの実験条件と結果。属性: 実験ID、booster学習配分、校正データ源（train holdout / 時系列OOF）、校正方式、固定seed/特徴version/目的関数、raw score分布移植チェック結果、直近fold go/no-go、フルwalk-forward結果（PairedEvalReport参照）。
- **TrainingProvenance**: metadata拡張。`model_fit_through` / `calib_from` / `calib_through` / `n_model_rows` / `n_calib_rows`。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 既存2モデル（lgbm-062 vs lgbm-061）を同一race集合でpaired評価し、winner NLL・started-all・finished-only・block bootstrap CIを含む1レポートを再現可能に生成できる（lgbm-062/063 は recipe 共有＝paired 再fit で等価、061 は歴史的比較対象。本 demo は物差しの再現性検証が目的で active 昇格はしない）。
- **SC-002**: 同一スナップショット・同一seed・**単一スレッド固定**で評価を2回実行し、winner NLL・paired差・bootstrap CI の絶対差が **`< 1e-9`**（LightGBM 非決定性を吸収する単一 pass 条件）以内で一致する（決定論）。この閾値は gate-config に記録する。
- **SC-003**: A〜Dの校正分割実験を直近foldで比較し、各条件のwinner NLLとgo/no-go判定を出力できる。少なくとも1つの構成（B/C/D）が現行A（70/30）に対して直近窓winner NLLで非劣化かどうかを、CI付きで判定できる。
- **SC-004**: 採用ゲート（FR-008）の全条件がコードで機械判定され、閾値・seed・fold境界が実行前に固定されたartifactとして残る。
- **SC-005**: 学習後のmetadataに `model_fit_through` 等5項目がpopulateされ、校正分割時に `train_through` と `model_fit_through` が異なることを確認できる。
- **SC-006**: leak-guard test が緑（評価派生値がモデル特徴に流入しない）。DBスキーマ・API・OpenAPI・FEATURE_VERSION・**`feature_schema_hash`（既存の列名 hash＝列名/列順の不変、model_artifact_hash は arm 別で変わるのが正常）** が不変。

## Assumptions

- 評価は既存の walk-forward OOS 基盤（`eval/harness.py` の `evaluate` / `reliability_bins`、`splits.py` の fold 境界）を拡張して行う。winner NLL・equal-mass ECE・block bootstrap・paired 比較は新規追加だが、fold 生成と予測経路は既存を再利用する。
- eval は predictor-agnostic を維持する（`eval/` は `training/` に依存せず、`LightGBMPredictor` は CLI が注入する。020 で確立した循環回避）。
- started/finished の母集団定義は既存 `eval/dataset.py` の entry_status に従い、本featureで新たなDB読み取り経路を足さない。
- C/D の全履歴refit・OOF生成は計算コストが大きい（pl_topk フル学習は perf 改善後で ~20分/回、[perf-training-eval-speedup]）。直近foldでのsuccessive halving 的 de-risk を先に行い、フル walk-forward は勝ち候補のみに限定する。
- 初回baselineは **DB の `adoption_status='active'` モデル**（`--active db-active` が解決する実モデル）とする。実 active は **lgbm-063**（lgbm-062 と同一 recipe＝features-017/pl_topk+isotonic/n_model_rows 673,561 を、weights_uri 絶対パスで再学習したもの、[weights-uri-relative-path-ops-bug]。win LogLoss ≈0.214886）。lgbm-062/063 は recipe 共有のため paired 再fit では等価。A〜Dはこの実 active と同一スナップショットで比較する。実装時に DB の active を確認して baseline を確定する。
- 対象レース自身のオッズ・結果・人気は評価入力にも特徴にも使わない（憲法II・提案書 §2.2 の境界）。paired評価のwin labelは結果だが、これは採点専用であり特徴経路には流れない（既存の leak boundary と同じ規律）。
- codex second-opinion は plan フェーズで取得済み（codex-rescue agent は起動不可だったが親から `codex exec` 直叩きで取得）。correctness-critical 4点（C1–C4）+ 設計明確化6点を採用し spec/plan/data-model/contracts/research D7 に反映済み。

## 憲法チェック

- **II（リーク境界）**: 評価派生値（winner NLL・reliability・paired差・CI）を特徴に戻さない（leak-guard test）。win label は採点専用で特徴非流入。対象レース市場・結果は特徴に使わない。
- **III（事前登録ゲート）**: 閾値・non-inferiority幅・seed・fold境界・screening基準をOOS前に固定。直近foldはgo/no-goのみ。OOS結果を見た後の条件変更禁止。**baseline 比較要件は candidate-vs-active paired（active は人気順より強い baseline）で充足**、市場q比較は製品目的どおり SECONDARY 診断（analyze Con1）。
- **IV（確率整合）**: 校正後もレース内 Σ=1・順位保存（009整合）。
- **V（監査）**: provenance 5項目記録・bootstrap seed記録・実験条件artifact。既存行は遡及しない。
- **VI（契約）**: スキーマ・API・OpenAPI・migration 不変。metadata は既存 JSONB 内。

## 関連

- [モデル予測精度向上 提案書](../../docs/plan/model-accuracy-improvement-proposal.md)（Phase 0/1 の正本）
- [モデル特徴量 再制定書](../../docs/plan/model-feature-redesign.md)（後続 Phase -1/3/4 の特徴契約）
- [校正分割 calibration.py](../../training/src/horseracing_training/calibration.py)
- [学習・校正結線 predictor.py](../../training/src/horseracing_training/predictor.py)
- [評価 harness.py](../../eval/src/horseracing_eval/harness.py) / [metrics.py](../../eval/src/horseracing_eval/metrics.py)
- 前例: 060（expanding-window 初期foldアーティファクトで毛差FAIL=直近窓ガードの動機）、020/023/039（worst-fold ECE blip のゲート摩擦=CI/直近ガードの動機）
