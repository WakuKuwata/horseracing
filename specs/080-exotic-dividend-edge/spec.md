# Feature Specification: Real Exotic Dividend Ingestion & Exotic Edge Measurement

**Feature Branch**: `080-exotic-dividend-edge`
**Created**: 2026-07-23
**Status**: Draft
**Input**: 現行モデル lgbm-065 は WIN 市場で市場効率に勝てず ROI 天井 ~0.82 < 1.0(policy-gate-eval で実証済)。ROI>1.0 に届きうる唯一の道 = WIN より非効率な exotic 市場で 009 の per-race joint(PL/Harville)EV が実配当を上回るかを測ること。だが実 exotic 配当(`exotic_odds` テーブル)は 0 行で一度も取得していない。この feature はその測定を可能にする最小の enabling step。

---

## 背景と目的 *(context)*

このリポジトリの製品目的は「正直な意思決定支援」であり、市場超過は採否バーではない([[product-goal-decision-support]])。しかし ROI を上げたいという要求に対し、WIN 市場でのモデル改善路線は**枯れたことが最新最良モデルで実証された**:lgbm-065(features-018/F02・歴代最良精度 win LogLoss 0.2145)でも odds-cap ROI 天井は ×0.816 で lgbm-061 世代 ×0.818 と実質同一([[lgbm-065-roi-ceiling-confirmed]])。精度ゲインは ROI に 0 変換。天井 <1.0 は市場効率という構造であってモデル品質でない。

ROI>1.0 に手が届きうるのは **WIN より非効率な exotic 市場**で 009 joint の EV が実配当を上回る場合だけ。だがそれを**測る材料(実 exotic 配当)が存在しない**。本 feature は「儲ける feature」ではなく「**exotic edge が存在するか否かを正直に測れる状態を作る** feature」である。edge の有無は本 feature の成果ではなく、収集後の測定で初めて分かる(null 結果も成功)。

**この feature が主張しないこと**(honest limitations):
- exotic で儲かるとは主張しない。控除率は WIN(20%)より高い(馬連/ワイド 22.5%・馬単/三連複 25%・三連単 27.5%)= 構造的逆風。それを「exotic プールは casual money で非効率」が上回るかは**未知**。
- 実配当が統計的に十分貯まるまで、いかなる edge も主張しない。
- 過去全レースの実配当は取得しない(netkeiba 大量 backfill はブロック領域)。答えは前向き収集で数週間〜数ヶ月かけて出る([[feature-065-prospective-shadow-log]] と同型)。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 実 netkeiba 払戻ページから exotic 配当を parse できる (Priority: P1)

オペレータが実際の netkeiba result ページ HTML を parser に渡すと、全券種(複勝/枠連/馬連/ワイド/馬単/三連複/三連単)の確定配当が正準 selection 形式で抽出される。現状 `parse_exotic_odds` は fixture 形状(合成クラス名 `table.exotic`/`data-bet-type`)で実 markup(`Payout_Detail_Table`)に非対応=1 レースも取れない。

**Why P1**: これが全ての前提。parser が実 markup を読めなければ以降のデータ収集も測定も成立しない。

**Independent Test**: 実 netkeiba result ページ fixture を 1〜2 枚捕獲し、期待する券種・selection・配当・同着分割・複勝複数払戻を parser が正しく返すことを network-free に検証。

**Acceptance Scenarios**:
1. **Given** 実 result ページ HTML(確定済みレース), **When** parse する, **Then** 存在する全対応券種の (bet_type, selection 正準配列, 配当倍率) が抽出される
2. **Given** 同着で複勝が 4 頭払い戻し, **When** parse する, **Then** 各当選 selection が別行として全て抽出される(取りこぼしなし)
3. **Given** 未対応券種(枠連グリッド等)や欠損テーブル, **When** parse する, **Then** その券種はスキップされ他券種の抽出は継続(partial coverage)
4. **Given** 発走前(結果未確定)の result ページ, **When** parse する, **Then** 配当行ゼロ=空を返す(誤った確定値を作らない)

### User Story 2 - 日次 results 処理で実配当が前向きに貯まる (Priority: P1)

