# Research: 062 as-of レーティング特徴

**Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

実査で確認した既存機構:

- `features/materialize.py` `build_asof_features`: 全 as-of ブロックの単一源。059 の `build_relative_ability_features` は組み立て済み `out` フレームを入力に取り最後に走る前例 → レーティングの field-relative 列も同様に後段で作れる。
- `features/pace_features.py` `_rolling_asof`: `merge_asof(backward, allow_exact_matches=False)` = strictly-before + 同日除外の確立パターン。
- materialize は per-race 決定的・pool-end 非依存が必須(025)。**逐次状態は 061 までの per-row 独立特徴と異なり、ここが最大リスク**。
- `COMPATIBLE_PRIOR_FEATURE_VERSIONS`: features-017 に features-016(lgbm-061)と features-015(lgbm-058-acc/lgbm-060-mkt)をピン。features-016 canonical hash = `300b28a9312a3fb6e171b1dfd38cc88413ccbae2a0cfa9936ed278b5d14b66ac`(128 列、lgbm-061 metadata と一致確認、bump 前計測)。

## D1: 更新式

**Decision(codex 反映で確定)**: Elo 多者ペアワイズ。1 レースの着順から全ペア (i,j) の勝敗を作り、各馬 i を `ΔR_i = K/(m−1) · Σ_{j≠i}(S_ij − E_ij)`、`E_ij = 1/(1+10^((R_j−R_i)/400))` で更新。**m = 除外後の有効頭数(finish_order の付く valid ranked finishers のみ、raw starter count でない=codex #12)**。着順全体を使う。着差(margin)は初版で使わず OOS variant として deferred(codex #6: 距離/馬場/ペース依存ノイズ)。

**Rationale**: codex #4 = pairwise Elo + K/(m−1) 正規化は「実用的で決定論検証が容易な baseline」。Plackett-Luce は full finishing order に統計的により綺麗だが実装・決定論検証コスト大で初版見送り(deferred)。

**Alternatives**: PL/BT オンライン(codex: cleaner だが pragmatic でない)、着差重み付き Elo(deferred)。

## D2: 逐次状態の materialize 安全性(最重要)

**Decision**: `build_asof_features` 内で全レースを **(race_date, race_id) 昇順に 1 パス**処理。各レースで「更新前レーティング」を出走各馬の特徴として記録 → 結果でレーティング更新。累積状態は過去のみ反映=各行は strictly-before 依存 → **pool-end 非依存**。

**codex #1(最重要 PARITY RISK)への回答**: 「窓ロードが履歴途中から始まると full-history と不一致」— 実コード確認済みで**回避されている**: `build_feature_matrix` は `load_frames(session, end_date=end_date)` で**下限なし=常に 2007(INGEST_SCOPE_START)から**ロードする(窓は上限のみ、`start_after` は fingerprint delta 専用)。よって in-memory も materialize も全て 2007 から 1 パスでレーティングを構築 → 重複行は一致。中間開始の窓ロードは存在せず、checkpoint 機構は不要。**この前提が崩れる変更(下限窓ロード導入)を禁止する回帰テストを置く**。

**決定論(codex #11)**: stable sort(race_date, race_id, horse_id)・順序固定の集約・float64 固定・並列/非順序 reduce 禁止。

**検証**: (a) 決定論(同一データ 2 回 build で bit 一致)(b) **pool-end 非依存(end_date=D1 build と end_date=D2>D1 build で races≤D1 の行が一致)**(c) in-memory/materialized bit 一致 (d) **full-history vs 窓(下限あり)build の parity 回帰**。全て機械固定。

## D3: 同日レースの更新順序(同日除外との両立)

**Decision(codex #2/#3 で確認)**: **日単位凍結**。同日の全レースには「その日の朝時点(= その日の最初のレース処理前)のレーティングスナップショット」を特徴として使い、レーティング更新は**日末にまとめて**適用(その日の全レースの delta を朝スナップショットから計算し、日末に一括適用)。

**Rationale**: codex #2 = 同日除外には whole-day-batched 更新が必須(race-by-race incremental は同日リーク)。codex #3 = 同日 2 走する馬は両スタートで同一の朝レーティングを見て、両レースが対称に日末更新へ寄与する。023/026 の同日除外規律と厳密に整合。

## D4: DNF/取消/失格/初出走

**Decision(codex #7 反映)**: finish_order が付く valid ranked finisher のみ対戦に算入。DNF(started だが finish_order NULL/stopped)・失格は対戦更新から除外(「全馬に負け」扱いにしない)。**同着(tie)は S=0.5(勝敗を分ける)、任意の行順タイブレークは禁止**。取消は出走なし。初出走は固定初期値 1500。除外後の有効頭数 m を K の分母に使う(D1)。ただし**除外馬も「その日出走した」ので、朝スナップショットの特徴行は作る**(対戦更新に入らないだけ)。

## D5: 派生列セット(FEATURE_GROUPS: rating、暫定)

**Decision(codex #8/#9 反映で確定)**: 5 列 — `asof_rating`(水準)/ `asof_rating_recent_delta`(直近 n レースの変化=勢い)/ `asof_rating_vs_field`(今走出走馬の as-of レーティング平均との差、059 LOO 同型)/ `asof_rating_max`(自己ベスト)/ `asof_rating_starts`(出走数=信頼度、初出走 0.0)。**全派生列は同一の朝スナップショット状態から計算する**(codex #8: recent_delta/max/starts も base rating と同じく leak-prone、naive groupby/shift で同日結果を見てはいけない → レーティング状態オブジェクト内で朝時点値として一緒に記録)。

**二重相対化(codex #9)**: asof_rating_vs_field はレーティングが既に相手品質を織り込むため 059 と冗長の可能性大だが「有害ではない」→ **pl_topk ablation に判定を委ねる**(初版は含め、pl_topk spike で寄与を見る)。

## D6: ハイパラ固定

**Decision**: K=24・初期値 1500・スケール 400 を全期間固定(OOS 調整しない=選択リーク回避、017/035 前例)。

## D7: serving 互換(058/061 第3回)

**Decision**: `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-017"] = {"features-016": <上記 hash>, "features-015": <既存 060 で計測済み 0a93f2...>}`。実 DB E2E で lgbm-061(016)byte-parity + 058-acc/060-mkt(015)compat-load。

## D8: spike 設計(FR-011)

**Decision**: (1) 小規模既知対戦データでレーティング正しさ(強い馬が高レーティングに収束)+ materialize 決定性・pool-end 非依存をユニットで固定 → (2) 実 DB 直近 fold で binary + **pl_topk 両方**の group-marginal(061 教訓: Elo は既存能力と重複しうるため pl_topk 確認必須)。Go = binary 改善かつ pl_topk 非悪化。No-go は中断・記録。

## D9: 初期コールドスタート(2007、codex #10)

**Decision**: 2007 初期の全馬初期値 1500 でレーティングが弱信号になる期間は**リークでない**(ハイパラを全期間の後知恵で調整しない限り)。starts 列で信頼度を明示し受容。walk-forward 評価は初期 fold から自然に扱う。

## codex 指摘の採否まとめ(plan.md にも記載)

全 12 指摘を採用: #1 窓ロード parity(実コードで回避確認+回帰テスト)・#2 whole-day-batched・#3 同日 2 走対称・#4 Elo pragmatic baseline・#5 K=24 は後知恵調整しない・#6 margin は deferred・#7 DNF 除外/tie=0.5/行順タイブレーク禁止・#8 派生列も朝スナップショットから・#9 vs_field は pl_topk ablation 判定・#10 コールドスタート受容・#11 float 決定論(stable sort/固定 dtype/非並列)・#12 K 分母は除外後有効頭数 m。不採用ゼロ。

## 必要テスト(codex 推奨、tasks へ)

手計算小フィクスチャ Elo delta・同日複数レース朝凍結・同日 2 走馬・full-history vs 窓 parity・pool-end 非依存(異なる end_date で重複行一致)・NULL/DNF/DQ/取消/tie フィクスチャ・in-memory vs parquet bit parity・pl_topk walk-forward ゲート(binary は予備)。
