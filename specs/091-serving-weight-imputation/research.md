# Research: 馬体重欠損時の serving 入力是正

**Feature**: 091 | **Date**: 2026-08-10 | **Spec**: [spec.md](./spec.md)

Phase 0 の設計判断。実装可能性はすべて現物コードと実 DB で裏を取った(推測で決めた箇所は無い)。

---

## D1: 新規列は `prev_weight` 1 本のみ(当初 3 列案を実測で縮小)

**Decision**: 追加する特徴列は **`prev_weight` の 1 本だけ**。当初案(codex 推奨)にあった `weight_age_days` と `has_prev_weight` は**追加しない**。

**Rationale**: 実測したところ、この 2 列は既存列と**完全に同一**だった(2021-2026 の 262,733 行):

| 当初案の列 | 既存列 | 一致率 | 欠損パターン |
|---|---|---|---|
| `weight_age_days` | `days_since_last` | **100.0000%**(差 0 行) | 完全同一(availability differs = 0 行) |
| `has_prev_weight` | `has_past_race` / `is_debut` / `past_race_count>0` / `career_starts>0` | **100.000000%** | 同上 |

機構は明快である。前走体重の供給元を「出走かつ体重あり」に限ると、**出走行の 99.95%(955,650 / 956,102)に体重がある**ため、「直近の計量済み出走」は事実上つねに「前走」と一致する。したがって体重の鮮度は前走からの経過日数そのものであり、体重の有無は過去走の有無そのものになる。

**実装時に判明した訂正(2026-08-10)**: 上表の 100% 一致は **2021-2026 の測定窓**での結果である。全履歴(2007-2026)で測り直すと、過去出走のある **862,274 行のうち 1 行**(0.000116%)だけ例外があった — 唯一の過去出走が計不(体重 NULL)だった馬(`201303020301` / `2011101732`、初出走 2013-06-08 が体重なし)。したがって厳密には `has_prev_weight ≢ has_past_race` であり、その 1 行では `weight_age_days` も定義できない。**それでも 1 列に留める判断は変えない** — 96 万行中 1 行のために専用列を 2 本足すのは、木の容量と寄与の帰属を悪化させるだけである。ただし不変条件は「0 件」ではなく**境界つき**(過去出走行の 0.01% 以下、かつ全例外が『過去に計量済み出走が一度も無い』で説明できること)に改めた。カバレッジが劣化すれば率が上がって fail-closed する。

冗長列を足すのは木の容量を食い、寄与の帰属も曖昧にする(062/070 で確立した規律)。1 列で必要な情報はすべて揃う: 代理値 `prev_weight`・その鮮度 `days_since_last`(既存)・有無 `has_past_race`(既存)。

**不変条件として固定する**: この縮小は現在のデータの性質に依存する。将来 netkeiba 側の体重カバレッジが劣化すれば乖離が広がりうる。よって「過去出走がある行で `prev_weight` が欠損する率が境界(0.01%)以下」かつ「全例外が『過去に計量済み出走が一度も無い』で説明できる」を不変条件テストにし、崩れたら fail-closed で気づけるようにする(INV-W4)。崩れた時点で鮮度・有無の列追加を再検討する。率の境界だけでなく説明可能性も見るのは、解決器が拾えるはずの供給元を落としている不具合を率の境界では検出できないためである。

**Alternatives considered**:
- 3 列すべて追加(codex 案): 実測で 2 列が完全重複と判明したため却下。codex の推論自体は妥当だったが、この DB の体重カバレッジという事実を知らずに立てた仮説だった。
- `starts_since_weight`(codex が層別軸として言及): 上記と同じ理由でほぼ恒等的に 1 になる。列にしない。

---

## D2: 前走体重の定義

