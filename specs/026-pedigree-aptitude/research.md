# Phase 0 Research: 血統適性 as-of 特徴 (026)

## R1: 血統データの実データ在庫（最重要・spec 前提を修正）

**Decision**: 集計キーは **`sire_name`/`damsire_name`（名前）**。`*_id` は使わない。

**Rationale**: 実 DB（horseracing, 2007–2025）を直接確認した結果:
- `sire_name`/`dam_name`/`damsire_name` = **100%**（horses 94,223/94,231、race_horses 920,023/920,031）。
- `sire_id`/`dam_id`/`damsire_id` = **0%（2 行のみ）**。ingest（JRA-VAN）は col67–69 の名前のみマップ、scrape の血統 ID 解決は未稼働（[[scrape-parser-is-stub]] と整合）。
- 種牡馬 distinct = 1,721。産駒 lifetime finished-starts 分布: p25=10, p50=37, p75=182, p90=1279, max=22,731。

当初 spec/調査エージェントは「sire_id が ingest/scrape で populate 済み」としたが、それは**コード経路の存在**であって**データ投入**ではなかった。実 DB 確認で名前のみ投入と判明 → 名前キーへ pivot。名前が 100% 揃うため 026 は scrape 完了を待たず実データ評価できる。

**Alternatives considered**:
- ID キー（理想・同名衝突に頑健）→ 実データ 0% で評価不能。**deferred**（scrape 血統 ID 解決後に移行）。
- 名前正規化（カタカナ統一・trim）→ JRA 登録名はほぼ一貫。まずは生 name で評価、必要なら正規化を後追い。limitation として開示。

## R2: リーク安全な「他産駒のみ・strictly-before」集計

**Decision**: `他産駒 = sire 累積(cumsum−当日) − 対象馬自身の累積(cumsum−当日)`。分母（他産駒 finished cnt）0 → NaN。

**Rationale**: human_form は「対象行+同日除外」で足りる（騎手統計は馬と独立）。しかし sire 母集団には対象馬自身の過去レースが入る → 含めると history 特徴と二重・自己強化で血統シグナルが濁る。020 の `_cum_before_by`（daily 集計→cumsum−当日）を sire_name と horse_id の両方で計算し差し引けば、per-pair 展開なしに O(n) で「他産駒のみ・strictly-before」が得られる。当日除外で同日他産駒のリークも自動回避。

**Alternatives considered**:
- 自馬を残す（当日除外のみ）→ history と二重計上・自己相関。**却下**。
- per-(sire, excluded_horse) を直接 group → 計算量爆発。**却下**（差し引き方式で回避）。

## R3: スパース性 / min_starts 閾値

**Decision**: 全体率（sire_win_rate 等）は分母>0 で算出（020 と一貫、sire_starts を信頼度として併渡し）。距離帯別・芝ダート別の**条件付き率は他産駒 finished cnt < `min_starts`（既定 10）で NaN**。`min_starts` は configurable。

**Rationale**: 実分布で p25=10 → 全体は大半の sire で十分なボリューム。距離帯×馬場で割ると薄くなる（1/1=100% のノイズ）ので min_starts=10 で雑音を NaN に落とす。p50=37 なので 10 は過度に多くを潰さない。shrinkage/階層 Bayes は 020/023 と非一貫・過剰 → 採らず、Unknown(NaN)+信頼度列でモデルに委ねる。

**Alternatives considered**: min_starts=20/30 → 条件付き列の充足率が下がりすぎ。10 を既定にし research の根拠を残す（tasks で実データ充足率を確認）。

## R4: damsire(BMS) を入れるか

**Decision**: **任意 group `damsire_aptitude`（ablation-gated）として全体 win_rate/avg_finish のみ**追加。dam（母）は入れない。

**Rationale**: damsire_name も 100% カバレッジで BMS 理論は日本競馬で有力。ただし母父は sire より 1 世代分母数が薄いので距離/馬場別までは割らず全体に絞る。dam は 1 母あたり産駒数が極小で統計的に無意味 → スコープ外。023 の position_style と同型で、効けば採用・効かねば drop。

## R5: 025 パリティ / staleness と FEATURE_VERSION

**Decision**: FEATURE_VERSION を features-006 → **features-007** に bump。source_fingerprint に horses 血統列を追加。`_restrict` は horses を kept-race の出走馬に絞る。

**Rationale**: 026 は新シグナル＝出力変化なので version bump（025 の「不変」は infra-only だった）。パリティは「同一 version 内で materialize==in-memory」の不変条件で、両経路が features-007 を出すので維持される。血統 backfill（races/race_horses/race_results 不変のまま sire_name 補完）を検知するには fingerprint が horses 血統列を含む必要がある（含めねば黙って古い血統特徴を出す）。未来馬で fingerprint が誤発火しないよう、horses は `through` までの kept races に出走する horse_id に restrict してハッシュ。

