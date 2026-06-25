# Research: 実 exotic オッズ取込と疑似→実 ROI 化

008(取込)・011(exotic EV)・憲法 V を踏まえた設計判断。codex second opinion(plan.md の表)を反映。codex の `odds_phase` は
憲法 V 優先で不採用。

## R1. selection 突合(BLOCKER)

- **Decision**: `exotic_odds.selection` は 011 の `to_selection`(`betting/.../exotic_selection.py`)を**唯一の正準化経路**として
  生成した**素の JSON 配列**で格納。順序券種(exacta/trifecta)=順序付き、無順序(quinella/wide/trio)=horse_number 昇順整列、
  複勝=単一要素 `[i]`。一意キーは `UNIQUE(race_id, bet_type, selection)` の複合 B-tree(JSONB 等価)。突合は recommendations/推定と
  **完全一致**(同一配列)。
- **Rationale**: `5`(スカラ)と `[5]`、順序差、整列差で JSONB 等価 join が外れる(codex BLOCKER)。011 と同一シリアライザを共有すれば
  キーが必ず一致。GIN ではなく複合 B-tree で等価検索/一意制約。
- **Alternatives**: 文字列キー("3-7-1")→ パース脆弱。frozenset → JSONB 非対応。却下。
- **実装**: scrape のパーサは horse_number の組をそのまま出し、`betting` 側の to_selection 互換ロジック(または共有関数)で正準化。
  パーサ層に 011 betting を依存させない場合は `db` 側に共有の正準化ヘルパを置くか、scrape で同一規則を複製しテストで一致を保証。

## R2. 単一最新値オッズ(憲法 V 優先、codex `odds_phase` 不採用)

- **Decision**: `exotic_odds` は (race_id, bet_type, selection) ごとに**単一の最新 odds + updated_at**を持つ(`race_horses.odds`
  と同型、`TimestampMixin`)。`odds_phase`/履歴行は持たない。スクレイプのたび最新値で**上書き**(レース前=事前オッズ、確定後=
  最終配当)。決定時オッズは推奨時に `recommendations.market_odds_used` へスナップショット。
- **Rationale**: 憲法 V「オッズはスナップショット履歴を保存せず、最新値で上書きし updated_at のみ保持」。codex の `odds_phase`(2 行)は
  この履歴禁止に反する。exotic は **netkeiba 単独源**で JRA-VAN 確定オッズの保護対象が無く(win オッズと違い)、上書きで問題ない。
  「事前=推奨 / 確定後=実払戻」は、推奨時スナップショット(recommendations)+ 過去レースの最新値(=最終配当)で達成。
- **上書き規律**: win オッズ(`update_odds`)は結果確定後に JRA-VAN を保護してスキップするが、exotic は保護対象が無いため**結果確定
  後も最新スクレイプ(=最終配当)で上書き**する。これにより過去レースの `exotic_odds` は最終配当に収束しバックテスト実払戻に使える。
- **Alternatives**: odds_phase 2 行(codex)→ 憲法 V 違反。別 history テーブル → V 違反 + スコープ過大。却下。

## R3. 冪等・ingestion_jobs 監査(BLOCKER)

- **Decision**: 取込は `ON CONFLICT (race_id, bet_type, selection) DO UPDATE`(最新値上書き)で冪等。`ingestion_jobs` は
  **`job_type='exotic_odds'`**(`event_type` ではない、`db/.../models/ingestion.py` で確認)・`status`(succeeded/partial/failed)・
  `summary`(期待/観測/欠損の組み合わせ数、券種別)で監査。部分取得は `status=partial` + `coverage_scope=partial`。
- **Rationale**: UNIQUE が無いと部分取込が重複/曖昧化(codex BLOCKER)。008 の Counts/summary 監査パターンを踏襲。
- **Alternatives**: 取込ごと全削除→再挿入 → 履歴破壊・非冪等の競合。却下。

## R4. 実/推定フォールバック配線(BLOCKER)

- **Decision**: betting に `load_real_exotic_odds(session, race_id) -> dict[(bet_type, tuple(selection)) -> odds]` を追加。推奨生成は
  **必ず 011 の `canonical_field`/`to_selection` を経由**して候補 selection を作り、その selection で実オッズ辞書を引く。
  - ヒット: `market_odds_used=実オッズ`・`is_estimated_odds=false`・`estimated_market_odds_used=null`・EV=P_model×実オッズ。
  - ミス: 011 推定(`is_estimated_odds=true`・`estimated_market_odds_used=O_est`・二重疑似)にフォールバック。
  - **行単位で実/推定を区別**(同一レースに両方混在しうるが、各 recommendation 行は一方のみ)。
  - 推奨後に取消が発生した馬を含む買い目は**void/skip**(推定で無理に払戻しない、採点で除外監査)。
- **Rationale**: canonical 母集団/to_selection をバイパスすると母集団ズレ・horse_number キー不一致(codex BLOCKER)。011 の母集団
  規律を単一経路で共有。
- **Alternatives**: 実オッズ母集団で別途正規化 → 011 と二重母集団。却下。

## R5. カバレッジ方針(RISK)

- **Decision**: netkeiba が公開する券種別オッズグリッドを格納し、`coverage_scope`(full/partial)で区別。**完全グリッドは期待件数
  テスト**(N 頭で exacta=N·(N−1)、trio=C(N,3) 等)で証明できる場合のみ full。欠損は推定(011)フォールバック + カバレッジ明示。
  三連単/三連複は取得コスト大のため取込を**期間/レース駆動で polite**に行う。
- **Rationale**: 完全グリッド未検証のまま full と誤認すると評価が歪む(codex RISK)。partial を明示しフォールバックで埋める。
- **Alternatives**: 全レース全グリッド強制取込 → スクレイプ/storage 過大(18 頭三連単 4896×レース数)。将来最適化(分割/間引き)。

## R6. 推定 vs 実 乖離評価(RISK・評価先行 III)

- **Decision**: `exotic_divergence` を実装。推定 O_est(010/011)を baseline、実 exotic オッズを実測として、券種別・レース単位で:
  - **カバレッジ率**(実オッズが存在した組み合わせ割合)
  - **符号付き log 比** `log(実/推定)` の中央値・MAE・P90
  - 実/推定ラベル分離、推定側は二重疑似明示。
- **Rationale**: 生の相対誤差や実/疑似混在ラベルは誤誘導(codex RISK)。log 比は乗法的乖離に頑健、カバレッジ率で部分カバーを明示。
- **Alternatives**: 単純差分・相対誤差のみ → 外れ値/スケール依存。却下。

## R7. リーク境界・決定論(II/V)

- **Decision**: exotic オッズは**特徴量・予測入力に一切使わない**(`features`/`serving` に流さない)。取込・突合・評価は決定論。
  buy 決定は p + オッズ + entry_status のみ、結果は採点のみ。`exotic_odds` は監査可能(updated_at, ingestion_jobs)。
- **Rationale**: 憲法 II/V。win オッズと同一のリーク境界。
- **Alternatives**: なし。

## まとめ(設計判断 → 要件)

| 研究項目 | 対応 FR / SC |
|---|---|
| R1 selection 突合 | FR-002 / FR-008 / SC-001 / SC-004 |
| R2 単一最新値(V) | FR-002 / FR-004 / SC-003 |
| R3 冪等監査 | FR-005 / SC-001 |
| R4 実/推定配線 | FR-007 / FR-008 / FR-009 / SC-004 / SC-005 |
| R5 coverage | FR-005 / SC-001 |
| R6 乖離評価 | FR-010 / SC-006 |
| R7 リーク/決定論 | FR-006 / FR-011 / SC-007 |
