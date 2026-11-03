# Data Model: 177 注目条件の該当理由の表示

**Date**: 2026-11-03 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

DB スキーマは変えない(migration なし)。ここで定義するのは eval の値オブジェクト、api の応答モデル、front の表示状態。

## 1. eval: 部品の判定(値オブジェクト・保存しない)

```python
ComponentKind = Literal["ens_expected_return", "win_odds", "days_since_last"]
ComponentStatus = Literal["met", "not_met", "missing"]

@dataclass(frozen=True)
class ComponentState:
    kind: ComponentKind
    status: ComponentStatus
    value: float | None        # None ⇔ status == "missing"
```

`component_states(defn, *, ens_ev, single_ev, odds, days_since_last) -> tuple[ComponentState, ...]`

| 条件 | 部品(定義順) |
|---|---|
| S1 / S2 / S4 | ens_expected_return → win_odds → days_since_last |
| S3 | ens_expected_return |
| S5 | (対照。`uses_ensemble=False` の条件は api が reason を作らない。関数自体は単 seed の EV で 1 部品を返す) |

### 不変条件(テストで固定)

- **INV-R1(合成)**: 任意の入力で `match_mask(defn, …)[i]` == `all(c.status == "met" for c in component_states(defn, …_i))`。既存の格子・乱数 20,000 行・None/NaN。
- **INV-R2(基準との一致)**: 新 `match_mask` == 177 以前の本体の逐語コピー `_match_mask_138_reference`(テスト内)。同じ入力集合でビット一致。
- **INV-R3(欠損)**: 入力が None/NaN の部品は `status="missing"` かつ `value=None`。それ以外は `value` が float で `status ∈ {met, not_met}`。
- **INV-R4(境界)**: EV は `value > ev_gt` で met(1.20 は not_met・1.20+ε は met)。帯は `lo <= value < hi`(19.99 not_met・20.0 met・39.99 met・40.0 not_met)。間隔は `lo <= value <= hi`(13 not_met・14 met・112 met・113 not_met)。間隔の小数(Decimal 由来)はそのまま比較する(丸めない)。
- **INV-R5(chip_now との一致)**: `chip_now(defn, …)` の本体は不変(EV か odds の **引数が None** → unknown、それ以外は `matches()`)。固定するのは「`chip_now == "matches"` ⇔ `component_states` が全部 `met`」(None/NaN/境界の全ケース)。**部品の `missing` を unknown に結び付けない**(NaN は None でないので従来どおり no_longer・S3 は odds 部品を持たないが odds None で unknown)。
- **INV-R7(対照でない条件は ens15)**: `RULE_DEFINITIONS` の `control=False` な条件はすべて `uses_ensemble=True`(該当理由が単 seed の条件に当たらないことを定義で固定)。
- **INV-R6(定義ハッシュ)**: `definitions_sha256()` の値は 177 前後で同じ(コードはハッシュに入っていない)。

## 2. api: 応答モデル(`schemas.py`・純追加)

```python
class ReasonValue(BaseModel):
    value: float | None
    status: Literal["met", "not_met", "missing"]

class EvReasonComponent(BaseModel):
    kind: Literal["ens_expected_return"]
    ev_gt: float                      # 厳密に超える
    is_pseudo: Literal[True]          # 期待回収率は推定値(V)
    judged: ReasonValue
    current: ReasonValue | None       # horse.current が None なら None

class OddsBandReasonComponent(BaseModel):
    kind: Literal["win_odds"]
    lo: float                         # lo <= odds
    hi: float                         # odds < hi
    judged: ReasonValue
    current: ReasonValue | None

class GapReasonComponent(BaseModel):
    kind: Literal["days_since_last"]
    lo: int                           # lo <= days
    hi: int                           # days <= hi
    judged: ReasonValue
    current: ReasonValue | None       # 値は pick の凍結値
    current_source: Literal["pick_frozen"]

ReasonComponent = Annotated[EvReasonComponent | OddsBandReasonComponent | GapReasonComponent, Field(discriminator="kind")]

class AlsoApplicable(BaseModel):
    rule_id: RuleId
    #: チップの条件と帯・間隔が同じで EV 閾値だけ違う条件にだけ入る(S1 チップのときの S2 = 1.3)
    note_ev_gt: float | None

class AttentionReason(BaseModel):
    status: Literal["available"]
    rule_id: RuleId                   # == AttentionHorse.chip_rule
    rule_set_version: str
    components: list[ReasonComponent] # 定義順・条件に無い部品は含めない
    also_applicable: list[AlsoApplicable]   # 判断時点の該当のうちチップ以外の非対照(rank 順・重複なし)。対照 S5 は含めない

class AttentionReasonUnavailable(BaseModel):
    status: Literal["unavailable"]
    reason: Literal["inconsistent"]   # builder の整合検査に落ちた(WARNING ログに違反一覧)

AttentionReasonResponse = Annotated[AttentionReason | AttentionReasonUnavailable, Field(discriminator="status")]
```