**Alternatives considered**: fingerprint に horses 全行を入れる → 未来馬追加で誤 staleness。kept-race 出走馬に絞る方式を採用。

## R6: Frames への horses 追加と後方互換

**Decision**: `Frames` に **optional** `horses`（default 空 DataFrame）。`make_frames` は specs から horses を合成（sire_name/damsire_name 既定 None）。

**Rationale**: 既存の `Frames(races=, race_horses=, race_results=)` 呼び出し・多数の make_frames 利用テストを壊さないため optional。空 horses では血統ブロックは left-merge ミスで全 NaN（既存 025 パリティテストは血統 NaN 列を両経路で一致して出すので維持）。pedigree builder は空/欠損 horses を NaN で安全処理。

## R7: 採用見込みと評価設計

**Decision**: 採用ゲートは 020/023 同型の全体 OOS。加えて prior_starts 少セグメント限定 OOS を SECONDARY 診断。market_edge は SECONDARY。

**Rationale**: 020/023 は「絶対品質↑だが市場 q 超えず」。血統は市場が情報を持ちにくいデビュー/少数出走（全出走の数%）に効く想定 → 全体 LogLoss は動きにくい。効きどころを prior_starts バンド別 OOS で可視化（採否は全体ゲートで一貫）。market_edge は努力目標で採否バーにしない（製品目的＝意思決定支援、[[product-goal-decision-support]]）。デビュー馬比率や全体寄与の実値は実データ feature-eval で確認（tasks）。

**実データ結果（implement T017, 18 fold walk-forward OOS, baseline=features-006 vs cand=features-007）**: cand win **LogLoss 0.23340→0.23313**・**AUC 0.74534→0.74693**(+0.0016)・Brier 0.06230→0.06225↓・**14/18 fold 勝ち**(strict majority)・worst_dLogLoss +0.00032(<5e-3)・worst_dECE +0.00155(<2e-3)・mean ECE 0.00907→0.00955(微増だが tol 内) → **primary_pass=True・ADOPTED=True**。020(ガード分離が必要)・023(research ゲート不通過→operational 採用)と異なり、**026 は初回 OOS で素直に採用ゲートを通過**した初の特徴。実 DB カバレッジ: sire_win_rate 99.4% 非null、**デビュー馬(全出走の10.5%)の98.6%に sire 特徴**(他産駒由来=狙い通り)。market_edge(SECONDARY) は別途 feature-diagnostic で取得（採否に使わない）。prior_starts セグメント別 OOS の専用ハーネスは diagnostic(SHOULD) として deferred（デビュー馬カバレッジで効きどころは定性確認済み）。識別力↑×ECE 微増のトレードオフは 020/023 同様で、Kelly/recommendation 時は 017 のモデル p 校正(power γ<1)で相殺可能。

## Codex second opinion（implement 時に取得・反映済み）

spec/plan フェーズでは当環境の background 機構不調で 2 回とも結果取得不能だったが、implement フェーズの 3 回目で取得成功。実装済みコードに対する独立レビュー結果:

1. **自馬除外の実装 = OK**: `_cum_before_by`（strictly-before 日次累積）→ `_other_offspring`（sire 累積 − 自馬累積）で対象馬の自己履歴が分子分母から正しく除かれる。well-formed データでは負にならない。唯一の未ガードは「自馬履歴 > sire グループ」という不整合入力（重複等）だけ — 実データでは起きない（自馬は必ず自分の sire グループの部分集合）。
2. **名前キー = CONCERN → 対処採用**: 生 `sire_name` を canonicalize せず key にすると全角半角・空白・接尾辞の表記ゆれで 1 種牡馬が複数グループに割れる/別馬が同名で混ざるリスク。limitation 開示済みだが、低コストの `_normalize_name`（NFKC 正規化 + strip）を 1 箇所で噛ませると split リスクを下げ、将来の sire_id 移行コストも下がる、と助言 → **採用**。`pedigree_features._normalize_name` を `_runs` で sire_name/damsire_name に適用（決定論・単一経路なのでパリティ不変）。
3. **float64 固定 = OK**: int64↔float64 のプール依存ドリフトの診断は正しく、cast が最小摩擦の解。nullable Int64 はマスクのオーバーヘッド・下流複雑化、sentinel 行はより侵襲的。race-start count に float 精度問題は実務上なし。現方式が妥当。

**reconcile 差分**: codex R2 を受けて `_normalize_name`(NFKC+strip) を追加（spec の「名前キーの限界」を実装で緩和）。R1/R3 は現設計を追認。spec/plan の他項目に変更なし。

（spec/plan 時の自己検証は (a) 実 DB 実態確認、(b) 既存 020/human_form の as-of/leak 機構照合、(c) 代替案明記 により実施済みで、codex 追認と一致した。）
