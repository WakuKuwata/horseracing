# Feature Specification: JRA-VAN 生データ未使用カラムの活用 — テン3F・馬主/生産者・賞金レベル・系統

**Feature Branch**: `055-raw-column-features`

**Created**: 2026-07-03

**Status**: Draft

**Input**: User description: "win/place/show すべての精度改善のため、生 CSV 73 列中 35 列しか読んでいない ingest の未使用列(テン3F・馬主・生産者・本賞金・系統)を取り込み、features-013 候補束として feature 化する。"

## 背景と根拠(spike 実証済み)

精度レバーの棚卸し(2026-07)で「残る道は新データ」と結論されたが、**netkeiba 不要でディスクに既にあるデータ**が未活用だった。ingest は生 CSV 73 列中 35 列しか読んでいない。spike(2026-07-03)で価値を実証:

| 列 | 中身 | 検証結果 |
|---|---|---|
| col55 | **テン3F(前半3F 秒)** | 意味確定: 1200m(=6F)戦で「走破秒 = テン3F + 上がり3F」が 2010/2018/2024 の約 3 万レースで **100.000% 成立**。カバレッジ 96%。粗い OOS spike(logistic、train 2016–22 / test 2023–24、レース内相対→as-of 平均)で**上がり 3F 統制下の増分 LogLoss −0.006**(0.25490→0.24885)= 強シグナル |
| col65/66 | **馬主・生産者** | カバレッジ 100%。as-of 勝率単体で base 0.26267→owner 0.25999・owner+breeder 0.25920。高カーディナリティ categorical = **036 TE(全履歴最大レバー −0.0134)と同型の未着手エンティティ** |
| col24 | **本賞金(万円)** | カバレッジ 100%。race_class より細かいレースレベル連続 proxy(昇級度合い・馬の賞金クラス相対) |
| col70/71 | **父系統・母父系統** | カバレッジ ~100%。低カーディナリティ(〜20 系統)で 026 種牡馬名集約の粗い補完(少数産駒種牡馬の backoff) |

意義: (1) テン3F は 023(上がりのみ)に無い**前半ペース**の新情報で、netkeiba ブロックで止まっている 035 区間ラップの部分代替(no-netkeiba 方針 [[no-netkeiba-scrape-features-from-db]] と完全両立)。(2) 「新情報を効く形に」(031/032/033 の勝ち筋)と「高カード categorical の活用」(036)という実証済み 2 パターンの直撃。

codex CLI は本セッション 5 回起動不可 → single-opinion(spike 実測とシリーズ前例で補強)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 未使用カラムの ingest 拡張 (Priority: P1)

データ基盤の利用者は、既存の生 CSV 再取込だけで新列(テン3F・本賞金・馬主・生産者・父系統・母父系統)が DB に populate され、既存データは一切変わらないことを期待する。

**Why this priority**: 後続の全特徴の前提。これ単体でも「データが DB に揃う」価値がある。

**Independent Test**: 1 年分の再 ingest で新列が期待カバレッジ(テン3F ~96%・他 ~100%)で populate され、既存列・既存行数がバイト不変であることを検証。

**Acceptance Scenarios**:

1. **Given** migration 適用済みの DB、**When** 既存 CLI で年次再 ingest、**Then** 新列が populate され(冪等=再実行で同一結果)、既存列の値・行数は不変。
2. **Given** テン3F が空の行(~4%)、**When** ingest、**Then** NULL として格納(0 埋めしない、Unknown≠0)。
3. **Given** 025 の materialize 済み parquet、**When** 新ソース列を含む fingerprint で読み出し、**Then** fail-closed(不一致検知)で再生成が要求される(026 前例)。

---

### User Story 2 - features-013 特徴群 (Priority: P2)

モデル開発者は、新列から リーク安全な as-of 特徴群を構築し、既存の evaluation 経路(feature-eval/ablation)でそのまま評価できる。

**Why this priority**: 本 feature の中核価値(精度改善)。US1 完了が前提。

**Independent Test**: 特徴群が全行で計算され(カバレッジ明示)、leak-guard テスト(今走結果・同日・オッズ非流入)が緑、実 DB parity(materialize == in-memory)ビット一致。

**Acceptance Scenarios**:

1. **Given** populate 済み DB、**When** feature 構築、**Then** 新群が生成される:
   - **pace_first3f 群**: 過去走のレース内相対テン3F(finisher 平均との差)の as-of avg/best + 前後半バランス(rel_first3f − rel_last3f 的な脚質・ペース配分指標)— 023 の in-race relative + merge_asof(allow_exact_matches=False)機構流用
   - **owner_breeder 群**: 馬主・生産者の予測活用(TE 拡張か as-of 集約かは plan で確定 — 036/human_form 前例)
   - **race_level 群**: 賞金由来のレースレベル(log 賞金・馬の過去出走賞金レベルとの相対=昇降級度合い)
   - **sire_line 群**: 父系統・母父系統(粗い categorical または系統別 as-of 集約)
2. **Given** 対象レースより後・同日の結果を改変、**When** 特徴再計算、**Then** 対象行の特徴は不変(strictly-before、憲法 II)。
3. **Given** FEATURE_VERSION、**Then** features-012→013 に bump され、旧モデルの feature_hash 検証は 013 マージ前の状態で壊れない(**035 教訓: bump を含む変更は採用決定後にのみ main へマージ**)。

---

### User Story 3 - 採用ゲートと再学習 (Priority: P3)

モデル運用者は、事前登録ゲート(シリーズ標準)で bundle の採否を機械判定し、通過時のみ lgbm-055 を再学習・昇格する。

**Why this priority**: 憲法 III。数値を見てからの変更を許さない。

