# Feature Specification: 血統適性 as-of 特徴 (Pedigree-Aptitude Features)

**Feature Branch**: `026-pedigree-aptitude`

**Created**: 2026-06-28

**Status**: Draft

**Input**: 種牡馬(sire)を主軸に「市場が情報を持ちにくいデビュー馬・少数出走馬」に効くリーク安全な血統適性特徴を、025 の feature materialization 基盤の上に追加する。020/023 の公開情報特徴は予測の絶対品質は上げたが市場 q には勝てなかった。血統は市場が過小評価しがちな初のレバー。スキーマ変更なし(血統データは horses テーブルに既存)。

> **データ実態（plan 着手時に実 DB で確認、重要）**: horses テーブルの血統「名前」列 `sire_name`/`dam_name`/`damsire_name` は **100% populate 済み**（horses 94,223/94,231、race_horses 920,023/920,031、種牡馬 1,721 頭）。一方 血統「ID」列 `sire_id`/`dam_id`/`damsire_id` は **ほぼ未投入（0%、2 行のみ）**＝ingest は名前のみマップし、scrape の血統 ID 解決は未稼働。したがって 026 の集計キーは **`sire_name`/`damsire_name`（名前）** を用いる。ID ベース結合（同名・表記ゆれに頑健）は scrape の血統 ID 解決が稼働してから別途（deferred）。名前は 100% 揃っているため 026 は scrape 完了を待たず実データで構築・評価できる。名前キーの限界（同名種牡馬の衝突・カタカナ表記ゆれ）は limitation として開示する。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 種牡馬(sire)適性をリーク安全に特徴化 (Priority: P1)

予測モデルが、各出走馬について「同じ種牡馬の“他の”産駒が、対象レース日より前に、(全体／この距離帯／この芝ダート)でどれだけ走ったか」という血統適性シグナルを受け取れるようにする。これにより、自馬の戦績が乏しい（デビュー・少数出走）馬でも、父の産駒傾向から走力の事前推定が可能になる。

**Why this priority**: これが本 feature の核。020/023 の公開情報特徴では届かなかった「市場が情報を持ちにくい層（デビュー馬は自馬実績ゼロ）」に対する初のシグナル。sire は産駒数が多く統計的に堅いため主軸として単独で MVP を成立させる。

**Independent Test**: 合成データで、ある種牡馬の過去産駒に勝ち星を与え、対象レースの当該産駒に `sire_win_rate` 等が「自馬・同日を除いた他産駒の勝率」として正しく付くことを確認。デビュー馬（自馬実績ゼロ）にも sire 特徴が付くことを確認。リーク不変テスト（後述 US3）で時間境界と自馬除外を保証。

**Acceptance Scenarios**:

1. **Given** ある種牡馬に過去複数レースで複数の産駒（対象馬を含む）が走っている, **When** 対象馬の対象レースの特徴を生成, **Then** `sire_win_rate`/`sire_avg_finish`/`sire_starts` が「対象レース日より前・対象馬自身を除いた他産駒」の集計値になる。
2. **Given** 対象レースが芝1600m, **When** 特徴を生成, **Then** `sire_dist_band_win_rate`（対象レースの距離帯における他産駒の勝率）・`sire_surface_win_rate`（芝における他産駒の勝率）が距離帯/馬場で条件付けて集計される。
3. **Given** デビュー馬（自馬の過去出走ゼロ）, **When** 特徴を生成, **Then** history 特徴は Unknown でも sire 特徴は父の他産駒から値が付く（血統の価値）。
4. **Given** ある種牡馬の産駒が距離帯×馬場で `min_starts` 未満, **When** 特徴を生成, **Then** その条件付き率は Unknown(NaN) になり 0 補完されない。

---

### User Story 2 - 母父(damsire/BMS)適性を任意 group として追加・効果測定 (Priority: P2)

母父（BMS）適性 group（`damsire_win_rate`/`damsire_avg_finish` 等）を任意（ablation-gated）で追加し、採用ゲートで sire 単独に対する寄与を測る。日本競馬で BMS 理論は有力だが産駒数は sire より薄いため、効果が確認できた場合のみ採用する（023 の position_style と同型）。

**Why this priority**: sire（P1）で MVP は成立する。damsire は上積みの仮説検証であり、効かなければ落とせる独立スライス。

**Independent Test**: `damsire_aptitude` group を含む候補 vs sire のみの baseline を walk-forward OOS で比較し、採用ゲート（後述）で damsire の寄与を判定できる。group 単位で drop 可能。

**Acceptance Scenarios**:

1. **Given** damsire group を有効化, **When** feature-eval を実行, **Then** sire のみ baseline に対する damsire の OOS 寄与（LogLoss 差・fold 別差）が出力される。
2. **Given** damsire group, **When** group 単位で drop 指定, **Then** sire のみで matrix が成立し、damsire 列が除外される。

