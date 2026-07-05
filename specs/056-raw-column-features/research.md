# Research: JRA-VAN 生データ未使用カラムの活用 (056)

Phase 0 決定記録。codex CLI 起動不可(セッション 5 回)→ single-opinion(spike 実測+シリーズ前例で補強、各決定に却下代替案を明記)。

## D1: 新列の置き場所 — 意味に沿った最小配置

- **Decision**: `race_results.first_3f`(numeric、テン3F 秒=結果由来で last_3f の隣)/ `races.prize_money`(int 万円、レース内定数を 2024 全 3454 レースで検証済み=1着賞金)/ `horses.owner_name`・`breeder_name`・`sire_line`・`damsire_line`(text)。全て nullable、migration 0010(head=0009 から)1 本。
- **Rationale**: first_3f は行=出走結果に紐づく(last_3f と同格)。prize はレース属性。breeder・系統は馬に不変。owner は稀に変わるが horses に last-write-wins で保持(下記)。
- **Alternatives considered**: `race_horses.owner_name`(行時点の馬主)— 意味的には最も正確だが、馬主変更は稀で、026 の sire_name(horses 静的列を集約キーに使う)前例と同じ扱いで十分。行単位の大テーブル拡張コストに見合わない。却下(制約は D2 の rationale に明記)。

## D2: 馬主・生産者の予測活用 — as-of 集約(human_form 同型)。TE 拡張は deferred

- **Decision**: `asof_owner_win_rate`・`asof_owner_place_rate`・`asof_breeder_win_rate` を **020 human_form と同一機構(daily cumsum − 当日 = 対象行+同日除外)** の跨馬(跨エンティティ)統計として実装。キーは NFKC 正規化名(026 `_normalize_name` 流用)。TE(target_encode_cols への owner/breeder 追加)は**今回入れない**。
- **Rationale**: (a) シリーズ標準の採用ゲート(feature-eval)は TE 無しの素の LightGBMPredictor で回る設計 — as-of 集約なら新群がゲートでそのまま測れる。TE 拡張は「モデリング変更」であり 036 の model-eval 経路で別途事前登録すべき(束ねると採否が混濁)。(b) owner は horses 静的列のため「現馬主に過去走を帰属」する近似になる — 勝率集約なら意味が明快で、未来情報リークはない(現馬主は予測時点で既知)。
- **Alternatives considered**: TE 同時導入 — ゲート経路の非対称(feature-eval は TE を使わない)で bundle 判定が TE 効果を測れず、採用時だけ効く形になり評価と運用が乖離。deferred(採用後の追い feature)。owner/breeder の産駒数 min_starts ゲート — 026 同様 rate 系は分母極小で不安定なので `min_starts=20` 未満は NaN。

## D3: 特徴列設計 — 4 群 11 列(features-013)

- **Decision**:
  - **pace_first3f 群(as-of、materialize 対象)**: 過去走ごとに `rel_first3f = first_3f − そのレース finisher 平均`(023 の in-race relative と同型、距離/馬場を自然吸収)→ `asof_rel_first3f_avg`・`asof_rel_first3f_best`(小さいほど先行力)+ `asof_pace_balance_avg`(= 過去走の `rel_last3f − rel_first3f` 平均。正=前傾型(前半相対速い)、負=後傾型)。recent-N でなく全過去 expanding(023 既定に合わせる)。
  - **owner_breeder 群(as-of)**: D2 の 3 列。
  - **race_level 群**: `prize_money_log`(今走、static — レース条件=事前公開情報でリーク安全)+ `asof_prize_avg`(過去走レースの log1p(prize) 平均=馬の賞金クラス)+ `prize_rel`(今走 log − asof 平均=昇降級度合い、static×as-of の導出)。
  - **sire_line 群(static categorical)**: `sire_line`・`damsire_line`(〜20 値)。
- **Rationale**: pace は spike の増分実証済み(前半ペース=023 に無い軸)。balance は「単独の速さ」でなく「配分」で、031(展開)との将来交互作用の素地。prize_rel は 033 の class_transition の連続版で「新情報を効く形に」。系統は 026 の少数産駒 backoff。
- **Alternatives considered**: first_3f の生値/絶対値 — 距離・馬場で非可換。却下(相対のみ)。recent-N rolling 追加 — 列数増に対しシリーズで expanding が安定実績。却下(deferred)。頭数正規化(041 流) — first3F は位置でなく時間なので finisher 平均差で十分。

## D4: prize の意味とリーク安全性

- **Decision**: col24 = 1 着賞金(万円)、レース内定数(2024 年 3454 レースで非定数 0 件を検証)。**レース条件として事前公開される情報**であり結果由来ではない → 今走 static 特徴として使用可(POST_FRAME 以前に確定)。
- **Alternatives considered**: 獲得賞金(結果由来)との混同に注意 — 本列はレース属性であり全行同値のため結果ではないことを機械検証済み。as-of 側(asof_prize_avg)は過去走に限定し二重に安全。

## D5: backfill 方式 — 既存 ingest-year の全年再実行(新 CLI 不要)

- **Decision**: `upsert_core` は PK(race_id / horse_id / (race_id,horse_id))キーの列上書き upsert のため、**layout/parser に新列を足して既存 `ingest-year` を 2007–2025 で再実行するだけ**で新列が populate される。同一ファイル+同一 parser の再実行なので既存列は同値上書き=バイト不変・冪等。
- **Rationale**: 044 の backfill 哲学(既存経路の再利用=新リーク面ゼロ)。EXPECTED_COLUMNS=73 の検証は parser が fail-fast(全年レイアウト一貫の Assumption を実行時に保証)。
- **Alternatives considered**: 新列専用 UPDATE スクリプト — parser の二重実装になり、既存 upsert の監査(ingestion_jobs)から外れる。却下。

## D6: materialize fingerprint と feature-eval 既定

- **Decision**: `source_fingerprint` の射影列に race_results.first_3f / races.prize_money / horses 新 4 列を追加(既存 parquet は不一致=fail-closed で再生成要求、026 前例)。`training feature-eval` の既定 drop_groups を 055 の新 4 群に更新(041 の `_DEF_041` → `_DEF_055`、baseline=features-012 / candidate=features-013)。
- **Rationale**: 黙って古い materialize を読まない(025 非交渉)。ゲートの既定は「直近 feature の限界価値」を測るシリーズ運用。
- **Alternatives considered**: fingerprint 据え置き — 新列 backfill を検知できず 025 の fail-closed 原則違反。却下。

## D7: dtype・Unknown 規律

- **Decision**: as-of 数値列は float64 固定・NaN 伝播(0 埋め禁止)。categorical(sire_line/damsire_line)は builder の category 変換に乗せ、欠損は NaN(Unknown)。owner/breeder rate は min_starts=20 未満 NaN。
- **Rationale**: 憲法 IV(Unknown≠0)と 026 のプール依存 dtype ドリフト対策(パリティの前提)。
