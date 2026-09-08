# 127 実装・検証計画

1. 既存122のFactory/receipt/paired-eval方針を新127独立namespaceへ複製し、旧WORKの付替えを使わない。独立gap reviewerが定義・scope・temporal boundaryを先に確認する。
2. F04/088の既存builderをそのまま呼び、新しいweight_deviationをscripts内で構築する。全部float64の17列を111 matrixに追加し、基準125と各candidate131/135/126を厳密な列差分で検査する。prefix/target-day/NaN/曖昧日・母集団監査を実装する。
3. 122/113/111/110凍結来歴とnative125/anchor138の再利用証明をprepareに結合する。3jobのみ固定、欠落cacheのfallbackなし。source/hash/runtimeが途中で変化したら停止する。
4. unit testsで旧builder実効式・レシピ差がdropのみ・actualparams・cache/receipt/全結果resume・不正値拒否・小差gate・126学習重複拒否を検査する。独立実装レビューを通し、最終編集停止とSHAを親へ共有する。
5. 親がprepare→独立scalar特徴監査→smoke→train --workers2→evaluate→独立予測/CI/判定監査の順で実行する。126本学習の完了を待つ。監査methodはevidence内で事前固定し、新旧sourceを変更しない。

## 費用と資源

122の実測はF03/F05/colsample各611.3/629.3/567.4秒（OOF込みouter）。127の3候補は同じ年・基準で、概算1 outer10〜11分、3件のfit時間合計30〜33分、2workerの経過時間20〜22分。新しい17列の算出・準備・監査・smoke・評価は別。列追加による実測時間・RSSはreceiptから別記する。基準native fit543.35秒は再利用で新規費用に加算しない。1 outer8 boosterを時間に再乗算しない。

本学習24 boosterとsmoke8 boosterを別計上する。将来通過案を7年比較する場合は候補ごと7 outerが別途必要だが、このscreen実装には含めない。max2worker1thread、24GiB hostで旧大年peak約8.4GB/workerが上限の根拠。126と127の本学習の同時起動は禁止。

## 制限

既存旧期間の良い点差は現在の増分証拠ではない。F04/088は旧校正holdout0.3から現行8OOFに変わっており、weightは素材計算可能性だけが既知。現在125＋補正と確率平均の改善へ今回の差を足さず、screen通過は全期間品質研究の候補に限定する。
