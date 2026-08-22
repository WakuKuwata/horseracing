# Feature Specification: Early-Mid Pace Features (rel_early_mid)

**Feature Branch**: `097-early-mid-pace`

**Created**: 2026-08-22

**Status**: Draft

**Input**: User description: "early-mid pace features (rel_early_mid) — 供給停止で失われたテン3F 情報の全距離・恒久回収。新列 asof_rel_early_mid_* 群として per-horse の「走破時計 − 上がり3F」のレース内相対を as-of 集約し、FEATURE_VERSION bump + 再学習 + 事前登録ゲートで採否を決める。"

## 背景と根拠(実測済み)

`race_results.first_3f`(馬ごとのテン3F)は JRA-VAN 供給停止で死んだ(2024 年 96.8% → 2025 年
74.4% → 2026 年 0.0%)。netkeiba は馬ごとの上がり3F しか出さないため**全復旧は原理的に不可能**
([[first3f-per-horse-unavailable]])。1200m の恒等式導出は実装済みだが、回収できたのは軸の価値の
25% に留まる。

kill-test 実測(`evidence/first3f-killtest.json`・稼働モデル固定・再学習なし・開催日クラスタ
bootstrap・n=26,410):

    テン3F 軸を全て失う           : -0.016253  CI[-0.016733, -0.014841]
    1200m 導出のみの定常状態      : -0.011681 (対 完全) / 何も無いより -0.004082 良い
    代替量を全距離に流し込む(arm E): -0.006231 (対 完全) = 失った分の 60% を回収
    ただし arm E − 実測           : +0.006231 = 完全代替ではない

**代替量** `rel_early_mid = (走破時計 − 上がり3F) のレース内相対` は per-horse で全距離
99.5% 供給され続け、1200m ではテン3F と恒等、1200m 超でも corr +0.9754・着順との相関は実測
テン3F を上回る(+0.3484 vs +0.3374)。

**なぜ既存列への流し込みではなく独立新列か(非交渉)**: arm E は現行レジーム(2025-2026)で、
充足 19% しかない実測(1200m のみ)と比べ実測比 0.0100 悪い side を示した — データが 4.7 倍
あるのに負ける。長距離では「前半+中盤」という別の量であり、実測テン3F で学習した木の分割閾値と
合わない。**列名を変えず意味を変えるのは 017/091 と同型の train/serve 事故**。全履歴で一貫した
量として独立列に置き、再学習でその量を学ばせるのが唯一の健全な形。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 一貫した早め区間ペース特徴の構築 (Priority: P1)

モデル運用者として、供給が死なない入力(走破時計・上がり3F)だけから作られる早め区間ペースの
as-of 特徴群を持ちたい。テン3F 供給停止後も、馬の「序盤〜中盤をどれだけ速く走るか」という軸が
予測に恒久的に供給され続けるようにするため。

**Why this priority**: 軸の価値は実測 -0.0163(arm E 級超)で、現状の 1200m 導出では 25% しか
守れていない。構築が全ての前提。

**Independent Test**: 特徴ビルドを実 DB で走らせ、(a) 新列が全年でほぼ 100% 充足する(入力が
99.5% あるため)、(b) 既存の共有列が 1 バイトも変わらない、(c) リーク境界テスト(今走・同日・
未来の結果を変えても対象行の特徴が不変)が緑、で単独検証できる。

**Acceptance Scenarios**:

1. **Given** 実 DB の全履歴, **When** 新 FEATURE_VERSION で特徴を全量ビルドする, **Then**
   既存の全共有列は旧版ビルドとバイト一致し(check_exact + check_dtype)、新列のみが追加される
2. **Given** 対象レースの結果・同日他レースの結果・未来レースの結果, **When** それらを変更して
   再ビルドする, **Then** 対象行の新列は不変(strictly-before + 同日除外)
3. **Given** 2026 年のレース(first_3f 実測 0% の年), **When** 新列の充足率を監査する, **Then**
   過去走を持つ馬の行で 95% 以上が非欠損
4. **Given** materialized parquet 経路, **When** 新版で materialize→読み出しする, **Then**
   in-memory ビルドとバイト一致(新規ソース列ゼロ = source_fingerprint 不変)

---