---

### User Story 3 - リーク安全性とパリティの保証 (Priority: P1)

血統特徴の追加が憲法 II（リーク防止）と 025 のパリティ／staleness 不変条件を破らないことを、自動テストで保証する。具体的には (a) 対象馬自身の過去/今走結果、(b) 同日の他産駒の結果、(c) 未来レースの結果 のいずれを変えても当該 target の血統特徴が不変であること、materialize 経路と in-memory 経路の出力が bit 一致すること、血統データ（sire_id）の後埋めを staleness が fail-closed で検知すること。

**Why this priority**: 過去 035/036 で片側判断による校正・リークのミスがあったため、新シグナル追加では leak-guard が採用と同格の必須要件。025 のパリティ／fail-closed を 026 の新ブロックでも維持しなければ「黙って古い／リークした値を出す」リスクが生じる。

**Independent Test**: leak-guard テスト群（自馬結果・同日他産駒・未来結果の変更不変）、materialize parity テスト（assert_frame_equal check_exact）、staleness テスト（sire_id を後から変えると fail-closed）が緑。

**Acceptance Scenarios**:

1. **Given** 対象馬の過去レース or 今走の着順を変更, **When** 当該 target の血統特徴を比較, **Then** 値が不変（自馬除外＝二重計上なし、今走非依存）。
2. **Given** 同日に走る同じ種牡馬の別産駒の結果を変更, **When** 当該 target の血統特徴を比較, **Then** 値が不変（同日除外）。
3. **Given** 対象レースより未来のレース結果を変更, **When** 当該 target の血統特徴を比較, **Then** 値が不変（strictly-before）。
4. **Given** 血統特徴を materialize した parquet, **When** materialize 経路と in-memory 経路で build_feature_matrix を実行, **Then** 全列 bit 一致（dtype 含む）。
5. **Given** materialize 後に horses の sire_name（血統列）を後埋め変更, **When** `use_materialized=True` で build, **Then** source_fingerprint 不一致で fail-closed（黙って古い血統特徴を出さない）。

---

### User Story 4 - 採用判定（OOS）と効きどころ診断 (Priority: P2)

血統 group を、020/023 と同型の walk-forward OOS 採用ゲートで「事前固定の候補が baseline(features-006) を上回るときだけ採用」する。加えて、血統が効くと期待される「prior_starts 少（デビュー/少数出走）セグメント」限定の OOS 改善を SECONDARY 診断として併記し、全体 LogLoss が動きにくくても効きどころを可視化する。

**Why this priority**: 採用は infra（US1/US3）の後。客観ゲートが無いと「絶対品質改善＝市場超過」と誤読するリスク（020/023 で確認済み）。セグメント診断は採否バーではないが、血統の価値が「全体平均」では薄まる構造を正しく説明するために要る。

**Independent Test**: `training feature-eval --drop-groups sire_aptitude,damsire_aptitude` で baseline=features-006、候補=features-007 を比較し、AdoptionReport（PRIMARY=平均 win LogLoss 改善 AND ECE 非悪化、fold ガード）と prior_starts セグメント別 OOS 指標が出力される。

**Acceptance Scenarios**:

1. **Given** 血統 group を事前固定（OOS で特徴選択しない）, **When** walk-forward feature-eval を実行, **Then** 平均 win LogLoss 差・ECE 差・fold 別勝敗・worst-fold ガード判定を含む AdoptionReport が出る。
2. **Given** 採用ゲートの判定, **When** strict majority + worst-fold ECE/LogLoss tol を適用, **Then** 偶然 fold や単一 fold の校正悪化で誤採用しない。
3. **Given** SECONDARY 診断, **When** prior_starts バンド別に OOS を分解, **Then** デビュー/少数出走セグメントの改善が全体とは別に確認できる。
4. **Given** 市場 q 超過は採否バーでない, **When** market_edge を出力, **Then** SECONDARY 診断として扱われ採用判定には用いられない。

### Edge Cases