**Independent Test**: feature-eval が baseline=features-012(新 4 群 drop)vs candidate=features-013 で 18-fold 実行され、ゲート判定・fold 別数値がレポートされる。

**Acceptance Scenarios**:

1. **Given** 18-fold walk-forward、**When** bundle feature-eval(020/023/030/041 同型)、**Then** PRIMARY=win LogLoss 改善+mean ECE 非悪化(tol 1e-3)+strict majority+worst-fold ガード(ECE 2e-3 / LogLoss 5e-3)で機械判定。
2. **Given** ADOPTED、**When** train-evaluate で lgbm-055 学習、**Then** 機械ゲート通過で active 昇格・lgbm-042 retired・serving ロード確認。per-group 寄与は ablation で diagnostic 記録(採否には使わない)。
3. **Given** 不採用、**Then** 負結果を記録し FEATURE_VERSION bump を main にマージしない(ブランチ保全、035 前例)。

---

### Edge Cases

- **テン3F 欠損(~4%)**: NULL 伝播(0 埋め禁止)。as-of 集約は present な過去走のみで計算し、全欠損馬は NaN(Unknown)。
- **馬主名の表記ゆれ・変更**: 名前キーは NFKC 正規化(026 `_normalize_name` 流用)。馬主変更(移籍)は行時点の値をそのまま使う(as-of 整合)。
- **地方・海外遠征等で系統欠損**: NaN(Unknown)。
- **賞金 0/欠損レース**: NULL。race_level 特徴は NaN 伝播。
- **再 ingest の冪等性**: 同一ファイル再実行で行数・値が完全一致(既存 upsert 規律)。既存行の既存列は 1 ビットも変わらない(バイト不変テスト)。
- **fingerprint 移行**: 新ソース列追加により materialize 済み parquet は全て要再生成(fail-closed が正しく発火することがテスト対象)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: ingest は新列(テン3F・本賞金・馬主・生産者・父系統・母父系統)を既存 CSV から追加取込し、**既存列・既存行はバイト不変**、再実行冪等でなければならない。欠損は NULL(0 埋め禁止、憲法 IV)。
- **FR-002**: スキーマ変更は 1 migration(0010、head=0009 から)に限定し、nullable 列の追加のみ(既存契約を壊さない、憲法 VI)。列の置き場所(race_results/races/horses/race_horses)は plan で確定。
- **FR-003**: 全特徴は対象レースより**厳密前**の as-of のみ(merge_asof allow_exact_matches=False、同日除外、跨馬統計は自馬除外の 026 規律)。オッズ・今走結果は特徴に流入しない(leak-guard テスト、憲法 II)。
- **FR-004**: 特徴は 025 `build_asof_features` 単一源に結線し、source_fingerprint を新ソース列込みに拡張(fail-closed)、materialize == in-memory のビットパリティを維持する。
- **FR-005**: 採否は US3 の事前登録ゲート(シリーズ標準の feature-eval bundle 判定)に完全一致で機械判定する。per-group ablation は diagnostic のみ。
- **FR-006**: FEATURE_VERSION は features-013 に bump するが、**採用決定後にのみ main へマージ**する(不採用ならブランチ保全、035 教訓)。
- **FR-007**: API・front・openapi 契約変更なし。予測経路の出力スキーマ不変。

### Key Entities

- **テン3F(first_3f)**: 過去走の前半 3 ハロン秒。結果由来のため学習ラベル・as-of 特徴にのみ使用(今走値は特徴にしない)。
- **馬主・生産者**: 馬に紐づく高カーディナリティ名前エンティティ(ID 列は生データに無いため名前キー+NFKC 正規化)。
- **本賞金**: レースに紐づく万円単位整数。レースレベルの連続 proxy。
- **父系統・母父系統**: 馬に紐づく低カーディナリティ系統名(〜20 種)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 全期間再 ingest 後、新列カバレッジがテン3F ≥95%・馬主/生産者/賞金/系統 ≥99% で、既存列は全行バイト不変。
- **SC-002**: 新 4 群の leak-guard・パリティ(materialize==in-memory ビット一致)・冪等テストが全緑。
- **SC-003**: 18-fold bundle feature-eval が機械実行され、採否判定と fold 別数値が記録される(目標: win LogLoss 改善。spike の −0.006 は粗い単変量なので本番では縮むが、シリーズ実績 [031: −0.00077 / 036: −0.0134] のレンジ内の改善を期待)。
- **SC-004**: (採用時)lgbm-055 が active 昇格し、serving が features-013 の 100+ 列をロードして予測(win/top2/top3)が全整合性テストを通過。
- **SC-005**: 全パッケージスイート緑・openapi drift-check 一致。

## Assumptions

- 生 CSV の列レイアウトは全年度(2007–2025)で一貫している(2010/2018/2024 の 3 断面で検証済み。全年 EXPECTED_COLUMNS=73 検証は ingest 時に fail-fast)。
- 再 ingest は既存の年次 CLI(ingest-year)を全期間実行する運用で行う(新 CLI 不要見込み、plan で確定)。
- owner/breeder の TE vs as-of 集約の選択は plan の技術判断(TE は training 側の te_cols 拡張、as-of は features 側の human_form 同型)。両方は入れない(冗長)。
- 採用ゲートの数値(tol 等)はシリーズ標準(020/023 で確立、feature-eval 既定値)をそのまま使う=本 spec で新規に動かさない。
- deferred: 残り未使用列(c44 走破秒・c54 上がり順位・毛色等)/ 2026 年データ ingest(ユーザーのデータ取得待ち)/ 条件付き λ(頭数バンド別、次 feature 候補)/ 直接 top2/top3 モデル / テン3F×脚質・展開(031)交互作用の深掘り。