**Decision**:
- **供給元** = `entry_status = started` かつ体重が非 NULL かつ `200 <= weight <= 800`
- **時系列規約** = `merge_asof(on=race_date, by=horse_id, direction="backward", allow_exact_matches=False)`。既存 [`_prev_started`](../../features/src/horseracing_features/lowcost_features.py) と同一規約 = 厳密前 + 同日除外
- 同一馬・同日に供給元候補が複数ある場合は **NaN**(安定ソート任せの暗黙選択を禁止)
- 取消・除外時の計量値は**使わない**
- 配置 = 新モジュール `weight_history_features.py` を `build_asof_features`(025 の単一 as-of 源)に 1 箇所結線

**Rationale**: 既存の前走系特徴と同一機構を使えば、リーク境界は構造的に保証される(新しいリーク面がゼロ)。取消時の計量値を含めるかは kill-test で **C−C2 = −0.000018(ns)** と無差だったので、単純な方(出走のみ)を正とする。体重の範囲ガードは実 DB では 1 件も発火しない(実測 330–640 kg・非正値ゼロ・範囲外ゼロ)が、DB に範囲制約が無い以上ガード自体は置く。同日重複も実測 0 件だが同様。

**Alternatives considered**:
- 「直近観測体重」(取消含む): kill-test で無差。感度アームとしても回さない(差が測定限界以下)。
- 窓関数による carry-forward SQL: kill-test の探索段では使ったが、repo 標準の merge_asof に揃える。同日除外の意味論が既存特徴と一字一句同じになる方が安全。

---

## D3: 学習時 mask はレース単位・rate 0.5 を事前登録

**Decision**:
- **対象** = `weight` / `weight_diff` / `carried_weight_ratio`(体重に依存する全列)
- **単位** = レース。1 レース内の全馬を同時に mask する。行単位の独立ランダム mask は**禁止**
- **決定論** = `race_id` と seed のハッシュで選択。seed を gate-config に事前登録
- **mask 率 m = 0.5** を事前登録
- 診断アームとして m=0.0 と m=1.0 も測るが、**verdict には使わない**

**Rationale**:
mask が無ければこの feature は機能しない。`prev_weight` は当日体重とほぼ同一(数 kg 差)なので、体重がほぼ常に存在する学習データでは木は `weight` に分割を置き `prev_weight` を無視する。serving で `weight=NaN` に落ちた瞬間、また未学習の分岐に流れる。**mask は付随的な工夫ではなく機構の本体**である。

レース単位である理由は、欠損が時間帯 regime 単位(そのレースの予測がいつ実行されたか)で発生するからである。行単位 mask は「同一レース内に体重が分かる馬と分からない馬が混在する」状況を学習させるが、これは本番で**起こしてはならない**状況である。

**混在が起きたらどうなるか、およびその扱い**: 目的関数はレース内 softmax なので、1 頭の入力が全馬の正規化確率を動かす。レース単位 mask で学習したモデルにとって混在レースは **out-of-distribution** であり、本 feature が是正しようとしているのと同型のスキューを小規模に再導入する。混在が実際に起きるかは**検証できていない**(結果未確定レースは揮発的で母集団が取れず、体重の履歴スナップショットも保存していないため遡及確認もできない)。

そこで**観測して対処するのではなく、起きない形にする**。詳細は **D12** を見よ — serving 側で可用性をレース単位に二値化し、started に一頭でも未計量がいればそのレースは全馬 serving regime に倒す(FR-034)。これにより本番の入力分布が学習分布と厳密に一致し、混在は構造的に発生しなくなる。前向き観測(T067)は残すが、正しさの担保ではなく**どれだけ頻繁に full-info を手放しているか**を知る運用上の可視化に役割が変わる。

m=0.5 の根拠は**両 regime に等しい学習質量を与える**こと。結果を見て決めていない。両端は退化する: m=0 なら `prev_weight` が `weight` に食われ、m=1 なら当日体重の経路が未学習になり full-info regime が確実に劣化する。なお LightGBM は「weight が NaN か否か」で分割できるので、混合学習でも regime 別の部分木を構成でき、データ効率の損失は名目の半分よりずっと小さい。

