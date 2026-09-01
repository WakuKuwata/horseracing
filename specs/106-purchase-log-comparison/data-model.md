# Data Model: 実購入記録と三者比較 (106)

## purchase_records (新テーブル・migration 0017・append-only)

| 列 | 型 | 意味 |
|---|---|---|
| purchase_record_id | uuid PK | |
| race_id | text NOT NULL | 対象レース(races FK) |
| kind | text NOT NULL | as_presented / modified / skipped_presented / no_recommendation / presentation_unavailable / freeform / correction / void (codex Q2: 見送りは 3 区分 — 提示ゼロの検証済みだけが政策線ゼロとして正当・unavailable は不明でありゼロではない) |
| bets | jsonb NOT NULL | 実際の購入明細 [{bet_type, selection, amount_yen, odds_used?}] 。skipped は [] |
| presented_snapshot | jsonb | 記録時に画面に出ていた提示(選択・整数金額・オッズ値/出所/時刻・win_policy・**snapshot_schema_version**)。correction/void は複製せず null(元行参照・codex Q2)。freeform/presentation_unavailable も null |
| prediction_run_id | uuid | 参照した run(snapshot 内と同値・検索用に列にも) |
| corrects_record_id | uuid | correction/void の対象行。それ以外は null |
| result_pending_at_record | boolean NOT NULL | 記録時に race_results 行が無かったか(観測事実・D5 codex Q4: 「発走前」の主張はしない) |
| pending_basis_at | timestamptz NOT NULL | 上の判定を行った時刻(根拠の恒久記録) |
| client_request_id | text NOT NULL UNIQUE | 冪等キー(D8) |
| payload_hash | text NOT NULL | 同一 id 別内容の検出(D8: 一致=リプレイ 200 / 不一致=409) |
| recorded_at | timestamptz NOT NULL | |
| note | text | 利用者メモ(任意) |

- **INV-P1 (append-only)**: UPDATE/DELETE は DB トリガで拒否(084 前例)。訂正は correction 行
- **INV-P2 (有効状態の導出)**: レースの現在の記録 = そのレースの行を recorded_at 順に畳んだ結果
  (correction は対象を置換・void は無効化・**void は bets=[]**)。導出は読み取り側の純関数 1 箇所。
  **同一レースへの 2 本目の非 correction 行は書き込み時に 422 で拒否**(U1: 暗黙の置換を
  畳み込みに持ち込まない — 置換は必ず明示の correction)
- **INV-P3 (観測事実の恒久性)**: result_pending_at_record / pending_basis_at は保存後不変
- **INV-P6 (冪等)**: client_request_id 一意。同一 id+同一 payload_hash の再送はリプレイ・不一致は 409
- **INV-P7 (run 整合)**: prediction_run_id はクライアント送信値をそのまま保存(サーバは race_id との整合のみ検証・「最新」の推測代入はしない)
- **INV-P4 (bets の形)**: selection は 011 の canonical 形(順序券種=順列 / 集合券種=昇順)。
  amount_yen は 100 円単位の正整数
- **INV-P5 (リーク境界)**: features のどの loader もこのテーブルを SELECT しない(leak-guard テスト)

## 導出(保存しない・読み取り時計算)

### 有効記録 (EffectiveRecord)
レースごとに行系列を畳んで得る現在の状態。行動種別・購入明細・発走前フラグ・訂正回数。

### 精算 (Settlement) — レコード内の 1 買い目ごと
- 状態: pending(未確定) / settled_real(公式配当) / settled_estimated(推定オッズ精算・二重疑似) /
  refunded(返還) 
- 的中判定は公式結果のみ(011 規約)。払戻: win=race_horses.odds(公式)、exotic=exotic_odds を
  第一・欠落時は 010 推定オッズ(clarify Q2=B)。**win はオッズ欠落時 pending(精算不能・
  件数開示)** — 010 推定は win オッズ由来なので win 自身の欠落は推定でも埋められない(U4)
- 実配当が後から取り込まれたら settled_estimated → settled_real に自動遷移(読み取り時計算なので
  状態保存なし)

### 三者比較 (ComparisonSeries)
- 応答に `as_of`(計算時点)と推定器の出所を含める(codex Q3: 実配当の後着による数値変化を正当な restatement として明示)
- 系列の順序は**レースの時系列**(記録時刻ではない)。訂正は元レースの時点に効く
- 対象レース集合 = 有効記録のあるレース(見送り含む)
- 実購入線 = Σ(settled の払戻 − 購入額)。全券種。うち推定精算の件数・金額を併記
- 政策線 = Σ(presented_snapshot 内の**単勝**買い目の counterfactual_snapshot 精算)(clarify Q1=A)
- 賭けない線 = 0
- 対称ビュー: 実購入線を単勝のみに絞った切替(clarify Q1)
- 事後入力を含む/除くの切替(既定=含む・件数明示)
- 訂正件数 n_corrections を応答に含める(US3・G1)

### 記録率 (CoverageRate)
期間内の全開催レース数を分母、**correction/void を畳んだ後の有効記録**があるレース数を分子
(clarify Q3=A + codex Q5)。結果取込前の記録による率と取込後(事後入力)による率を分けて開示。
