# Codex 設計レビュー — feature 074 (OOF-faithful Calibration)

**取得**: 2026-07-16 `codex exec --sandbox read-only`。実コード読解に基づく。抜粋(§1〜必須テスト)。

- betting/API jointのλ=1維持
- 既存PredictionRun/Recommendation不変
- manifest改竄・partial publish・未知schema・並行生成の拒否
- 同一payloadの冪等成功、同一key異内容のconflict

要約すると、**schema-zero は可能、persisted-run 再利用は不可能**です。074は OOF bundleと最小manifestまでを持ち、製品activationとリポジトリ全体のartifact registry化を後続へ分けるのが最も安全です。
tokens used
230,186
## 結論

判定は **Request Changes** です。暫定方針の中心は正しいですが、次の4点を spec 前に修正すべきです。

1. `base_model_version` 絞り込みは必要条件であって、OOF の証明にはならない。
2. 「最終確率」は1種類ではなく、表示・推薦・API joint ごとに別契約。
3. OOF provenance の manifest は074に必須だが、リポジトリ全体の base artifact create-only 化は分割推奨。
4. 048 の two-gamma 採用根拠も persisted prediction 由来なので、OOF で再検証が必要。

独立 second opinion も同じ結論でした。

## 1. OOF-faithful sample

「新しい外部データなし・DB migration なし」は可能です。ただし、recipe から再生成した OOF prediction という新しい派生 artifact は必要です。

既存 persisted prediction の再利用は不可です。

- 保存 artifact は full-history serving model であると明記されています。[artifacts.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/artifacts.py:8)
- 正しい評価経路は、保存 booster を使わず fold ごとに再 fit します。[foldfit.py](/Users/kuwatawaku/workspace/horseracing/eval/src/horseracing_eval/foldfit.py:1)
- `prediction_runs` には `model_version` はありますが、fold・recipe hash・model fit cutoff・artifact digest・OOF 種別がありません。[prediction.py](/Users/kuwatawaku/workspace/horseracing/db/src/horseracing_db/models/prediction.py:61)
- `base_model_version='lgbm-063'` で絞っても、「063を2026年まで学習して2024年へ backfill」した run は排除できません。

推奨構成は次です。

```text
固定DB/source snapshot
  → 完全な base recipe
  → fold別 strict-past 再学習
  → immutable OOF prediction bundle
  → prior OOFだけで two-gamma / stage λ を prequential fit
  → strictly-later OOF foldで評価
  → 評価完了後に production用 final transform artifactを作成
```

OOF bundle は `prediction_runs` ではなく content-addressed disk artifact が適切です。DBに入れると、

- active model の最新 run としてAPIに露出し得る
- `_has_run_for_model` により serving backfill が「生成済み」と誤認する
- fold modelごとに ModelVersion を作るとモデルセレクタを汚染する

という問題があります。[selection.py](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/selection.py:21) [pipeline.py](/Users/kuwatawaku/workspace/horseracing/serving/src/horseracing_serving/pipeline.py:289)

`_latest_run_predictions(..., base_model_version)` の修正自体は defense-in-depth として行うべきですが、OOF bundle の正本には使わない方が安全です。また、「latest」ではなく manifest に固定した `prediction_run_id` を読むべきです。

### recipe-faithful の追加穴

現 `ModelRecipe` は完全な再現 recipe ではありません。resolved booster params、feature version・順序付き列、HPO条件などが不足しています。[recipe.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/recipe.py:34)

lgbm-063 metadata には params、seed、TE、feature version等がありますが、`calib_frac` や legacy split の完全な記録はありません。[metadata.json](/Users/kuwatawaku/workspace/horseracing/artifacts/model_versions/lgbm-063/metadata.json:472)

したがって074には、legacy lgbm-063 recipe の明示 attestation と以下が必要です。

- resolved LightGBM params
- objective/postprocess
- ordered feature columns、feature_version
- TE列・smoothing
- internal calibration method・fraction・split unit
- seed・thread設定
- drop list
- source/materialized snapshot hash
- code SHA

## 2. serving parity の線引き

現行には、単一の「final probability」はありません。