**診断値を事後採用しない**: m=0 や m=1 が勝っても、それを理由に採用値を差し替えることはしない(068 C2・憲法 III の selection leak)。差し替えたい場合は新たな事前登録が必要になる。

**Alternatives considered**:
- 行単位ランダム mask: 欠損構造と不一致。codex も明確に不適と判断。却下。
- augmentation(masked/unmasked の両方を行として複製): TE encoder の母集団とレースグループ構造を汚し、同一レースが損失に二重計上される。却下。
- early/late の 2 モデル routing(codex が「より筋がよい候補」として挙げた): 設計として妥当だが、**この repo の「1 feature 1 判定」規律に反する**。ルーティングは serving/ops の変更を伴い、判定軸も 2 つになる。本 feature で単一モデルの限界を測ってから、必要なら別 feature に切る。plan の Out of Scope に明記。
- m を fold 内の inner-valid で選択(nested selection): 統計的には最も正しいが、pl_topk の再学習コストが m の候補数だけ倍増する。初回は固定値で測り、必要なら次に回す。

---

## D4: 校正 holdout にも同じ mask を掛ける

**Decision**: isotonic 校正 holdout にも、model-fit 行と**同一の規則・同一 seed**でレース単位 mask を適用する。校正だけ full-info で fit することはしない。

**Rationale**: 校正器は runtime で serving regime のスコアに適用される。full-info のスコア分布で fit した校正器を masked スコアに当てると、校正器の定義域と実際の入力がずれる。codex が前回レビューで指摘した点である。model-fit と校正 holdout に同一の mask を掛ければ、パイプライン全体が単一の混合分布を見ることになり、runtime の混合(体重ありの予測も体重なしの予測も両方起きる)と整合する。

現行の分割は `calib_frac=0.3` の最新 30% 切り出し(active は booster が 2020-08-30 まで、校正が 2020-08-30..2026-07-12)。この分割自体は変更しない — 変えると 073 が固定した `calibration_split_unit` の契約に触れ、本 feature の測定に別の変数が混入する。

**Alternatives considered**:
- 校正のみ full-info: 上記のずれを受け入れることになる。却下。
- 校正を regime 別に 2 本持つ: 単一モデル 1 校正器という現行構造を壊す。routing と同じく別 feature。

---

## D5: serving regime 評価は純関数を両アームに適用する

**Decision**:
- `features` に純関数 `apply_weight_mask(frame, *, spec)` を置き、**training の fit 前と eval の predict 前の両方**がこれを呼ぶ
- **既定 `spec=None` は現行とバイト同一**(059/072 の「None = 挙動不変」規律)
- 評価時の mask は **候補・現行の両アームに適用**する
- PRIMARY = serving regime(predict 側 mask 率 1.0)のレース単位 winner NLL paired 差
- GUARD = full-info regime(mask なし)の非劣化
- 診断 = 校正前(race-softmax 直後)の winner NLL

**Rationale**:
[`LightGBMPredictor._ensure_data`](../../training/src/horseracing_training/predictor.py) は特徴量行列を一度だけ構築してキャッシュし、fit と `predict_race` の両方がそこから行を取る。したがって mask はキャッシュ行列そのものではなく**各利用箇所の行部分集合に対する純変換**として掛けるのが正しい。そうすれば学習側 mask 率(m=0.5)と評価側 mask 率(1.0 または 0.0)を独立に指定でき、キャッシュ行列は無改変のまま既存経路のパリティが保たれる。

両アームに適用する理由: 現行モデルは `prev_weight` 列を持たないので masked 評価では素直に劣化する。**それがまさに測りたい差**である(現行が serving 時に失っている量)。片側だけ masked にすると比較の意味が消える。

校正前 winner NLL を必須診断にするのは codex の前回指摘による。校正器由来の反転(raw で改善・calibrated で悪化)を識別できる。