`AttentionHorse` に `reason: AttentionReasonResponse | None` を追加(末尾・既存項目の順序不変・**既定値なし**)。全モデル `extra="forbid"`。

### 整合の検査(`check_reason_consistency` = builder と `AttentionHorse` の `model_validator(mode="after")` が共用する純関数)

| # | 規則 | builder で違反 | validator で違反 |
|---|---|---|---|
| V1 | `reason is None` ⇔ `chip_rule is None`(`unavailable` はチップありのときだけ) | (構造・builder は作らない) | ValidationError |
| V2 | `reason.rule_id == chip_rule` | unavailable に置換 | ValidationError |
| V3 | `reason.rule_set_version == RULE_SET_VERSION` | 同 | 同 |
| V4 | 部品の `kind` が `RULE_DEFINITIONS[rule_id]` の構成(定義順・重複なし)と一致 | 同 | 同 |
| V5 | 条件値(`ev_gt` / `lo` / `hi`)が定義と一致 | 同 | 同 |
| V6 | `horse.current is None` ⇔ `chip_now == "unknown"` ⇔ 全部品の `current is None`。`current` あり ⇒ (`chip_now == "matches"` ⇔ 全部品 `met`)、それ以外は `no_longer` | 同 | 同 |
| V7 | 部品の `judged.value` が `judged.ens_expected_return`(EV)/ `judged.odds`(odds)と一致(float 等値) | 同 | 同 |
| V8 | 部品の `current.value` が `current.ens_expected_return` / `current.odds` と一致。間隔部品は `current.value == judged.value` | 同 | 同 |
| V9 | `also_applicable` の `rule_id` は `applicable` ⊂、対照とチップを含まない、rank 順、重複なし | 同 | 同 |
| V10 | `note_ev_gt` は「チップの条件と `uses_ensemble`・`odds_band`・`gap_days` が等しい条件」にだけ、その条件の `ev_gt` | 同 | 同 |

V2〜V10 の違反は実装のバグ。builder は WARNING ログ(race_id・horse_id・違反一覧)を出し `AttentionReasonUnavailable` で返す(500 にしない・黙って None にしない)。validator は `status="available"` の reason にだけ V2〜V10 を当てる。判断時点の部品がすべて `met` であることは **要求しない**(research D5)。`days_since_last` の NaN/None、EV の NaN は部品 `missing`(value None)で、V6 の「全部品 met」に入らないだけ(unknown にはならない)。

### 値の由来

| 項目 | 由来 |
|---|---|
| 部品の `judged.value` | pick(`attention_picks` の `kind='pick'` 行): `ens_expected_return` / `odds_used` / `days_since_last`(Decimal → float) |
| 部品の `current.value`(EV / odds) | 表示版 `mev-ens15-v1` の最新行(`expected_return` / `odds_used`)= 既存 `current` と同じ行 |
| 部品の `current.value`(間隔) | pick の `days_since_last`(凍結値・描画時刻で変えない) |
| 条件値(`ev_gt` / `lo` / `hi`) | `RuleDefinition`(`RULE_DEFINITIONS`) |
| `also_applicable` | 馬の `applicable`(pick が生きている条件=判断時点の該当)から対照とチップを除いたもの |
| `rule_set_version` | `attention_rules.RULE_SET_VERSION`(応答の `rule_set_version` と同じ) |

## 3. front: 表示状態(導出・保存しない)

| 状態 | 由来 | 表示 |
|---|---|---|
| 行(部品) | `reason.components` の順 | 条件の文(`criterionText(component)`)・判断時点の値と適合・現在値の値と適合 |
| 適合の語 | `ReasonValue.status` | met → 「満たす」/ not_met → 「満たさない」/ missing → 「値なし」。記号は `aria-hidden` |
| 現在値なし | `component.current == null` | 全行「最新の計算なし」 |
| 間隔の現在値 | `current_source == "pick_frozen"` | 値 + 「(判断時点で固定)」 |
| 要約文 | 判断時点の全部品 / 現在値の部品 | spec §2 の文型表(3 形)。判断時点に `not_met`/`missing` があれば「判断時点では S1 の 3 条件のうち 2 つを満たしました(満たさない: …)」の第 4 形で事実を書く |
| 「…にも該当」 | `reason.also_applicable` + `horse.stages`(閉じた段階のタグ) | `note_ev_gt` があれば「S2(期待回収率 130% 超)にも該当」、他は「S3・S4 にも該当(表示する条件は最上位の S1)」 |
| 折りたたみ | `<details>` の `open` 属性 | 初期 閉・保持しない |
| 理由なし | `reason.status == "unavailable"` | 要約と表の代わりに「該当理由を表示できません(応答の整合が取れていません)」の一文。見出し・折りたたみ・免責文は不変 |
| EV の表示桁 | `formatEvAgainstThreshold(value, ev_gt)` | 1 桁。丸めた文字列が閾値の表示と同じなら 2 桁、まだ同じなら 3 桁(適合は API の `status`) |

front は閾値・帯・日数の定数を持たない。文はすべて応答の構造から組む。
