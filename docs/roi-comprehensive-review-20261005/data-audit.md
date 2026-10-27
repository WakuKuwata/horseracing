# ROI実装の独立データ・精算監査（2026-10-05）

担当: `data_audit`。138/139、`market_ev`、注目条件API/UI、実購入精算を読み、READ ONLYトランザクションと純関数の小再現で確認した。新規の買目選別、学習、ROI探索、DB更新、既存ソース編集はしていない。DB確認のレース日付は2007-01-01以降、結果確認の上限は2026-10-04。基礎資料は `docs/roi-missed-patterns-20261004/report.md` と `docs/roi-pattern-exploration-20260930/followup-20261001.md`。

## 確認できた整合点

- 市場モデルの既定入力下限は2007-01-01。同馬の履歴は日付順のshift、騎手・調教師・組合せ集計は当該日を含めない。`days_since_last` は暦日差。今回、2007年以前や当該結果を現在行の履歴特徴に混ぜる変更は確認していない。
- ens15は15本のseed・1スレッド・deterministic・2007年学習開始をspecで照合し、各boosterのSHAをmanifestと照合する。表示と注目条件は同じevalレジストリの定義を使用する。
- 139では注目条件の主精算が `official_win_payouts` に変更され、払戻なし・勝馬との不一致をレース単位で除外する。旧判断時オッズ精算は参考として明示される。S1〜S5の凍結値自体は書き換えず、判断時点への換算を別欄に追加している。
- v2の集計開始日は2026-10-05。監査時のpickは全2,717行がv1、チェックポイント判定は0行であり、v2による利益の前向き確認はまだ無い。過去の研究で採用可になったという実装にはなっていない。
- `buy-time-v2` は2推定量の幅と区間の包絡を表示する近似値で、前向き実績と区別する文がある。「独立検証済み」は計算の再導出確認であり、将来収益の確認ではない。

## A1 / P1: 実購入の単勝精算が公式払戻表に接続されていない

場所: [purchase_view.py:125](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/purchase_view.py:125)、入力取得 [purchase_view.py:46](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/purchase_view.py:46)。

`race_context` は単勝を `RaceHorse.odds` から取り、`settle_bet` は的中時に `amount * odds` を `status='settled_real', is_estimated=False` として返す。139で追加された `OfficialWinPayout` をimport・参照していない。同じアプリ内でも、注目条件は実払戻、実購入記録はオッズ近似という不一致が残る。

実DBを現在の `race_context` と `settle_bet` に渡した再現（100円単勝）:

| レース | 日付 | 馬番 | 保存最終オッズ | 公式表の100円払戻 | 実購入関数の払戻・扱い |
|---|---|---:|---:|---:|---|
| 202607020811 | 2026-08-16 | 5 | 26.5 | 1,340円 | 2,650円・実払戻扱い |
| 202607020811 | 2026-08-16 | 15 | 39.6 | 1,960円 | 計算式は3,960円・実払戻扱い |

