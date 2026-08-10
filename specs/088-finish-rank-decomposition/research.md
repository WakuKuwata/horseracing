# Research: 着順の頭数正規化+ラグ分解 bundle (088)

## D1: 正規化の分母 = 出走頭数(STARTED 行数)【codex 2 回目レビューで改訂】

**Decision**: `finish_pct = (finish_order − 1) / (N_started − 1)`。N_started はその過去走の `race_horses` で `entry_status=STARTED` の行数。N_started=1 は NaN(理論ケース)。値の意味=「自分より先に完走した出走馬の割合」。`finish_order` の範囲検証(1 ≤ finish_order ≤ N_started、違反は NaN+監査)を併設。

**Rationale**: 当初案は完走頭数分母(「最下位完走=1.0」が閉じる)だったが、codex 論点A の指摘を採用して改訂 — **動機(18頭立て5着と8頭立て5着の区別)はフィールド規模の正規化**であり、完走頭数分母は「完走馬内の相対順位」という別の推定対象を測ってしまう(DNF が多いレースで同じ着順の値が膨らむ)。出走頭数分母なら「先着された頭数 ÷ 相手の数」という動機どおりの量になる。「最大値が 1 に届かない」のは仕様として明記(最下位=1 の保証を捨てる)。

**Alternatives considered**: N_finished 分母(却下: 推定対象が動機とずれる=codex)。「N_started 分母 + DNF を最下位扱い」(却下: DNF に着順を捏造する。非完走の情報は既存 `prev_was_stop`/counts 列の領分)。

## D2: ラグ・平均・トレンドの系列 = 完走走のみ(既存規約と一致を実査確認)

**Decision**: 新列の系列は全て完走走(FINISHED)のみで数える。

**Rationale**: 既存 `prev_finish` は [history.py:76-89](../../features/src/horseracing_features/history.py) で `finished` frame からの merge_asof=**既に完走系列の lag**であり、`avg_last3_finish` も finished-only rolling([extra_features.py:81](../../features/src/horseracing_features/extra_features.py))。新列は既存規約とちょうど一致し、`prev2_finish` は「`prev_finish` の 1 つ前」という自然な意味論になる。spec Assumptions の「差異があれば確認」は解消(差異なし)。

## D3: 窓内 NaN の伝播

**Decision**: rolling 平均・トレンドの窓内に finish_pct=NaN の走(N_started=1 の退化・着順範囲異常)が含まれる場合、その集約値は NaN(伝播・スキップしない)。生値系(prev2/prev3_finish・avg_last5_finish)は影響を受けない(finish_order は定義される)。

**Rationale**: D1 改訂(出走頭数分母)後、退化ケースは出走 1 頭=JRA に実質存在しない理論ケースとなり、NaN 源は主に範囲異常(データ品質)。「NaN をスキップして窓を伸ばす」複雑化は定義の単純性(事前登録の検証容易性)に見合わない。カバレッジ監査(FR-018)で実頻度を開示する。codex 論点A「NaN 伝播が未定義」の指摘は spec FR-003・contracts INV-C6 への明記で解消。

**Alternatives considered**: NaN スキップで直近 N 有効値(却下: 窓の意味が行ごとに変わり検証が複雑化。実益ほぼゼロ)。

## D4: FEATURE_VERSION 採番 = features-020(019 は焼却済み)

**Decision**: bump 先は `features-020`(実装時に未使用を最終確認)。`features-019` は 070 で使用され revert 済みのため再利用禁止(FR-008)。compat pins は現行 `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"]` の pin 集合をコピーし、features-018 自身の canonical hash(実装時に実測)を追加する(058 方式: 各版に直接 pin・推移させない)。

**Rationale**: 070 の revert 済み artifact(features-019 刻印)が残る環境で識別子を再利用すると、別内容の同名版が並ぶ(fail-closed 検証の前提が壊れる)。

## D5: 072 serving 投影(target_race_ids)対応 = per-horse 型で実装

**Decision**: 新 block は 072 の per-horse 型として `target_race_ids` を実装する。race-level primitive(過去走ごとの N_started=出走頭数)は**全過去レースで計算**し、per-horse 集約の source だけを対象馬に絞る(pace/history と同型)。採否ゲートは 072 と同一: projected == full.loc[target_rows] の check_exact。

