# 110: 現行モデルの特徴群削除検証

2026-09-07。測定のみ。製品・active・既存特徴定義は変更しない。

**結論：今回の特徴量削除は採用しない。** 4案を優先順に一次比較し、相対能力13列の削除だけを長期比較へ進めた。長期では改善方向だったが、必要改善幅・統計条件を満たさず既存v4ゲートはREJECT。現行138列とactive `lgbm-094-cap900` を維持する。正本は `verdict.json` と `evidence/full-relative_ability.json`。

## 問いと候補（結果を見る前に固定）

現行 `lgbm-094-cap900` の138列、PL top3目的、900本、strict-past OOF isotonic 8 blocks、seed42、馬体重mask 0.5/20260810、騎手・調教師TEを固定する。

1. human_absolute: 騎手・調教師の通算勝率の絶対値2列。相対派生は残るため通算情報全体の不要判定ではない。
2. relative_ability: 相対能力13列。原特徴は残す。
3. course_aptitude: 自馬の競馬場勝率・複勝率2列。
4. pedigree_interactions: 新馬・少履歴×血統の交互作用4列。sire_debut_win_rateは残す。

候補の追加、結果を見た組合せ、閾値変更は本ラウンドで行わない。

## 段階と既存ゲート

- smoke: 2008年1月、5本・OOF2。配線確認のみ、効果数値を非表示。
- screen: 2018年、現行と同じ900本・OOF8。4候補全てを比較。winner NLL差が負かつtop2/top3差<=0.0005、ECE差<=0.001の候補のみADVANCE。他はDEFER（不要確定ではない）。
- full: ADVANCE候補を元の優先順に2019-01-01..2026-08-23で評価。v4のpaired_eval/final_decisionをそのまま使う。最低400開催日、delta=0.0035188338580500285、4候補族のbootstrap alpha=0.0125、再学習noise sd_fold=0.001816。
- sd_foldは既存実験からの移送仮定で、今回の削除幅について再測定した値ではない。
- 過去期間は既に多数の研究で利用済み。fullでも研究上の採否であり、未使用の将来期間の証明や自動昇格ではない。
- 学習時の馬体重maskは現行と同じ0.5だが、今回の評価時入力は当日体重を含むfull-information条件。馬体重公表前のserving条件の改善を証明するものではなく、本番反映の検討にはその条件の別評価が必要。
- 全候補DEFERの場合は本ラウンドの高コストfullを行わず結果を保全する。小さい/局所的/時代依存の改善を否定しない。

## 入力と再現性

REPEATABLE READ / READ ONLY のDBトランザクションから、2007以降・2026-08-23までのTrainingMatrixを一度だけ新規構築し、評価レースとともにローカルsnapshotへ固定する。既存parquetを新しいDB静的列と混ぜない。全候補は同一snapshotを読み、列指定だけ変える。

snapshot、実験設定、実行コードをhashで固定する。foldの学習レース集合、特徴列、recipe、入力snapshotをcacheに結び付ける。各foldの基準予測は一度だけ計算し再利用する（過去の別DB時点のOOFは流用しない）。削除列の存在、138列の順序一致、実際の学習列、確率の有限性と合計を検証する。

## 独立レビュー

別エージェントが現行factoryとv4ゲートを確認。OOF既定3の回避、部分列drop、無音dropの検出、static/labelを含むsnapshot、重要部分集団の明示、4候補の多重性、noise移送の限界を反映した。

## 実行

`training/.venv/bin/python scripts/feature_pruning.py prepare|freeze|smoke|screen|full`

prepare後の実装レビューを終えてからfreezeを行う。データ準備時のコードhashは元のsnapshotメタに保全し、実行するdriverと依存パッケージのソースhashは別のrun-freeze.jsonへ固定する。

fullはscreenでADVANCEした候補だけ実行する。各段の出力は既存ファイルへの上書きを拒否する。中断した学習は完成したfold cacheを再利用できる。

## 実行前確認

- 新しい削除driverの単体テスト26件が通過。削除列の厳密な適用、既定OOF数の回避、凍結入力からの校正結果参照、cacheの列・recipe検証を含む。
- 既存trainingのdrop scope / OOF isotonic / calib splitテスト30件、evalのfrozen contract parityテスト22件が通過。
- smokeは240レースで完走（26.5秒）。小規模モデルなので効果の判定には使わない。
- 次候補の素材確認は `evidence/next-candidate-readiness.json`。これはデータの存在確認であり、改善効果や新しい実験条件の事前登録ではない。

## 一次比較の結果（完了）

2018年、3,454レース（winner NLLの判定対象3,448、109開催日）。差は候補−基準で負が改善。基準のwinner NLLは2.030061。