オペレータの日次 result 取得(既存 `scrape_results`)で、同じ result ページから exotic 配当も抽出・保存される。result ページは既に fetch(cache 済)されているため**追加の netkeiba リクエストは発生しない**。保存は既存 `exotic_odds` テーブル(migration 0005)に (race_id, bet_type, selection) 単一最新値で冪等 upsert(憲法 V:snapshot 履歴なし・post-result 上書き)。

**Why P1**: parser 単体では DB は空のまま。日次相乗りで初めて実配当データセットが育ち始める。

**Independent Test**: 実 DB で日次 result 処理を 1 日分走らせ、その日の確定レースの exotic_odds 行が生成され、再実行で行数が増えず値が一致(冪等)、netkeiba への追加リクエストが 0 であることを確認。

**Acceptance Scenarios**:
1. **Given** 確定済みレースの result 取得, **When** 日次処理する, **Then** そのレースの exotic 配当が exotic_odds に保存され、result(着順)保存と原子的に整合
2. **Given** 同一レースを再取得, **When** 再処理する, **Then** exotic_odds 行は重複せず値が一致(冪等 upsert)
3. **Given** 結果未確定レース, **When** 日次処理する, **Then** exotic_odds には何も書かない(部分・誤確定値を混ぜない)
4. **Given** 相乗り parse が例外, **When** 日次処理する, **Then** result(着順)保存は成功したまま・exotic だけ skip・監査に記録(既存 result 経路を壊さない)

### User Story 3 - 実配当が貯まったら exotic edge を pre-registered ゲートで測れる (Priority: P2)

十分な実配当が貯まった後、アナリストが 009 joint EV(モデル p 由来)と実配当を突き合わせ、(a) 推定オッズ vs 実配当の乖離(exotic-divergence)と (b) 券種別の realized ROI(exotic-backtest)を、**結果を見る前に固定した pre-registration** に沿って測定する。edge は事前登録した最小サンプル数・baseline 超過・多重比較補正を満たすまで主張しない。

**Why P2**: データが貯まるまで実行できない。US1/US2 が先に価値を出す(データ収集自体が前進)。

**Independent Test**: 蓄積された実配当サブセットで exotic-divergence / exotic-backtest を走らせ、pre-registration 文書に記載した券種・窓・baseline・最小 n・成功条件のみで verdict を出す(結果を見てから条件を動かさない)。

**Acceptance Scenarios**:
1. **Given** 実配当 n が最小サンプル未満, **When** ゲートを走らせる, **Then** verdict=NO_DECISION(データ不足)で edge を主張しない
2. **Given** 実配当が十分, **When** ゲートを走らせる, **Then** 券種別に realized ROI・baseline(最低オッズ/uniform)超過・cluster-bootstrap CI を出し、事前登録条件を満たす券種のみ ADOPT 候補として報告
3. **Given** 過去の当たり穴目を拾う overfit の疑い, **When** walk-forward/out-of-sample で検証, **Then** in-sample の見かけ edge が OOS で崩れれば REJECT

### Edge Cases

