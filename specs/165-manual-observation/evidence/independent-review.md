# 165 手動観測CLI・独立レビュー

2026-10-27。新規収集を開始せず、将来の手動観測で一式の証跡を保存する実装についての独立レビュー。

## 設計上の必須条件

- 実観測の起動は手動のみ。明示的な有効化と事前planのSHA・対象レース・予定時刻・モデル/係数/source固定を、DBやネットワーク接続より前に検査する。デフォルトやrehearseから収集を開始しない。
- source originをmanual_live / fixture_rehearsal / saved_replay等で分離する。fixture adapterや保存readerを有効化フラグだけで前向き証拠へ昇格させない。データとしての条件完備とモデル採用可否を区別する。
- guardはbefore / after / decisionで別々の実観測を行う。各観測のDB snapshot/read開始から完了までを記録し、古いtransaction snapshotへ新しいメソッド呼出時計を付けない。readonly transactionのみを許し、各段階でfresh factsを確認する。
- before guardはquote開始以前、afterは両モデル計算終了以後、finalは両モデルの耐久保存完了以後。before/afterの5秒鮮度は各工程anchorに対して、finalは判断時刻に対して検査する。
- 同じ原quoteをMEV特徴へ直接渡し、原文・全頭ID/馬番/価格/人気・全as-of入力・履歴・変換行列・全member出力・モデル/source SHAを一つのsessionに保存する。旧モデルの現在市場非入力/preweightを維持する。
- 取得から判断/封印確認まで0〜1800秒。全出走集合とpostが各段階で一致し、結果未確定。固定窓/締切を結果やEVに応じてずらさない。
- fsync/readback後の実時計でready receiptを作る。DB transaction now()や自己申告record_written_atを保存完了と見なさない。decision/sealの保存確認も締切前に必要。
- 1レース1sessionの排他予約と内容同一時の冪等性を持つ。中断や失敗をledgerへ残し、不完全decisionやpartial sourceをacceptedにしない。未採用raw stagingの扱いを明示する。
- 公式精算は判断から分離し、元原文・全出走結果・全winnerの単勝払戻を一致させる。不足は両腕未精算、同着は全winner各公式額、訂正はrevision追記。確定オッズ代替や未来official観測の先取りを禁止する。
- 既存162〜164は凍結したまま使う。164のMODE=falseやROOT/pathに依存した検証をそのままliveへ偽装せず、165の実行origin/保存契約を明示する。

## 検証範囲

実DB接続・実ネットワーク・新規収集は実施しない。fake engine/fetcher、保存資料、合成の完全packetと実21モデルによる試運転で、実行順序・時刻・保存完了・失敗時の非採用を検証する。最終実装に対する攻撃テストと成果物検算は完成後に追記する。

## 実装後の独立攻撃テスト

`training/.venv/bin/python -m pytest scripts/tests/test_manual_observation_review_165.py -q` は **30 passed (20.02s)**。独立テストと検算スクリプトの Ruff も PASS。

- 明示的有効化なし、fixture plan の実観測への昇格、plan SHA不一致、準備窓外、遅い事前登録、source差替えを、adapter/factory構築前に拒否。
- 3段階のguardが別のreadonly repeatable-read snapshotを使い、元quoteをcache経由で取得しないこと、取得開始/受信を別時計で残すことをfake engine/fetcherで確認。
- UTC/JSTで同じ予定発走時刻を表す場合は受理する。naive DB timestampは拒否。
- 両モデルそれぞれの保存完了receiptが判断の1µs後なら拒否。予測計算前のafter snapshot、両保存完了前のfinal snapshot、finalで出現した結果行を拒否。
- manifestのSHAを整合させた上でも、両ready・モデル開始/完了・quote受信・特徴量生成・final snapshot開始のclock log行が欠けると拒否。
- 稼働中writerの排他lockがある場合、abortはsession内容を変えず拒否。status/clock/runtime保存済み・manifest未作成の失敗sessionも、元台帳を保全して回復できる。abort receiptの耐久保存直後に強制中断を注入しても、同じreceipt SHAのまま再実行で回復できる。

独立レビューで見つかった点はroot実装へ反映済み: (1)予定発走時刻の文字列表現比較、(2)旧予測の消費履歴と特徴量packetの完全結合、(3)予測や保存receipt等の全工程と実時計logの結合、(4)close例外による台帳消失、(5)abortの既存台帳/receiptとの競合、(6)observe/abortの同時更新。

テストで使用したのはfake I/Oと合成3頭packetであり、予測のみ既存の実MEV15・旧6モデルを使用した。API環境にはpandasがないためML環境のtraining/.venvを使用。実DB・実HTTP・新規資料取得は行っていない。

## 最終成果物の独立検算

`artifacts/165-manual-observation/run-02` に対し、[independent_manual_audit.py](independent_manual_audit.py) を実行し **independently_verified**。結果は [independent-audit-run-02.json](independent-audit-run-02.json)。165本体の検証・推論・混合・精算関数は使用せず、JSON/manifest/source SHA、実時計とmonotonic、全工程receipt、3段階の出走馬集合・結果未登録、両予測保存完了、固定判断窓を直接確認した。

quote受信から判断までは **0.658256秒**。MEV15本・旧6本をnative LightGBMへ直接入力し、前処理・旧isotonic・正規化・履歴補正、混合確率・EV・買い目まで独立に再計算して保存内容と一致した。試運転はfixture由来で前向き証拠には不採用、比較データ追加は0件。

別資料の保存済み公式HTML **9レース129頭**も原gzip/hash/取得時刻を維持して再parseし、各勝馬の印字単勝払戻と照合した。両腕とも既存8点・800円・払戻2590円で一致することは過去資料の精算再現であり、今回の合成sessionと結線していない。新たなモデル優劣やROIの証拠にはしない。

実運用でのDBスナップショット・外部取得の動作は未実行。特徴量packetには対象行・旧履歴・変換行列・元quoteとsource hash/countを残すが、全過去入力poolのraw値の完全保存と全特徴量の独立再構成はこの検算の対象外。