| 優先順 | 削除する特徴 | winner NLL差 | screen進行判定 |
|---|---|---:|---|
| 1 | 騎手・調教師の通算勝率の絶対値2列 | +0.000037 | DEFER |
| 2 | 相対能力13列 | −0.007062 | ADVANCE |
| 3 | 自馬の競馬場適性2列 | +0.001331 | DEFER |
| 4 | 新馬・少履歴×血統の交互作用4列 | +0.001755 | DEFER |

相対能力13列の削除はtop2差−0.000739、top3差−0.000452、ECE差+0.000350でscreenの進行条件を満たした。ただし再学習ノイズ込み98.75%CIは[-0.015993,+0.001648]でゼロを跨ぐ。v4のreadoutはNO_DECISIONであり、採用結果ではない。

## 長期比較の計算方法

相対能力13列の削除だけが進行。2019〜2026の8fold×2armを比較する。

凍結driver・入力・recipe・ゲートは変更せず、`scripts/feature_pruning_prefill.py`で同じ公式expanding_foldsとCachedFactoryを呼び、独立した年×armのcacheを前計算する。各学習は従来どおり1thread。まず2026年の基準foldでpeak RSSを測り、その後の並列数を決める。実行順以外の測定条件を変えない。

job manifestとorchestratorを追加でhash固定し、各workerの開始・終了時に元のrun-freezeも照合。cache keyは元driverと同一で、同一keyを重複実行しない。終了検証を完了していないcacheは隔離し、完成cacheのSHAとpeak RSSをreceiptに保存する。全workerが終了し全receiptを確認してから、元の`feature_pruning.py full`をそのまま実行し、cache・予測対象・最終ゲートを照合する。

独立execでsmoke条件を再学習し、基準側・human_absolute側とも各240レース/3,679出走のwin/top2/top3予測が元cacheと完全一致（相違0）。小規模条件での実行分離の確認で、900本の全fold同士を二重学習して一致検証したものではない。方法コードと結果は `evidence/parallel-smoke-parity.json`。

2026年の基準foldは954.5秒、peak RSS 8,428,568,576bytesで完了。実機24GiBに対して3並列は余裕が乏しいため、残り15jobは**2並列**とする。完成済みの2026基準cacheも最終比較に再利用する。

## 長期比較の結果（完了・REJECT）

2019-01-01..2026-08-23、26,482レース（winner NLL判定対象26,437）、825開催日。16組すべてでOOF校正十分性を確認し、元driverで全cacheを照合して完走した。

| 指標 | 相対能力13列の削除−基準 | 判断 |
|---|---:|---|
| winner NLL | **−0.002273**（基準2.050434 → 候補2.048162） | 改善方向だが必要改善幅0.003519に未達 |
| sample 98.75%CI | [−0.004847, +0.000233] | ゼロを跨ぐ |
| 再学習ノイズ込み98.75%CI | **[−0.005306, +0.000702]** | 統計条件未達 |
| top2 / top3 | −0.000235 / −0.000250 | 非劣性条件PASS |
| ECE | −0.000072 | 校正条件PASS |
| 直近3年 / 5年 | −0.002572 / −0.002385 | 既存の直近期間条件PASS |

REJECTの直接理由は必要改善幅の未達で、CI上限<0の統計条件も満たしていない。**「小さい改善も存在しない」「削除が有害と確定した」とは言わない。** 他3案は2018年のscreenのみDEFERで、長期比較で否定したものではない。

重要部分集団のガードもNOT_PROVEN。canonicalはPASSだが、`nk`はNO_DECISION、2026年単独はINCONCLUSIVE_LOW_PRECISIONだった。2026年単独のwinner NLL差は**+0.004819**（70開催日、同集団のbootstrap CI[−0.004616,+0.014197]）で、悪化方向の点推定だが精度不足。最近3・5年の条件通過を、現在の非悪化が証明されたと読み替えない。

最終結果も独立レビューし、上述の読み方を確認済み。モデル昇格・feature version変更・製品コード変更は行わなかった。

## 次候補の着手可否

能力観測の古さ・ばらつき、過去馬体重の個体内偏差は、既存素材から計算できることを確認済み。最高速度指数を最後に記録してからの日数は、比較可能な出走の65.5%で既存の休養日数と異なる。速度指数SDの計算可能率79.2%、前回体重の過去平均からの偏差64.4%。ただし予測改善は未測定で、新馬や履歴の薄い馬への適用範囲にも制約がある。

詳細は `evidence/next-candidate-review.md` と `evidence/ability-weight-readiness.json`。調教などの新素材は現行DBに専用データがなく、取得環境・履歴範囲・公開時刻の確認が先となる。

## 実行コスト

長期比較の前計算は壁時計で約122.0分。各jobのfit経過時間の合計は213.2分（並行実行のwall timeを足した値で、CPU時間ではない）。元driverのcache再読込・ゲート評価は約9.6秒で、学習時間とは区別する。正本は `evidence/full-prefill-summary.json`。この値から厳密な逐次実行に対する速度比は主張しない。
