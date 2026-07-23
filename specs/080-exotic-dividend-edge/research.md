# Phase 0 Research: Real Exotic Dividend Ingestion & Exotic Edge Measurement

## D1. 実 netkeiba result ページの払戻 markup(最大リスク)→ **RESOLVED(T0 spike 2026-07-23)**

**T0 spike 結果(実施済・fixture `scrape/tests/fixtures/real/results_202602011206.html` 捕獲)**:
- live result ページ(84KB)に **`Payout_Detail_Table` × 2** + `Result_Pay_Back` ブロックが含まれる → **日次 result 相乗りで追加リクエスト 0 が実物で確認**(最大リスク解消)。
- netkeiba 疎通 OK(単一 capture-fixture fetch 成功=日次相当の低負荷はブロックされていない再確認)。
- 実 markup を完全に把握し **contracts/parser.md「検証済み実 markup」節に確定記載**(行=th 券種+td.Result+td.Payout+td.Ninki、Payout は `<br>` 区切り・円/100=倍率、Result は combo 系=`<ul><li>` 反復/複勝=`<div><span>` 非空、選択と払戻を 1:1 zip、単勝・枠連スキップ)。
- 既存の縮小 fixture `results_202406050911.html` は着順のみ・払戻なし(合成 head)→ US1 テストには**新規捕獲した実 fixture を使う**。

**Decision(当初)**: 実装前に T0 spike で実 markup 確認 → **実施済・想定どおり相乗り可能・parser 契約を実物で確定**。想定と大きな乖離なし(parser rewrite の設計は有効)。

**現状(コード確認済)**: `parse/exotic_odds.py::parse_exotic_odds` は合成 fixture 形状:
- `div.race` → `table.exotic[data-bet-type]` → `tr.combo[data-horses][data-odds]`
これらは実 netkeiba のクラス名ではない。実 result ページの払戻は `Payout_Detail_Table`(bet-type ごとの行、`Result` テーブル群)に日本語ラベル(複勝/枠連/馬連/ワイド/馬単/3連複/3連単)と 100円あたりの払戻金(円)で載る。

**要確認事項(spike で埋める)**:
- 券種行の実クラス名/構造(ラベルセル・組合せセル・払戻金セル)
- 組合せ表記(`1 - 2` / `1 → 2` / 改行区切り複勝)と 馬番の抽出規則
- yen payout → odds 倍率変換(倍率 = 払戻金 / 100)
- 同着(複勝 4 頭等)の複数行・ワイドの複数払戻の行構造
- 結果未確定ページに払戻テーブルが存在するか(空 or 非存在)

**Rationale**: parser 書き換えが最大の不確実性。実物を見ずに markup を仮定すると 059/040 で学んだ「実 DB でしか出ない罠」を踏む。spike を最初のタスクに固定。

**Alternatives considered**: 仮定で parser を書いて後で直す → 却下(手戻り大)。既存合成 fixture を維持 → 却下(実データ 0 のまま=feature の目的を達成しない)。

**共有すべき既存ヘルパ**: `race_id_from_html`(results parser と同じ実 markup 用 key 抽出)を exotic parser でも使い、fixture 専用 `race_key_from(div)` を捨てる。`to_float`/`soup_of` は流用。

## D2. 券種ラベル日本語→canonical マップ

**Decision**: parser 内で netkeiba 日本語ラベル → 既存 canonical bet_type にマップ:
- 複勝→`place` / 馬連→`quinella` / ワイド→`wide` / 馬単→`exacta` / 3連複→`trio` / 3連単→`trifecta`
- **枠連**は既存 canonical 集合外 → スキップ(parser docstring の既存方針を踏襲)。
- 単勝(`win`)は exotic_odds に入れない(WIN は既存 race_horses.odds=real 単勝で扱い済み、049/045)。

**Rationale**: 既存 `_EXOTIC_BET_TYPES`(place/quinella/exacta/wide/trio/trifecta)と `canonical_selection`/`_expected_count`(upsert.py)が既にこの 6 券種前提。parser 側のラベル解決だけ実 markup 対応にすれば upsert は無改修で載る。

## D3. selection 正準形と upsert(無改修で載るか)

**Decision**: parser は `ScrapedExoticRow(bet_type, numbers=馬番 tuple, odds=倍率)` を返し、既存 `upsert_exotic_odds` がそのまま `canonical_selection`(011 to_selection と同一:ordered exacta/trifecta・sorted quinella/wide/trio・single place)へ正準化・冪等 upsert する。**upsert は変更しない**。

**Rationale**: upsert は堅牢(ON CONFLICT 上書き・coverage_scope 判定・馬番キーで id-mapping 不要)。契約 `ScrapedExoticOdds` を保てば parser 差し替えのみで完結。

