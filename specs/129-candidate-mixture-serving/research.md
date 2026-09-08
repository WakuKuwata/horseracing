# 129 Design Research

- Decision: artifact-only six-member candidate, separate shadow CLI. Reason: legacysave_model_version commits immediately and standardload_serving_model intentionally rejects arbitrary125 subsets. New profile checks actual columns/meaning without changing old compatibility. Independentdriver/designreview agree.
- Decision: preserve125/118 saved2026 coefficient vectors. Reason: this matches the retained configuration and avoids selecting a refreshedfit based on2026 scores. Final base models use all frozen111 data through2026-08-23; coefficients explicitly retain their through2025 fit window. New model equality with old2026 boosters is not claimed. Gapreview proposed refreshedannualheldfit as an alternative; deferred as a separately identified candidate.
- Decision: strict same-ID started history, D2<D1<target date, no result-label filter for priorgap. Evidence:111 has one result-less2025 race in its matrix but outsideEvalRace; prediction history must not silently drop it. Currentgap retains existingfeature definition; leap/sex terms match119.
- Decision: eachbase OOFcalibrated p is clipped/normalized once; q corrected theneps0 assembly; six3heads arithmeticmean in fixedorder. No averagewin→Harville, extra calibration or stage discount. Bothreviewers identify this as essential125 parity.
- Decision: primarypreweight shadow with full/partial rehearsal. Existingserving drops allrace same-dayweights when partly observed. Prior full-information research does not establish preweight performance. User preference asked asynchronously; default documented.
- Decision: freeze actual anchor display-calibration for confirmation. Existingserving defaults runtime stage discount; raw138 research baseline lacks it. Replacing anchor with researcher-convenient uncorrected probabilities would change the product comparison.
- Decision: ensemble confirmation schema alongside112 sharedledger, requiredzeroeffect/98.75%CI/annual8budget unchanged. Current112 single-selectedseedtemplate cannot honestlyrepresent sixmembers. Member seeds are not independentbundle repetitions; no divisionby6 or old7fold transfer. Finalpower/window must be justified from intendedregime evidence. Registration stayspending if that evidence is missing.
- Cost: sixnewouter/48boosters; old2026 jobs ~982–1046sec each, peakworker~8.5GB; estimated2worker50–60min excludingexport/parity. Actualresults storedafterexecution.

## 2026-09-08 実装段階の追加判断(Claude)

- Decision: anchor の stage 割引 λ は `fit_product_stage_discount(before_date=2026-08-24)` で一度だけ fit し `artifacts/129-candidate-mixture-serving/anchor/stage-discount.json` に凍結(λ2=0.85839・λ3=0.71566・n2=9170・n3=9143)。Reason: 本番は対象日より前の永続化予測から毎回 fit するが、確認の比較相手は固定物である必要がある(contracts/shadow-confirmation)。as-of を bundle 締切翌日にすると、締切後 4 開催日の rehearsal(8/29・8/30・9/5・9/6)にも将来窓にも λ の fit 標本が先行し、どちらにも結果が漏れない。実運用の λ との差は fit 標本 9,170 レースに対し数十レースの差で無視できる。win は λ に依らず不変。
- Decision: shadow の主条件 preweight は候補・anchor とも同一の `prepare_race_inputs` 行(同日体重 3 列を NaN)を渡し、anchor は `predict_race` にその行を与える。両者の入力 SHA は同一オブジェクトから取るので構造的に一致し、確認の `candidate_input_sha256==anchor_input_sha256` は定義から成立する。
- Decision: bundle 組み立ては `scripts/candidate_mixture_bundle.py` に分離。Reason: build freeze が `candidate_mixture_build.py` の digest を pin するため、同ファイルへの追記は freeze を無効化する。
- Decision: 検出力計画の development evidence は締切後 4 開催日の rehearsal(bundle にとって真の OOS・144 レース)から `mixture_shadow develop` で作る。研究 125 の年別モデル replay は booster が別物なので serving regime の day-SD の代理にしない。4 日は薄いことを NOT_READY の理由候補として明記する。
- codex unavailable: usage limit(2026-09-15 10:24 まで)。stdin 未閉鎖(`Reading additional input from stdin`)と `--skip-git-repo-check` 不足の 2 回の起動失敗を経て 3 回目で上限エラー。独立レビュー(`reviewer_role: independent`)を要する confirmation の evidence は本セッションでは作れないので、preflight は NOT_READY のままにする(偽の review を書かない)。
- Self-review checklist(codex 代替):
  1. リーク: 候補・anchor の入力は `build_feature_matrix(end_date=race_date, target_race_ids)` の as-of 行のみ。prior_gap の履歴は race_horses の started 行を対象日より厳密前に絞る(`history_for`・結果列を持たない)。λ は 8/24 以前の予測から fit。prospective は post_time>now かつ結果 0 行でしか受理しない。
  2. パリティ: 保存前後の一致は build の serialization parity(6 member・probe 全年)、旧研究演算の再現は annual-serving-parity(23,030 レース・1e-15)。新モデルと旧年別モデルの一致は主張しない。
  3. 契約: DB 書込ゼロ(`SET TRANSACTION READ ONLY` を session 冒頭で発行)・active 登録なし・記録は排他作成(append-only)。
  4. 冪等: 同一レース・同一分類の再記録は FileExistsError。bundle/anchor/compatibility は上書き不可。
  5. 未検証の残余: rehearsal は結果確定後の DB 状態で as-of を組むので「発走前の DB 状態」そのものではない(entries の訂正・馬主修復等の遡及は含みうる)。prospective 記録は将来レース登録後にしか作れない。