- **同着(dead heat)**: 複勝・ワイド等は複数当選 selection が別配当で払い戻される → 全行を保存、bet-level で採点(011/012 の place/wide MULTIPLE 規約を踏襲)。
- **結果未確定レース**: 発走前の result ページには確定配当がない → 空を返す。exotic_odds に書かない。憲法 V の「post-result 上書き」は確定後のみ発火。
- **partial coverage**: 一部券種のみ抽出できた場合 `coverage_scope='partial'`、全券種グリッドが期待数と一致すれば `'full'`(期待数テストで担保)。
- **cache が volatile**: win odds は use_cache=False(発走前変動)。exotic 配当は確定後不変なので result ページ cache 再利用は安全だが、**確定前 cache を配当ソースにしない**(結果確定シグナルで gate)。
- **取消・不成立レース**: 該当券種が存在しない → その券種スキップ、他は継続。
- **netkeiba markup 変更**: 将来クラス名が変わると parser が黙って 0 行を返しうる → 期待券種数の下限チェックで異常検知(silent-empty を fail にする)。
- **edge 測定のサンプル不足**: 前向き収集初期は n が小さい → NO_DECISION を正しく返し、偽の勝ちを出さない。

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: parser は実 netkeiba result ページ(`Payout_Detail_Table` 相当)から、対応 6 券種(複勝/馬連/ワイド/馬単/三連複/三連単)+ 単勝(既存 win 経路と重複しない範囲)の確定配当を抽出できる MUST。selection は 011 `to_selection` 正準形(ordered exacta/trifecta・sorted quinella/wide/trio・single place)で表現する MUST。
- **FR-002**: parser は同着による複数払戻(複勝・ワイド等の複数当選 selection)を取りこぼさず全行抽出する MUST。
- **FR-003**: parser は未対応券種・欠損テーブル・結果未確定ページを、他券種の抽出を止めずに安全にスキップする MUST(partial coverage・空返し)。
- **FR-004**: 日次 result 処理は、既に fetch 済みの result ページから exotic 配当を抽出し、**追加の netkeiba リクエストを発生させない** MUST。
- **FR-005**: exotic 配当の保存は既存 `exotic_odds` テーブルに (race_id, bet_type, selection) 単一最新値で冪等 upsert する MUST。snapshot 履歴を持たない MUST(憲法 V)。**スキーマ変更・migration を追加しない**(0005 で既存)。
- **FR-006**: exotic 配当の書き込みは**結果確定後のみ** MUST。結果未確定レースに配当行を作らない MUST。
- **FR-007**: 相乗り parse の失敗は既存 result(着順)保存経路を壊してはならない MUST(例外隔離・監査記録)。
- **FR-008**: exotic 配当は**決してモデル特徴・校正入力にしない** MUST(憲法 II リーク境界)。結果は edge 測定の採点にのみ使う。
- **FR-009**: exotic edge 測定は、券種・評価窓・baseline・最小サンプル数・成功条件・多重比較補正を**結果を見る前に pre-registration 文書へ固定**する MUST。実配当 n が最小未満なら verdict=NO_DECISION を返す MUST。
- **FR-010**: exotic edge 測定は 009 joint EV = P_model(モデル p 由来)× 実配当で行い、p≠q を保つ MUST(市場 q をモデル確率として使わない)。実配当が無い券種は 010 推定(double-pseudo)で分離ラベルする MUST。
- **FR-011**: 過去全レースの実配当を取得する大量 backfill は**スコープ外** MUST(netkeiba 負荷回避)。歴史配当は netkeiba cache に既にある分のみ機会的に利用する。
- **FR-012**: parser・upsert・edge scorer は他パッケージへの逆依存を作らない MUST(scrape は betting/features を import しない・既存 import-graph 境界を維持)。

### Key Entities

- **exotic_odds**(既存, migration 0005): (race_id, bet_type, selection JSONB, odds=確定配当倍率, coverage_scope, source, updated_at)。UNIQUE(race_id, bet_type, selection)。単一最新値・append しない・upsert 上書き。
- **ScrapedExoticOdds**(既存 model): parser 出力。実 markup 対応で中身の抽出ロジックのみ差し替え(契約は不変)。
- **exotic edge pre-registration**(新, 文書 artifact): 券種・窓・baseline・最小 n・成功条件・補正を結果前に固定(068/073 の採用ゲート pre-registration と同型)。append-only 監査。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 実 netkeiba result ページ fixture に対し、parser が対応全券種の配当を期待値どおり(同着分割・複勝複数払戻を含む)抽出できる(network-free fixture テストで検証)。
- **SC-002**: 日次 result 処理 1 日分で、その日の確定レースの exotic 配当が exotic_odds に生成され、**netkeiba への追加リクエストが 0**(既取得 result ページ相乗り)。
- **SC-003**: 同一日を再処理しても exotic_odds の行数が増えず値が一致(冪等)。
- **SC-004**: 相乗り parse を意図的に失敗させても、既存 result(着順)保存が成功し続ける(回帰なし)。
- **SC-005**: exotic 配当がモデル特徴・校正に流入しないことを leak-guard テストで機械的に固定(配当を変えてもモデル予測 byte 不変)。
- **SC-006**: exotic edge 測定は、実配当 n が事前登録した最小未満のとき NO_DECISION を返す(偽の勝ちを出さない)。
- **SC-007**: 前向き収集が稼働し、日次で exotic_odds の被覆(確定レース中の配当取得率)が可視化できる。

