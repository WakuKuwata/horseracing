# Feature Specification: 着順の頭数正規化+ラグ分解 bundle(前走着順軸の測定的クローズ)

**Feature Branch**: `088-finish-rank-decomposition`

**Created**: 2026-08-08

**Status**: Draft

**Input**: User description: "前走着順軸の残り隙間を測定で閉じる特徴 bundle: 着順の頭数正規化+ラグ分解。gain importance を prev_finish(生値)が支配しているが、生着順は頭数依存(18頭立て5着と8頭立て5着を同一視)で、個別ラグ列(前々走・3走前)・5走平均・着順トレンドも存在しない。1 bundle 事前固定で 068/069 ゲートにかけ、null(不採用)でも軸を閉じることが成功条件(null-is-success 型)。margin-aware 教師信号はスコープ外(別 spec)。"

## この feature が答える問い

「前走着順の分解(頭数正規化・個別ラグ・長い窓・トレンド)に、既存特徴(生着順+3走平均+キャリア平均+時計 z-score+着差)がまだ取りこぼしている予測情報が残っているか?」

事前の期待値は**低い**ことを明示する:

- 081 残差検証で「前走着順のリバージョン」のモデル残差はほぼゼロ(モデルは既に搾っている)
- 070 で市場 rank の percentile 化(pm_rank_robust)は REJECT(percentile 化という変換自体が過去に負けている)
- 061 の時計 z-score が「頭数に依存しない走りの質」を既に供給している
- GBM は prev_finish と avg_last3_finish から中間ラグを代数的に補間できる

したがって**本 feature の成功条件は ADOPT ではなく「事前登録ゲートによる確定的な判定」**である。REJECT なら「この 10 列構成の分解では残余情報を取り出せない」が本番目的関数(pl_topk)上で確定し、以後この軸の再提案には本測定を上回る新根拠(別の列構成と、その構成がなぜ今回と違う結果になるかの説明)を要求できる(080 の null-is-success 型)。判定が閉じるのは**この bundle**であって軸の全可能構成ではない — この限定は codex 2 回目レビュー(論点B)を採用したもの。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 分解特徴の構築(リーク安全・バイト不変) (Priority: P1)

モデル開発者として、着順の頭数正規化・個別ラグ・5走平均・トレンドを、既存の as-of 機構(strictly-before + 同日除外)の上に純加算で構築したい。既存の共有列は 1 バイトも変えない。

**Why this priority**: 特徴が正しく(リークなく・既存を壊さず)作れなければ評価に意味がない。リーク境界とバイトパリティは憲法 II/III の非交渉事項。

**Independent Test**: 新列を含む build を実 DB で実行し、(a) 既存列が baseline build と check_exact+check_dtype で完全一致、(b) リーク不変テスト(今走結果・同日他レース・未来レースの変更で新列不変)が通ることで単独検証できる。

**Acceptance Scenarios**:

1. **Given** features-018 の build 出力, **When** 本 bundle を追加した build を同一データで実行, **Then** 既存共有列は全行バイト一致し、新列のみが追加される
2. **Given** ある馬の対象レース, **When** その対象レース自身の結果・同日の他レース・未来のレースの着順を変更, **Then** その馬の新列の値は 1 つも変化しない
3. **Given** 過去走が 0 走(新馬)の馬, **When** build を実行, **Then** 新列は全て欠損(NaN)であり 0 埋めされない(Unknown と 0 の区別、憲法 IV)
4. **Given** 過去の完走が 1 走のみの馬, **When** ラグ 2・3 やトレンドを計算, **Then** 定義不能な列は NaN(トレンドは最低 5 完走・5 走平均は最低 5 完走を要求)

---

### User Story 2 - 事前登録ゲートによる 1 bundle 判定 (Priority: P1)

モデル開発者として、本 bundle を 068 評価契約(19-fold walk-forward paired winner NLL + 開催日クラスタ bootstrap CI)+ 069 subgroup ガード(critical={2026_only, nk, 2026_nk}・FR-012)で **1 bundle として**判定し、三値(ADOPT / REJECT / NO_DECISION)の機械的な verdict を得たい。OOS 結果を見た後の列選別・閾値変更は行わない。