| 出力面 | 074後の契約 |
|---|---|
| `race_predictions.win_prob` / API `horses[].win` | **バイト不変** |
| persisted/API `top2/top3` | 新しい表示用 λ で、新規 run の値は変更可 |
| win recommendation | two-gamma変更により、選択・pseudo odds/ROI・Kelly stake変更可 |
| exotic recommendation | two-gamma由来値は変更可。stage discountは既定 λ=1 を維持 |
| API `?bet_type=` joint | raw persisted winからλ=1で再計算する現行契約を維持 |
| `race_dispersion.model_delta` | 066 calibratorを作り直すなら変更可 |

two-gamma は prediction serving の `win_prob` には入っておらず、推薦生成時だけ適用されています。[betting cli.py](/Users/kuwatawaku/workspace/horseracing/betting/src/horseracing_betting/cli.py:181) [recommend.py](/Users/kuwatawaku/workspace/horseracing/betting/src/horseracing_betting/recommend.py:90)

stage discount は serving で既定ONですが、winを触らず persisted top2/top3だけを変えます。[pipeline.py](/Users/kuwatawaku/workspace/horseracing/serving/src/horseracing_serving/pipeline.py:85) [predictor.py](/Users/kuwatawaku/workspace/horseracing/serving/src/horseracing_serving/predictor.py:88)

したがって SC は次のように書き分けるべきです。

- `model_internal_win_parity`: byte-identical
- `display_topk_parity`: evalとservingで同一、値の更新は許可
- `betting_two_gamma_parity`: evaluatorと推薦純関数が一致
- `betting_joint_identity_stage_parity`: betting stageは既定λ=1
- `api_joint_legacy_parity`: API jointもλ=1維持

既存 PredictionRun/Recommendation は更新せず、新 manifest で作った新規 run だけ値を変える契約が安全です。

## 3. manifest

073 の legacy freeze を「legacy import の起点」として使うのはよいですが、その単純拡張だけでは不足します。

現 freeze は model/calibrator/preprocessor の3ファイルしか固定しません。[legacy_freeze.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/legacy_freeze.py:19)  
一方、serving は `metadata.json` の objective、feature version/hash、TE、market offset等にも依存します。[model_loader.py](/Users/kuwatawaku/workspace/horseracing/serving/src/horseracing_serving/model_loader.py:174)

最低限、以下を含めるべきです。

