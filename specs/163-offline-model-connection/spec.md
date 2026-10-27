# 163 実モデル・停止中取得アダプタ・実時計保存の接続契約

2026-10-27。162を変更せず別の研究経路を追加する。新規収集、DB接続/更新、学習、定期実行、購入、本番配信は行わない。

## 契約の位置付け

固定値は `mode=offline_model_connection`、`simulated_clock=false`、`synthetic_post=true`、`eligible_for_prospective=false`。実モデルを実行し実時計で保存するが、原入力と予定発走は明示的なローカル合成fixtureである。実配信の観測・実レースの前向き比較ではない。既存9レースにデータを加えない。

この版のCLIは`rehearse`/`verify`のみ。取得を有効にする引数やschedulerは存在しない。`StoppedQuoteAdapter(enabled=True)`とすべての`fetch()`は無条件で拒否する。後から属性を書き換えても通信できるtransport自体がない。

## 1. 実モデル入力・推論証跡

- MEV `mev-ens15-v1`: frozen 2026 manifestと15本体SHAを検査し、全15本の実出力を算術平均する。消費する列は**66数値＋11カテゴリ、計77列**。162の合成試運転は66列だけだったため実接続には不足していた。163ではカテゴリ原値・変換後float32行列も保存する。
- 旧 `mix-129-nj6`: 138特徴と2007年以降の厳密な過去started履歴を明示入力する。6本体・preprocessor・校正器・係数をSHA検査し、既存product経路の校正/補正/平均を実行する。履歴をゼロへ推測補完しない。合成入力ではcareer_starts=0、days_since_last=nullと明示した空履歴を使う。
- 同日体重関連3列は明示nullのpreweight。旧モデルへ現在オッズを混入しない。MEVには使用quoteの価格・人気・市場派生特徴を渡す。
- 各予測にrun_id、入力利用/開始/完了の実時計、全頭確率、全raw特徴、消費行列とdtype/カテゴリ、各memberの出力、入力/出力/モデル/source SHAを保存する。旧履歴・補正入力も含める。
- dictの挿入順で消費行列の列が動かないようartifact定義順を固定する。canonical JSONで保存/読込後も同じ入力証跡となることを検査する。
- モデルファイルを変更・学習しない。loaded Boosterのnative `reset_parameter`を使用せず、MEVはpredict引数num_threads=1、旧は保存済みnum_threads=1のproduct経路を使う。
- `verify`では保存した全入力から実21memberを再実行し、全頭確率と消費証跡を再現する。入力/出力ハッシュを揃えるだけで実行を証明したとは扱わない。

## 2. 停止中取得アダプタ

`quotes.py`はローカル原文と明示metadataを純解析する。形式はnetkeiba単勝JSON。raceIDはAPI本文にない場合があるため、厳密に検査した元URLと、明示された馬番→canonical ID集合で結ぶ。推測のID変換はしない。

返却quoteの必須keyは `format/source/origin/eligible_for_prospective/fetch_started_at/received_at/provider_at/cache/source_url/raw_text/raw_sha256/rows`。formatは`netkeiba_win_json_v1`、origin/sourceは`saved_response`または`local_fixture`、eligibleはfalse。

- 原文のURL/domain/path/race/type、成功応答、明示body raceIDがある場合の一致、全頭集合、重複/価格/人気を検査する。
- `from_saved`は原文bytesをそのまま読んでSHAを保持する。CRLFを勝手に変えず、ファイル読込時計を取得時計にしない。
- 元の取得開始/受信完了時刻は必須。保存fixtureに日付しかない場合、時刻不明のまま鮮度適格とは扱わない。
- 配信側`official_datetime`があればJSTとして解析し保持。受信より未来を拒否し、判断時にはその配信時刻からの30分制限も検査する。
- `make_local_fixture`は今ここで作った試験JSONを明示する。runnerはraw body生成後に別の実時計で完了を観測する。これは配信元からの取得ではない。

## 3. 実時計と保存順序

`RealClock`はoffset/fake-time引数を持たず、UTC wall timeとmonotonic_nsを組で記録する。逆行とwall/monotonicの1秒超の経過差を拒否し、全eventのepochと時計ログを保存する。異なるprocess epochのmonotonic値を直接比較しない。

ローカル試験ではpostを準備開始前の実時計から595秒後に固定し、現在時刻を既定の判断窓内へ置く。これは待機なしで実時計の処理を試すための**合成予定**で、実レースの発走時刻を変更したものではない。計算結果を見てpostや判断窓を動かさない。

引き継ぐ条件:

1. 全頭quote・両予測・消費入力が一致し、モデル/係数の学習末日が対象日より前。
2. `received_at <= input_available_at <= started_at <= computed_at`、各行の入力as-of/更新時刻も判断以前。
3. 全入力/両予測を排他的にpublishし、ファイルと新規ディレクトリentryをfsync/readback後にready receiptを作る。
4. ready保存確認後に判断。判断と封印保存確認の双方で0〜1,800秒以内、発走前、発走10分前を含み9分前を含まない固定窓内、直前guard5秒以内を満たす。
5. sealファイル自身の保存確認時刻でも再検査してからaccepted receiptを残す。acceptedはdecision/sealの保存完了を観測した事後帳簿であり、自分自身の過去保存を証明する時計ではない。
6. 予約・ready・protocol/input/model/member/sourceの結合を採用前に確認。同内容再実行は検証、差替えは拒否。中途sealを採用しない。未採用stagingのabort/復旧規則は162と同じ。

clock.jsonと、取得/入力利用/推論開始終了/ready/判断/seal確認の各時刻を照合する。現source/runtimeが変わればmanifest検証を拒否する。Python/numpy/pandas/LightGBM/scipy/scikit-learn/architectureも記録する。

## 4. 公式払戻と時点の分離

現在の合成予定は未来なので公式結果を持たない。`pending-settlement.json`に両腕の払戻/利益nullを保存し、推測で0円・精算済みにしない。

163の公式精算入口は、原文/結果/全winner払戻一致、race/全頭/公式URL/SHA、観測が判断/発走より後であることに加え、**observed_at <= 現在のRealClock**を要求する。未来の観測時刻を自称した公式fixtureを今受け入れない。同着・不足・不整合・取消・訂正revision規則は162と同じ。確定オッズによる代替はしない。

保存過去の実公式精算は別の `historical_settlement_only` 経路で検算する。159の凍結decision/settled全129行・9レースをSHA照合し、全ID/馬番、winner/払戻集合、保存原HTML/gzip/本文SHAと単勝額を再検査して会計する。

この過去検算では、現在sessionへ過去結果を結合せず、原取得時刻を現在へ移さず、存在しない過去seal/ready receiptを作らない。raw horseIDを`nk:`除去で再結合せず、既に凍結されたcanonical decision/settlement対応と原HTML単勝馬番/金額の一致を検査する範囲を明記する。

## 非保証・次段階との境界

- 実レースのas-of入力作成・実取得・DB読取・実発走guardへの接続はしていない。実推論しても合成特徴での能力評価にはならない。
- SHA/receiptは入力・保存・演算の整合検査。配信元/時計の暗号的真正性や敵対的な全記録再生成を防ぐ署名ではない。
- socket/DNS/送受信/subprocess/SQLiteとSQLAlchemy接続入口を停止するが、敵対コードのOS sandboxとは異なる。取得transportは実装自体が停止専用である。
- 1race1判断の使用入力証跡に限定し、汎用オッズ履歴・既存latest表・汎用archive除外は変更しない。
- 全成果物を比較採用不可に固定し、実運用や収集開始へ自動移行しない。