### User Story 2 - 事前登録ゲートでの採否判定 (Priority: P2)

モデル運用者として、新列の価値を**結果を見る前に凍結した判定基準**で測り、ADOPT / REJECT を
一度だけ決めたい。効果はテン3F 供給が死んだレジームでしか現れない(2024 年以前は実測が生きて
いるので新列は冗長)ため、**判定はレジームを正しく反映した評価で行う**必要がある。

**Why this priority**: 構築(US1)だけでは価値はゼロ。ただし US1 が先に完成しないと測れない。

**Independent Test**: 凍結 gate-config(hash 付き)の下で確認評価を 1 回実行し、事前登録した
単一の判定式が ADOPT / REJECT を返すことで検証できる。

**Acceptance Scenarios**:

1. **Given** 凍結済み gate-config(evaluation contract v4・seed 分散宣言込み), **When** 確認
   評価を実行する, **Then** 判定式は単一(`gate.adopted AND subgroup_guard`)で、レポートに
   gate-config hash・レース集合 hash・両アームの recipe hash が記録される
2. **Given** 供給死亡レジームを反映した primary 評価, **When** 候補(新列あり)と基準(新列なし)
   を比較する, **Then** 差の判定区間(再学習分散込み)が事前登録した最小効果 δ を下回る場合のみ
   primary 成立
3. **Given** full-info(実測テン3F が生きている窓)の guard 評価, **When** 候補が基準より
   悪化していないかを検査する, **Then** 非劣性マージン内でない場合は ADOPT しない
4. **Given** 評価が実行不能な場合のみ, **Then** NO_DECISION(ボーダーの数値の読み替えによる
   NO_DECISION は禁止)

---

### User Story 3 - 判定に従った後始末 (Priority: P3)

モデル運用者として、REJECT の場合は FEATURE_VERSION bump と結線だけを revert し、モジュールと
テストを負の結果の記録として非結線保全したい(062/070/090 と同じ規律)。ADOPT の場合は serving
互換(旧版 pin)を検証した上で候補モデルとして登録したい。

**Why this priority**: 判定後の作業。どちらの分岐でも repo を汚さない終端が要る。

**Independent Test**: REJECT 分岐 = revert 後に active モデルの予測がバイト一致し、保全モジュール
の単体テストが直呼びで緑。ADOPT 分岐 = 旧 FEATURE_VERSION の active モデルが compat pin で
serving 継続できる。

**Acceptance Scenarios**:

1. **Given** REJECT, **When** bump と結線を revert する, **Then** active モデルの実 DB 予測が
   revert 前後でバイト一致し、新列モジュール+単体テストは非結線で緑
2. **Given** ADOPT, **When** 新版で候補を登録する, **Then** 現 active(旧版)の serving は
   compat pin(実測 hash)で無変更継続する

---

### Edge Cases

- **走破時計 ≤ 上がり3F の行**(入力破損): rel_early_mid の元値を作らない(NaN 伝播)。0 や負を
  埋めない。1200m 導出 backfill と同じ規律
- **上がり3F または走破時計が欠けた過去走**: その走は集約から除外(既存 023 の finished-only
  規約と同じ)。0 埋め禁止
- **過去走ゼロの馬(デビュー)**: 新列は NaN(既存 as-of 列と同じ意味論)
- **障害レース**: 既存の pace 集約の母集団規約に従う(新しい除外を発明しない)
- **1200m の行**: rel_early_mid はテン3F と恒等になるが、これは仕様(同一量の一貫版)であって
  重複バグではない。実測テン3F 列とは独立に共存する

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 新列は `rel_early_mid = (走破時計 − 上がり3F) − そのレースの完走馬平均` を単位と
  し、既存 pace 集約と同じ recent-N rolling + strictly-before + 同日除外の as-of 機構で馬単位に
  集約する
- **FR-002**: 新列は**独立列**であり、`first_3f` 列にもその派生列にも値を流し込まない。既存の
  `asof_rel_first3f_*` 3 列は本 feature では削除しない(帰属分離 — 058/070 の規律)
- **FR-003**: 列集合は事前登録で固定する。**近線形冗長の開示**: `_avg` は既存
  `rel_time_avg − rel_last3f_avg` のほぼ厳密な線形結合である(全集約が同一 rolling 窓を共有する
  ことをコードで確認済み)。採用列は plan 段階の codex レビューを経て凍結し、OOS 後の列選別は
  禁止する
