# Data Model: exotic EV 推奨と疑似ROIバックテスト

スキーマ変更なし。既存 `recommendations`(Feature 001)に exotic 推奨を書き、`race_predictions` / `race_horses.odds` /
`race_results` を読む。以下は**コード上の値オブジェクト**と selection 形・的中規則・不変条件。

## 1. CanonicalField(値オブジェクト・非永続)

p と q を同一母集団に揃える(R1)。

| フィールド | 型 | 説明 |
|---|---|---|
| `race_id` | int | 対象レース |
| `horse_numbers` | list[int] | canonical 母集団(整列、win_prob>0 かつ odds>0 かつ 非取消) |
| `p_norm` | dict[int,float] | 再正規化済みモデル win 確率(009 入力)。Σ=1 |
| `odds_norm` | dict[int,float] | 母集団に絞った win オッズ(010 入力) |
| `field_size` | int | len(horse_numbers)。009/010 の field 規則に使用 |
| `excluded` | list[ExcludedHorse] | p または odds 欠損で除外した馬(監査) |

- 規則: p のみ有効 / odds のみ有効 の馬は除外。`excluded` に reason(`no_prob`/`no_odds`/`scratched`/`競走除外`/`出走取消`)。
  非出走ステータス(出走取消・競走除外・取消)は FR-011 に従い母集団から除外。
- 不変: `set(p_norm) == set(odds_norm) == set(horse_numbers)`。
- **空母集団**(有効馬 0〜1)は `field_size=0/1`、`p_norm`/`odds_norm` を**空 dict のまま**返し(Σ=1 正規化しない)、
  呼び出し側が推奨/採点をスキップ + 監査(正規化で 0 除算しない)。

## 2. ExoticBet(値オブジェクト・非永続)

1つの買い目候補。

| フィールド | 型 | 説明 |
|---|---|---|
| `bet_type` | BetType | place/quinella/exacta/wide/trio/trifecta |
| `selection` | Selection | JSONB 安全(下記) |
| `p_model` | float | 009 から得た的中確率(p 由来) |
| `o_est` | float | 010 から得た推定オッズ(q 由来) |
| `ev` | float | `p_model * o_est` |
| `pseudo_odds` | float | `1 / p_model` |
| `pseudo_roi` | float | `ev - 1` |

- 不変: `p_model∈(0,1]`、`o_est>0`(推定不能=候補から除外)、`ev≥0`。
- **stake は ExoticBet に含めない**(flat stake は推奨保存/採点時に適用する戦略/レポート パラメータ)。
  spec.md Key Entities の「ExoticBet…stake」は flat stake を指し、候補オブジェクト自体には持たせない。

## 3. Selection(JSONB 安全形・R2)

`selection` は**素の JSON 配列**(ラッパオブジェクトなし)。順序性は **`bet_type` 列から導出**(冗長保存しない)。
spec.md AC3「順序券種=順序付き配列/無順序券種=整列配列」と一致。

```json
[3, 7, 1]   // trifecta: 「3番→7番→1番」の着順(horse_number の順序)
```

| bet_type | selection | 順序 | 例 |
|---|---|---|---|
| place | 単一要素 `[i]` | — | `[5]` |
| quinella | 整列2(horse_number 昇順) | 無 | `[3,7]` |
| exacta | 順序2(着順) | 有 | `[7,3]`(7番→3番) |
| wide | 整列2(horse_number 昇順) | 無 | `[3,7]` |
| trio | 整列3(horse_number 昇順) | 無 | `[1,3,7]` |
| trifecta | 順序3(着順) | 有 | `[3,7,1]`(3番→7番→1番) |

- 無順序券種は horse_number 昇順整列で正準化(往復・重複排除・タイブレーク安定)。順序券種は着順を保持。
- selection_key = `(bet_type, tuple(horses))` の辞書順文字列(決定論整列キー)。順序性は bet_type で既知。

## 4. 的中規則(R3 / R4)

着順は `finish_pos: dict[horse_number → 着順(1始まり)]`(`race_results.finish_order` 由来)で表す。**単一の正準表現**。
top-N は最小着順の N 頭。

| bet_type | hit 条件 | field 規則 |
|---|---|---|
| place | `finish_pos[i] ∈ 払戻順位`(8頭+:1–3着、5–7頭:1–2着、≤4頭:対象外) | 009 と同一 |
| quinella | `{i,j} == {1着,2着の horse_number}` | — |
| exacta | `[i,j] == [1着, 2着]`(順序) | — |
| wide | `i,j` が共に払戻順位内(top3/top2) | 009 と同一 |
| trio | `{i,j,k} == {1,2,3着}` | — |
| trifecta | `[i,j,k] == [1,2,3着]`(順序) | — |

- **field_size は生成時の canonical field_size**(P_model/O_est を導出した母集団サイズ)を採点でも使用(EV 恒等式と整合)。
- **同着(dead-heat)**: spec.md Edge Cases と一致 →
  - 順序/集合券種(exacta/quinella/trifecta/trio)で**必要順位が一意に決まらない同着**(対象順位境界に同着が跨る)は
    該当レースを**スキップ + 監査**(規則確定まで)。
  - place/wide は**圏内同着を的中**(包含判定なので順位の一意性は不要)。
- 複勝/ワイドは**ベット単位**で独立採点(R4)。1レース複数行が各々 hit しうる。
- payout(疑似)= hit なら `stake * o_est`、miss なら 0(二重疑似)。

## 5. recommendations への射影(永続)

| 列 | 値 |
|---|---|
| prediction_run_id | 推論実行 FK |
| race_id | 対象レース |
| bet_type | ExoticBet.bet_type |
| selection | Selection(JSONB) |
| market_odds_used | **null**(実 exotic オッズ無し) |
| estimated_market_odds_used | ExoticBet.o_est |
| is_estimated_odds | **true** |
| pseudo_odds | ExoticBet.pseudo_odds = 1/p_model |
| pseudo_roi | ExoticBet.pseudo_roi = ev−1 |
| logic_version | EV式/閾値/K/stake/控除率/q ソース/cap/母集団ポリシー/009/010 版 |
| computed_at | 生成時刻 |

- append-only(上書き/削除なし)。

## 6. バックテスト集計(値オブジェクト・非永続)

| エンティティ | フィールド |
|---|---|
| `ExoticRaceOutcome` | race_id, finish_pos(dict[horse_number→着順]), field_size(=生成時 canonical field_size) |
| `ExoticRoiReport` | bet_type 別 + 総合: n_bets, n_hits, hit_rate, total_stake, total_payout, roi, skip_rate, max_drawdown, max_consecutive_losses, **pseudo=True(二重疑似ラベル)** |
| `BaselineReport` | strategy(lowest_oest/uniform)別の同形 ExoticRoiReport |

- 不変: `roi = total_payout/total_stake`(total_stake>0)。`pseudo` は常に True(本 feature)。

## 7. 不変条件まとめ

1. canonical: `p_norm` と `odds_norm` の母集団は同一(R1)。
2. selection は JSONB 安全配列(frozenset 非保存、R2)。
3. EV = p_model(009 on p) × o_est(010 on q)。p と q を混同しない(II)。
4. 買い目決定は結果非参照。採点のみ結果参照(II)。
5. is_estimated_odds=true, market_odds_used=null, 二重疑似ラベル(V)。
6. 決定論: 同一入力 → 同一推奨・同一順序(selection_key タイブレーク)。
7. append-only。