**Alternatives considered**:
- キャッシュ行列を masked 版に差し替える: fit と predict の mask 率を独立に指定できなくなる。却下。
- eval 専用に別の行列を組む: ビルドが 72 秒 × アーム × fold になる。却下。
- 現行アームは masked にしない: 比較が成立しない。却下。

---

## D6: verdict の正本と事前登録する数値

**Decision**: 採否の正本は**単一の合成式**:

```
ADOPT ⟺ serving_regime.gate.adopted
    AND full_info_guard
    AND serving_regime.subgroups.subgroup_guard
```

- `serving_regime.gate.adopted` = serving regime の winner NLL paired 差について、**点推定 < −δ かつ CI 上限 < 0**(+ 既存 068 ゲート)
- **subgroup は serving regime のものを読む**。既存 `PairedReport.subgroups` はトップレベルにあり regime 別スコアは純追加なので、修飾しないと既定(full-info)の subgroup を読んで PRIMARY と regime が食い違う
- レポートは上式を評価した `verdict.adopt` を**単一の真偽値**として出力する(FR-026)
- **δ = 0.002** を事前登録
- `full_info_guard` = full-info regime の paired 差が事前登録した非劣化幅以内
- `subgroup_guard` = 069 の三値 intersection-union。critical subgroup は評価前に固定
- 実行不能なら NO_DECISION。ボーダー数値を理由に NO_DECISION にはしない

**Rationale(δ の根拠)**: kill-test の粗い版(既存列への流し込み)が −0.0123 を示している。機構が意図どおり働いていれば同程度が出るはずで、−0.002 に届かない場合は「mask 学習が効いていない」疑いが強く、列追加と再学習のコストに見合わない。既存の採用例(059/061 の −0.001 級)より高い水準を要求するのは、本 feature が「新情報の追加」ではなく**既知の大きな劣化の是正**であり、期待値が一桁違うためである。CI 上限 < 0 だけを条件にすると、機構が効いていない状態でも偶然の毛差で通りうる。

`_` 始まりのキーは canonical hash から除外される(069 が警告し 088 で再確認された罠)ため、**凍結したい値はすべて実キーで書く**。`evaluation_contract_version: "v2"` は [`assert_confirmatory`](../../eval/src/horseracing_eval/decision.py) が MUST として要求するので必ず含める(070 の gate-config はこのキーを持たない = 073 以前の作なので、そのままコピーすると起動時に落ちる)。

**Alternatives considered**:
- CI 上限 < 0 のみ(068 の既定): codex の指摘どおり、実務上の最小効果を欠く。却下。
- full-info を PRIMARY に含める: 利用者が見るのは serving regime。full-info はガードで十分。

---

## D7: backfill と live の非対称 — 既定は変えず、regime を記録し、065 の罠を明記する

**Decision**:
- backfill の既定挙動は**変えない**(全馬計量済みなら従来どおり体重を使う)。ただし **FR-034b により混在レースだけは backfill でも可用性正規化が効く** — 「体重が存在すれば必ず使う」ではなく「レース内で全馬揃っていれば使う」が正確な記述である。follow-up として残すのは『全レースを強制的に serving regime で回すオプション』の方
- `logic_version` に体重 regime の compact marker を付ける
- **full-info backfill 予測を「live の品質」の代理として使ってはならない**ことを契約に明記する
- backfill を serving regime で回す選択肢(`--weight-regime`)は follow-up とし、本 feature では実装しない

**Rationale**: 憲法 V の再現性は**すでに満たされている**。[`predict_race`](../../serving/src/horseracing_serving/predictor.py) は per-horse の post-preprocessing モデル入力ベクトルを `feature_snapshots` に保存しており、体重が入っていたか否かは事後に確認できる。marker は監査のためではなく**フィルタ**のため(shadow log や backtest で「体重公表前の予測だけ」を選べるようにする)。

