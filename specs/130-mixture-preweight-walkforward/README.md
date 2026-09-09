# 130 発走前条件での 7 年 walk-forward 再採点(6 モデル平均 vs 本番レシピ)

**目的**: 129 の候補(125 の new_joint_mixed6)は研究では全情報条件(当日馬体重既知)で −0.0092 だったが、本番は体重公表前に予測する。締切後 4 開催日の rehearsal(+0.0015)では符号が定まらなかった。**条件の差だけを 23,000 レース規模で測る**。

**方法**(`scripts/mixture_preweight_walkforward.py`):

- 111 snapshot の expanding fold(2020〜2026、学習は 2007 から前年まで)で、6 member と anchor-42(138 列・seed42・補正なし=本番レシピ)を毎年 fresh に再学習。900 本・OOF 8 ブロック・学習時 weight mask 0.5・TE 10 は 129 と同一。
- **同じ fit から 2 条件で予測**: `full`(そのまま)と `preweight`(`MaskSpec(rate=1.0)` で同日体重 3 列を全レース NaN)。fit ノイズは条件差に入らない。
- 候補は各 member の base 確率に研究 125/118 の**年別係数**(前年までで全情報 fit)を掛け、win/top2/top3 を等重み平均。比較相手は anchor-42 の base。
- 指標は eligible レースの winner NLL 差(候補 − anchor42)。日クラスタ bootstrap(B=4000・seed 20260907・α=.0125)の sample CI と、再学習ノイズ sd_fold 0.001816(k=1)を足した total CI。年別も出す。
- `anchor42 preweight − full` も出す(091 型の条件損失の実測、文脈用)。

**読み方**:

- `full` の差が研究値 −0.0092 の近傍に来なければ、本駆動の再現性の問題(fresh fit と cache の差以上のズレ)。
- `preweight` の差が本題。total CI 上限 < 0 かつ点推定が −0.005 より良ければ、将来窓を待たずに override 採用する根拠になる(判断は別途)。
- これは診断であって prospective 確認ではない。can_adopt=false。

**費用**: 42 fit(各 1 base + 8 OOF)。2 worker で 4〜5 時間の見込み。成果物は `artifacts/130-mixture-preweight-walkforward/`(cache/ receipts/ run-freeze.json)、要約は `evidence/walkforward-summary.json`。

codex unavailable(使用上限・9/15 まで)。設計は 129 の build driver と 091 の predict-regime hook の組み合わせで、新規ロジックは「同一 fit から 2 条件を予測して差を取る」部分だけ。

## 結果(2026-09-09・42 fit・594 分・平均 14.2 分/fit)

22,990 eligible レース・715 開催日。差は候補(6 モデル補正済み平均)− anchor-42(本番レシピ)。

| 条件 | winner NLL 差 | sample CI(98.75%) | total CI(再学習ノイズ込み) |
|---|---:|---|---|
| 全情報 | **−0.009245** | [−0.01144, −0.00716] | [−0.01428, −0.00426] |
| 発走前 | **−0.008969** | [−0.01108, −0.00687] | [−0.01397, −0.00397] |

- 全情報の −0.009245 は研究 125 の −0.009245463 と一致(同一レシピ・seed・データの fresh fit は研究キャッシュを再現した=本駆動の再現性確認)。
- **発走前条件でも利得は −0.0090 で、total CI 上限 −0.0040 < 0**。条件の差で利得が消えるという仮説は棄却。anchor-42 自身の条件損失は +0.0011(CI ゼロ跨ぎ)。
- 年別(発走前): 2020 −0.0095 / 2021 −0.0087 / 2022 −0.0080 / 2023 −0.0107 / 2024 −0.0104 / 2025 −0.0101 / **2026 −0.0038**。7 年すべてで候補が良い。2026 は最小だが負。
- 中間値で見た「125 列側 3 seed だけの平均」は 2026 で +0.0015 だった。138 列側 3 seed を混ぜた 6 モデル平均が 2026 の弱さを −0.0038 まで埋めている(125 の結果レビューが 138 側を残した理由と整合)。
- 129 rehearsal(締切後 4 日・実 lgbm-094-cap900 比)の +0.0015 とは矛盾しない(日 SD 0.012・4 日の SE 約 0.006)。

**読み**: 採用を待たせていた条件の不一致は解消。残る未検証は「将来レース」の 1 点のみで、これは待つ以外に測れない。

成果物: `artifacts/130-mixture-preweight-walkforward/`(cache 42 本・receipts・run-freeze)、`evidence/walkforward-summary.json`、`evidence/walkforward-races.json`(全レースの両条件 NLL)。
