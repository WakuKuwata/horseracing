# 145 実装計画

2026-10-24完了。120新規fit・3対照・独立監査が終了。併用案102.88%・利益146,360円で各対照を下回った。詳細は[result-review.md](result-review.md)。提案3は保留。

Python3.12 training/.venv、既存numpy/pandas/LightGBM/scipy、研究CLI。macOS24GiB、最大2worker各1thread。143/144と共有変更を保護し、145新規ファイルだけを作成する。

1. models.pyは143のexact-key load_data・training_weightsを再利用する。fit kernelは凍結143を変更せず、paceとweightを同時に渡せる独立実装を作る。numeric66→pace14→category11の列順を維持する。
2. experiment.pyは8年×15seedの120fit。identity・recipe・元③のweights・source/runtime・保存本体・予測を検証する。再開時も実入力から期待重みを計算。完了markerはidentity/evaluation/receiptの360hashを束縛する。
3. run.pyで旧3対照360fitをreceiptから再構築し、既存ensembleと完全一致確認。入力・依存source・特徴・重み・評価・3比較を実行前SHA凍結する。
4. report.pyで15seed確率を先に平均し、143の固定scoreを適用。共通予算と実投資を区別し、3比較・既知レース・最大差・同暦窓・年別を保存する。市場混合係数は移送しない。
5. 独立監査は実験の集計実装を使わず、全480receipt/1440artifactSHA・元③と同じ重み・2019展開との全seedパリティ・購入配分・3比較区間・保存本体サンプル再生を確認する。

## 独立設計意見

展開14列は143protocol bundles.paceの順をそのまま使用。教師は展開モデルのOOFへ変更しない。年次trainで③と正確な同一重みを再計算する。2019年の比較先は基準77列ではなく旧pace91列。新案−基準/③/paceの3比較に固定し、対照の再学習を避ける。複数要素の相互作用があるため、利益を一意に因果分解したとは表現しない。

## Constitution check

- I: 2007以降・12桁race ID、exact race/horse結合。
- II: OOF fit-throughは馬の年より前、trainは対象前年末まで。追加特徴は凍結143の過去限定履歴を再利用。
- III: 評価器・比較族・seedを先に固定し採用不可を維持。
- IV: 完全field・取消と欠損の扱いを維持し、raw EVと正規化NLLを区別。
- V: input/source/runtime/weight/model/prediction SHA・実時間・再現誤差を保存。
- VI: 本番/API/UI/DB変更なし。提案3は保留。