**Rationale**: serving 1 レース予測のレイテンシは 072 系投影で 113s→24s まで詰めた経緯があり([[predict-latency-breakdown]])、新 block が full 計算のままだと予測経路に逆行を持ち込む。N_started は race-level の確定値なので per-horse filter で byte が変わらない(072 の taxonomy 実証済みパターン)。非対応の full-and-slice でも正しさは保たれるため、投影がゲートを通らない場合は full fallback(072 と同じ縮退)。

## D6: 新ソース列ゼロ = source_fingerprint 不変・materialize-safe

**Decision**: 入力は `races.race_date` / `race_results.finish_order`・`result_status` / `race_horses.entry_status` のみ(history._runs と完全に同一の射影)。loader・source_fingerprint・parquet manifest は無改修。

**Rationale**: 025/026 の fingerprint は既にこれらの列を含む。新規ロード列が無い=materialize 済み parquet と衝突しない(031/059/061 同型)。

## D7: 評価手順 = 診断(binary)+判定(本番 pl_topk paired 評価・必ず実行)【codex 2 回目レビューで改訂】

**Decision**:
1. **診断段(binary feature-eval・非ゲート)**: `training feature-eval --drop-groups finish_decomp`(baseline=bundle 無し)で 19-fold walk-forward。fold パターン・効果量の観察のみ — **判定・打ち切りの機能を持たない**。
2. **判定段(必ず実行)**: `training paired-eval --candidate "pl_topk:isotonic:0.3" --active "pl_topk:isotonic:0.3:drop=finish_decomp" --subgroups --confirmatory --gate-config …`(アームは recipe spec で両側 fold ごと再学習=069 の drop=group 方式・保存モデル非消費。gate-config.json を OOS 前に凍結し confirmatory モードで hash 照合)。verdict は spec FR-013a の一本化された正本: **レポートの `gate.adopted`(068 組込みゲート一式: winner NLL 勝ち+CI 上限<0・直近 3/5 年ガード・top2/top3 non-inferiority・ECE 非劣化)AND `subgroup_guard`(069)**(070 B2 と同一式・個別数値の事後読み替え禁止)。

**Rationale**: 当初案は「段1 binary REJECT で終了」だったが、codex 論点B の指摘を採用して改訂 — 系列前例(058/059/061)が示すのは binary が**過大**評価する方向だけで、binary が過小評価し pl_topk で効く逆方向を排除する根拠は無い。binary 打ち切りで bundle を「閉じた」と主張すると、その主張自体が測定に裏付けられない。判定を常に本番目的関数上で行うことで、REJECT の前例価値(FR-017)が成立する。コスト増(pl_topk フル学習・十数時間)は null-is-success の目的そのものへの投資として受け入れる。

**Alternatives considered**: binary REJECT → 打ち切り(却下: 上記)。binary 不通過を NO_DECISION 扱い(却下: 「軸を閉じる」目的が達成されない。常時判定段実行のほうが強い)。判定段を直近窓(2019+)に限定してコスト削減(却下: 068 契約の既定=全期間 19-fold を維持。REJECT の credibility を優先)。

## D8: 1 bundle 一括判定・主張は bundle に限定【codex 2 回目レビューで明確化】

**Decision**: 10 列一括で三値判定。per-arm keep/drop matrix(070 方式)は使わない。**判定が閉じるのは「この 10 列 bundle」であり、軸の全可能構成ではない** — spec 冒頭・FR-017 の主張をこの範囲に限定した(codex 論点B 採用)。あわせて bundle 内の冗長を事前に削減: finish_trend3 → finish_trend5(3 点 OLS 傾きは端点差分/2=採録済みラグの線形結合で独立情報ゼロ、5 走なら 4・5 走前ラグ非採録のため真に独立)。`avg_last3_finish_pct` の完全従属(3 ラグの算術平均)は残すが理由を事前登録(FR-002a: 木は線形結合を分割で構成できない=単一分割アクセスの価値。既存生値 avg_last3_finish の正規化対)+INV-C11 で従属を明示。

**Rationale**: 070 の per-arm matrix は 5 段の staged 評価を要し、期待値の低い bundle には過大。一部列だけ有害で bundle が沈む帰属リスクは受け入れるが、その帰結の主張を「この構成では負け」に限定することで誠実性を保つ。列を組み替えた再挑戦は新 spec で再事前登録(その際、本測定を上回る新根拠を要求)。

## D9: モジュール構成と revert 手順