**Known nuance(記録・別 issue)**: `_expected_count(place, n) = n` だが実 netkeiba 複勝払戻は placed 頭数(通常 2〜3 行)しか無い → place の `coverage_scope` は常に `partial` になる。機能は阻害しない(coverage はメタデータ)。edge 測定は行の有無で判定するため影響なし。将来 place の expected を「field_size ルールの placed 頭数」に是正する別 issue として残す。

## D4. 日次 results 相乗り(追加リクエスト 0・確定後・例外隔離)

**Decision**: `pipeline.scrape_results` の per-race work 内で、`parse_results`(着順)成功後・**同一 html**(既に `fetcher.get(result_url)` 済)に対し `parse_exotic_odds` を呼び `upsert_exotic_odds`。
- **追加 fetch 0**: 同じ html 変数を渡す(再 `fetcher.get` しない)。テストで fetcher 呼び出し回数を assert。
- **確定後のみ**: result 保存が成立した(着順行がある=確定)レースだけ exotic を書く。未確定は parse_exotic_odds が空/例外 → skip。
- **例外隔離**: exotic parse/upsert を try/except で包み、失敗しても result 保存・job 全体を落とさない(既存 scrape_laps の per-page skip 前例、[[feature-034-035-sectional-laps]])。監査(Counts.error_messages)に記録。

**Rationale**: result ページは既に日次取得済。相乗りが最小・最安。憲法 V の「post-result 上書き」は確定後発火で自然に整合。

**Alternatives considered**: 独立 `scrape-exotic-odds` を日次で別途走らせる → 却下(result ページを二度 fetch=無駄なリクエスト・netkeiba 負荷増)。

## D5. exotic edge 測定の統計設計(pre-registration)

**Decision**: edge は結果前に固定した pre-registration 文書に沿ってのみ測る。要素:
- **券種**: place/quinella/wide/exacta/trio/trifecta を個別に測る(束ねない=049 の trio 悪化が place に隠れた前例)。
- **baseline**: 各券種で「最低 O_est(人気)」と「uniform」(011/012 の既存 backtest baseline)。成功=baseline 超過(ROI>1.0 ではない=市場超過が真のバー)。
- **最小サンプル数 n_min**: 券種別に事前固定(三連単は組合せ数大=当たり希少で n_min 大)。n<n_min は **NO_DECISION**。
- **CI**: 開催日クラスタ bootstrap(i.i.d. 禁止・seed 固定、068/016 の race-day cluster と同型)。
- **多重比較**: 6 券種 × 窓で偽陽性増 → Bonferroni 相当の補正または intersection-union、事前固定。
- **前向き vs cache backfill**: 主系列は **前向き収集**(065 型・楽観バイアスなし)。netkeiba cache に既にある過去 result の配当は補助(in-sample 寄り・別ラベル)。
- **control**: p≠q。EV は必ず P_model(009 on model p)× 実配当。q はモデル確率にしない。

**Rationale**: 「実配当が貯まる前に edge を主張しない」を構造化。049 の教訓(束ねたゲートで trio 悪化が隠れる)・016/068 の cluster-bootstrap・073 の NO_DECISION 三値を踏襲。

**Open**: n_min の具体値は Phase 1 contracts で券種別に事前登録(組合せ数と観測ペースから算出)。

## D6. リーク境界(exotic 配当がモデルに混入しないか)

**Decision**: exotic_odds は features/serving/training のどの load 経路にも入れない。leak-guard テスト=exotic_odds 行を変えても features materialize / モデル予測が byte 不変。scrape は betting/features を import しない(既存 import-graph 境界テストに追加確認)。

**Rationale**: 憲法 II NON-NEGOTIABLE。exotic 配当は結果由来 → 特徴化は結果リーク。edge 採点にのみ使う。

## D7. codex 代替 self-review(codex unavailable)

**Status**: codex 設計レビューを 2 回 `codex exec --sandbox read-only` で試行。いずれも repo `AGENTS.md` の codex 向け「second opinion を並走させる」指示に反応して前置き(「並走させます」)のみ出力し、レビュー本文が出ずに終了([[codex-env-recovery]] の derail パターン)。CLAUDE.md「同一タスク再試行は最大 1 回」に従い打ち切り。

**Self-review で洗い出した穴 → 対応**(plan.md Complexity 表と同じ):実 markup 未確認(T0 spike)/確定タイミング(gate)/silent-empty(下限チェック)/place coverage nuance(別 issue)/小 n 偽陽性(NO_DECISION)/overfit(pre-registration+OOS)/控除率逆風(正直な限界)。

**残リスク**: 実 payout markup の構造(T0 spike で解消)。edge の有無自体は測定結果で feature の成否ではない(null も成功)。