ただし本質的な罠がある。**full-info の backfill 予測を retrospective な品質評価に使うと、live が持っていなかった情報で測ることになる**。これは 065 が odds について指摘した closing-oracle バイアスとまったく同型である。この feature の導入でそのギャップが 0.0187 に拡大するので、契約として明示的に禁じる。

**Alternatives considered**:
- backfill も常に masked(= live と完全一致): 一貫性は最良だが、過去レースの「最善の推定」を意図的に劣化させる。製品は過去レースの予測も表示する。却下(ただし follow-up でオプション化)。
- 当日体重列を丸ごと捨てる(Design B): live == backfill になり非対称が消える。魅力的だが、体重が判明した後の予測を意図的に劣化させる。**m=1.0 の診断アームが実質これに相当する**ので、本 feature の測定結果を見てから次で判断する。

---

## D8: FEATURE_VERSION と serving 互換

**Decision**:
- `features-018` → **`features-021`**(`features-019` は 070 の revert で焼却済み。[registry.py:408-409](../../features/src/horseracing_features/registry.py) に「No model was ever trained on features-019」と明記されている)
- compat pin: `"features-021": {"features-018": "263ef6b7ac5eccf45faf90005a5904de91adfed639b8d3f14a04c4d20f141a3f"}`
  - この hash は lgbm-064-f02acc の artifact metadata から**実測**した値(070 の gate-config に記録された値とも一致)
- `features-017` は pin しない(lgbm-063 は retired)
- 純加算 1 列なので既存列はバイト不変。additive left-merge による構造的保証に加え、**一度きりの共有列 parity を実データで実測**する(058/061/069 と同型)

**Rationale**: `feature_hash` は列名のみのハッシュなので、1 列追加で必ず変わる。よって active(lgbm-064)は compat 経路が要る。058 で確立した exact/compat 分離がそのまま使える — 今回は**既存列の値が変わらない純加算**なので、017 のときのような「共有列の値が動いたので全 pin を空にする」事態には当たらない。

**Alternatives considered**:
- 列を追加せず既存 `weight` 列に流し込む(kill-test の形): FEATURE_VERSION は上がるが列名は不変なので `feature_hash` が変わらず、**古いモデルが黙って新しい意味の値を食う**。017 が踏んだ「値変更 bump の serving fail-close」問題の同型。却下(この危険性こそ独立列にする実務上の理由でもある)。

---

## D9: source_fingerprint と materialize

**Decision**: 新しいソース列を読まない → `source_fingerprint` は**不変** → materialize-safe(059/061/069 と同型)。

**Rationale**: `prev_weight` の導出に必要な `weight` / `race_date` / `entry_status` は [`loader.py`](../../features/src/horseracing_features/loader.py) が既に SELECT している。fingerprint は読み込んだ frames の射影列から計算されるので変化しない。`prev_weight` は as-of 列なので `materialized_columns()` の registry 駆動導出に自動的に入る(STATIC_COLUMNS ではない)。

**注意**: 現在の `artifacts/features.parquet` は既に stale で、fingerprint 検証が fail-closed している(kill-test の準備中に実確認。設計どおりの挙動)。本 feature の実装時は 1 回 re-materialize が必要。

---

## D10: REJECT 時の後始末

**Decision**:
- FEATURE_VERSION bump と build 結線のみ revert し、`weight_history_features.py` と単体テストは**非結線で保全**(062/070 の前例)
- mask 機構(`apply_weight_mask`)と serving の可用性正規化も結線を revert する。ただし**純関数と単体テストは非結線で保全**する(062/070 の前例。純関数を残すコストは小さく、次の事前登録の土台になる)。負の結果は memory に記録する
- **registry の availability_timing 是正(US3)は REJECT でも残す**

**Rationale**: US3 は値不変・列不変の宣言修正であり、本 feature の採否と論理的に独立している。宣言と実装の不一致はそれ自体が是正すべき欠陥なので、測定結果に紐づけない。

---

## D11: codex plan レビューの採否

**状況**:

