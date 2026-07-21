# Feature 078 research — codex 設計レビュー反映

codex(gpt-5.6-sol・xhigh)結論=「骨格は条件付き採用、現案のまま production eligible 化は不採用」。
以下、各指摘の採否と是正後の設計。**初期 spec に 2 つの実設計バグがあり是正**、加えて **076 に 3 つの実ギャップ**を検出。

## 採用(設計を是正)

### D1【P0-1・最重要是正】stage-λ は raw p で fit(two_gamma 後 p ではない)
**初期案の誤り**: 「stage-λ を two_gamma 校正後 p で fit(049 D4)」は現 076 の実適用と不一致。
現 076 は単一チェーンでなく**分岐**:
- betting/dispersion: raw p → two_gamma
- **serving 表示 top2/top3: raw p → stage discount(two_gamma を通さない)**
- serving win: raw model win 不変 / exotic joint: λ=1
049 research D4 も「serving=raw p、betting=two_gamma 後 p′」と明示。
→ **是正**: 078 の `stage_lambdas` は **serving 用として raw OOF win で fit/eval**。betting で post-two-gamma
λ を将来使うなら `serving_raw` と `betting_after_two_gamma` を**別 params・別 verdict**(manifest v3)。
`probability_stage_order` を普遍チェーンとして記録せず **consumer ごとの pipeline** を記録。
**副次利点**: raw-serving λ なら two_gamma と stage-λ は独立 → verdict matrix(D5)がクリーンに成立。

### D2【P0-2】prequential 評価 params ≠ production params
`prequential_held_out.last_params` は「最終 held-out 年より前」だけで fit された**評価用**値。これを
production params + bundle 末日 fit_through と組むと provenance 不一致、identity fallback 時に古い
nonidentity params が残る恐れ。→ **是正**: 明確に分離:
- `prequential_evaluation`: fold ごと prior-only params + held-out metrics(verdict 用)
- `deployment_final_fit`: verdict 決定後、**全 eligible OOF sample で再 fit**
- manifest `full_precision_params` = deployment final-fit または policy-selected identity のみ
- rejected candidate の fitted params は evaluation 内に保存(監査)

### D3【P0-3】eligibility gate を事前登録フル gate に強化
現 `calibrate_oof` verdict は **ECE 点推定 + margin のみ**=074 gate-config(race-day cluster bootstrap
CI・prob bands・min count・固定 eval window・raw-score KS)も 049 stage gate(top2/top3 LogLoss/ECE・
fold 多数決・worst-fold guard)も未実装で活性ゲートに使えない。→ **是正**: 078 eligibility に最低限:
paired race-day bootstrap CI・top2/top3 ECE **+ LogLoss/Brier 非悪化**・recent/worst-fold guard・
**top2/top3 両方満たす atomic stage verdict**・実採点 held-out days/samples による sufficiency・
transfer check を**結果非依存 statistic**(全馬確率等)に再定義 or raw score を bundle に追加。
- 副次: 現 `n_eval_days` は最初の fit-only fold の日も含む(バグ)→ 実採点日のみに。

### D4【A】dead-heat / label 契約の厳密化
`load_topk_samples_from_oof` は採用。ただし fitting core([stage_discount.py:166])の条件を契約化:
- λ2 fit: **1着 AND 2着が両方一意** / λ3 fit: **1〜3着すべて一意**
- 1着同着 → λ2/λ3 とも除外 / **2着同着 → λ2/λ3 とも除外**(λ3 も無効) / 3着同着 → λ3 のみ除外
- 「該当順位 None」だけでは 2着同着が λ3 を無効にする依存を読み落とす → 明示。
- **fit label ≠ ECE label**: fit=exact 2nd/3rd の条件付き NLL / ECE=**全 started 馬**の複数-positive
  binary `y_top2`/`y_top3`(finishers 限定でなく OOF p_dict 全馬を採点)。dead-heat で positive 数≠k の
  race は stage-k ECE から除外。結果馬が p_dict 外なら silent negative でなく fail/skip 件数記録。
- **リークテスト**: 「held-out 年 Y の結果を変えても Y に適用する γ/λ 不変、Y の metrics と後続 fold
  params だけ変わる」が正しい境界。

### D5【B】fit_through provenance
fold `train_through`(base model 学習証跡)≠ manifest `fit_through`(校正 params/verdict/promotion に
結果が影響した最終日)。全 OOF final refit なら `fit_through=max(final-fit labels 日付)`。top-level:
```
fit_through = max(two_gamma deployment_fit_through, stage deployment_fit_through, decision/eval through)
```
stage 別に `fit_race_set_hash`/`fit_through`/`n_fit` を残す。**content addressing は虚偽 fit_through を
真にしない**(trusted generator の誤操作防止であって自己申告の暗号証明ではない)。

