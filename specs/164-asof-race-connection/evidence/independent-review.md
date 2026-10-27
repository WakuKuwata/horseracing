# 164 独立設計・実装レビュー

2026-10-27。新規収集・DB接続を行わず、実レースの保存特徴量と保存guardを比較保存契約へ接続する作業を対象とする。

## 事前レビュー

- 特徴値のevent/race dateが過去であるだけでは足りず、当該版の生成・観測・保存/利用可能時刻が判断以前であることをsource原文/receipt・SHAへ結ぶ。現在のDBや後日訂正値から過去特徴を作り直して当時入力とみなさない。
- MEV77列、旧138列・preweight・strict-past履歴を全頭分要求する。既知の欠損nullは保持できるが、未保存列をnullや0で捏造しない。source最大available時刻と入力/行列digestを保存する。
- 旧入力のorigin availableがquoteより前であること自体は正常。現在のmaterialization/replay時刻とorigin availableを区別する。旧133の入力がそろうことを、MEVがそろうことや両予測が当時保存済みだったことの代用にしない。
- guardは自己申告boolだけでなく、保存raw/実capture recordからrace、予定post、ID/馬番/status集合、結果未確定の観測根拠を導出する。before/after/final stageに適切な観測clock、判断以前・post以前・鮮度・同一出走集合を要求する。
- 朝の133 captureをT-10窓の直前seal guardへ昇格させない。保存資料から再確認できるcomponentと主比較採用可能性を別々に数える。
- `nk:`除去や現在IDmappingで過去集合を推測結合しない。取消・除外・予定時刻変更は版と状態を残し、後知恵の再正規化や買い目変更をしない。
- 過去オッズの元受信/配信時計を読み込み時刻へ更新しない。30分以内・両予測durable保存が判断以前・公式払戻の各条件は維持する。
- 現在の実再推論は再現検算であって、当時の実行/保存receiptにはならない。全条件を満たす既存組が無ければ0件のままfail-closedで報告する。
- 公式精算は163と同じく判断から分離し、保存公式原文/全winnerの額・結果集合を検算する。不足は未精算null。同着・訂正を扱っても当時判断は変えない。

## 最終検証

**PASS**。最終run-02と独立攻撃テスト25件を確認した。詳細は末尾の検算範囲を参照。

## 保存133の独立spot-check

保存capture46件を直接読み、before/afterのDB result countは全件0だった。一方、post_prediction_check_atからscheduled_startまでのleadは60.638638〜24,360.962280秒で、現在の固定判断窓（leadが540秒超〜600秒以内）に位置する観測は0件だった。旧モデルの入力が発走前に残ることと、現在採用する判断窓の最終guardが残ることは別である。

## 実装レビューと攻撃テスト

独立テスト `scripts/tests/test_asof_review_164.py` は25件通過。主実装を変更せず、以下を検査した。

- 完全query fixtureのraw/receipt/source時計と全頭集合を検査し、source statusをfixture、前向き適格をfalseに維持。
- 1時間前にsnapshot開始・今完了という遅いqueryを「5秒以内」とみなさない。レビュー指摘後、開始/完了両方を鮮度検査する修正を確認。
- 未来ready、観測前ready、metadataによる元時計上書き、source/receipt SHA改変、path traversal、race/post/field/stage/age-policyの取り違えを拒否。
- 実133のafter guardではfieldを再観測しておらず、decision guardは保存されていないことを保持し、beforeのfieldを流用して完全guardへ昇格させない。
- 特徴量source_times・行・履歴の未来依存、未保存列、未来の永続化receiptを拒否。元feature availableがquoteより前でも正常に保持する。
- 保存実133の16頭/340履歴をcomponentとして読み出せるが、durable receipt欠落のまま完全ペアへ昇格させない。
- rootの完全controlled fixtureで、旧予測の保存receiptが判断+1µsなら拒否、同じquote SHAでもMEV生成時計がquoteより前なら拒否を確認。

主契約ではbefore/after/finalのstage間因果も結合されている。beforeの保存確認はquote開始以前、afterのsnapshot開始は両予測完了以後、finalの開始は両予測durable-readyとafter確認以後である。最終guardだけを判断直前へ結び、before/afterは各工程anchorへ鮮度を適用する。

完成source/frame/predictionのSHA結合と、任意のcaller SHAで既存160登録source pinを置き換えない境界を確認した。一般的な悪意ある全証跡再生成を防ぐ暗号署名という保証ではない。


## 最終成果物の独立検算

正式対象は `artifacts/164-asof-race-connection/run-02`。manifest SHA256 は `5b55ce65eab700a20ecf816d42f04aff9fe10ae36c57de18eb51f0f31d845b8c`。

`independent_asof_audit.py` は164のsource/feature/guard/admission/store/selector/model adapterをimportせず、保存ファイルを直接読み、次を別計算した。163の既存独立auditorの純読取り/ネイティブ計算補助だけを再利用している。

- 全出力・source・入力manifest SHA、予定46レース/621頭、固定判断時計post−600秒、元取得時計の保持を照合。
- 各133原記録から全138特徴・全頭ID/馬番・preweightを比較し、packet/input SHAを再計算。45件でmemory feature時計は固定目標以前、全46件でdurable-ready・MEV完全as-of入力・finalguardがそろわず完全ペア0件。
- before/after/decisionのguard原根拠を再照合。afterのfield未再観測、decision guard未保存を保持し、未保存をtrueや現在時計で補わない。
- 8候補MEV表のschema/行数・77列完備判定を再読取りし、列が存在することを判断以前のavailable証拠と混同しないことを確認。
- 保存実入力16頭/340履歴からネイティブ旧6本を実行し、TE/カテゴリ再encode・isotonic補間・正規化・履歴補正・平均を別計算。現時点の再推論と保存当時確率が一致。再推論時計は元レース発走より後で、historical receiptや当時予測には昇格していない。
- current replayのready時計・全wall/monotonic観測・source SHAを照合。過去の判断を現在新規封印しておらず、acceptedファイルも0件。
- 別の9レース公式HTMLの単勝払戻を再parseし、両案とも8点・800円・公式払戻2,590円の既存会計に一致。現在のreplayへ過去結果を結合せず、元時計を保持。

独立検算結果は `independent-asof-audit.json`。**PASS**。完成契約が通る合成controlled packetのrootテストと、保存実レースでは完全組0件という結果は別の範囲として扱う。新規収集・DB接続なし、前向き比較への追加0件、判定保留を維持する。
