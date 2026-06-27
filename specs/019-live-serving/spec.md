# Feature Specification: ライブ serving（未開催レースの予測・推奨生成）

**Feature Branch**: `019-live-serving`

**Created**: 2026-06-27

**Status**: Draft

**Input**: User description: "008 スクレイピングを serving に接続し、未開催（結果未確定）レースの entries + pre-race オッズで予測・推奨を生成。closing-leaning 制約を解消。リーク境界 critical、結果が無いため eval はパリティ + prospective ログ。"

## 概要

これまでの予測・推奨は全て retrospective で、`race_horses.odds` が closing-leaning（確定後オッズ寄り）という
制約を Feature 010〜017 で開示してきた。本 feature は Feature 008（netkeiba スクレイピング）を serving 経路に
接続し、**未開催（結果未確定）レース**の entries + **pre-race オッズ**で予測と推奨を生成して、この
closing-leaning 制約を解消する（主目的）。スキーマ変更なし。

重要な現実: races テーブルには post_time カラムが存在するが多くのデータで null であり、特徴量(Feature 004)は
日付粒度で walk-forward する。したがって walk-forward cutoff は
**日付粒度（race_date）**（Feature 004 の既存規律と同一）とし、本 feature では時刻粒度 cutoff を新設しない。「まだ走って
いない」の判定は壁時計ではなく **結果行の不在（result-pending）**で行う（堅牢・明確）。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 未開催レースの live 予測（Priority: P1）🎯 MVP

運用者は、未開催（結果未確定）の対象レースについて、最新の entries と pre-race オッズを取り込み、モデル予測を
生成・永続化したい。誤って既に走ったレースや不完全なデータで予測しないよう fail-closed であってほしい。

**Why this priority**: 未来レースの予測生成が本 feature の中核。pre-race データでの予測が後段（推奨）の前提。

**Independent Test**: result-pending の race_id に対し live-serve（scrape→features→predict）を実行し、
prediction_run + race_predictions が永続化される。結果が既に存在する／entries 不完全／race_id 不正な場合は
予測せず明確に拒否する（fail-closed）。

**Acceptance Scenarios**:

1. **Given** valid JRA-VAN 12桁の result-pending race_id, **When** live-serve を実行, **Then** 008 で entries +
   pre-race win オッズを取り込み、features（race_date cutoff、004 規律）を構築し、予測を生成して prediction_run
   （model_version / feature 版 / computed_at）+ race_predictions に永続化する。
2. **Given** 既に結果が存在する（result-pending でない）race, **When** live-serve を実行, **Then** **拒否**
   （live モードは走行済みレースを処理しない。retrospective 経路を使うよう案内）。
3. **Given** race_id が valid JRA-VAN 12桁でない（fake/未確定）, **When** live-serve, **Then** 行を書かず拒否。
4. **Given** entries が部分取得（馬番欠落・頭数不整合・スクレイプ失敗）, **When** live-serve, **Then** 予測せず
   fail-closed で中止し、理由を surface する。
5. **Given** 新馬 / unmapped（nk: surrogate）馬を含む, **When** 予測, **Then** 当該馬は Unknown=欠損特徴として
   渡し（0 代入しない）、出走頭数に含める（母集団から落として正規化を壊さない）。

---

### User Story 2 - 未開催レースの live 推奨（pre-race オッズ）（Priority: P1）

運用者は、live 予測に続けて、pre-race オッズに基づく推定オッズ・exotic EV・Kelly 推奨を生成し、判断に用いた
オッズ値と時点を後から監査できる形で残したい。

**Why this priority**: closing-leaning 制約の解消を実運用の推奨に届ける。pre-race オッズでの推奨が本 feature の
ユーザー価値。

**Independent Test**: live 予測済みレースで、pre-race オッズから 010 推定オッズ → 011 exotic EV / 016 Kelly を
生成し、recommendations に **使用オッズ値 + as_of + 対象出走集合**が記録される（append-only）。再実行で新しい
オッズが反映され、各時点が監査できる。

**Acceptance Scenarios**:

1. **Given** live 予測済みの result-pending race と pre-race オッズ, **When** 推奨生成, **Then** 009 結合確率 →
   010 推定オッズ（pre-race odds 由来）→ 011/016 推奨が recommendations に append-only 保存され、各行が
   **使用オッズ値**（market_odds_used / estimated_market_odds_used）+ computed_at + odds as_of を持つ。
2. **Given** pre-race オッズが未確定/欠損（発売前・一部馬欠損）, **When** 推奨生成, **Then** 推奨を出さず
   fail-closed（欠損のまま EV/Kelly を計算しない）。
3. **Given** オッズが発走まで動く, **When** 再 live-serve, **Then** 新しいオッズで新しい推奨行が append され、
   旧行は残る（computed_at + 使用オッズ値で各時点を再現・監査）。スナップショット履歴テーブルは持たない。
4. **Given** 推奨生成後に出走取消, **When** 再 serving, **Then** 当該買い目は void/skip（011/012 規約）。
5. **Given** 013/017 校正器, **When** opt-in 指定, **Then** 校正を適用して推奨を生成し、logic_version に記録する。