### D6【C】verdict matrix(atomic stage pair)
| two_gamma | stage-λ | params | eligible |
|---|---|---|---|
| ADOPT | ADOPT | fitted / fitted | Yes |
| ADOPT | REJECT | fitted / identity | Yes |
| REJECT | ADOPT | identity / fitted | Yes |
| REJECT | REJECT | identity / identity | Yes |
| any | NO_DECISION | 未解決側 identity | **No** |
| NO_DECISION | any | 未解決側 identity | **No** |
REJECT=「manifest 拒絶」でなく「candidate 拒絶 → evidence-backed identity 選択」。NO_DECISION=証拠不足で
promotion 停止。raw-serving λ(D1)なので two_gamma と stage は独立に評価可(post-two-gamma 依存が消える)。

### D7【D/V】決定論・append-only の穴
「同一 bundle + code_sha ⇒ 同一 manifest」は**現状不成立**(bundle 外の mutable Race/RaceResult 再読込・
mutable latest persisted prediction を transfer reference に使用)。再現条件 = bundle + **frozen
race_date/result/started snapshot** + **frozen transfer reference** + attestation + gate config +
eligibility policy + code/env。必須追加: `calibration_sample_hash`・`result_snapshot_hash`・reference
run IDs/checksum・gate-config hash・policy version。加えて (race_date,race_id)/horse_id 明示 sort・
NaN/Inf 再帰拒否・production で dirty tree/`code_sha=unknown` 拒否・env digest 記録・**2 process byte
一致確認**・publish 後 `read_bundle(path)`(戻り payload に digest 未 stamp)。
- **append-only 穴**: `calibrate-oof --json` が `open("w")` 上書き可 / manifest path が **bundle_digest
  固定**で同 bundle 再評価が conflict → `artifacts/oof/<bundle>/manifests/<manifest_digest>/` で全候補保存 +
  **promotion は別 append-only record**。

### D8【F】生成後検証(2 種)
production persisted predictions で「改善」再評価しない(fit 窓内なら非OOS)。
- **activation 前(replay parity)**: OOF win vector を production の**純 apply 関数**へ replay → per-horse
  top2/top3 完全一致・win byte 不変・Σ≈2/3・単調性・identity byte parity。
- **activation 後(prospective)**: `target_date > fit_through` の shadow 蓄積 → 結果確定後 append-only
  confirmatory eval(065 shadow-log 基盤)。
- 実値で再走すべき 076 gate: full-precision γ/λ parity・real digest logic token・runtime fit/RaceResult
  非参照・全 entry path・改竄/世代/時間 fail-closed・digest-aware idempotency。

### D9【P0-4】manifest v3 + eligibility を pure policy 化・verifier 再計算
現 verifier は `activation_eligible` の型しか見ない。→ **manifest v3 / evaluation contract v3**(DB schema
不変)で構造化 evaluation + versioned eligibility policy を載せ、**verifier が再計算**: nonidentity params
⟺ 対応 verdict=ADOPT・REJECT stage⟹identity・gate-config hash・policy version・exact stage set/order・
pivot 固定/探索範囲/fallback 整合。

## 076 の実ギャップ(codex 検出・要対応判断)
1. **live-serve / collect-prospective に manifest 引数なし** → prospective 推薦に pcal 未伝播。
2. **ops 通常 per-race serving/recommend subprocess に calib flag なし**(refresh_range job のみ env 対応)。
3. **serving backfill の skip 判定が race+model のみで manifest-digest 非対応**([pipeline.py:342])→
   manifest predict-backfill が既存 legacy run のあるレースを skip し manifest 版を生成しない(betting は
   digest-aware にしたが serving は未対応=非対称バグ)。
→ これらは 076 の「全 entry path 結線」主張と不整合。**078 の前提整備 or 076 補修として別途対応要**。

## 推奨フロー(codex)
frozen calibration-sample artifact → prequential evaluation → policy decision → all-OOF final refit →
candidate manifest → real-function parity + strong binding → 別 promotion record → prospective shadow。

## 影響: 078 スコープは初期見積り(「infra 8 割」)より大幅増
stage-λ raw fit + **hardened gate**(bootstrap CI/LogLoss/Brier/worst-fold)+ eval/deployment params 分離 +
**frozen sample artifact**(result_snapshot_hash 等)+ **manifest v3**(構造化 evaluation + policy + verifier
再計算)+ candidate/promotion 分離 + replay parity + prospective shadow。**単一 feature でなく段階分割を推奨**。