**Why this priority**: この feature の成果物は verdict そのもの。判定が事前登録されていなければ null 結果に証拠能力がない(憲法 III)。

**Independent Test**: gate-config.json に閾値・seed・margin を OOS 実行前に凍結(canonical hash 記録)し、confirmatory モードの paired 評価を実行して verdict(`gate.adopted` AND `subgroup_guard`)とレポート(fold 別差・CI・subgroup 別)が artifact として残ることで検証できる。

**Acceptance Scenarios**:

1. **Given** 事前登録済みの bundle 列集合, **When** 本番 pl_topk 構成の paired 評価(baseline=現行版, candidate=bundle 込み)を実行, **Then** winner NLL 差・CI・subgroup 判定・非悪化ゲート(FR-013a の決定表)から三値 verdict が機械的に決まる
2. **Given** binary feature-eval(診断)がどんな結果でも, **When** verdict を決める段, **Then** 本番 pl_topk paired 評価が必ず実行されており、binary 段の結果だけで判定・打ち切りされていない(FR-013)
3. **Given** OOS 結果が出た後, **When** 一部の列だけ残す・閾値を動かす等の変更をしたくなった場合, **Then** それは行わず、変更するなら別 spec で再事前登録する

---

### User Story 3 - 判定後の後始末(採用でも不採用でも一貫) (Priority: P2)

モデル開発者として、REJECT の場合は特徴版の bump を revert して現行 serving を完全不変に保ち(027/062/070 前例)、モジュール+単体テストは負の結果の記録として保全したい。ADOPT の場合は serving 互換(旧版 pin の compat 経路)を維持したまま新モデルを昇格したい。

**Why this priority**: 判定の価値は後始末の規律で決まる。不採用 bump の放置は active モデルを compat 降格させるだけ(062 前例)。

**Independent Test**: REJECT 経路では revert 後に active モデルの予測が実 DB E2E でバイト一致すること、ADOPT 経路では旧版 pin モデルが compat-load でバイト一致することで検証できる。

**Acceptance Scenarios**:

1. **Given** verdict=REJECT, **When** 特徴版 bump を revert, **Then** active モデルの serving 予測は変更前とバイト一致し、bundle モジュールと単体テストは build 非結線のまま緑で残る
2. **Given** verdict=ADOPT, **When** 特徴版を bump して新モデルを登録, **Then** 旧版で学習済みの既存モデルは互換 pin(058 で確立した compat 経路)で予測バイト不変のまま serving 可能

---

### Edge Cases

- **出走頭数 1 の過去走**(正規化の分母が 0): その走の正規化着順は定義不能として NaN(JRA では実質存在しない理論ケース。その走はラグ・平均・トレンドの正規化系列からスキップしない — 生値系列には残る)。正規化系の rolling/expanding/トレンド集約は窓内に NaN があれば NaN(伝播・スキップしない)
- **着順の範囲異常**(`finish_order` が 1 未満または出走頭数超): データ異常としてその走の finish_pct を NaN とし、カバレッジ監査で件数を開示する(黙って値を作らない)
- **最下位の同着**: 正規化着順の最大値は 1 に届かない場合がある(例: 3頭出走で着順 1,2,2 → 0, 0.5, 0.5)。これは仕様 — 値の意味は「自分より先に完走した出走馬の割合」であり「1=最下位」の保証はしない
- **非完走走(取消・除外・中止・失格)**: 着順が定義されないため、新列のラグ・平均・トレンドは**完走走のみの系列**で数える(既存 `avg_last3_finish` の finished-only rolling と同一の母集団規約)。非完走の事実自体は既存の `prev_was_cancel/exclude/stop`・counts 列が既に保持しており本 bundle では扱わない
- **同着**: 公式着順(`finish_order`)をそのまま使う(同着 2 頭はどちらも同じ着順値・正規化も同値)
- **同日複数走**(地方・海外等でデータ上ありうる場合): 既存機構の同日除外規約に従い、同日の走はラグ・集約に含めない
- **障害レースの過去走混入**(誤ラベル 3.2%): 既知のデータ品質バグだが平地精度への影響は null と A/B 確定済み(2026-07-09)。本 bundle では特別扱いしない