- **kill-test 設計レビュー = 取得成功**。13 件の指摘を全件トリアージし、採用 11 / 部分採用 1 / follow-up 1 で本 plan に反映した(下表)。
- **plan 段階レビュー = `codex unavailable: CLI 内部エラー(exit 144)`**。2 回試行して 2 回とも失敗した(1 回目は `codex_core::tools::router: timeout_ms must be at least 10000` と `codex_models_manager: failed to renew cache TTL: missing field supports_reasoning_summaries`、2 回目は stdin 待ちでハング)。CLAUDE.md の規約に従い再試行は 1 回で打ち切り、セルフレビュー checklist で代替した。

なお plan で扱う論点のうち、**mask のレース単位化・`prev_weight` の独立列化・校正器の regime 追随・前走体重の source 定義**は kill-test レビューで既に codex の意見を得て採用済みである。codex 入力を得られなかったのは以下 4 点に限られる。

### セルフレビュー checklist(codex 代替)

| 論点 | 判断 | 自己反証 |
|---|---|---|
| **3 列 → 1 列への縮小(D1)** | リスク低 | 判断の根拠が意見でなく**実測**(262,733 行で 100% 一致・不一致 0 行)である点が強い。反証すべきは「将来カバレッジが変わって乖離する」ケースのみで、これは INV-W4 の fail-closed テストで検知できる。冗長列を保険で入れる案は、木の容量を食い寄与の帰属を曖昧にする(062/070 の規律)ため却下 |
| **mask 率 m=0.5(D3)** | 中リスク・要注意 | 「両 regime に等しい学習質量」は原理的だが唯一の正解ではない。実務上の予測はほぼ 100% が serving regime なので m→1 が最適の可能性がある。**この不確実性を診断アーム(m=0/1)の併走で可視化し、事後採用は禁じたうえで次の事前登録の材料にする**設計にしてある。最悪ケース(m=0.5 が両 regime とも中途半端)でも、診断アームがその事実を示すので学習が残る |
| **δ=0.002(D6)** | 中リスク | 既存採用例(−0.001 級)より厳しい。δ を高く置きすぎると、実際には有用な小さい改善を捨てる。逆に低いと機構が効いていなくても通る。replay の −0.0123 の 1/6 という水準は防御可能だが恣意性は残る。**恣意性が残ることを contract に明記**し、δ 未達 REJECT の場合は「機構が効かなかった」と「効いたが小さかった」を診断アームで区別して報告する |
| **backfill 非対称の扱い(D7)** | リスク低 | 既定を変えない選択は保守的で安全。むしろ本当の危険は「full-info backfill 予測を live 品質の代理に使う」ことで、これは 065 が odds で踏んだ罠と同型である。contract で明示的に禁じた。marker はその識別手段 |

**残リスク(plan では解消できないもの)**: 独立列 + mask 方式そのものは未測定である。kill-test が測ったのは既存列への流し込みであり、本設計はその効果を包含すると**期待**しているに過ぎない。Phase D0 の outcome-blind 受入(直近 fold のみ)と配線 E2E smoke を「フル walk-forward に進む前の中断点」として置き、配線が通っていなければそこで止める(効果では止めない=選択リーク回避)。

### kill-test 設計レビューの採否

