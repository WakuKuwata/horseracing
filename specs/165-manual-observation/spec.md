# 165 手動観測CLIの保存契約

2026-10-27。ユーザー指示により実装する。新規収集は開始しない。162〜164の資料と仕様は凍結し、165に将来の手動観測入口を追加する。

## 要求

1. 一回の手動実行で取得前確認、発走前オッズ原応答、判断時点までの両入力、旧6本とMEV15本の実推論、予測後確認、判断直前確認、両予測の耐久保存完了記録を一つのsessionへ保存する。
2. `observe` の既定値は収集停止。明示フラグ、事前登録SHA、対象日・レース・予定発走時刻、コード/モデル/固定係数の一致、実時計の準備窓を検査してから実アダプタを構築する。schedulerと自動再試行は作らない。
3. 登録はT−15分以前。実行開始は[T−15分,T−9分)、判断/保存確認は[T−10分,T−9分)。準備が早く完了した場合だけ判断窓まで待つ。タイムアウト後や再試行で窓を動かさない。現固定モデルは2026年のみ。
4. 各guardは別のread-only snapshotでrace/entries/resultsを再取得する。query snapshot開始からanchorまで5秒以内。発走前、予定発走時刻と全出走ID/馬番の不変、結果未保存を検査する。afterは両計算後、finalは両予測の保存後。
5. 同一原quoteをMEV入力へ渡す。取得開始・受信時刻・provider更新時刻を保持し、取得開始/受信/provider鮮度を既存163契約で検査する。判断・封印確認時にも1800秒以内、未来quote不可。確定オッズを判断へ使わない。
6. 特徴は実read-only repeatable-read snapshotで既存product builderを呼ぶ。旧138列、MEV77列、厳密な過去started履歴、全行/列の観測時刻、変換後行列とモデルmember出力を保存する。ターゲット結果・未来履歴は拒否。旧モデルは市場非入力/preweightのまま。
7. 入力/予測bodyはatomic exclusive publish/fsync/readbackし、その後の実時計でready receiptを残す。両予測のinput→start→compute→save→final→decision→seal確認の順序を照合する。実時計はwall UTCとmonotonicを照合し、全工程stampがclock logにあることを再検証する。
8. 1レース1sessionの排他予約。失敗時は不採用、理由と除去したstagingのSHAを残し、未採用quote/特徴/予測bodyを削除する。中断時の`abort`は実行中のprocess lockが取れなければ拒否、既存acceptedは変更不可。中断回復は冪等。race予約は解除せず、やり直して選別しない。
9. `settle`は保存済み公式資料のみ。原HTML/SHA・全出走結果・全winner公式単勝額を一致させる。取消後の不一致、結果不足、未保存払戻は両腕未精算/null。全winner各公式額で同着処理し、訂正はrevision追記。ID変換は判断時に凍結した明示mapだけを使う。確定オッズ代用不可。
10. fixture_rehearsalは収集有効化でもmanual_liveへ昇格不可。試運転は実21本を実行するが比較証拠/モデル採用不可。manual_liveでも当該一記録だけでモデル採用可とはしない。

## 一式の保存範囲

plan、registration-ready、invocation、3guardの原query結果、quote原文とsource/取得時計、全入力行列/履歴/source fingerprint、全member出力と入力消費証拠、各ready receipt、index、decision、seal、accepted、clock、runtime、status、manifest。公式精算は別の連続revisionとして追加する。

保存する原DB poolは件数とfingerprintであり全raw値ではない。保存行列からの推論再現は保証するが、raw DB poolだけから特徴生成の全工程を再構築できるとは主張しない。ローカルSHA/receiptは整合性検査であり外部署名ではない。