### Non-Goals(スコープ外)

- exotic で儲かることの証明・実運用ベッティング(本 feature は測定基盤のみ)。
- 過去全レースの実配当大量 backfill。
- exotic を betting 推奨経路の default にすること。
- 発走前 exotic オッズ(オッズ変動)の取得・snapshot 履歴化。
- スキーマ変更・新テーブル・API/front 変更(edge 測定は CLI/artifact 完結)。

---

## Assumptions

- netkeiba は日次相当の低負荷 scraping には応答する(2026-07-21 まで odds/entries/results 成功実績)。大量 backfill のみブロック領域。
- netkeiba result ページ(`result_url`)は確定後に全券種の払戻を含み、日次 `scrape_results` が既にこれを fetch・cache している。
- `exotic_odds` テーブル(migration 0005)・`ScrapedExoticOdds` model・`upsert_exotic_odds`・`scrape-exotic-odds` CLI・`exotic-backtest`/`exotic-divergence` CLI は配線済みで、本 feature は parser の実 markup 対応・日次相乗り・pre-registered 測定の 3 点を足す。
- exotic 配当は確定後不変(単一最新値=憲法 V に整合、win odds のような発走前変動なし)。
- 控除率は JRA 既定(馬連/ワイド 22.5%・馬単/三連複 25%・三連単 27.5%)を logic_version に記録。

---

## Constitution Self-Check *(codex unavailable — self-review)*

**codex unavailable**: 設計レビューを 2 回 `codex exec` で試行したが、いずれもリポジトリ AGENTS.md の「second opinion を並走させる」指示に引っ張られ前置きのみでレビュー本文が出力されず(既知の repo-skill/parallel-agent derail、[[codex-env-recovery]])。CLAUDE.md「同一タスク再試行は最大 1 回」に従い打ち切り、以下セルフレビュー checklist で代替。

- **憲法 II(リーク境界)**: exotic 配当・オッズはモデル特徴/校正に流入させない。結果は edge 採点のみ。leak-guard テスト(SC-005)で機械固定。scrape→betting/features 逆依存なし(FR-012)。
- **憲法 III(事前登録・OOS)**: edge 測定は券種/窓/baseline/最小 n/成功条件を結果前に固定(FR-009)。in-sample の見かけ edge を OOS/walk-forward で潰す(US3-AC3)。NO_DECISION 許容。
- **憲法 IV(確率不変)**: モデル p・win 予測は本 feature で不変(exotic は読み取り・採点のみ)。
- **憲法 V(単一最新値・監査)**: exotic_odds は snapshot なし・冪等 upsert・post-result 上書き(FR-005/006)。pre-registration は append-only 監査。
- **憲法 VI(契約先行・スキーマ不変)**: migration 追加なし(0005 既存)・API/front 不変。parser は既存 model 契約を保ち抽出ロジックのみ差し替え。

**セルフレビューで確認した設計上の穴と対応**:
1. **相乗りの確定タイミング**: result ページの配当は結果確定後のみ有効 → 確定シグナル(既存 result 保存の成立)を gate にし、未確定ページから配当を書かない(FR-006・Edge Cases)。
2. **silent-empty リスク**: netkeiba markup 変更で parser が黙って 0 行 → 期待券種数の下限チェックで異常検知(Edge Cases)。
3. **統計的健全性**: 前向き収集初期の小 n で偽の勝ちを出さない NO_DECISION 設計(FR-009/SC-006)。
4. **overfit(過去の当たり穴目)**: pre-registration + OOS 検証で防ぐ(US3)。
5. **feature 分割**: US1(parser)/US2(収集配線)は本 feature で一体(parser 単体では価値ゼロ)、US3(edge 測定)はデータ蓄積後に走るため実行時点が分離するが spec は 1 本に保持(pre-registration は着手時に文書固定)。

---

## Dependencies & Sequencing

- US1(parser)→ US2(日次相乗り)は同一 feature 内で順次。US2 稼働で前向き収集開始。
- US3(edge 測定)は実配当が pre-registered 最小 n に達してから実行(数週間〜数ヶ月後)。spec 着手時に pre-registration 文書だけ先に固定する。
- 既存日次オペレーション([[local-db-setup]] の worker/ops)への相乗りが前提。