## Requirements *(mandatory)*

### Functional Requirements

**列定義(事前固定・OOS 後変更禁止)**

- **FR-001**: 各過去走に走単位の正規化着順 `finish_pct = (公式着順 − 1) / (その走の出走頭数 − 1)` を定義しなければならない。推定対象は**フィールド規模の正規化**(動機の「18頭立て5着と8頭立て5着の区別」)であり、値の意味は「自分より先に完走した出走馬の割合」(0=勝ち・大きいほど後方。分母は出走頭数=STARTED 数のため、非完走馬がいるレースでは最大値が 1 に届かないのは仕様)。出走頭数 1 の走は NaN。出走頭数はその過去走の確定値から数える(過去走の全フィールド情報は結果確定済みのため利用可)。**分母を完走頭数でなく出走頭数とするのは codex 2 回目レビュー(論点A: 推定対象の未整理)を採用した決定** — 完走頭数分母は「完走馬内の相対順位」という別の推定対象になり動機とずれる
- **FR-002**: bundle は次の 10 列で事前固定する:
  1. `prev_finish_pct` — 直近完走走の finish_pct
  2. `prev2_finish` — 2 走前(完走系列)の生着順
  3. `prev3_finish` — 3 走前(完走系列)の生着順
  4. `prev2_finish_pct` — 2 走前の finish_pct
  5. `prev3_finish_pct` — 3 走前の finish_pct
  6. `avg_last3_finish_pct` — 直近 3 完走の finish_pct 平均
  7. `avg_last5_finish` — 直近 5 完走の生着順平均
  8. `avg_last5_finish_pct` — 直近 5 完走の finish_pct 平均
  9. `best_finish_pct` — 全過去完走走の finish_pct 最小(expanding min)
  10. `finish_trend5` — 直近 5 完走の finish_pct を走順序 x∈{1..5}(古→新)に対して単回帰した OLS 傾き(負=改善傾向。完走 5 未満は NaN)。**3 走でなく 5 走とするのは codex 2 回目レビュー(論点A)を採用した決定** — 3 点等間隔の OLS 傾きは (端点差)/2 と代数的に等価で、採録済みの `prev_finish_pct`/`prev3_finish_pct` の線形結合になり独立情報を持たない。5 走なら 4・5 走前の個別ラグは列に無いため真に新規の単一分割アクセスになる
- **FR-002a**: `avg_last3_finish_pct` は採録済み 3 ラグ(prev/prev2/prev3_finish_pct)の算術平均と完全従属である。それでも列として残す理由を事前登録する: GBM の木は特徴の線形結合を分割で構成できず、「平均への単一分割アクセス」は木にとって非自明の表現である(既存の生値 `avg_last3_finish` と対になる正規化版でもある)。この従属性は帰属解釈時に必ず注記する
- **FR-003**: 全列 float64・欠損は NaN(0 埋め禁止)。定義に必要な観測が不足する行・窓内に NaN を含む正規化集約・着順範囲異常(1 未満または出走頭数超)の走は NaN とする(範囲異常はカバレッジ監査で件数開示)

**リーク境界(憲法 II)**

- **FR-004**: 全列は対象レースより strictly-before(同日除外)の完走走のみから計算しなければならない。既存 history 機構(daily cumsum−当日 / merge_asof backward, allow_exact_matches=False)と同一の境界規約に従う
- **FR-005**: 対象レース自身の結果・市場情報・同日の走・未来の走は入力にしてはならない(リーク不変テストで機械的に固定)
- **FR-006**: 新規ソース列を読まない(必要な入力=races.race_date / race_results.finish_order・result_status / race_horses.entry_status は全て既存ロード済み)。したがって source_fingerprint は不変であり、materialize 済み parquet と衝突しない

