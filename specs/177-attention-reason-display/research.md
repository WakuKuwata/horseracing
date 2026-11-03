# Research: 177 注目条件の該当理由の表示

**Date**: 2026-11-03 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

spec に NEEDS CLARIFICATION は無い。ここでは実装上の設計判断(D1〜D12)と、codex plan レビューの採否を記録する。

## 現行コードの読み(2026-11-03 実査)

- 判定の唯一の実装は `eval/src/horseracing_eval/attention_rules.py` の `match_mask`(ベクトル)で、`matches`(1 要素)・`applicable_rules`(training の pick 書込み)・`chip_now`(api の現在値判定)・凍結スクリプト `scripts/roi_explore/freeze_rules_S1_S5_20261001.py` がすべてこれを呼ぶ。意味論: EV は厳密に `>`、帯は `lo <= odds < hi`、間隔は `lo <= d <= hi`、使う入力が None/NaN なら非該当。`definitions_sha256` は定義 dataclass と意味論の文字列をハッシュし、コードはハッシュしない。
- `chip_now(defn, current_ens_ev, current_single_ev, current_odds, days_since_last)`: 条件が使う EV か odds が None なら `unknown`、それ以外は `matches(...)` の真偽で `matches`/`no_longer`。
- api `attention.py::build_attention`: 馬ごとに `source`(チップの条件の pick 行。`StoredPick` に `days_since_last: Decimal | None`)、`current`(表示版 `mev-ens15-v1` の最新行から `EvSnapshot`。market-ev 状態が available でなければ None)、`chip_now`(`current` が None なら `unknown`、それ以外は `ar.chip_now(...)` に pick の間隔を渡す)。応答に間隔は出ていない。
- `AttentionHorse`(pydantic・`extra="forbid"`)に `judged`/`current` の `EvSnapshot` はあるが部品ごとの適合は無い。pick の DB CHECK は `kind='pick'` なら `ens_expected_return NOT NULL AND odds_used >= 1.0`。`days_since_last` は NULL 可(S3 の pick)。
- front `AttentionPanel.tsx::ChipPanel`: 見出し(チップラベル・副チップ・バッジ・進み具合)→ `<dl>`(該当する条件 / 過去検証 / 判断時点の見込み / 前向き検証 / 価格ずれ試験 / 価格鮮度 / 期待回収率)→ 免責文。閾値は `GET /attention-rules` の `definition_ja` を一覧ページが出すだけで、パネルには無い。
- 表示規律のテスト基盤: `front/src/tests/attentionDiscipline.ts`(`ATTENTION_SCOPE`・`UNMEASURED_ODDS_DRIFT`・損益色・勝率/p̂・aria-label の 印/推奨/おすすめ)、`attentionScope.ts`(`N%` の文字列は `[data-kind]` の中に限る)、`pseudo.ts`(`[data-pseudo]` の全走査)。`HorseEntriesTable.test.tsx` に「チップに `%|倍|超` が無い」が既にある。
- OpenAPI: `front/openapi.json` と `admin/openapi.json` はバイト一致が admin のテストで固定。再生成は `front/scripts/gen-types.sh` / `admin/scripts/gen-types.sh`(稼働中の API から取得・キー順序ソート)。path 一覧は `front/src/api/openapi.test.ts` と `api/tests/integration/test_openapi_contract.py` が固定(本 feature は path を増やさない)。

## D1. 部品判定は `match_mask` を部品マスクの合成に書き換えて作る(二重実装を作らない)

**Decision**: `attention_rules.py` に部品ごとの private helper(`_ev_mask` / `_band_mask` / `_gap_mask`)を置き、`match_mask` をその `logical_and.reduce` に書き換える。新関数 `component_states(defn, *, ens_ev, single_ev, odds, days_since_last) -> tuple[ComponentState, ...]`(1 馬向け・定義順・条件に無い部品は含めない)は同じ helper を 1 要素で呼び、`status ∈ {met, not_met, missing}` と `value` を返す。`matches` / `applicable_rules` / `chip_now` の署名と戻り値は不変。