- **FR-004**: 新規ソース列を読まない(走破時計・上がり3F は既存 loader が供給済み)=
  source_fingerprint 不変 = materialize-safe
- **FR-005**: FEATURE_VERSION を bump する。焼却済み番号(019, 020)は再利用しない。現 active
  モデルの旧版は実測 hash で compat pin する
- **FR-006**: 共有列のバイト不変を全量(約 96 万行)で検証する(check_exact + check_dtype)
- **FR-007**: リーク境界は挙動テストで固定する: 今走結果・同日他レース・未来レースの変更で
  対象行の新列が不変
- **FR-008**: 採否は evaluation contract v4 の事前登録ゲートで決める。gate-config は実行前に
  凍結し hash で fail-closed。判定式は単一・最小効果 δ と seed 分散(sd_fold)を宣言する
- **FR-009**: primary 評価は**テン3F 供給死亡レジームを反映した設計**とする(2024 以前の
  full-info 窓の標準 paired-eval では新列は構造的に冗長に見えるため、そのままでは判定に使えない)。
  レジーム反映の具体機構(劣化シミュレーション / opportunity-set / 実劣化窓)は plan で codex
  レビューを経て決定し、**決定後に凍結**する
- **FR-010**: full-info regime の非劣化 guard を併設する(候補が実測テン3F の生きた過去で
  悪化しないこと)
- **FR-011**: REJECT 時は bump+結線のみ revert し、モジュール+単体テストを非結線保全する。
  revert 後に active モデルの予測バイト一致を実 DB で検証する
- **FR-012**: モデル入力・スキーマ・migration・API・OpenAPI・買い目・ops は不変。実装変更は
  features/training/eval に閉じる。**表示専用ラベル**(front/admin の featureLabels)の追加は許容
  する(088 の教訓: 表示ラベル網羅テストが model-input 列すべてに日本語ラベルを要求する)
- **FR-013**: 判定に使った evidence(kill-test JSON・確認評価レポート)は追跡されるパスに置く

### Key Entities

- **rel_early_mid(走単位)**: 過去走 1 走における「上がり3F を除いた自分の走破時間」のレース内
  相対値(秒)。負 = フィールドより速い early-mid ペース
- **asof_rel_early_mid_\***(馬×レース単位): 対象レースより厳密前の完走走に対する rolling 集約。
  欠損 = 情報なし(NaN)
- **採否判定レポート**: gate-config hash・両アーム recipe hash・レース集合 hash・判定区間・
  verdict を持つ append-only 成果物

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 新版ビルドで既存共有列が全量バイト一致する(mismatch 0 行)
- **SC-002**: 2026 年(実測テン3F 0% の年)で、過去走を持つ馬の行の新列充足率が 95% 以上
- **SC-003**: リーク境界の挙動テスト(今走/同日/未来の 3 方向)が全て緑
- **SC-004**: 採否が単一の事前登録式で決まり、レポートから hash 3 点(gate-config・レース集合・
  recipe)で再現可能
- **SC-005**: REJECT 分岐では revert 後の active モデル予測がバイト一致(mismatch 0)。ADOPT
  分岐では旧版 active の serving が無変更で継続
- **SC-006**: 判定レジームでの効果の点推定と判定区間が evidence として追跡パスに残る

## Assumptions

- 走破時計・上がり3F の供給(netkeiba 経由 99.4-99.6%)は今後も継続する。これが死ねば本 feature
  の前提ごと崩れる(その場合は充足監査 SC-002 で検知される)
- kill-test の -0.0095(arm E)は**流し込み方式での値**であり、独立列+再学習方式の効果の保証では
  ない。判定レジームでの期待効果は現状比 -0.005 前後と見込むが、これは見込みであって事前登録値
  ではない(登録するのは最小効果 δ)
- 近線形冗長のため、full-info 窓での効果はゼロに近いことを**期待として**受け入れる(そこで
  効かないことは失敗ではない — guard が守るのは非劣化のみ)
- 現 active は lgbm-094-cap900(arm E + rounds 900 + 091 体重マスク)。確認評価のアームは
  これと同レシピを基準にする