**純加算性(バイトパリティ)**

- **FR-007**: bundle の追加は既存列に対して純加算でなければならない。既存共有列のバイト一致を build 全行で検証する
- **FR-008**: 特徴版識別子は additive bump とするが、**過去に使用済みで revert された識別子(features-019)は再利用してはならない**(070 の revert 済み artifact との衝突防止)。次の未使用識別子を採番する
- **FR-009**: 既存 active/candidate モデルは旧版 pin の互換経路(058 確立)で予測バイト不変のまま serving 可能であること

**評価・採用ゲート(憲法 III・事前登録)**

- **FR-010**: 判定は 1 bundle 単位(10 列一括)。OOS 結果を見た後の列の追加・削除・定義変更・閾値変更を禁止する
- **FR-011**: PRIMARY ゲート=068 評価契約を**本番 pl_topk 構成の paired 評価に適用**: 19-fold walk-forward paired 評価(baseline と candidate を同一現 DB・同一 race 集合で同時評価)、race-level winner NLL 差 + 開催日クラスタ bootstrap 95%CI(seed 固定)、CI 上限 < 0 を要求
- **FR-012**: subgroup ガード=069 の critical subgroup 集合 **{2026_only, nk, 2026_nk}** の intersection-union 非悪化(harness 既定と同一・membership は gate-config に pin)。canonical subgroup は報告のみ(guard 非算入)。**coverage 帯は guard 対象外** — 069 の coverage 帯は F02 の市場観測数(asof_pm_obs_count)で定義され本 bundle(完走履歴)と無関係な上、凍結コマンド(paired-eval)は obs_count を注入せず産出されない(analyze 3 周目 C1)。完走数依存の欠損構造の解釈材料は FR-018 の列別×年別カバレッジ監査が担う
- **FR-013**: verdict を決めるのは**本番 pl_topk 構成(TE+isotonic)での paired 評価**であり、binary feature-eval の結果に依らず必ず実行する。binary feature-eval は安価な診断(fold パターン・早期シグナルの観察)であって、打ち切り・判定のいずれの機能も持たない。**codex 2 回目レビュー(論点B)を採用した決定** — binary 段の REJECT で終了すると「binary が過小評価し pl_topk では効く」逆方向を排除できず、bundle を閉じたと主張できない
- **FR-013a**: verdict の正本を 1 本に固定する: **verdict = paired 評価レポートの `gate.adopted`(068 契約組込みゲート一式: winner NLL 勝ち+CI 上限<0・直近 3/5 年非劣化ガード・top2/top3 non-inferiority・ECE 非劣化)AND `subgroup_guard`(FR-012 の intersection-union)**(070 で確立した式と同一)。閾値・許容値・seed・subgroup margin は gate-config.json に OOS 実行前に凍結し canonical hash を記録、評価は confirmatory モード(hash 不一致・欠落は fail-closed=073 契約)で実行する。レポートの個別数値を事後に読み替えて別の判定を構成することを禁止する(verdict 定義の二重化の禁止)。harness が併記する組込み三値 `report.decision` は 073 契約の**参考値**であり 088 の verdict ではない(underpowered 系で NO_DECISION を返す点が本式と乖離しうる — 乖離時は本式を正とし、転記時に `report.decision` の値と cause を併記する。070 前例: CI ゼロ跨ぎ=REJECT)。三値: 上式成立 → ADOPT / 評価が実行され不成立 → REJECT / データ・環境要因で実行不能 → NO_DECISION(ボーダーの数値を NO_DECISION の理由にしない)。paired 評価のアームは recipe 指定(candidate=本番構成の全列、baseline=同一構成から bundle 群のみ drop)で両アーム fold ごと再学習する(保存済みモデルは消費しない)
- **FR-014**: verdict は三値(ADOPT / REJECT / NO_DECISION)で artifact に記録し、fold 別差・CI・subgroup 別内訳・実行条件(seed・窓・列集合)を含める
- **FR-015**: active モデル(compat pin 検証・ADOPT 時の昇格対象)は実装着手時に DB から確定する(推測で固定しない)。paired 評価のアームは recipe 指定であり保存済み active モデルを消費しない(FR-013a)

