# 162 保存契約とオフライン試運転

2026-10-26確定。161提案の保存/検査契約を実装する。新規収集、DB接続、スケジューラ、学習、実モデル推論、既存UI/API/テーブルの変更は対象外。

## 目的と適用範囲

取得30分以内の全頭オッズ、判断以前に保存された両モデルの予測、別保存の公式結果/払戻を組にできることをオフラインで検査する。すべて`mode=offline_rehearsal, simulated_clock=true, eligible_for_prospective=false`。CLIは`rehearse`と`verify`のみ。live/collectへの昇格オプションは設けない。

実装は保存・比較・精算の状態機械と合成fixtureを対象とし、実際の取得/推論の認証・実時計での利用可能性を証明するものではない。モデル本体は現在の実保存ファイルをSHA固定するが、実行せず、fixtureの確率を使う。これを実モデルの推論再現と呼ばない。

## 固定プロトコル

- 使用年2026、MEV `mev-ens15-v1`15seedと旧`mix-129-nj6`6memberのモデル/特徴定義・各本体SHAを固定。
- A=`q+MEV`、B=`q+MEV+旧preweight`。151の2026混合係数と学習末日を読み、変更しない。旧モデルに市場入力は加えない。
- EV>1、100円、最大10頭、共通予算1,000円。EV降順、馬番、IDで順位固定。無購入も採用レースとして残す。
- 発走15分前準備、判断窓は発走10分前を含み9分前を含まない。窓内の最初の有効な封印1件のみ。遅延時に窓を移動しない。
- 取得完了から判断・封印確認までそれぞれ0〜1,800秒を含む。封印完了も窓内・発走前。最終guardは各検査時刻以前5秒以内。
- 時刻はtimezone付きISO8601、精度はマイクロ秒まで。それより細かい小数秒を黙って丸めず拒否する。時計逆行も拒否。

## 保存契約 schema_version=1

JSONはUTF-8、key sort、余分な空白なし、非有限数禁止のcanonical形式。SHA256はその保存バイトを対象とする。正規化入力の未知値は明示null。主要payloadはPython validatorの`keys()`で必須/余分なkeyを厳密検査する。

| ファイル | 規範的な意味・主要項目 |
|---|---|
| `protocol.json` | offline flags、モデル/全本体SHA、特徴列、学習末日、両腕係数、年、窓/鮮度/購入規則。code-defined protocolとの一致を要求 |
| `schedule.json` | 今回は合成対象11件。対象IDとscenarioを先に保存し成功/失敗全件をreportに残す |
| `races/<id>/reservation.json` | race単位のsession_idと入力SHA。異なる入力/sessionでの差替え禁止 |
| `inputs.json` | race/date/post/field、前guard、quote、両予測。両予測にrun/モデルSHA、入力利用/開始/完了時刻、全行feature列と確率、行created/updated/input_asof |
| `ready.json` | 全入力ファイルのfsync/readback後に別時計で観測した`available_by_at`、入力/プロトコルSHA、offline flags |
| `decision.json` | `decision_at`、全頭確率/EV、両案の仮想買い目と予算、最終guard、ready/input/protocol参照SHA |
| `seal.json` | decision fsync/readback後の`observed_saved_at`とdecision SHA |
| `accepted.json` | sealファイル自身のfsync/readback後の`acknowledged_at`とseal SHA。判断/封印の保存完了を確認した事後receipt |
| `failure.json` | 不採用理由・記録時計・staging SHAのみ。不採用の入力原本を採用証拠として残さない |
| `settlements/0001/evidence.json` | race/revision/取得時計/source、公式原文とSHA、全結果、全winnerの100円当たり単勝払戻 |
| `settlements/0001/result.json` | 状態/理由、両腕の投資/払戻/利益、primary適格性、decision/evidence/前revisionのSHA |
| `report.json` / `manifest.json` | 全予定の結果、過去資料の不足、全出力/実装source SHA |

`inputs.json`内quoteは、原文と正規化全頭値を持つ。この版で許可するraw形式/producerは`synthetic_quote_v1`/`synthetic_fixture`のみであり、実データ取得アダプタは存在しない。将来の実取得は別実装/契約版で行う。