馬番5の実行結果: `SettledBet(bet_type='win', selection=[5], amount_yen=100, status='settled_real', hit=True, payout_yen=2650.0, is_estimated=False)`。馬番15は同じDB照合とコード式の確認。両行は同着なので、保存オッズを正しく修復しても払戻との差は残る。JRAも最終オッズは単独勝者を仮定した概算で、同着時に実払戻と異なると説明する。[JRA公式FAQ](https://jra.jp/faq/pop03/1_20.html)

必要な修正: 単勝実購入にも公式払戻表を接続する。払戻未取得を「実払戻」と断定しない。単勝同着2頭、公式なし、公式訂正後の再読込を検証する。今回の監査は実購入記録の書込みや利用者の購入実績の調査をしておらず、上の例を実際に購入した記録があるとは主張しない。

## A2 / P1: ワイドの3着同着ペアを的中させ、架空の推定払戻を出す

場所: [settlement.py:65](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/settlement.py:65)、推定への接続 [purchase_view.py:131](/Users/kuwatawaku/workspace/horseracing/api/src/horseracing_api/purchase_view.py:131)。

`wide` は2頭がともに `rank <= 3` であれば的中とする。しかし3着同着の2頭同士のワイドは不的中。[JRA公式・馬券の種類](https://jra.jp/kouza/beginner/baken/index.html)

純関数再現:

```python
from horseracing_api.settlement import is_hit
is_hit("wide", [3, 4], {1: 1, 2: 2, 3: 3, 4: 3}, 8)
# True（正しくはFalse）
```

さらに実DBの `202608040204`（2026-10-04）の馬番3・9はともに3着。現在の `race_context` と `settle_bet` に100円ワイド `[3, 9]` を渡すと `status='settled_estimated', hit=True, payout_yen=5824.55037267742`。存在しない的中組だから公式配当が無いのに、それを「的中だが配当未取得」と解釈して推定配当に進む。

必要な修正: 同着を含む的中可否を先に正しく確定する。「1着＋3着」「2着＋3着」は的中、「3着＋3着」は不的中、同着のない3組も的中、を同じテスト族にする。全購入精算、研究scorer、確定配当キーとの一致を比較する。

関連する静的発見: [betting/exotic_selection.py:82](/Users/kuwatawaku/workspace/horseracing/betting/src/horseracing_betting/exotic_selection.py:82) はワイドにも複勝の `place_top_n` を流用し、5〜7頭では3着を対象外とする。APIのワイド判定とも不一致。通常package importによる追加再現はtraining環境の `horseracing_serving` 依存欠落で止まったため、こちらの独立実行確認は親担当へ引き継いだ。A2のAPI側実再現はその依存を使わず成功している。

## A3 / P2: 「オッズ取得時刻」が出走馬行の更新時刻であり、価格鮮度を保証しない

場所: [market_ev.py:50](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/market_ev.py:50)、[market_ev.py:518](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/market_ev.py:518)、[upsert.py:204](/Users/kuwatawaku/workspace/horseracing/scrape/src/horseracing_scrape/upsert.py:204)、[attention.ts:126](/Users/kuwatawaku/workspace/horseracing/front/src/lib/attention.ts:126)。

読み込みは `rh.updated_at AS horse_row_updated_at`、予測出力ではそれを `odds_observed_at` と改名する。一方 `upsert_entries` はオッズを一切更新せず馬体重・騎手・性齢・出走状態等をUPSERTする。実DBの `race_horses` は全UPDATEに対するトリガを持ち、関数内容は `NEW.updated_at = now()`。したがって出走表だけを再取得した場合も「オッズ取得時刻」が進む。

これはコードと稼働DBのトリガ定義で確認した構造上の問題。既存pickで何件の10分/60分区分が誤ったかは測定していない。無関係な更新によって古いオッズを新鮮に表示し、前向き集計の判断時鮮度帯も誤分類できる。137のplanが明示的に `updated_at` を採用しているので、実装の写し間違いというより記録仕様の不足。

必要な修正: 単勝quote固有の取得時刻（可能ならsourceのofficial時刻も）を保持し、現在値・pick・鮮度の全経路で使用する。price非更新のentries再取込では時刻を進めない。これはoddsの長期全履歴を要求する話ではなく、現在quoteと判断時点snapshotの来歴の話。

## 未検証の判断パターンを検証する前に必要な記録

### B1: 初回の選択外馬と全市場・特徴量が残らず、代替判断の再生ができない

場所: [attention_picks.py:123](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/attention_picks.py:123)、[attention.py:54](/Users/kuwatawaku/workspace/horseracing/db/src/horseracing_db/models/attention.py:54)、[market_ev.py:616](/Users/kuwatawaku/workspace/horseracing/training/src/horseracing_training/market_ev.py:616)。

scanは初回0該当も残すが、pickは該当した馬だけ。scanに全馬のオッズ・確率・入力featuresは無く、pickもEV・odds・gap等だけ。市場予測は次回計算でDELETE/INSERTされる。監査時の5,827scan中、結果前のscanは18レース。その18レースすべてで、初回 `run_id` は現在のens15予測表には残っていない。初回0pickのレースも含む。これを「初回記録が無い」とは呼ばないが、初回の全馬判断を再計算する材料はattentionテーブル群からは得られない。

このため、同じ判断時点における (a) S3と本命・MCU拒否等の対照、(b) 初回対発走直前、(c) 天候・馬場更新を固定した価格だけの比較、(d) 条件から漏れた馬の検証、を保存pickだけで厳密に行うことはできない。R02の本番再現M2が74.1%だった既知の問題とも整合する。新しい前向き検証は全馬の予測・全市場ベクトル・入力snapshot/ハッシュ・モデルmanifestハッシュ・価格取得時刻を同じrunで凍結する必要がある。現在の選択固定ROI集計を否定する問題ではなく、代替判断と原因分解のための不足。

### B2: 公式払戻の欠落と古い保存オッズは、研究の追加実行でも区別が必要

監査時の公式表は505行・504レース、期間は2026-08-15〜10-04。139の適用記録（500行・499レース）より5レース増えているため、報告の固定件数を現在値として再利用しない。公式表と現保存オッズ×100の不一致は上記の同着2行だった。

一方、2026-07-04〜08-09の結果あり平地419レースは全419が公式払戻なし。これを全件オッズ汚染と断定してはいけないが、この期間の近似精算を「139で全修復済み」とも扱えない。新規ルールは旧freeze入力、修復後現在DB、公式取得可能集合を別名で固定し、欠落レースを勝ち馬単位で落とさず、同一母集団の対照・coverageを添えて測る必要がある。

### B3: 前向き0件と研究上の換算値を、常設の期待回収率注記でも区別する

[ExpectedReturnNote.tsx:20](/Users/kuwatawaku/workspace/horseracing/front/src/components/ExpectedReturnNote.tsx:20) は106.4%/111.3%を常設表示し、発走前オッズの前向き検証は未実施とする。139の約85%という判断時換算は注目条件の展開/一覧に別途ある。数値が数学的に矛盾するわけではないが、常設注記からその選定価格の差と換算への導線が読み取れない。v2の実績が増えても固定文の「まだ行っていません」は自動更新されない。表示の採用判断を変えることなく、確定寄りの過去選定と判断時点選定が違う点を常設注記へ短く揃える余地がある。

## 追加検証の優先順

1. 先に精算器の同着・少頭数・公式欠落・返還・訂正を小さい固定例で照合する。買い方の良否を比較する器が異なる状態で、ROIの追加最適化をしない。
2. 2007年以降の同じ固定買目に対し、保存オッズ近似と公式払戻を比較する。違いを発見しても、その結果で買目を選び直さない。
3. 判断時点の全馬snapshotがある共通集合だけで、S1/S3と本命・MCU拒否・賭けない対照を比較する。snapshot不足を現在DBの更新済み天候・馬場・価格で埋めない。
4. 初回計算とT−10分等の時刻ルールは別の前向き規約として固定する。現行は「最初に計算できた時点」であり、直前の価格で買えるという研究結果にはならない。

既存の研究がROI>100%を保証しないという結論を覆す証拠は、この監査では得ていない。新しく確定したのは、アプリの別精算経路に実際の誤判定が残ることと、判断時刻を比較するための来歴が不足すること。