- **sire_name 欠損**: horses に sire_name が無い馬（実 DB で 94,231 中 8 頭のみ）は血統特徴 Unknown(NaN)。0 補完しない。
- **種牡馬の初年度産駒**: 産駒の母集団が極小 → 条件付き率は `min_starts` で NaN。`sire_starts`（信頼度）で量を表現。
- **自馬が唯一の産駒**: 自馬除外後に他産駒ゼロ → sire 特徴 NaN（自己強化の防止が正しく働く）。
- **同日に同種牡馬が複数頭出走**: 同日除外により相互にリークしない。
- **dam（母）**: 産駒数が極小で統計的に無意味 → 特徴化しない（スコープ外）。
- **未来（serving）レース**: parquet 非カバー → 025 の単一レース fallback で生成と同一実装により血統特徴を計算。
- **血統 backfill**: scrape が後から血統を埋めると source（races/race_horses/race_results）不変のまま血統特徴が変わる → fingerprint が horses 血統列を含まないと検知漏れ（FR で必須化）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: features は horses テーブルの血統データ（horse_id→sire_name/damsire_name、及び将来用に sire_id/damsire_id）をロードし、血統適性特徴の生成に用いる MUST。集計キーは現状 100% populate されている **名前列**（sire_name/damsire_name）。これは現状ロードされていない新規ロード対象。
- **FR-002**: sire group（必須）として、対象レースより前の「同じ種牡馬の他産駒」の as-of 集計値 `sire_win_rate`/`sire_avg_finish`/`sire_starts`/`sire_dist_band_win_rate`/`sire_surface_win_rate` を per-(race_id, horse_id) で生成する MUST。距離帯・芝ダートは対象レースの属性で条件付ける。
- **FR-003**: damsire group（任意, ablation-gated）として `damsire_win_rate`/`damsire_avg_finish` 等を生成し、group 単位で有効化／drop できる MUST。dam（母）は特徴化しない MUST NOT。
- **FR-004**: 血統 as-of 集計は strictly-before（対象レース日より前）であり、かつ集計母集団から対象馬自身（同一 horse_id）を除外する MUST。これにより自馬戦績が history 特徴と二重計上されず、血統＝他産駒シグナルとして純化する。
- **FR-005**: 同日（同 race_date）の結果は集計に含めない MUST（同日除外＝target-encoding 的リーク回避、020 human_form と同型）。
- **FR-006**: 値が無い（産駒ゼロ・条件付き産駒 `min_starts` 未満・sire_id 欠損）場合は Unknown(NaN) とし 0 補完しない MUST。`sire_starts` を信頼度として併せて渡す。
- **FR-007**: 血統特徴は 025 の単一 as-of 源（`build_asof_features`）経由で生成され、in-memory builder・serving fallback・materialize 生成が同一実装を共有する MUST（二重実装禁止）。
- **FR-008**: registry に sire_aptitude（必須）・damsire_aptitude（任意）group を登録し、materialize 対象列が registry から機械導出される MUST。血統列に odds/payout/dividend/今走結果 トークンが含まれない MUST NOT（leak-guard）。
- **FR-009**: materialize 経路と in-memory 経路の build_feature_matrix 出力は血統特徴を含めて bit 一致する MUST（決定論・dtype 保持）。
- **FR-010**: 025 の source_fingerprint を horses の血統列（集計に使う sire_name/damsire_name、及び存在すれば sire_id/damsire_id）を含むよう拡張する MUST。血統 backfill（source の races/race_horses/race_results が不変のまま血統が変わる／名前が補完・修正されるケース）で staleness を fail-closed 検知できなければならない。
- **FR-011**: FEATURE_VERSION を features-006 → features-007 に bump する MUST（新シグナル追加＝出力変化＝採用済みモデルとは別バージョン）。
- **FR-012**: 採用判定は walk-forward OOS で行い、候補特徴は事前固定（OOS で特徴選択しない＝評価モデル==デプロイモデル）、PRIMARY=平均 win LogLoss 改善 AND ECE 非悪化、fold ガード=strict majority + worst-fold ECE tol + worst-fold LogLoss tol を満たすときのみ採用する MUST。`feature-eval --drop-groups`（既定=026 群）で baseline=features-006 を構成できる MUST。
- **FR-013**: 市場 q 超過（market_edge）は SECONDARY 診断であり採否バーにしない MUST。加えて prior_starts 少セグメント限定の OOS 改善を SECONDARY 診断として出力する SHOULD。
- **FR-014**: 血統特徴は win→joint（009）に介入しない MUST（確率整合性不変）。オッズ・今走結果を特徴にしない MUST NOT。
- **FR-015**: DB スキーマ変更なし（migration head=0006 不変、horses の既存列を使用、parquet は artifacts 配下・非コミット・DB から決定論再生成）MUST。
- **FR-016**: 血統特徴の生成フェーズはオペレータが手動再生成可能で、manifest に血統込み source_fingerprint・FEATURE_VERSION（features-007）・データ範囲・行数を記録する MUST。

### Key Entities *(include if feature involves data)*