---

### User Story 3 - リーク無しの検証と prospective ログ（Priority: P2）

開発者は、結果が無い未来レースでも「リークしていない」ことと「後日 backtest で事後評価できる」ことを保証したい。

**Why this priority**: 憲法 III（評価先行）を未来レース文脈で満たす。結果が無いため backtest 不能 → パリティ +
リーク境界 + prospective ログで代替。

**Independent Test**: 過去レースで「live 経路（race_date cutoff、pre-cutoff データのみ）」と「retrospective 経路」が
**同一の予測 p** を出すこと、cutoff 以降・他レース・結果が features に出ないこと、生成物が computed_at 付きで
後日 backtest 可能に残ることを検証。

**Acceptance Scenarios**:

1. **Given** 結果のある過去レース, **When** live 経路（cutoff=race_date、pre-cutoff のみ）と retrospective 経路で
   予測, **Then** **予測 p が一致**する（リーク無しの確認）。**注意**: 過去の pre-race オッズは残っていない
   （closing で上書き済み）ため、**オッズ依存の推奨・EV の過去パリティは対象外**。パリティ対象は features と p。
2. **Given** live features, **When** リーク境界テスト, **Then** cutoff 以降・他レース・結果由来の値が features に
   出現しないことが機械検証される。
3. **Given** live で生成した予測・推奨, **When** 後日結果が確定, **Then** computed_at + 使用オッズ値で既存
   backtest（007/011/016）に流せる（prospective 評価が可能）。
4. **Given** live Kelly 推奨, **When** 初期運用, **Then** 校正の外部妥当性が未確立のため shadow（記録のみ・
   実資金執行なし）として扱う旨が明示される。

### Edge Cases

- **post_time 多く null**: cutoff は race_date（004 と同一日付粒度）。同日先行レースの混入リスクは 004 から継承
  （本 feature が新設する問題ではない）旨を開示。
- **走行済みレースの live-serve**: 結果行が存在 → 拒否（retrospective を使う）。「result-pending かつ valid race_id」
  のみ live 対象。
- **部分取得 / スクレイプ失敗**: 予測・推奨とも fail-closed（不完全データで計算しない）。
- **pre-race オッズ欠損**: 予測は可（オッズは特徴でない）が、オッズ依存の推奨は fail-closed。
- **新馬 / unmapped**: Unknown 特徴 + 出走頭数に含める（正規化を壊さない）。
- **オッズ変動**: 再実行で新オッズの新推奨を append、旧行保持。使用オッズ値を行に保存（as_of だけに依存しない）。
- **同一入力での決定論**: 同じ entries・同じオッズ値・同じ model/calibrator で再実行すると同一の予測・推奨。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは result-pending（結果行が存在しない）かつ valid JRA-VAN 12桁の race_id のみを live 対象と
  する MUST。結果が存在する／race_id 不正な場合は予測・推奨を行わず拒否する（fail-closed）。
- **FR-002**: システムの scrape は **URL 駆動 or 既存 DB 状態駆動** とする MUST。operator が netkeiba URL を
  渡した場合のみ Feature 008（`scrape_entries`/`scrape_odds`）を実行し（008 規約踏襲: result-pending のみ
  pre-race オッズ上書き、netkeiba ID は id_mappings 経由、unmapped は nk: surrogate、idempotent +
  ingestion_jobs audit）、URL 無指定時は既に DB に取り込まれている entries+odds（008 を別途実行済み）で動作する。
  **JRA-VAN race_id → netkeiba URL の自動逆引きは本 feature では行わない（deferred）**。いずれの場合も予測・
  推奨の可否はガード（FR-001/005/009）が DB 状態を検証して決める。
- **FR-003**: features は race_date を walk-forward cutoff として構築し、cutoff 以降・他レース結果・将来情報・結果を
  使ってはならない MUST（Feature 004 の規律を踏襲）。オッズは予測モデルの特徴量にしない。
- **FR-004**: 新馬 / unmapped 馬は Unknown=欠損として渡し（0 を代入しない）、出走頭数（母集団）に含める MUST
  （除外して正規化を壊さない）。
- **FR-005**: entries が部分取得・頭数不整合・スクレイプ失敗の場合、予測・推奨を行わず fail-closed で中止し理由を
  surface する MUST。
- **FR-006**: システムは予測を生成し prediction_run（model_version / feature 定義版 / computed_at）+
  race_predictions に永続化する MUST。
- **FR-007**: システムは 009 結合確率 → 010 推定オッズ（pre-race odds 由来）→ 011 exotic EV / 016 Kelly 推奨を
  生成し recommendations に append-only 保存する MUST。013/017 校正器は opt-in。
- **FR-008**: 各推奨行は**使用したオッズ値**（market_odds_used / estimated_market_odds_used）+ computed_at +
  odds as_of を保持 MUST。as_of だけに依存せず使用オッズ値で判断を再現・監査できる（codex 指摘）。