**Decision**: 新モジュール `features/src/horseracing_features/finish_decomposition_features.py`(group 名 `finish_decomp`・10 列)+単体テスト。`materialize.build_asof_features` に 1 箇所結線(025 単一 as-of 源)。registry は ALL_COLUMNS 派生・FEATURE_GROUPS・FEATURE_VERSION bump・compat pin。REJECT 時は**結線と bump だけを revert**し、モジュール+単体テストは build 非結線のまま保全(単体テスト直呼びで緑維持、062/070 同型)。

**Rationale**: 確立済みパターンの反復。revert 単位を「結線+bump」に限定することで、負の結果の記録(モジュール・テスト・spec への測定結果転記)が消えない。

## D10: codex second opinion の記録

- **1 回目(2026-08-08・広域レビュー)**: 実質タイムアウト(最終構造化回答未達)。途中所見 3 点を回収し採否済み: ①soft label の教師意味論変化への注意(→ margin-aware は別 spec 送りの根拠を強化) ②081 突き合わせで期待値下方修正(→ spec 冒頭の期待値明示に反映) ③stage1 据え置き・stage2/3 のみ変調(→ 別 spec の設計方針として引き継ぎ)。
- **2 回目(2026-08-08・絞り込みレビュー)**: 論点A(列定義)+論点B(評価設計)限定で実行・**完了・実質的指摘多数**。採否:
  - **採用①(分母の推定対象)**: 完走頭数分母は「完走馬内の相対順位」で動機(フィールド規模の正規化)とずれる → 出走頭数(STARTED)−1 に改訂(D1)。「1=最下位」保証を仕様として放棄・最下位同着 fixture 追加・着順範囲検証(1≤finish_order≤N_started)を追加
  - **採用②(trend3 の退化)**: 3 点等間隔 OLS 傾き=(端点差)/2 で prev2 が寄与せず採録済みラグの線形結合 → finish_trend5(直近 5 完走)に改訂。4・5 走前の個別ラグを採録しないことを独立性の構成条件として contract に固定(INV-C9)
  - **採用③(binary 打ち切りは不可)**: binary 不通過だけで REJECT し軸を閉じるのは、binary が過小評価する逆方向を排除できず不十分 → 判定段(本番 pl_topk paired 評価)を常時実行に改訂(D7)。binary は診断専用に降格
  - **採用④(ゲートの曖昧さ)**: 「全指標非悪化」が点推定か CI か不明・三値決定表が無い → FR-013a で (a)CI/(b)subgroup/(c)点推定非悪化と決定表を凍結。subgroup ガードは判定段(pl_topk)に適用と明記
  - **採用⑤(主張の限定)**: 10 列一括判定で閉じられるのは「この bundle」のみ → spec 冒頭・FR-017 の主張を bundle 限定に修正(D8)
  - **採用⑥(NaN 伝播・best の飽和の明文化)**: 集約の NaN 伝播を FR-003/INV-C6 に、best_finish_pct の 0 飽和(一度勝てば恒久 0)の注記を data-model に明記
  - **不採用①(avg_last3_finish_pct の削除)**: 完全従属は事実だが、GBM の木は線形結合を分割で構成できず「平均への単一分割アクセス」は非自明の価値。削除せず FR-002a で理由を事前登録+INV-C11 で従属を明示(帰属解釈時に注記)
  - **不採用②(binary 不通過=NO_DECISION 案)**: より強い「判定段の常時実行」を採るため不要
  - **確認のみ(prev_finish_pct の冗長性)**: codex は既存列との冗長性を判定不能としたが、既存 `field_size` は今走の頭数であり過去走の出走頭数は列に無い=導出不能を data-model に明記
  - 追加するテスト: 最下位同着 fixture(INV-C3)・範囲異常 NaN(INV-C2a)・trend5 符号(INV-C9)・従属等式(INV-C11)。残リスク: bundle 一括ゆえの列間相殺(受け入れ・主張限定で対処)

## D11: speckit-analyze 1 周(2026-08-08)の記録 — HIGH 2 件を含む 8 件検出・全件修正済み