**判定後の後始末**

- **FR-016**: REJECT の場合、特徴版 bump を revert し(027/062/070 前例)、bundle モジュール+単体テストは build 非結線で保全する(単体テスト直呼びで緑を維持)
- **FR-017**: REJECT の場合でも、本 spec に測定結果(効果量・CI・fold パターン・subgroup 内訳)を記録し「この 10 列 bundle は本番目的関数上で測定済み・棄却」を前例として残す(軸の別構成での再提案には本測定を上回る新根拠を要求)

**カバレッジ監査(憲法 V)**

- **FR-018**: 列別の非欠損率を年別に監査し、判定レポートに含める(特に prev2/prev3/avg_last5/finish_trend5 は完走数要件で欠損が増えるため、欠損率が判定の解釈に必要)。着順範囲異常の件数も含める

### Key Entities

- **過去完走走(finished run)**: ある馬の strictly-before の走のうち result_status=FINISHED のもの。着順が確定している唯一の母集団(ラグ・集約の系列はこの上で定義)
- **finish_pct**: 走単位の正規化着順=「自分より先に完走した出走馬の割合」(0=勝ち・大きいほど後方・分母は出走頭数−1)。フィールド規模の異なるレース間で着順を比較可能にする表現
- **bundle verdict**: 10 列一括の三値判定と、その根拠(効果量・CI・subgroup・カバレッジ)を持つ append-only の評価記録

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 事前登録された 10 列の bundle に対し、三値 verdict が機械的に確定する(人手の裁量判断がゲート通過の条件に入らない)
- **SC-002**: 既存共有列のバイトパリティが実 DB build 全行で成立する(不一致 0 行)
- **SC-003**: リーク不変テスト(今走・同日・未来の変更で新列不変)が全ケース緑
- **SC-004**: REJECT 経路では revert 後の active モデル serving 予測が実 DB E2E でバイト一致(mismatch 0)
- **SC-005**: ADOPT 経路では旧版 pin モデルの compat-load 予測が実 DB E2E でバイト一致(mismatch 0)
- **SC-006**: 列別・年別カバレッジ監査が判定レポートに含まれ、新列の非欠損率が既存 `avg_last3_finish` と比較可能な形で開示される
- **SC-007**: verdict がどちらに転んでも、spec に測定結果が記録され「この軸を再提案する場合は本測定を上回る新根拠が必要」という前例が成立する

## Assumptions

- 出走頭数はその過去走の race_horses から数える(entry_status=STARTED 行数)。過去走は結果確定済みなので全フィールド情報の利用はリークでない
- ラグ・平均・トレンドの系列は**完走走のみ**で数える(既存 `avg_last3_finish` の母集団規約と一致)。既存 `prev_finish` も finished-only の merge_asof であることを実査確認済み(plan research D2)=系列規約に差異なし
- `finish_trend5` の回帰は走の時系列順序(1=最古、5=最新)を説明変数、finish_pct を目的変数とする OLS 単回帰の傾き
- 本 bundle は結果履歴由来(市場データ非使用)のため default モデル系譜に入れてよい(p⊥q 制約と無関係。058/069 のような accuracy-first candidate 限定は不要)
- 特徴版の次の識別子は実装時に未使用を確認して採番する(features-019 は 070 で使用済み・revert 済みのため飛ばす見込み)
- 新規スクレイピングは行わない(netkeiba 予算制約)。DB 既存データのみで完結する
- スキーマ変更・migration・API/OpenAPI 変更なし(features/training/eval のみ)

## 測定結果(実装・実 DB 実測)

### US1 構築フェーズ(2026-08-10 完了)

