# Research: Early-Mid Pace Features (097)

事実はすべて実測(evidence/first3f-killtest.json・本セッションの DB クエリ・codex-review.md)。

## D1: 列集合 = `asof_rel_early_mid_avg` + `asof_rel_early_mid_best` の 2 列(凍結)

- **Decision**: 新 FEATURE_GROUP `early_mid_pace` に 2 列のみ。balance 類似列(2·rel_last3f −
  rel_time)は追加しない。
- **Rationale**: codex Q1 回答 (a) を採用。`_avg` は有限深さの木が引き算を安く再構成できない
  こと+欠損パターン差で近似従属にとどまることから残す。`_best`(min)は順序統計で分解不能=真に
  新しい。balance 類似は純線形結合かつ last3f 係数がノイズ増幅・相関候補追加で seed 依存の分割
  入替を悪化させるため不採用。070 の教訓(束を小さく=帰属面最小化)。
- **Alternatives**: `_best` のみ(中心傾向の安定情報を不必要に捨てる)・既存 2 列に任せて死列を
  落とす案 (d)(→D3 のとおり基準アームがその再学習版なので、ゲート自体が (d) との比較になる)。

## D2: 近線形冗長の開示(期待値の正直な設定)

- 全 pace 集約は同一 rolling 窓(`_RECENT_N=5`)・同一 source frame(`fin`)を共有すると
  **コードで確認済み**(pace_features.py の `_rolling_asof` 呼び出し)。ゆえに
  `_avg ≈ rel_time_avg − rel_last3f_avg`(厳密でないのは欠損パターン差のみ)。
- 効く機構は (1) 単一分割アクセス(088 FR-002a 前例) (2) `_best` の非分解性 (3) **供給死亡
  レジームで一貫供給される唯一の early-pace 表現**であること。full-info 窓で効かないのは期待
  どおりで失敗ではない。

## D3: primary 評価 = 擬似供給停止シミュレーション(codex Q2 の全条件を凍結)

- **Decision**: 事前登録カットオフ **3 本**(2019-01-01 / 2021-01-01 / 2023-01-01)。各カット
  オフで DB セッション内(未 commit)に「cutoff 以降かつ距離≠1200m の `first_3f` を NULL」の
  マスクを適用(**1200m 導出は本番で生きているのでマスクしない** = 定常状態の忠実な再現)。
  マスクされた環境で特徴を再構築し、**両アームを各 fold 内で再学習**して paired 評価。
  - 採点窓は washout 1 年後の 1 年間: [cutoff+1y, cutoff+2y) = **2020 / 2022 / 2024**(互いに
    素 → 開催日クラスタ bootstrap の独立性が保たれる)。pooled paired winner NLL が primary。
  - washout 実測: cutoff+1 年時点で直近 5 完走の **98.1%** が cutoff 後(n=42,621)。
    = 推定対象(estimand)は**定常状態**と宣言する(移行期ではない)。
  - アーム: 候補 = features-022 全列、基準 = 同一環境で `drop=early_mid_pace`(069 の drop-group
    機構)。**同一マスク・同一ビルド・同一 fold・差は新列のみ** = codex 帰属条件 (i) を機構で充足。
  - 両アームとも本番レシピ(arm E = OOF isotonic 8 blocks + rounds 900 + 091 体重マスク・seed 42)。
  - ゲート式は **v4 標準**(point < −δ, δ=0.002 + seed 分散込み CI 上限 < 0)。codex の
    `ci_high < −0.002` 案は不採用 — feature ごとの判定式分岐は v3 が排除した「2 つのゲート実装」
    の再発(codex-review.md に記録)。sd_fold=0.001816・pooled n_folds=3 で inflate。
- **Rationale**: 実劣化窓(~2,700 レース)は MDE 0.0094 で検出力不足。標準 full-info 評価は新列が
  構造的に冗長に見える(≤2024 は実測 first3f が生きている)。091 前例は二重ゲート構造まで、
  履歴集約を変える本介入はより深い — よって codex 4 条件(結果非依存・as-of 因果・対称・反実仮想
  頑健性としての報告)を契約に固定する。
- **Alternatives considered**: 学習は非マスクで採点のみマスク(→ stale モデルの serving 衝撃を
  測ることになり採用判定と別物・比較が反転しうる = codex (iii)。棄却)/実劣化窓のみ(検出力不足)/
  opportunity-set(v4)単独(適用集合は定義できるが、適用集合内でも ≤2024 は実測が生きており
  同じ冗長問題。棄却)。