- **FR-009**: pre-race オッズが欠損/未確定の場合、オッズ依存の推奨（010/011/016）を生成してはならない MUST
  （fail-closed）。予測（p）自体はオッズに依存しないため生成してよい。
- **FR-010**: 推奨生成後の出走取消は void/skip MUST（Feature 011/012 規約）。
- **FR-011**: 同一 entries・同一オッズ値・同一 model/calibrator での再実行は同一の予測・推奨を生成 MUST（決定論）。
- **FR-012**: システムは過去レースで live 経路（cutoff=race_date、pre-cutoff のみ）と retrospective 経路の**予測 p
  一致**を検証できる MUST。**過去の pre-race オッズは残らない**ため、オッズ依存の推奨/EV の過去パリティは対象外
  とし、パリティ対象は features と p に限定する。
- **FR-013**: システムはリーク境界テスト（cutoff 以降・他レース・結果が features に出現しない）を提供 MUST。
- **FR-014**: 生成した予測・推奨は computed_at + 使用オッズ値とともに残し、後日結果確定後に既存 backtest
  （007/011/016）で prospective 評価できる形にする MUST。
- **FR-015**: CLI で未開催レース指定の live-serve（scrape → predict → recommend）と、日付指定での対象（result-
  pending）レース列挙を提供 MUST。日本語規約維持。スキーマ変更を行ってはならない。
- **FR-016**: live Kelly 推奨は初期運用では shadow（記録のみ・実資金執行を伴わない）として扱う旨を出力・
  ドキュメントで明示 MUST（校正の外部妥当性が未確立のため）。

### Key Entities *(include if feature involves data)*

- **live 対象レース**: result-pending かつ valid JRA-VAN 12桁。race_date（cutoff）、entries、pre-race オッズ。
- **prediction_run / race_predictions**: 既存。live 予測の永続先（model_version / feature 版 / computed_at）。
- **recommendations**: 既存。live 推奨の append-only 先。使用オッズ値・is_estimated_odds・computed_at・odds as_of・
  logic_version（校正・Kelly 設定込み）。
- **prospective ログ**: 予測・推奨 + computed_at + 使用オッズ値（後日 backtest 入力）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: result-pending かつ valid race_id の live-serve が予測を生成・永続化し、結果あり/不正 race_id/部分
  取得は 100% 拒否される（fail-closed）。
- **SC-002**: live 推奨が pre-race オッズで生成され、各行に使用オッズ値 + as_of + computed_at が記録される。
- **SC-003**: pre-race オッズ欠損時、オッズ依存推奨は生成されない（0 件）。予測 p は生成され得る。
- **SC-004**: 過去レースで live 経路と retrospective 経路の予測 p が一致する（リーク無し）。
- **SC-005**: features に cutoff 以降・他レース・結果由来の値が出現しない（リーク境界テスト 100% パス）。
- **SC-006**: 新馬/unmapped を含むレースで、当該馬が出走頭数に含まれ確率正規化が壊れない（Σ 整合）。
- **SC-007**: 同一 entries・同一オッズ値・同一 model/calibrator の再実行で予測・推奨が完全一致（決定論）。
- **SC-008**: 生成物が computed_at + 使用オッズ値付きで残り、結果確定後に既存 backtest に投入できる。
- **SC-009**: スキーマ変更ゼロ。live Kelly 推奨が shadow（実資金執行なし）と明示される。

## Assumptions

- **cutoff 粒度**: post_time が多く null のため cutoff = race_date（004 と同一日付粒度）。同日先行レース混入リスクは
  004 から継承し本 feature では解消しない（開示）。post_time を用いた時刻粒度 cutoffは deferred。
- **「走行済み」判定**: 結果行の不在（result-pending）で判定。壁時計・発走時刻には依存しない。
- **過去 pre-race オッズ非保持**: closing で上書き済みのため、過去レースで推奨/EV のパリティは取れない。パリティは
  予測 p のみ（codex 指摘 D）。
- **使用オッズ値の保存**: recommendations の market_odds_used / estimated_market_odds_used に使用オッズ値を保存
  （スナップショット履歴は持たないが、判断は再現可能、憲法 V）。
- **校正の live 妥当性**: 013/017 校正は retrospective 前提のため、live Kelly は初期 shadow 運用（実資金なし）。
- **依存**: serving / scrape(008) / probability(009/010) / betting(011/016) を結線。016 の stake_fraction（0006）を
  再利用。スキーマ変更なし。手動 CLI 実行（自動 scheduler は deferred）。
- **scrape 入力**: live-serve の scrape は operator 指定の netkeiba URL（or 008 を事前実行済みの DB 状態）に依存。
  **race_id→netkeiba URL の自動逆引きは deferred**（id_mappings は netkeiba→JRA-VAN 方向で、逆引きは別途設計）。
- **deferred**: race_id→netkeiba URL 自動逆引き、自動スケジューリング、実資金執行、push 通知、オッズ変動履歴/
  トラッキング、複数オッズソース、発走直前リアルタイム最適化、post_time を用いた時刻粒度 cutoff、馬体重/馬場/
  天候の直前更新。