- **I1(HIGH・実行不能な事前登録)**: `paired-eval --candidate/--active` はモデル版でなく **recipe spec**(`objective:calibration[:frac][:drop=groups]`)を取り両アーム fold ごと再学習する(073 C1)。「T017 の candidate・T001 の active を渡す」は実行不能 → アームを `pl_topk:isotonic:0.3`(candidate)/ `pl_topk:isotonic:0.3:drop=finish_decomp`(baseline=069 方式)に事前登録。train-evaluate(本番モデル学習)は paired-eval に消費されないため ADOPT 分岐(T022)へ移動
- **I2(HIGH・verdict 定義の二重化)**: FR-013a 独自の (a)(b)(c) 決定表と harness 組込み `gate.adopted`(直近 3/5 年ガード込み)が併存し OOS 後にどちらかを選べてしまう → verdict の正本を **`gate.adopted` AND `subgroup_guard`**(070 B2 と同一式)に一本化し、閾値・margin は gate-config.json に凍結(D10 採用④の決定表はこの形に更新される)
- **G1(MEDIUM)**: gate-config.json 作成タスクが無く confirmatory 契約(073)未使用 → T017 を gate-config 凍結タスクに変更し T018 に `--confirmatory --gate-config-hash` を追加
- **I3(MEDIUM)**: `feature-eval --use-materialized` は存在しないフラグ → 除去(診断段は in-memory build)
- **I4(MEDIUM)**: D5 に codex 改訂前の「N_finished」残骸 → N_started に修正
- **I5/A1/T1(LOW)**: `materialize --output`→`--out`・quickstart の artifacts-dir 注記整合・タスク ID 表記ゆれ → 修正
- contracts の fixture 算術(4/7・14/17・2/9・trend5 −0.15)は analyze が手検算で全て正と確認・CRITICAL 0 件・要件カバレッジ 100%

**2 周目(修正後の再検証)— HIGH 1・MEDIUM 2・LOW 4 を追加検出・全件修正済み**:

- **I1(HIGH・verdict 二重化の残存)**: harness が必ず併記する組込み三値 `report.decision`(073)が underpowered 系(`stat_guard_underpowered`/`critical_subgroup_underpowered`)で NO_DECISION を返し、FR-013a の式(評価実行済み・不成立=REJECT)とモーダルに乖離しうる — 期待値の低い本 bundle では「点推定勝ち・CI ゼロ跨ぎ」が最有力の着地でまさに乖離ケース → **FR-013a の式を正本・`report.decision` は参考値**と OOS 前に凍結(070 前例: CI ゼロ跨ぎ=REJECT)。乖離時は値と cause を転記に併記(隠さない)
- **P1(MEDIUM・事前登録の汚染経路)**: 診断段(binary)と gate-config 凍結が並行可となっており診断結果を見てから閾値を凍結できた → **凍結(T016)→ 診断(T017)に直列化**(タスク ID の役割も入替)
- **C1(MEDIUM・fail-closed の不活性)**: `--from/--to` を渡さないと confirmatory の `eval_window` 照合がスキップされる → gate-config に `eval_window` を明示凍結し T018 に `--to` を追加(窓照合を実際に作動させる)
- LOW: plan の INV-C1..C10→C11・tasks 生成済み表記・段2/baseline(active) 旧語彙・T019 に監査スクリプト実行を明記・T015→T018 の依存理由注記(paired-eval は parquet 非消費)
- 2 周目も事前登録コマンドの全フラグ・`gate.adopted`/`subgroup_guard` フィールド実在・fixture 算術を実 CLI/コードで検証済み(全正)

**3 周目(収束確認)— 前 2 周の反映は全件確認済み。新規 HIGH 1・MEDIUM 2・LOW 2 を追加検出・全件修正済み**:

- **C1(HIGH・要件が凍結コマンドで実行不能)**: FR-012 の「coverage 帯 subgroup で非悪化」は成立し得なかった — 069 の coverage 帯は F02 の市場観測数(`asof_pm_obs_count`)で定義され本 bundle(完走履歴)と無関係な上、`paired-eval` CLI は obs_count を注入しないため cov_* subgroup はレポートに 1 つも出ない。十数時間の OOS 後に発覚すると FR の事後書き換え=事前登録の毀損 → **guard を harness 既定の critical={2026_only, nk, 2026_nk} に修正**(canonical は報告のみ・coverage 帯は guard 対象外と明記。欠損構造の解釈は FR-018 監査が担う)
- **C2(MEDIUM)**: `subgroup_guard` の membership(critical_subgroups)が凍結対象から漏れていた → gate-config の実キー `subgroup_guard.critical_subgroups` に pin(T016・contract)
- **C3(MEDIUM・凍結の実効性)**: gate-config の canonical hash は `_` 始まりキーを除外し harness は実キーのみ読む=「`_comment` に既定値を記す」では hash 保護外・silently ignored(069 gate-config 自身が警告した同型の罠)→ **全 pin を実キーで行う**(top_noninferior/calibration/subgroup_guard/bootstrap.b/eval_window)・seed/B は T018 で CLI 上書き禁止を凍結条件とし転記時にレポート実測値(`bootstrap_ci.seed`/`b`)で照合
- **L1**: 既存 `avg_last3_finish` は min_periods=1・新 pct 版は 3 =「対」はカバレッジ非対称(data-model に注記・定義は不変)。**L2**: verdict 式の正確な JSON パスは `report["gate"]["adopted"]` / `report["subgroups"]["subgroup_guard"]`(contract に併記)