- schema/version、artifact kind
- base model versionと model/calibrator/preprocessor/**metadata** checksum
- 完全な resolved recipe hash
- feature_version、ordered-column hash、source fingerprint、materialized content hash
- entry/start population hash、result hash
- foldごとの train/valid race set hash、`train_through`、生成 model digest
- OOF race集合・prediction checksum
- probability stage名と変換順序
- two-gamma / stage λ の完全精度params、fit race hash、fallback
- code SHA、dirty-tree hash、seed、threads、主要依存version
- 最終出力 checksum

特に `logic_version` は gamma/λを小数5桁へ丸めています。実計算は丸め前の値なので、`logic_version` だけでは byte 再現できません。完全精度paramsを manifest に置き、DBには manifest digestを残すべきです。

create-only 契約は以下です。

- canonical payloadが同じ → 同digest、冪等成功
- 同じ論理keyで異なる内容 → fail-closed
- 一時directoryへ完全生成後にatomic publish
- checksum不一致、未知schema、世代不一致 → load前に拒否
- wall-clock時刻と自己digestはcontent hash対象外
- identity fallbackも明示artifact

### DB migration

artifact正本を disk/object store にし、digestを `logic_version` または deployment configで固定するなら migration は不要です。

ただし「DB内だけで provenance を型付き・参照整合付きに管理する」ことまで求めるなら、現スキーマでは不足します。schema-zero は可能ですが、整合性の担保を validated manifest側へ移す設計になります。

## 4. スコープ分割

推奨は3段階です。075が予約済みなので番号は例です。

### 074: OOF-faithful Calibration Evidence

- legacy recipe attestation
- strict-past OOF bundle生成
- OOF/calibration用の最小 content-addressed manifest
- two-gammaのOOF再検証
- display stage λのOOF fit/eval
- calibrated-stage ECE
- production結線なし

### 076: Probability Pipeline Activation & Parity

- 推薦がimmutable two-gamma artifactを読む
- servingがimmutable display-stage artifactを読む
- allowed-change matrix
- new-run/backfill/idempotency方針
- eval↔serving/API parity

### 077: Global Content-addressed Model Registry

- `save_model_version` の上書き廃止
- atomic publish
- loader checksum enforcement
- DB pointer/promotion lifecycle

現 `save_model_version` は同一directoryを上書きした後、DBも `ON CONFLICT DO UPDATE` します。[artifacts.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/artifacts.py:125) これは校正リーク是正とは別の、より広い artifact lifecycle変更です。

最小 OOF manifestまで074から外すのは反対です。OOF provenanceはリーク是正そのものだからです。

## 5. 073 FR-007

calibrated-stage ECEは074が所有するのが自然です。

- 074が `evaluation_contract_version=v2` の append-only evaluation artifactを出す
- 073 FR-007はそのartifactへの参照で fulfilled とする
- 073の過去 verdict/resultを上書き・再分類しない
- 073側を更新する場合も、依存解消のリンク追加だけにする

なお main 上の `tasks.md` では、ECE subsetと監査hash等がまだ未完扱いです。[tasks.md](/Users/kuwatawaku/workspace/horseracing/specs/073-eval-contract-correctness/tasks.md:63) 実装状態とtasksがずれているだけか、実際に未完かを074の prerequisiteとして確認すべきです。

## 6. 見落としやすいリスク

- **同日リーク**: single-race推薦は `(date, race_id)` で同日 earlier raceを使い得ますが、backfillは同日全除外です。race_id順では結果利用可能時刻を証明できないため、timestampがない現状では `race_date < target_date` に統一すべきです。[betting cli.py](/Users/kuwatawaku/workspace/horseracing/betting/src/horseracing_betting/cli.py:396)
- **048 verdict**: persisted sampleのOOS provenanceが証明されていないため、two-gamma方式の採用根拠をOOFで再検証する必要があります。結果は ADOPT/REJECT/NO_DECISION のいずれも許容すべきです。
- **066漏れ**: dispersion用 two-gamma artifactも同じ loaderを使っています。[training cli.py](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/cli.py:1055)
- **別 latest loader**: joint calibrationにも世代非限定の実装があります。[calibration.py](/Users/kuwatawaku/workspace/horseracing/probability/src/horseracing_probability/calibration.py:82)
- **OOF→full-history移植**: recipeが同じでも学習量が違うため、確率分布のtransfer checkが必要です。
- **冪等key不足**: recommendationの存在判定はpipeline digestを見ません。新calibratorへ切り替えても既存runでは再生成されません。
- **API切替の隙間**: historical force-run後、recommendation生成前はAPIが新PredictionRunを選び、推薦が一時空になります。
- **feature hash不足**:現hashは列名中心で、同列・値意味論変更を守れません。この限界はloader自身にも明記されています。[model_loader.py](/Users/kuwatawaku/workspace/horseracing/serving/src/horseracing_serving/model_loader.py:180)
- **development reuse**: 2008–2026を再利用するOOF ECEは正しい retrospective evidenceですが、新規confirmatory evidenceとは呼ばない方がよいです。

## 必須テスト

最低限、以下を受け入れ条件にしてください。

- 全OOF raceで booster/internal calibrator/TE/HPO の `max(train_date) < race_date`
- 同日raceをdownstream fitから全除外
- target result変更でそのraceのOOF predictionは不変、result artifact hashだけ変化
- 他model/full-history latest run追加でもOOF bundle digest不変
- recipe field欠落・差異、source/entry/result変更でfail-closedまたは新digest
- OOF生成2回のbyte決定論
- OOF→full-history分布transfer checkとNO_DECISION/fallback
- ECEはtransformのfit sampleではなくstrictly-later OOF blockで測定
- lgbm-063のpersisted win byte parity
- display stageでwin不変、top2/top3だけ期待どおり変化、Σ≈2/3・順位保存
- evaluatorとserving/recommendationの同一入力・同一manifestで完全一致
- betting/API jointのλ=1維持
- 既存PredictionRun/Recommendation不変
- manifest改竄・partial publish・未知schema・並行生成の拒否
- 同一payloadの冪等成功、同一key異内容のconflict

要約すると、**schema-zero は可能、persisted-run 再利用は不可能**です。074は OOF bundleと最小manifestまでを持ち、製品activationとリポジトリ全体のartifact registry化を後続へ分けるのが最も安全です。