| # | codex 指摘 | 採否 | 対応 |
|---|---|---|---|
| 1 | D は回収可能上限でなく oracle comparator。C が D を超えることもありうる | **採用** | spec の限界節に明記。「回収可能上限」の表現を撤回 |
| 2 | 主窓は isotonic に対して in-sample。校正前 winner NLL を必須診断に | **採用** | kill-test で実測(C−B = −0.011269)。D5 で本評価にも必須診断として組み込み |
| 3 | 前走体重の source は STARTED 限定・体重非 NULL に絞ってから直近を選ぶ | **採用** | D2。取消含む版は kill-test で無差(ns)と実測 |
| 4 | staleness は `days_since_last` でなく `target_date − source_weight_date` | **採用したが列は追加せず** | D1。実測で両者が 100% 一致と判明したため、既存列で足りる |
| 5 | 長期休養の cap を primary に設けない(同じデータで最良 cap を選ぶのは selection leak) | **採用** | cap を設けない。kill-test で >365 日でも有意と確認済み |
| 6 | デビュー馬を primary から除かない。ただし secondary で層別 | **採用** | spec の Edge Cases + FR-028 のカバレッジ監査 |
| 7 | 行単位ランダム mask は不適。レース単位で同時 mask | **採用** | D3 |
| 8 | `prev_weight` を独立列にする(同じ列に混ぜるとスキューが残る) | **採用** | D1/D8。列名不変の値変更が serving fail-close を回避してしまう危険も別途 D8 に記録 |
| 9 | 校正器も candidate の serving regime を再現して fit し直す | **採用** | D4 |
| 10 | `carried_weight_ratio` が PRE_ENTRY 宣言なのに POST_WEIGHT 依存 | **採用** | US3 として spec 化 |
| 11 | 高速評価経路は race ごとの contiguous group に softmax を掛ける。数レースの parity では弱い | **採用** | kill-test で全アーム × 5 コホートの parity を実施し PASS |
| 12 | early/late の 2 モデル routing の方が筋がよい | **部分採用(別 feature へ)** | D3。1 feature 1 判定の規律により本 feature では単一モデルの限界を測る。Out of Scope に明記 |
| 13 | 保存済み production feature snapshot に対する小規模 cohort patch の方が本番を保存する | **不採用(follow-up)** | 現時点で serving regime の feature_snapshot 蓄積が足りない。本 feature の marker(D7)がその前提整備になる |

## D12: レース内混在は「ゲート」ではなく「設計」で解消する(統合で最も価値のあった取り込み)

**Problem**(並走 spec `090-serving-weight-skew` の D4 が「本 feature 最大の穴」として提起): 測定した arm は全馬一律(全実体重 / 全 NaN / 全代替)だが、**本番はレース内で混在しうる**。発走が近い一部の馬だけ計量済み、という状態である。目的関数がレース内 softmax なので 1 頭の入力変化が全馬の正規化確率を動かし、しかも計量の公開順は無作為とは限らない(会場・時間帯で偏りうる)。レース単位 mask で学習したモデルにとって混在レースは **out-of-distribution** であり、本 feature が是正しようとしているのと同型のスキューを小規模に再導入する。

**Decision**: **serving 側で体重の可用性をレース単位に正規化する。** あるレースの started 馬に一頭でも体重未公表がいれば、**そのレースは全馬 serving regime として扱う**(全馬の当日体重 3 列を落とし、`prev_weight` を使う)。全馬が計量済みのレースだけが full-info regime になる。

**Rationale**: これは**ゲートではなく設計で問題を消す**。学習はレース単位 mask(全馬 masked か全馬 full-info のどちらか)なので、serving 側も同じ二値に正規化すれば**本番の入力分布が学習分布と厳密に一致**し、混在は構造的に発生しなくなる。並走 spec が「安全側の退避先」と呼んだものを、退避先ではなく**既定**にする。

失うものは「混在レースで一部の馬に当日体重がある」という情報だけである。混在は計量公表中の短い遷移窓でしか起きないはずで、しかも予測実行の中央値は T−6.7 時間なのでその窓に当たる確率は小さい。**測っていない挙動に賭けるより、測った二値のどちらかに倒す方が安全**である。

**この決定で不要になるもの**: 混在パターン replay を必須ゲートにする案(並走 spec の T007/T008)。混在が構造的に起きない以上、測る対象が無い。ただし**前向き観測は残す** — 「一頭でも未計量」の判定が実際にどれくらいの頻度で full-info を serving regime に倒しているかは、運用上知っておく価値がある(T067)。