入力はquoteの全頭ID/馬番/価格/人気とMEVの入力を照合し、市場派生列q・順位・人気馬価格・entropy等も再計算照合する。特徴列の欠落、出走集合不一致、確率不正、run/モデル識別不正、未来行更新を拒否する。MEVのraw binary確率は和1を要求せず、比較で既定clip/softmaxする。旧予測は和1と体重関連3列の明示nullを要求する。

## 保存完了時計と採用の順序

`computed_at`をcommit/保存完了時刻とみなさない。

1. 入力全体を一時ファイルへ書き、fsync、排他的hard-link公開、親ディレクトリfsync、読み戻しを完了する。新規ディレクトリは各親のentryもfsyncする。
2. その後の時計をready receiptへ保存。既存receiptを再利用する場合はmode・protocol/input SHA・全時刻・予約を採用前に検査する。
3. ready以前の予測がそろったこと、最終guard、鮮度/窓を検査してdecisionを保存する。
4. decision保存確認後にsealを保存する。seal自身の保存確認後の時計でも鮮度/窓を検査し、通過した場合のみaccepted receiptを残す。

acceptedはdecision/sealの保存確認を記す事後帳簿であり、accepted自身の保存がそのtimestamp以前だったという自己証明ではない。検査対象はdecisionとsealの保存完了。無限にreceiptを連鎖させた保存保証を主張しない。ここでの時計はすべて疑似時計。

同内容の再実行は既存証跡を再検証して返す。異なる入力は拒否する。ready直前の中断は同じ入力だけで再開でき、新しい確認時計を使う。decision作成後の不完全sealは自動採用せず、監査/abortが必要。abortは先に理由・SHAだけを保存し、その後staging原文を除去する。accepted後はabort不可。

## 精算契約

公式原文は保存netkeiba result形式。canonicalのhttps/domain/path/race_id一致を確認し、標準HTMLParserで結果表の着順/馬番/馬IDと単勝払戻表だけを読む。確定オッズ列は解析しない。正規化済み結果/払戻と原文の一致も要求する。IDを推測で結合せず、不一致は未精算とする。

- `pending`: 結果未保存。
- `incomplete`: 出走結果不足/馬集合変更、公式払戻不足。返還を推測しない。
- `inconsistent`: 原文/SHA/出典/race/結果/payout集合・金額・時刻等が不整合。
- `settled`: 全出走馬の結果と全1着馬の公式払戻が完全一致。

観測は封印後かつ発走時刻より後。未精算は両腕とも払戻/利益null、外れ0円へ埋めない。精算可能な外れは0円、的中は購入額/100×公式単勝額。同着は全勝馬の各公式額で会計するが、旧比較継続のprimary対象外。無購入も精算済みレースとして残す。

revisionは1から連続し観測時計は増加。同内容は冪等、既存版差替え不可、前版SHAを鎖にする。訂正時もdecision不変。欠落から後日精算可能への移行も新revisionで残す。

## 境界と非保証

- ネットワーク/DNS/socket送受信、subprocess/os.system、sqlite接続の代表入口は試運転中に禁止。そもそも取得/DB/実推論コードを持たない。これは敵対的コードを隔離するOS sandboxではない。
- SHA/receiptは保存物の整合・順序を検査する。悪意あるローカル改ざん者による全記録の作り直し、時計/sourceの真正性、ハードウェアのfsync保証までは証明しない。
- 実取得原文・実as-of特徴・実推論完了・本番時計/保存先の確認は将来のアダプタ実装時に別途必要。今回の合成採用件数を比較母数に加えない。
- 159の9レース/129頭は原文・全MEV入力・保存完了receipt不足。過去時計を生成して完全契約を通過したとは扱わず、昇格0件と記録する。
- 憲法Vに沿い、1race1判断の使用入力証跡に範囲を限定。汎用オッズAPI archiveの除外やlatest DBを変更しない。実取得原文の保管範囲の明文化は将来アダプタ実装時にも維持する。
