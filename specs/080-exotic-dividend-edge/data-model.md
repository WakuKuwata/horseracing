# Phase 1 Data Model: Real Exotic Dividend Ingestion & Exotic Edge Measurement

**スキーマ変更なし**。既存エンティティを再利用し、新規は文書 artifact(pre-registration)のみ。

## 1. exotic_odds(既存テーブル・migration 0005・無改修)

| 列 | 型 | 説明 |
|---|---|---|
| exotic_odds_id | uuid PK | gen_random_uuid() |
| race_id | text | 12桁 JRA-VAN/netkeiba 共通 |
| bet_type | text | place/quinella/exacta/wide/trio/trifecta |
| selection | jsonb | 011 canonical 配列(ordered exacta/trifecta・sorted quinella/wide/trio・single place) |
| odds | numeric | **確定配当倍率**(払戻金円 / 100) |
| coverage_scope | text | full / partial('partial' 既定) |
| source | text | 'netkeiba' |
| created_at / updated_at | timestamptz | |

- **制約**: UNIQUE(race_id, bet_type, selection)(`uq_exotic_odds_race_bettype_selection`)。
- **書き込み規律(憲法 V)**: 単一最新値・snapshot 履歴なし・ON CONFLICT で上書き(post-result 確定配当)。**append しない**。
- **状態遷移**: (なし) → post-result 確定で 1 回 upsert(再取得は同値上書き=冪等)。発走前は書かない。

## 2. ScrapedExoticOdds / ScrapedExoticRow(既存 model・契約不変)

```
ScrapedExoticRow(bet_type: str, numbers: tuple[int,...] 馬番, odds: float|None)
ScrapedExoticOdds(key: ScrapedRaceKey, rows: tuple[ScrapedExoticRow,...])
```

- parser 出力契約。**実 markup 対応で内部抽出ロジックのみ差し替え**、この dataclass 契約は変更しない(upsert が無改修で載る前提)。
- numbers は 馬番(race-local)→ id-mapping 不要。
- odds=None / <=0 は upsert 側で skip(既存)。

## 3. exotic edge pre-registration(新・文書 artifact・append-only)

edge 測定を結果前に固定する監査文書(068/073 の採用ゲート pre-registration と同型)。DB でなくリポジトリ内 doc/artifact。

| 項目 | 内容 |
|---|---|
| bet_types | place/quinella/wide/exacta/trio/trifecta を**個別**に測定(束ねない) |
| baseline | 券種別「最低 O_est(人気)」+「uniform」(既存 011/012 backtest baseline) |
| success 条件 | baseline 超過(市場超過が真のバー・ROI>1.0 単独では不可) |
| n_min | 券種別最小サンプル(組合せ数に応じ trifecta 最大)。n<n_min → NO_DECISION |
| CI | 開催日クラスタ bootstrap・seed 固定(i.i.d. 禁止) |
| 多重比較 | 6 券種×窓の偽陽性補正(Bonferroni 相当/IU)を事前固定 |
| 収集系列 | 主=前向き(楽観バイアスなし)、補=netkeiba cache 過去分(別ラベル) |
| probability | 常に P_model(009 on model p)× 実配当(p≠q) |
| logic_version | 控除率(馬連/ワイド22.5%・馬単/三連複25%・三連単27.5%)・窓・seed を記録 |

- **状態(verdict)**: NO_DECISION(n 不足) / REJECT(baseline 超過せず or OOS 崩壊) / ADOPT候補(条件全満+OOS 維持)。過去 verdict は遡及変更しない(append-only)。

## リーク境界(憲法 II)

- exotic_odds は features/serving/training の load 経路に**入れない**。leak-guard=exotic_odds 変更でモデル予測 byte 不変。
- 結果(配当)は edge 採点にのみ使用。selection 生成(009/010/011)は結果を読まない(既存不変式)。