- **Pedigree（血統リンク）**: 馬（horse_id）に紐づく sire_name（父）/dam_name（母）/damsire_name（母父）の血統名。horses テーブルに既存（名前は ~100% populate、ID は未投入）。結果に依存しない静的属性。集計キーは名前。
- **Sire-aptitude record（種牡馬適性レコード）**: 種牡馬（sire_name）ごとの、対象レース日より前・対象馬を除いた他産駒の (全体／距離帯別／芝ダート別) 走力集計（勝率・平均着順・出走数）。per-(race_id, horse_id) で対象馬に解決される as-of 値。
- **Damsire-aptitude record（母父適性レコード）**: 母父ごとの同種集計（任意 group）。
- **Materialized feature row（既存・025）**: per-(race_id, horse_id) の as-of 特徴行。026 で sire/damsire 列が追加される。
- **Manifest（既存・025、拡張）**: parquet の監査メタ。026 で source_fingerprint が horses 血統列を含むよう拡張、FEATURE_VERSION=features-007。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: デビュー馬（自馬の過去出走ゼロ）を含む全出走馬に対して、sire_name が存在する限り（実 DB で ~100%）血統適性特徴が値を持つ（自馬実績に依存しない）。sire_name 欠損馬は明示的に Unknown(NaN) で 0 混入ゼロ。
- **SC-002**: leak-guard テストで、対象馬自身の過去/今走結果・同日他産駒結果・未来レース結果のいずれを変えても当該 target の血統特徴が 100% 不変。
- **SC-003**: materialize 経路と in-memory 経路の血統特徴を含む全特徴行が bit 一致（差分 0）。
- **SC-004**: 血統データ（sire_name 等の血統列）の後埋め変更を staleness が 100% 検知し fail-closed（黙って古い血統特徴を出す事象ゼロ）。
- **SC-005**: walk-forward OOS で、血統候補（features-007）の採否が客観ゲート（平均 win LogLoss 改善 AND ECE 非悪化 + fold ガード）で機械的に決まり、採否理由（fold 別勝敗・worst-fold 判定）が報告に残る。
- **SC-006**: prior_starts 少セグメント限定の OOS 指標が全体とは別に算出され、血統が効きやすい層を可視化できる。
- **SC-007**: DB migration head が 0006 のまま不変、features に新テーブル定義（`__tablename__`）の追加ゼロ。
- **SC-008**: 実データ生成で、血統特徴を含む materialize が現実的な時間（025 の ~30s 規模＋血統 cross 集計の許容増分）で完了し、メモリ予算内。

## Assumptions

- horses テーブルの sire_name/dam_name/damsire_name は ingest（JRA-VAN, col67–69）で ~100% populate 済みであり、026 はこの既存データを名前キーで消費する（血統の取得・スクレイプ自体は本 feature のスコープ外）。sire_id/dam_id/damsire_id はほぼ未投入（scrape の血統 ID 解決が未稼働）のため本 feature では使わない（ID 版は deferred）。
- 名前キーの限界（同名種牡馬の衝突・カタカナ表記ゆれ）を limitation として開示する。JRA 登録名では稀で、まずは名前ベースで実データ評価し、ID 解決が走ったら ID ベースに移行する（deferred）。欠損は Unknown(NaN) として扱う。
- 距離帯（dist_band）・芝ダート（surface）の定義は 020 aptitude（dist_band_win_rate/surface_win_rate）の既存定義を再利用する（新しい区分は作らない）。
- `min_starts`（条件付き率を NaN にする産駒数閾値）の具体値は plan で実データ分布を見て確定する。
- LightGBM が欠損（NaN）を扱える前提で Unknown 維持を採用（020/023 と一貫）。shrinkage/階層 Bayes は導入しない。
- 採用ゲート・walk-forward fold 構成・worst-fold tol は 020/023 の既存実装（eval/feature_eval.py の AdoptionReport）を再利用する。
- prior_starts セグメント診断は 021 の prior_starts_band 定義（few/some/many）と整合させる。
- 025 の materialization 基盤（build_asof_features 単一実装・parquet/manifest・fail-closed staleness・use_materialized opt-in）は本 feature の土台として利用可能（main にマージ済み）。

## Deferred

- ID ベース血統結合（sire_id/dam_id/damsire_id；同名・表記ゆれに頑健）。scrape の血統 ID 解決が稼働してから名前キー→ID キーに移行。
- 血統の embedding／類似度（重く、過去 036 で校正ミス前例 → 今回は安全な集計版のみ）。
- 3代血統（曽祖父母）・インブリード（血量）・ニックス（配合相性）。
- dam（母）の本格活用。
- 距離適性の連続化（距離帯でなく回帰／カーネル）。
- 血統の市場バイアス補正（market_edge を採否に使う／FL 補正の血統版）。
- 産駒の質の時系列トレンド（若い種牡馬の初年度産駒補正・種牡馬の成熟曲線）。
- 血統データ自体のスクレイプ／カバレッジ向上（scrape 022/024 の責務）。
