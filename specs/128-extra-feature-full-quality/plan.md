# 128 実装・検証計画

1. 126 full-quality方式を新128へ移し、候補集合を127のf04/finish_decomp/weight_deviationに固定する。gap agentの独立設計レビューを受け、旧source不変・0候補0fit・全候補7fold完遂・旧native recipe境界を確認する。
2. source_stateで127全3結果と4cache/receipt/summary、scalar/prediction両監査method/jsonと122helperを完全結合する。全127終了後しかprepareできず、登録順を保ってADVANCE全候補を選択する。111/旧125native baseline全値・カテゴリ・race/foldの再利用証明を作る。
3. 新128Factoryのfitを独立namespaceで実装し旧WORKを触らない。127のrecipe・scope・dtype・実paramsの完全同値をunit testsで確認する。0/1/複数候補、exact7jobs/候補、禁止arm/新baseline fit拒否、126/127同時学習拒否をテストする。
4. cache/receipt/orphan/complete-result resume、元raw125全損失一致、品質/最近年/critical群の非有限や不整合拒否を検証する。独立レビューと全testsを通して本文/source/configを編集停止し、hashを親へ共有する。audit methodは別担当がevidence内で準備し、全結果前に独立固定する。
5. 親が127全結果＋両独立監査完了後、prepare→smoke→train --workers2→evaluate→独立数値監査を実行する。候補毎に全7年を完遂し、途中成績で打ち切らない。通過なしならsummaryまで0fitで完了する。

## 計算費用

旧900本8OOFのraw125・全7年2020..26の実測fit秒合計は約93.9分。固定年順で2並列なら約52分/候補。127追加列による時間増を含め52〜60分/候補を目安とし、1/2/3候補のwallは約52〜60 / 104〜120 / 156〜180分。候補毎完遂順による末尾idleを含む概算で、全候補のjobを混ぜて効率化しない。0候補はfit0。

1 outerはOOF込み8 boosterで、実測時間に8を再乗算しない。56 booster/候補、最大168。本学習とは別にsmoke4〜8 tiny booster（0候補は0）。matrix再利用・prepareのSHA/基準証明・smoke・評価/CI・独立監査の時間は別で、実測fit時間とRSSはreceiptから記録する。max2worker1thread、24GiB host、旧大年peak約8.4GB/workerを踏まえ126/127の本学習と重ねない。

## 解釈

127で負の差が出ても全期間品質は未確定。128で保持できてもseed42のraw125に対する特徴増分の研究であり、119/123/124/125の補正やモデル平均の改善へ加算しない。3候補族の条件付き次段だけを扱い、別候補族や別番号の研究を増やさない。