**4 周目(収束確認)— 3 周目指摘の反映漏れゼロ・HIGH 以上ゼロ。新規 MEDIUM 1・LOW 2 を検出・全件修正済み**:

- **N1(MEDIUM・凍結手順が書いたとおりに実行できない)**: gate-config のキー列挙に **`evaluation_contract_version: "v2"`** が欠落していた — `assert_confirmatory` は非 v2 を即 fail-closed する MUST キーで、しかも T016 が「形式を踏襲」と指した 069 gate-config は 073 以前の作でこのキーを持たない(=列挙どおり作ると T018 起動時に即エラー→凍結やり直し)。3 周目 C1 と同型の欠陥だが発覚が即時な点だけが違う → キー列挙に追加し「**キー形式は 069・contract version キーは 073 gate-config を正**」と注記
- **N2(LOW→fail-closed 強化)**: seed の凍結が「CLI 上書きしない運用規律+転記照合」だけだと、違反の検出が十数時間の完走後になる。harness は `bootstrap.seed` 実キーを CLI より優先して読む(069 前例)→ **`bootstrap.seed` も実キー pin** して CLI 上書きを無害化し hash 保護下の自己強制に(凍結時の確定値は **20260713**=069/070/073 lineage。近傍 bundle と CI を直接比較できることを優先)
- **N3(LOW・stale)**: checklists/requirements.md に codex D1 改訂**前**の「分母(完走頭数)」が現在形で残存 → 改訂の経緯注記に更新(正本は spec FR-001/data-model)
- 4 周目も実 harness コード(paired.py の critical 既定・decision.py の `_` キー除外と v2 検査・BootstrapCI の seed/b・PairedReport の JSON パス)と全て突き合わせ済み


## D12: 実装・実行フェーズで判明した運用知見(2026-08-10)

- **DB は実装中も動く(パリティ検証の方法を変更)**: ops ワーカーが常時取込しており(当日 12:02 に odds/results ジョブ成功)、features-018 baseline parquet と features-020 parquet を別々にビルドして比較する当初手順では、共有列の差が「実装のせい」か「データが動いたせい」か分離できない(実測で source_fingerprint が 495531f7… → 1efd3ff4… と変化。**現時点の再計算値は features-020 ビルドと一致**=変化は本 feature の変更由来でないと確認)。→ `scripts/parity_088.py` を **単一スナップショットからの in-process 二重ビルド**(`skip_blocks={"finish_decomp"}` vs 全ブロック)に変更。これは serving の compat 経路が使う機構そのものでもあり、加法性の証明として当初案より強い
- **表示ラベルは「front 無改修」の例外**: plan は front 無改修としていたが、`test_display_label_coverage` が **model-input 列すべてに日本語表示ラベル(front/src/components/featureLabels.ts + admin ミラー)を要求**するため、10 列分のラベル追加が必須だった(040 の根拠表示契約)。表示専用の対応表であり front の挙動変更はゼロ
- **finish_decomp を optional leaf に登録**: 本 bundle は下流に消費者を持たない真の leaf なので `_OPTIONAL_LEAF_BLOCKS` に登録した。これにより bundle を持たないモデル(compat 経路の features-018 系)を serving する際に**ブロックごとスキップ**され、予測レイテンシに逆行を持ち込まない(069 F02 と同じ扱い)
- **confirmatory の窓照合は `--from`/`--to` の両方が必要**: `--to` だけで起動したところ `ConfirmatoryContractError: eval window mismatch` で**即 fail-closed**(CLI は `{from: --from, to: --to}` を組み立てて凍結値と厳密比較するため、`--from` 省略時の None が `2019-01-01` と不一致)。analyze 3 周目 C1 で「窓照合を実際に作動させる」と決めた機構が、実行時に設計どおり働いたことの実証。contract/tasks/quickstart のコマンドを両フラグ必須に修正済み