**Rationale**: spec FR-003「総合判定と同じ構成条件から同じ関数群が同時に生成」。別関数で分解すると二重実装になり、境界の片方だけが変わる事故を構造的に許す。合成にすれば「総合 = 部品の AND」は構築上成り立つ。

**抽出は最小限**(codex #3): `_as_float_array` と比較式(`ev > defn.ev_gt`・`(o >= lo) & (o < hi)`・`(g >= glo) & (g <= ghi)`)をそのまま helper に移すだけで、正規化・型変換・broadcast・出力 dtype(bool)を新たに足さない。`np.errstate(invalid="ignore")` も同じ範囲で維持する。

**ビット一致の担保**(codex #2): 177 以前の `match_mask` 本体を eval のテストに逐語コピー(`_match_mask_138_reference`)し、(a) 既存の格子(`test_scalar_and_vector_agree_on_a_grid` と同じ odds × gap × ev × ev)、(b) 乱数 20,000 行、(c) **各閾値とその隣の表現可能な float**(`np.nextafter` で 1.2 / 1.3 / 1.1 / 20.0 / 40.0 / 14 / 112 の両側)、(d) スカラーとベクトルの混在・空配列・None・NaN・±inf・`Decimal` 入力(現行の呼び出し側が渡す型)で新 `match_mask` と `==`(値と shape と dtype)を固定する。独立した期待値の境界テスト(既存 `test_s1_boundaries`)は残す=コピーした実装が唯一の oracle にならない。`definitions_sha256` はコードを含まないので値は変わらないが、**ハッシュ不変は意味論不変の証明ではない**(codex #3)ので、training の pick 書込みテスト(`training/tests/unit/test_attention_picks.py`)と凍結スクリプトのテスト(`scripts/tests/test_freeze_rules_S1_S5.py`)も実装後に通して呼び出し経路を実際に踏む。関数名と署名は不変なので呼び出し側は無改修。

**Alternatives considered**: (a) `match_mask` を触らず `component_states` を別実装 → 二重実装(FR-003 違反)。(b) `component_states` だけ作り `match_mask` は `component_states` の AND で実装 → 1 要素関数をベクトル経路から呼ぶと凍結スクリプトの性能(数十万行)が落ちる。helper をベクトルで共有すれば両方が同じ式を通る。

## D2. 欠損の 3 値と総合の規則は 138 のまま

**Decision**: 部品の `status` は `met` / `not_met` / `missing`。総合の規則は変えない: `chip_now` は条件が使う EV または odds が None で `unknown`、間隔の欠損は `not matched`(=`no_longer`)。`component_states` は間隔欠損を `missing` と報告し、総合はそのまま `no_longer`。画面は両方を出す(FR-002)。

**Rationale**: 138 の「gap 欠損は非該当」は凍結スクリプトの選定と同じ規則で、変えると pick の母集団が変わる。表示は「値なし」と総合の「満たさない」を並べて出せば矛盾なく読める。

**NaN と None の違い**(codex #1): `chip_now` は EV / odds が **None** のときだけ `unknown` を返し、NaN は `matches()` に落ちて `no_longer` になる。`component_states` は None も NaN も `missing`(value None)と報告する。したがって「部品が missing ⇔ 総合が unknown」は成り立たない。総合との関係は「全部品 met ⇔ matches」だけで、`unknown` は api の規則(`current` が None)から決まる(D4 の V6)。

## D3. 応答の形(純追加)

**Decision**: `AttentionHorse.reason: AttentionReason | None`(チップが無い馬は None)。

```
AttentionReason
  rule_id: RuleId
  rule_set_version: str
  components: list[EvReasonComponent | OddsBandReasonComponent | GapReasonComponent]  # kind で判別・定義順
  also_applicable: list[AlsoApplicable]   # チップ以外の非対照の該当条件(rank 順)
EvReasonComponent     kind="ens_expected_return", ev_gt: float, is_pseudo: Literal[True], judged: ReasonValue, current: ReasonValue | None
OddsBandReasonComponent kind="win_odds", lo: float, hi: float (lo <= odds < hi), judged, current
GapReasonComponent    kind="days_since_last", lo: int, hi: int (lo <= d <= hi), judged, current, current_source: Literal["pick_frozen"]
ReasonValue           value: float | None, status: Literal["met","not_met","missing"]
AlsoApplicable        rule_id: RuleId, note_ev_gt: float | None
```

- `current` は `horse.current` が None のとき全部品で None。間隔の `current` は pick の凍結値(`current_source="pick_frozen"`)。
- `also_applicable` は **判断時点(pick)での該当** を列挙する(現在値ではない)。対照とチップ自身は含めず、rank 順・重複なし。`note_ev_gt` は、その条件がチップの条件と **`uses_ensemble`・帯・間隔が同じで `ev_gt` だけ違う** ときに限り入る(S1 チップのときの S2 = 1.3。codex #5)。front はこの値があるときだけ「S2(期待回収率 130% 超)にも該当」と書き、無い条件は id だけ書く(説明を作らない)。どの条件に閾値を添えるかを front が決めない。
- `AttentionReason` は `status="available"`。応答の整合が取れないときは `AttentionReasonUnavailable(status="unavailable", reason="inconsistent")` を返す(D4)。front は `status` で判別する。
- 該当理由は `uses_ensemble=True` の条件(S1〜S4)にだけ作る。チップは対照 S5 にならない(`chip_rule`)ので常に成り立つが、api は定義の `uses_ensemble` を見て作り、eval のテストで「対照でない条件はすべて `uses_ensemble=True`」を固定する(codex #4)。単 seed の条件を ens15 の値で説明する行は構造的に生まれない。
- 既存項目(`judged` / `current` / `chip_now` など)は値・型・順序とも不変。`EvSnapshot` も変えない(間隔は reason の部品に出る)。

**Rationale**: 閾値・帯・日数を構造で返せば front は文を組むだけで定数を持たない(FR-010)。`definition_ja` の文字列を流用しない(閾値を文から切り出す=front の再導出になる)。

**Alternatives considered**: `EvSnapshot` に `days_since_last` を足す → 判断時点と現在値で意味が違う(現在値は凍結値)ので別の形が要る。`AttentionDayItem` にも載せる → 日付一覧は不変(FR-013)なので載せない。

## D4. 応答の整合は builder の検査 + 応答モデルの validator で強制し、矛盾は 500 にせず「理由なし」で返す

**Decision**: 整合規則(data-model §2 の V1〜V10)を純関数 `check_reason_consistency(horse_fields) -> list[str]` に置き、2 箇所で使う。(1) `build_attention` は `AttentionReason` を組んだ直後にこの関数を呼び、違反があれば **WARNING ログ(race_id・horse_id・違反の一覧)を出して `AttentionReasonUnavailable(status="unavailable", reason="inconsistent")` に置き換える**。既存項目(`chip_now` など)は触らない。(2) `AttentionHorse` の `@model_validator(mode="after")` は同じ関数を呼び、`status="available"` の reason で違反があれば `ValueError`(=テストで `AttentionHorse(...)` を直接組んだ矛盾を拒む)。構造の規則(`reason is None` ⇔ `chip_rule is None`、`unavailable` はチップありのときだけ)は validator が常に検査する。

**Rationale**: codex #6。spec FR-004「矛盾する形を返さない」は満たしつつ、表示の補助項目の不整合で既存の応答全体を 500 にしない(014 の「純関数の例外を未処理 500 にしない」)。黙って None にする案は欠陥を隠すので、`unavailable` という明示の状態とログで見えるようにする。front は `unavailable` を「該当理由を表示できません(応答の整合が取れていません)」と中立に出し、統計の折りたたみと既存項目はそのまま出す。

**規則**(data-model §2 に表): V1 構造(None ⇔ チップなし)/ V2 `rule_id == chip_rule` / V3 `rule_set_version` 一致 / V4 部品の順序と種類が `RULE_DEFINITIONS[rule_id]` の構成と一致(重複なし)/ V5 条件値(`ev_gt` / `lo` / `hi`)が定義と一致 / V6 **`current` が None ⇔ `chip_now == "unknown"`、`current` あり ⇒ `chip_now == "matches"` ⇔ 全部品 `met`、それ以外 ⇒ `no_longer`**(missing を unknown に結び付けない・codex #1)/ V7 部品の `judged.value` が `judged` snapshot の EV / odds と一致 / V8 部品の `current.value` が `current` snapshot の EV / odds と一致、間隔の `current.value == judged.value`(凍結値)/ V9 `also_applicable` は `applicable` の部分集合・対照なし・チップなし・rank 順・重複なし / V10 `note_ev_gt` は定義どおり(帯・間隔・`uses_ensemble` が一致する条件にだけ、値はその条件の `ev_gt`)。

**Alternatives considered**: (a) validator のみで 500 → 表示補助の欠陥がレース詳細全体を落とす。(b) 矛盾時に reason を None → 欠陥が見えない。(c) builder の検査のみで validator なし → テストが `AttentionHorse` を直接組んだときの契約が緩む。

## D5. 判断時点の列が全部「満たす」であることは validator で要求しない

**Decision**: pick は判定関数が該当と判断した馬にしか書かれないので、判断時点の部品は原理上すべて `met` になる。validator ではこれを要求せず、front も前提にしない(spec Assumptions)。api の単体テストでは「合成 pick から作った応答の判断時点は全部 met」を事実として固定する。

**Rationale**: 将来 rule set 版が変わって古い pick を別の定義で読む事態になっても応答が止まらないようにする。その場合の表示は「満たさない」が判断時点の列に出て、利用者に見える。 保存データが不整合でも部品の状態は事実のまま返し(met に塗り替えない・codex #7)、整合の違反(D4)にも当たらない(V1〜V10 は判断時点の全 met を要求しない)。

## D6. 現在値の列は `chip_now` と同じ値源

**Decision**: 現在値の EV / odds は `current`(表示版の最新行)、間隔は pick の凍結値。これは `chip_now` に今渡している引数と同一。`current` が None(market-ev が available でない=再計算待ち・オッズ欠落・出走馬変更)のとき部品の `current` は None、front は全行「最新の計算なし」。

## D7. front の構成

**Decision**: 新コンポーネント `AttentionReasonTable`(`front/src/components/AttentionReasonTable.tsx`)が (a) 一文の要約 (b) 照合表 (c) 「…にも該当」の一文 (d) 値源の注記 (e) `field_changed_after_pick` の注記 を描く。`ChipPanel` は見出し → `AttentionReasonTable` → `<details className="attn-panel__evidence">`(`<summary>` 「検証の根拠(過去検証・判断時点の見込み・前向き検証・価格ずれ試験・価格鮮度)」・初期状態 閉)に既存の `<dl>` を入れる → 免責文、の順になる。文の組み立て(`reasonSummaryText` / `criterionText` / `statusText`)は `front/src/lib/attention.ts` の純関数に置き、応答の構造(`ev_gt` / `lo` / `hi` / `status`)だけから作る。

- 適合の表示は 1 要素に「記号 + 語」(例: `○ 満たす` / `× 満たさない` / `— 値なし` / `— 最新の計算なし`)。記号は `aria-hidden` にし、語を読み上げる。色クラスは使わない。
- 期待回収率の値は `EvValue`(`PseudoValue kind="expected_return"`)を再利用。`N%` は `[data-kind="expected_return"]`(値)または `[data-kind="criterion"]`(閾値の文)の中に置き、`attentionScope.ts` の規律に乗せる。
- 既存の「期待回収率」行(判断時点 / 現在値の `EvLine`)は照合表と重複するので、単 seed の値だけを残した 1 行に縮める(`attention-judged` / `attention-current` の testid は維持)。値源の注記と `field_changed` の注記は照合表の直下へ移す。
- 折りたたみは `<details>` の素の挙動(開閉状態は保持しない)。閉じていても DOM に残るので、既存テストの `findByText(/通算\(2010〜26\)/)` はそのまま通る。規律の検査は `summary` をクリックして開いた状態で行う(087 の教訓・FR-009)。
- **値の精度**(codex #4): 期待回収率は既存どおり小数 1 桁(`formatPct(value, 1)`)で出すが、丸めた表示が閾値の表示と同じ文字列になる場合(例: 1.2004 → 「120.0%」と「120% 超」)は 2 桁、それでも同じなら 3 桁まで増やす(`formatEvAgainstThreshold`)。適合は応答の `status` のままで、表示桁数は見た目の矛盾を避けるだけ。オッズは小数 1 桁、間隔は Decimal 由来の小数をそのまま(切り捨てない)。
- **表の構造**(codex #9): `<table>` に `<th scope="col">`(条件 / 判断時点 / 現在値)と `<th scope="row">`(条件の文)を置く。記号は `aria-hidden="true"`、語は常に DOM に出す(読み上げは語を 1 回)。間隔の現在値の列に「判断時点で固定」を添える。
- 「最新の計算なし」(`current == null`・計算そのものが無い)と「値なし」(計算はあるが入力が欠損=`missing`)は別の語で、置き換えない(codex #10)。
- `status="unavailable"` の reason は、要約と表の代わりに中立の一文「該当理由を表示できません(応答の整合が取れていません)」を出し、見出し・折りたたみ・免責文はそのまま。

**Alternatives considered**: `useState` の独自折りたたみ → `<details>` のほうがキーボード操作と読み上げが素で付く。

## D8. チップに閾値が無いことのテスト

**Decision**: `HorseEntriesTable.test.tsx` の既存「チップに `%|倍|超` が無い」に `日` と数字を加え、チップの **可視テキストとアクセシブルネーム**(`aria-label` / `aria-labelledby` / `title`)の両方を検査する(codex #10)。`AttentionPanel.test.tsx` に「見出し・バッジ・副チップの text と aria-label に `\d\s*(%|倍|日)` が無い」を追加する。照合表には出ている(同じテストで正の確認)。

## D9. OpenAPI の更新手順

**Decision**: 138 と同じ。API を起動して `front/scripts/gen-types.sh` と `admin/scripts/gen-types.sh` を流し、`front/openapi.json` と `admin/openapi.json` のバイト一致・`check:openapi`(drift)を緑にする。差分は `components/schemas` への追加(`AttentionReason` / `AttentionReasonUnavailable` / 3 部品 / `ReasonValue` / `AlsoApplicable`)と `AttentionHorse.reason`(required・nullable)のみで、path 一覧は不変。**純追加の確認**(codex #11): `git diff --numstat front/openapi.json` の削除行が 0 であること、`AttentionHorse` の既存 property の `required` / nullable が不変であることを quickstart で見る(api の単体テストでは `AttentionHorse.model_json_schema()` の既存 property 名と required 集合が 138 時点の一覧と一致することを固定)。新項目に既定値は付けない(V1 の「None ⇔ チップなし」を意味のある規則にするため・既存テストの直接構築は明示的に更新する)。

## D10. 変更しないもの(回帰の境界)

- training の pick 書込み(`attention_picks.py` → `applicable_rules`)、凍結スクリプト、`definitions_sha256`、`RULE_DEFINITIONS`、`RULE_SET_VERSION`。
- `chip_rule` / `chip_now` / `emphasisLevel` / `freshnessLevel` / `chipLabel` / 日付一覧 / `/attention` 一覧ページ / `AttentionDayItem`。
- DB スキーマ(migration なし)。`days_since_last` は pick に保存済み。

## D11. 性能

部品判定は馬ごとに 3 比較。応答のサイズは馬 1 頭あたり約 400 バイト増。測る対象ではない。

## D12. codex plan レビューの採否(2026-11-03・`codex:codex-rescue`・設計相談 11 件)

| # | 深刻度 | 指摘 | 採否 | 反映先 |
|---|---|---|---|---|
| 1 | BLOCKER | 部品の missing を `chip_now == unknown` に結び付けると既存契約を再現できない(S3 は odds 部品が無いのに odds None で unknown・NaN は None でないので no_longer) | **採用** | D2・D4 V6(unknown ⇔ `current` None、matches ⇔ 全部品 met) |
| 2 | HIGH | 逐語コピーの基準だけでは不十分。閾値の隣の float・スカラー/ベクトル混在・空・None・NaN・inf・Decimal・shape/dtype を比較し、独立の境界テストも残す | **採用** | D1・INV-R2 |
| 3 | HIGH | ハッシュ不変は意味論不変の証明ではない。抽出は最小限・新しい正規化を足さない・呼び出し経路(training / freeze / api)を踏む | **採用** | D1・quickstart §6 |
| 4 | MEDIUM | 単 seed の条件を ens15 で説明する余地を構造的に閉じる。EV の単位と表示桁数(丸めで閾値と同じに見えて met になる)を決める。missing と「現在の計算なし」を区別 | **採用** | D3(uses_ensemble のみ・eval テスト)・D7(桁数・語の区別) |
| 5 | LOW | `note_ev_gt` は妥当。判断時点の該当であることを明記・対照とチップを除く・重複なし・順序決定論・`uses_ensemble` も一致条件に | **採用** | D3・V9/V10 |
| 6 | HIGH | 表示補助の整合違反を未処理 500 にしない。builder で検査し「理由なし」の明示状態+ログ。「None ⇔ チップなし」を緩める | **採用(形を変えて)** | D4: `AttentionReasonUnavailable` を追加。「None ⇔ チップなし」は維持し、unavailable はチップありのときだけ(緩めるのは「available ⇔ チップあり」) |
| 7 | HIGH | 数値の等値だけでなく来歴と構造(現在値の等値・間隔の凍結値一致・部品の順序と一意性・条件値 = 定義・rule set 版)を検査。判断時点の全 met は正常な pick のテストで固定し、保存データの不整合では状態を塗り替えない | **採用** | D4 V3〜V8・D5 |
| 8 | HIGH | ライフサイクルのテスト(S3 で間隔 null・部分欠損・計算なし・不通過/保留のチップ・出走馬変更・void・取消・別 run の単 seed・小数の間隔 112.5・後の価格でチップが新規に付かない) | **採用** | quickstart §2・tasks |
| 9 | MEDIUM | `<details>` はテキスト検索でなく状態(初期 閉・summary で開く・可視)と構造(th・aria-hidden の記号・語を 1 回)を検査。実ブラウザで可視を確認 | **採用** | D7・quickstart §4/§5 |
| 10 | MEDIUM | チップは可視テキストとアクセシブルネームの両方を検査。移設で `PseudoValue`・基準ラベル・鮮度・免責文を落とさない。「最新の計算なし」で「値なし」を置き換えない | **採用** | D7・D8・display.md §5 |
| 11 | MEDIUM | path 不変だけでは純追加の証明にならない。既存 property の required / nullable 不変を確認し、生成型と drift を通す。新項目に既定値 | **採用(既定値は不採用)** | D9。既定値は V1 を無意味にするので付けず、既存テストの直接構築を明示的に更新する |

不採用 0 件(#11 の「既定値」の 1 点のみ理由つきで別の形)。残リスク: `<details>` の可視性は jsdom では証明できないので実ブラウザ(quickstart §5)で確認する。
