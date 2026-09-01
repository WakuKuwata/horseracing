# Research: 実購入記録と三者比較 (106)

## D1: 書き込み経路 — ops に同期 POST(api の read-only を守る)

**Decision**: 記録の書き込みは `POST /ops/v1/purchase-records`。api(:8000)は GET 2 本の追加のみ。
front に /ops proxy を追加(admin の 053 パターン・ops-openapi snapshot の byte 一致テストつき)。
同期書き込み(ジョブ化しない)。

**Rationale**: 憲法 VI「api は read-only(全 path GET をテストで固定)」は 014 以来の境界で、
崩す理由がない。書き込み系はすでに ops が担う(053 の refresh-range が前例)。記録は対話操作で
あってバッチ取込ではないので、ingestion_jobs のジョブ機構(advisory lock・ワーカー drain)には
載せない — 単純な同期 upsert…ではなく append-only insert。

**Alternatives considered**: (a) api に POST を足す = read-only 契約の破壊(全 GET テストの撤廃)。
契約変更のコストが大きく、得るものは proxy 1 本の節約だけ → 不採用。(b) ジョブ化 = 対話操作に
202+ポーリングは過剰で、記録の確認表示が非同期になる → 不採用。

## D2: 提示スナップショットを記録に凍結する

**Decision**: 記録行は「その時画面に表示されていた提示(買い目群・金額・使用オッズ・
prediction_run_id)」を丸ごと JSONB で凍結保存する。政策線の反実仮想はこのスナップショット内の
**単勝**買い目から計算する。

**Rationale**: 提示金額 = 予算 × stake_fraction の床関数(087)で、**予算は front の
localStorage にしかない**(サーバ非送信・087 の設計)。サーバ側で「当時の提示」を再現する
手段が構造的に無いため、記録時に凍結するのが唯一の再現可能な形。オッズも
`market_odds_used`(凍結値)を写す — 075 の counterfactual_snapshot 命名規約に従う。

**Alternatives considered**: (a) 予算をサーバに送って保存 = 087 の「予算はサーバ非送信」を
覆す設計変更で、しかも予算の変更履歴まで要る → 過剰。(b) 政策線を「現在の予算」で再計算 =
予算を変えるたび過去の政策線が動く(監査不能) → 不採用。

**codex Q2 修正の反映**: スナップショットには 選択・整数金額・オッズ値/出所/時刻・
**snapshot スキーマ版**を凍結。correction/void 行はスナップショットを**複製せず**元行を参照
(append-only の調整として表現)。見送りの下位区分を分ける:
`skipped_presented`(提示を見て見送り)/`no_recommendation`(検証済みの提示ゼロ=政策の
反実仮想もゼロで正当)/`presentation_unavailable`(提示が取得できなかった=**不明であって
ゼロではない**・政策線から除外し件数開示)。`prediction_run_id` は**クライアントが実際に
描画に使った値を送信**し、サーバは race_id との整合だけ検証する(サーバ側で「最新」を
推測して埋めない — 表示と記録の食い違い防止)。

## D3: 三者比較は読み取り時計算(保存しない)

**Decision**: 比較系列・記録率は保存せず、GET のたびに記録行+公式結果から導出する。

**Rationale**: 訂正・取消・配当の後着(推定精算→実精算の自動置換, clarify Q2)があるため、
導出値を保存すると必ず食い違う。レース数は高々数百/月で読み取り時計算のコストは無視できる。
021 の規律(オフライン計算→永続化→読むだけ)は「重い計算」のためのもので、ここは軽い。

**codex Q3 修正の反映**: 決定論的リプレイを契約に含める — (a) 応答に `as_of`(計算時点)を
含め、実配当の後着による数値の変化を**正当な restatement** として明示できるようにする
(b) 訂正は**元レースの時点**に効く(系列の順序はレースの時系列であって記録時刻ではない)
(c) 1 回の計算は単一の一貫した DB スナップショットから読む
(d) 推定精算に使った推定器の出所(devig 方式・takeout 前提)を応答に併記。

## D4: 的中判定と精算の実装位置

**Decision**: win は既存 `api/backtest.py::win_realized` を流用。exotic の的中規約
(011: exacta/trifecta=順序・quinella/trio=集合・wide=包含・place=頭数規則)は api 内に
純関数で実装する(betting は import しない)。配当は `exotic_odds` を第一、欠落時は
probability(010)の推定オッズで精算し、行に推定マーク。

**Rationale**: api→betting の import は 014 の境界違反。049 で同型の二重実装を
「definitional(定義そのもの)なのでドリフト面は実質ない」と判断した前例に従う。
的中規約は 011 の仕様が正本で、テストは仕様の数値例で固定する。

## D5: 「発走前」でなく「結果取込前」を記録する(codex Q4 修正)

**Decision**: 保存するのは**観測した生の事実** `result_pending_at_record`(記録時に race_results
行が無かったか)+ 判定根拠と時刻。「発走前」という客観的事実としては扱わない。
UI 文言も「結果取込前に記録 / 結果取込後に記録」とし、恒久注記「取込遅延により実際は
発走後だった可能性があります」を添える。

**Rationale**: codex 指摘 — 結果行の不在が証明するのは「まだ取り込まれていない」ことだけで
「レースが走っていない」ことではない。観測事実をそのまま保存し、解釈は表示層で限定する
(065 の shadow-log が受け入れたのと同じ限界クラス)。post_time があるレース(2025-10+)では
`記録時刻 >= post_time` に「発走後の可能性」の弱いマークを付けられる(補助・恒久保存しない)。

## D6: append-only の DB 強制

**Decision**: purchase_records への UPDATE/DELETE を DB トリガで拒否(084 の chaos_readouts
前例)。訂正・取消は新しい行(kind=correction/void + 対象行への参照)。

**Rationale**: FR-003。アプリ層の規約だけだと将来のコードが黙って UPDATE できる。
自己欺瞞(負けた記録を後から消す)の防止はこの feature の目的そのものなので、DB レベルで固定する。

## D7: リーク境界

**Decision**: purchase_records は features のどの loader からも読まれないことをテストで固定
(leak-guard)。予測・買い目生成・精算ロジックは一切変更しない(FR-011)。

**Rationale**: 憲法 II。利用者の行動がモデルに還流すると「自分の購入がオッズ経由でなく
直接特徴に入る」新しいリーク面になる。

## D8: 書き込みの冪等 = client_request_id + payload hash(codex Q1/Q5)

**Decision**: POST は `client_request_id`(クライアント生成)を必須にし、DB の一意制約 +
payload hash で守る。同一 id + 同一 payload の再送 = 既存行を 200 で返す(リプレイ)。
同一 id + 異なる payload = 409 conflict。advisory lock は使わない(単一行 insert の
原子性で足り、跨行の不変条件が無い)。

**Rationale**: codex — ジョブ機構の advisory lock は取込用の道具で、対話的な単一行書き込みには
一意制約 + 単一トランザクションが正しい形。payload hash が無いと「同じ id で別内容」が
黙って握りつぶされる。

## D9: append-only の検証は実行時ロールで(codex Q5)

**Decision**: UPDATE/DELETE 拒否トリガに加え、**TRUNCATE の revoke**・FK の cascade delete が
無いことを、テストは**実行時の DB ロール**で検証する。migration downgrade はトリガ/関数を
きれいに除去し grant を復元する。

**Rationale**: superuser でテストするとトリガ・権限の穴が見えない。