- **バイトパリティ(SC-002 / FR-007)= PASS**: 同一スナップショットからの in-process 二重ビルド(bundle あり/なし)で **共有 114 列すべてが 961,098 行にわたり check_exact + check_dtype 一致**、追加はちょうど 10 列、行数不変(`scripts/parity_088.py`)
  - **方法の是正**: 当初は features-018 の parquet と features-020 の parquet を比較する計画だったが、**ops ワーカーが常時取込しており(実測: 当日 12:02 に odds/results ジョブ成功)2 ビルド間で DB が動く**ため、parquet 同士の比較では共有列の差が「実装のせい」か「データが動いたせい」か区別できない(実際に source_fingerprint が 495531f7… → 1efd3ff4… と変化。**現時点の再計算値は features-020 ビルドと一致**=変化の原因は本 feature の変更ではないと確認済み)。よって単一スナップショットからの二重ビルド比較に変更した(FR-006 の「新規ソース列ゼロ」自体は成立)
- **serving compat E2E(SC-005 事前確認)= PASS**: active `lgbm-064-f02acc`(features-018)を features-020 registry 下で compat-load し、レース 202610020811 の 18 頭の `win_prob` が既存の永続化値と**バイト完全一致(mismatch 0)**。logic_version に互換マーカー `feat=features-018;…;reg=features-020` が記録されることも確認
- **リーク不変・fixture(SC-003)= PASS**: 24 テスト全緑(手計算 fixture 全件・INV-C1..C11・072 投影 parity)。features スイート全体 **359 テスト緑**・ruff クリーン
- **カバレッジ監査(FR-018 / SC-006)**:

| 列 | 全体 非欠損率 | 備考 |
|---|---|---|
| `prev_finish_pct` / `best_finish_pct` | 89.700% | 既存 `prev_finish`・`avg_last3_finish` と**完全同率**(下記) |
| `prev2_finish` / `prev2_finish_pct` | 80.123% | |
| `prev3_finish` / `prev3_finish_pct` / `avg_last3_finish_pct` | 71.548% | |
| `avg_last5_finish` / `avg_last5_finish_pct` / `finish_trend5` | 57.582% | 完走 5 走要件 |

  - 年別は 2008 年以降ほぼ安定(prev_finish_pct 89.5〜90.8%・5走系 56.9〜61.8%)。2007 年のみ低い(履歴の起点)
  - **退化・異常はゼロ**: 着順範囲異常(INV-C2a)**0 件**/ 出走 1 頭レース(INV-C2)**0 件**(完走 952,862 走に対して)。つまり実データでは正規化の分母は常に有効で、`prev_finish_pct` の欠損は「過去完走走が無い」ケースのみ=既存 `prev_finish` と同率になる
  - **min_periods 非対称(analyze L1)を実測で確認**: `avg_last3_finish`(既存・min_periods=1)89.700% に対し `avg_last3_finish_pct`(新・min_periods=3)71.548% = **18.2pt の差**。仕様どおりの構造差であり欠陥ではない

### US2 判定フェーズ(2026-08-10 実行)— **verdict = REJECT**

**判定段**: `paired-eval --candidate "pl_topk:isotonic:0.3" --active "pl_topk:isotonic:0.3:drop=finish_decomp" --subgroups --confirmatory`(gate_config_hash `521e6278eace…` 照合 OK・contract v2・eval_window 2019-01-01..2026-08-09・n_races 26,338 / eligible 26,294 / 821 開催日)。**`bootstrap_ci.seed=20260713` / `b=2000` が凍結値と一致することを照合済み**(FR-013a の転記照合)。レポート: `artifacts/088_paired_report.json`

**verdict = `report["gate"]["adopted"]`(False)AND `report["subgroups"]["subgroup_guard"]`(False)= REJECT**

| ゲート要素 | 結果 | 数値 |
|---|---|---|
| primary(winner NLL 点推定) | PASS | diff **−0.000551**(cand 2.069092 < active 2.069643・uniform 2.5949) |
| **stat_guard(CI 上限 < 0)** | **FAIL** | 95%CI **[−0.002214, +0.001171]** = **ゼロ跨ぎ(有意でない)** |
| **recent_guard(直近 3/5 年非劣化)** | **FAIL** | 直近3年 diff **+0.000914(劣化)**・直近5年 −0.000238 |
| top2/top3 non-inferiority | PASS | top2 −0.000214 / top3 −0.000210 |
| calibration(ECE 非劣化) | PASS | cand 0.001020 / active 0.000774 |
| **subgroup_guard**(critical) | **FAIL** | 2026_only / nk / 2026_nk すべて **NO_DECISION(underpowered)** |

