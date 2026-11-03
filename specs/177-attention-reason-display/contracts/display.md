# Contract: 展開パネルの表示(177)

対象: `front/src/components/AttentionPanel.tsx`(`ChipPanel`)と新規 `AttentionReasonTable.tsx`、文の純関数は `front/src/lib/attention.ts`。チップ(`HorseEntriesTable.tsx` の `AttentionChip`)・日付一覧・一覧ページは変えない。

## 1. パネルの順序(チップのある馬)

1. 見出し(既存・不変): チップラベル・S2 副チップ・探索後固定/前向き未確認バッジ・検証の進み具合。
2. **要約の一文**(`data-testid="attention-reason-summary"`)。
3. **照合表**(`data-testid="attention-reason-table"`・`<table>`・`<th scope="col">` 「条件 / 判断時点(HH:MM) / 現在値(HH:MM)」・各行の条件の文は `<th scope="row">`。現在値が無いときの列見出しは「現在値」)。`reason.status == "unavailable"` のときは 2〜4 の代わりに `data-testid="attention-reason-unavailable"` の一文「該当理由を表示できません(応答の整合が取れていません)」。
4. **他に該当する条件**(`data-testid="attention-reason-also"`)。無ければ出さない。対照 S5 の該当は既存の「対照 S5 にも該当」をここに併記。
5. **値源の注記**(既存文「判断時点の値はこのレースの最初の計算で凍結したもの、現在値は最新の計算の値です」)と `field_changed_after_pick` の注記(既存 `attention-field-changed`)。
6. **折りたたみ** `<details class="attn-panel__evidence" data-testid="attention-evidence">` + `<summary>` 「検証の根拠(過去検証・判断時点の見込み・前向き検証・価格ずれ試験・価格鮮度)」。中身は既存の `<dl>`(該当する条件 / 過去検証 / 判断時点の見込み / 前向き検証 / 価格ずれ試験 / 価格鮮度 / 期待回収率)。初期状態 閉。
7. 免責文(既存・不変)。

## 2. 照合表の行

| kind | 条件の文(`criterionText`) | 値の表記 | 適合 |
|---|---|---|---|
| ens_expected_return | 「期待回収率(15 seed 平均)が {ev_gt×100}% 超」 | `EvValue`(推定バッジ・`data-kind="expected_return"`)。桁数は `formatEvAgainstThreshold`(1 桁、丸めが閾値の表示と同じなら 2→3 桁) | 語 |
| win_odds | 「単勝オッズが {lo} 倍以上 {hi} 倍未満」 | `{value} 倍`(小数 1 桁) | 語 |
| days_since_last | 「前走から {lo}〜{hi} 日」 | `{value} 日`(整数なら整数・小数ならそのまま)。現在値は「{value} 日(判断時点で固定)」 | 語 |

- 条件の文は `<span data-kind="criterion">` に入れる(`N%` の規律)。閾値はここと「…にも該当」の文にだけ出す。
- 適合の表記(`statusText`): `met` → 「満たす」、`not_met` → 「満たさない」、`missing` → 「値なし」、`current == null` → 「最新の計算なし」。記号(○ / × / —)は同じ要素の中で `aria-hidden="true"` の `<span>` に置き、語は常に読み上げられる。`data-status` 属性に状態を持たせる(テスト用・色は付けない)。
- 「最新の計算なし」(`component.current == null`)と「値なし」(`status == "missing"`)は別の状態で、片方で他方を置き換えない。
- 色クラス(`.good/.bad/.danger/.success/.profit/.up/.down`)は使わない。並べ替えなし。「満たさない」行を隠さない。

## 3. 要約の文型(`reasonSummaryText`・凍結)

| 判断時点 | 現在値 | 文 |
|---|---|---|
| 全部 met | 全部 met | 「判断時点(HH:MM)では {rule} の {n} 条件をすべて満たしました。現在値でも満たしています。」(n=1 のときは「条件を満たしました」) |
| 全部 met | 一部 not_met / missing | 「判断時点(HH:MM)では {rule} の {n} 条件をすべて満たしました。現在値では{部品名の列挙}の条件を満たしていません。」(missing は「{部品名}は値なし」) |
| 全部 met | current == null | 「判断時点(HH:MM)では {rule} の {n} 条件をすべて満たしました。現在値はありません(再計算待ち・オッズ欠落など)。」 |
| 一部 not_met / missing | 任意 | 「判断時点(HH:MM)では {rule} の {n} 条件のうち {k} つを満たしました(満たさない: {列挙})。」+ 現在値の文(上の規則) |

部品名: 「期待回収率」「単勝オッズ帯」「前走からの間隔」。HH:MM は `judged.computed_at`(JST)。文に購入を促す語・確信度の語を入れない(`ATTENTION_SCOPE` の検査対象)。

## 4. 「…にも該当」の文(`alsoApplicableText`)

- `note_ev_gt` を持つ項目: 「S2(期待回収率 130% 超)にも該当」。
- 他の項目: id を「・」で連結し「S3・S4 にも該当(表示する条件は最上位の S1)」。閉じた段階の条件は既存どおり `(300 点不通過)` などのタグを添える(`stageLabel`)。
- 包含関係の図・順位・確信度の語は出さない。

## 5. 不変テスト(front)

- 既存 `assertAttentionDiscipline` / `assertRoiBasisLabels` / `assertPseudoLabelCoverage` は、`<details>` の `summary` をクリックして開いた状態で当てる。
- 見出し(`attention-panel-title`)・バッジ・副チップ・チップ(`attention-chips`)の **可視テキストとアクセシブルネーム**(`aria-label` / `aria-labelledby` / `title`)に `\d\s*(%|倍|日)` が無い。照合表には定義どおりの閾値がある。
- 照合表の値 = fixture の `reason` の値(`toBe` で文字列比較)。要約文は §3 の文型と一致。
- `<details>` が初期状態で `open` を持たない。`summary` を `userEvent.click` して開くと `open` が付き、既存の統計がすべて中にある(`dt` の並びは 138 と同じ)。可視性そのものは jsdom では確認できないので実ブラウザ(quickstart §5)で見る。
- 折りたたみの移設で `PseudoValue`(全 `[data-pseudo]` にバッジ)・回収率の基準ラベル・価格鮮度の文・免責文が 1 つも消えていない(138 の期待文字列をそのまま再利用)。
- `reason.status == "unavailable"` で表と要約が無く、一文だけ出る。チップ・見出し・折りたたみは通常どおり。
- 対照 S5 のみ・取消・チップなしでは `attention-reason-table` が無い。