**Alternatives considered**:
- 混在パターンでの replay を必須ゲートにする(並走 spec の採用案): 本番の混在分布が未知(結果未確定レースは揮発的で母集団が取れない)なので、再現すべきパターン自体が手に入らない。合成パターンで測っても本番の代理になる保証がない。却下。
- 混在レースでは代替値を使わない(= 完全に現行挙動に戻す): full-info でも serving regime でもない第三の状態を作ることになり、かえって分布が増える。却下。

---

## D13: 並走 spec `090-serving-weight-skew`(案 A)からの取り込みと不採用

同一問題に対する並走 spec が存在し、ユーザー決定により本 spec(091)に一本化した。案 A は「既存 `weight` 列に前走体重を流し込み、FEATURE_VERSION 据え置き・再学習なし・serving 経路のみ」という設計で、**kill-test と同一構成なので証拠がそのまま効く**という強い利点があった。

| 並走 spec の論点 | 採否 | 本 spec での扱い |
|---|---|---|
| **D4 レース内混在** | **採用(最重要)** | D12。ただしゲートではなく**設計で解消**する形に変えた |
| **D5 遡及ルックアップ ≠ 当時 serving が知り得た値** | **採用** | 訂正・遅延取込・ID 統合・順延・重複・タイムゾーンで過去の値が動きうる。決定的タイブレークを契約に固定し(D2 の同日重複 → NaN)、fixture で扱いを固定する。「当時のスナップショットでの as-of replay」は履歴非保存(憲法 V)のため実行不能という結論も同じ |
| **D5 `nk:` 分裂は誤結合でなく被覆率の低下** | **採用** | 表現を統一。カバレッジ監査で真デビューと分離して報告する(FR-028) |
| **D3 校正器 fit 窓** | **採用(ただし射程が違う)** | 並走 spec は固定 artifact に適用するので fit 窓内評価が本質的な弱点になる。**本 spec の confirmatory eval は fold ごとに recipe から再 fit する**(068 C1)ので校正器も fold 内で再 fit され、この漏れを構造的に継承しない。fit 窓の問題は **kill-test 証跡の限界**としてのみ残り、spec の「正直な限界」に記載済み |
| **D6 `logic_version` の版付きマーカーを冪等キーに参加させる** | **採用** | 予測の冪等条件に regime が入らないと、OFF 実行と ON 実行が「既にある」で取りこぼされる(076 の `;calib=<digest>` が同型の罠を既に踏んでいる)。本 spec は新モデルなので `model_version` で自然に分かれるが、regime marker を足す場合(T061)は冪等キー側も揃える |
| **D6 FEATURE_VERSION を bump しない** | **不採用(設計が違うため)** | 案 A は列構成も定義も変えないので据え置きが正しい。本 spec は**新規列を足す**ので `feature_hash`(列名集合)が必ず変わり、bump と compat pin が要る。この差は設計選択の帰結であって矛盾ではない |
| **D2 適用層を `serving/` に閉じる** | **不採用(設計が違うため)** | 案 A は特徴ビルダを触らずに済むので合理的。本 spec は新規列を学習にも使うので `features/` の as-of 源に置く必要がある。代わりに `spec=None` バイト同一(INV-W5)とパリティ実測(INV-W7/W8)で学習経路の不変を担保する |

**案 A を採らなかった理由**(記録として): 案 A は今すぐ出せて再学習も不要という実務的な強みがある。それでも本 spec の独立列方式を土台にしたのは、案 A が `weight` 列の**意味を変えるのに列名が変わらない**ためである。`feature_hash` は列名の集合なので変化せず、古い artifact が新しい意味の値を黙って食う経路が残る(017 の値変更 bump 問題と同型)。案 A はこれを `logic_version` マーカーで補うが、それは規約であって構造的な安全装置ではない。**ただしこれは判断であって証明ではない** — 案 A の方が早く利得を取れたのは確かで、独立列方式が実測で勝てなければこの判断は誤りだったことになる。

---