- subgroup 詳細: canonical のみ PASS(CI[−0.000191, +0.000090]・821 日)。2026_only CI[−0.006027, +0.008261](66 日)・nk CI[−0.000504, +0.001010](92 日)・2026_nk CI[−0.000614, +0.001108](66 日)
- **harness の `report.decision` は `NO_DECISION`(cause=`critical_subgroup_underpowered`)で本式と乖離**。事前登録どおり **FR-013a の式が正本**であり、この乖離パターン(underpowered 系)は analyze 2 周目で予見して凍結してあったもの。070 前例(CI ゼロ跨ぎ=有意でない=REJECT)と同型
- **診断段(binary feature-eval・非ゲート・判定に不使用)**: 19 fold・`adopted=False`。LogLoss base 0.22850 → cand **0.22833**(−0.00017)・AUC 0.76568 → **0.76630**・Brier 微改善・**ECE は悪化** 0.00893 → 0.00914・勝ち fold **14/19**・worst_dECE +0.00210。**binary では点推定が bundle 有利に見えるが(059 前例と同じ過大評価の向き)、判定段の pl_topk では有意差なし+直近3年劣化**。この乖離こそ「binary で打ち切らない」と事前登録した理由(codex 論点B 採用)の実証

### US3 後始末(REJECT 経路・2026-08-10 完了)

- **revert 済み**: `FEATURE_VERSION` を features-018 に戻し、compat pin(features-020 エントリ)・REGISTRY の 10 列・FEATURE_GROUPS・`build_asof_features` の結線・optional-leaf 登録をすべて撤去。`finish_decomposition_features.py` と単体テスト 24 件は **build 非結線のまま保全**(build 関数を直接呼ぶ形で緑を維持=062/070 同型)。registry/materialize には REJECT 理由と数値を注記
- **features スイート 359 テスト緑**・ruff クリーン。canonical parquet を features-018(112 列)で再生成
- **SC-004 = PASS**: revert 後の active `lgbm-064-f02acc` の予測が、**088 着手前の永続化値と 18 頭すべてバイト一致(mismatch 0)**。logic_version から compat マーカー(`reg=features-020`)が消え native 経路に復帰
- **SC-005 = PASS**(参考): 判定前に features-020 registry 下で compat-load したときの予測も同じ永続化値と mismatch 0 だった(bump が入っても既存モデルの serving は不変だったことの記録)

**解釈(FR-017 の前例)**: 点推定は全期間で bundle 有利(−0.00055)だが **CI がゼロを跨ぎ有意でない**うえ、**直近 3 年では逆に劣化(+0.00091)**。つまり「効いているとしても、近年のデータでは効いていない」。予測どおりの null 結果であり、**この 10 列構成では前走着順の分解に取り出せる残余情報は無い**と本番目的関数(pl_topk)上で確定した。閉じるのは**この bundle** であって軸の全構成ではない(SC-007)。

## Out of Scope

- **margin-aware 教師信号**(pl_topk stage2/3 の着差変調)— 別 spec(次 feature)。codex 途中所見(stage1 据え置き・stage2/3 のみ変調)を引き継ぐ
- 着順の相手品質調整(Elo 系)— 062 で REJECT 確定済み、再提案しない
- レース間隔の非線形特徴 — `days_since_last` 既存+081 でモデル捕捉済み確定
- 障害レース誤ラベルの ingest 修正 — 平地精度レバーでないと A/B 確定済み(データ品質としては別課題)
- 市場人気(popularity)側の分解 — 070 で REJECT 確定済み
- 本 bundle の default モデル以外(accuracy-first 系)への展開