## D4: full-info 非劣化 guard

- マスク無しの標準 paired 評価(同じ採点窓 2020/2022/2024・同レシピ)で、候補が**自信を持って
  +0.003 より悪い**場合のみ FAIL(evidence-of-harm 型・three_way 判定)。実測 first3f が生きた
  世界で新列が害にならないことの保証。効果ゼロは PASS(期待どおり)。

## D5: 実 2025-2026 方向一致 guard

- 実劣化窓(2025-10-11 以降)での paired 差。検出力不足(MDE 0.0094)なので **evidence-of-harm
  veto のみ**(ci_low > +0.005 で FAIL)。シミュレーションと実レジームの方向不一致を検知する
  安全弁であり、成立を要求しない。

## D6: マスクの実装機構と対称性の契約

- kill-test で実証済みの「セッション内 UPDATE(未 commit)→ 特徴再構築 → 必ず rollback」方式。
- マスクは `race_results.first_3f` のみに作用し、行の適格性(started/winner/採点対象)には一切
  触れない = 両アームの母集団が同一(codex (i) の契約テスト)。
- マスク環境の再構築は in-memory ビルド(parquet は非マスク世界の凍結物なので使わない)。
  非マスク世界と parquet の一致は T016 の materialize parity で担保する(driver 側では再検査しない)。
- **provenance**(codex tasks Q2): マスクを当てたセッションで Frames を 1 回だけロードし両アームに
  同一 matrix を注入。射影 hash・違反行 0・`use_materialized=False` を assert。対称性 assert だけ
  では parquet/キャッシュ/別コネクションによる非対称汚染を通してしまう。
- 概算コスト: 1 カットオフ = ビルド 2 回 + arm E fit 2 本(~650-700s/本) ≈ 40 分 → 3 本で ~2h。

## D7: FEATURE_VERSION と serving 互換

- features-021 → **features-022**(019=070 焼却・020=088 焼却・021=現行)。
- compat pin: 現 active lgbm-094-cap900 は features-021・hash `663fe86c7564…` と**実測一致**
  (metadata.json 照合済み)。022 の COMPATIBLE_PRIOR_FEATURE_VERSIONS に 021 を実測 hash で pin。
- 新規ソース列ゼロ(finish_time/last_3f は既存 loader が供給)→ source_fingerprint 不変 =
  materialize-safe(059/061/070 同型)。

## D8: リーク境界

- rel_early_mid は完走走の結果値から作るが、既存 pace 機構(strictly-before + 同日除外 +
  finished-only + merge_asof allow_exact_matches=False)にそのまま載る。新しい結合・新しい
  loader 列は無い。挙動テスト 3 方向(今走・同日・未来)で固定。

## D9: verdict 後の分岐

- REJECT: bump+結線のみ revert・モジュール+単体テスト非結線保全(062/070/090 同型)・active
  予測バイト一致検証。
- ADOPT: **列の採用 = registry 結線 + features-022 確定**。ただし**モデル昇格は別段**(verdict は
  `counterfactual_supply_simulation` 種別・`eligible_for_verdict=False` で、`evaluate_promotion`
  が構造的に拒否する=規約でなく機構で分離):
  実データで学習した候補は ≤2024 で実測 first3f を使えるため、標準窓の対 active 評価は非劣性
  しか示せない(それで正しい — 価値は前向きに発現する)。昇格は「標準窓の非劣化 PASS +
  097 シミュレーション verdict + prospective 監視」の 3 点セットで別途判断し、
  ここで自動昇格はしない。
- **事後採用禁止**: 3 カットオフの個別結果を見て「効いた窓だけ」で語ることを禁止。正本は
  pooled 1 本(068 C2 selection leak 同型の防止)。

## D10: 正直な限界

- シミュレーションは**定常状態**の推定(washout 98.1%)。実 2025-2026 の移行期(部分供給 74%→
  0%)の便益はこれより小さい可能性があり、D5 の方向 guard 以上のことは言えない。
- 単一 seed(42)。seed 分散は v4 の inflate で区間に畳むが、seed を振った再実行はしない。
- kill-test の -0.0095 は流し込み方式の値。本ゲートの期待効果(定常 -0.005 前後)は見込みで
  あって登録値ではない。
- early-mid は距離で物理区間が変わる(1200m=テン3F、3600m=前 15F)。距離遍歴の交絡は受容
  (既存 rel_first3f も同性質・レース内相対が距離を大部分吸収・023 以来の設計)。
