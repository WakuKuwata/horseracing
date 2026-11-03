# Quickstart: 177 注目条件の該当理由の表示 — 検証手順

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Contracts**: [api.md](contracts/api.md) / [display.md](contracts/display.md)

## 前提

- ローカルスタック(`scripts/stack.sh`)の DB に 138 の `attention_picks` / `attention_race_scans` 行があるレース(2026-10 以降の開催日)。
- 各パッケージの venv は既存(`uv run --project <pkg>`)。front / admin は `pnpm`。

## 1. eval: 部品判定と総合判定のビット一致(SC-002・INV-R1〜R6)

```bash
cd eval && uv run pytest tests/unit/test_attention_rules.py -q
```

期待: 緑。追加テストが (a) 177 以前の `match_mask` 逐語コピーと新 `match_mask` の一致(格子 + 乱数 20,000 行 + 各閾値の隣の float(`np.nextafter`)+ スカラー/ベクトル混在・空配列・None・NaN・±inf・Decimal・shape と dtype) (b) `component_states` の全部 met ⇔ `match_mask` (c) 境界値(EV 1.20 / 1.20+ε、odds 19.99 / 20.0 / 39.99 / 40.0、gap 13 / 14 / 112 / 112.5 / 113) (d) `chip_now == "matches"` ⇔ 全部品 met(missing を unknown に結び付けない) (e) `definitions_sha256()` の値が不変 (f) 対照でない条件はすべて `uses_ensemble=True`、を通す。

## 2. api: 応答の純追加と整合(US2・FR-004)

```bash
cd api && uv run pytest tests/unit/test_attention.py tests/integration/test_openapi_contract.py -q
```

期待: 緑。追加テストが (a) チップ馬に `reason`(`status=available`)が付き、チップなし・S5 のみ・void 済みで `null`、チップなしの馬の現在値が条件を満たしても `null` のまま (b) 部品の `judged`/`current` の値が `judged`/`current` snapshot と一致し、判断時点の部品は合成 pick で全部 `met` (c) odds 45.0 で odds 部品 `not_met` かつ `chip_now=no_longer`、`current` None(再計算待ち・オッズ欠落・出走馬変更)で全部品 `null` かつ `unknown`、S3 チップは部品 1 つで間隔 null でも `reason` あり、S1 pick の間隔 null は部品 `missing` で総合 `no_longer`、間隔 112.5 は `not_met`、別 run の単 seed 行は reason に影響なし、不通過/保留のチップでも reason あり (d) `also_applicable` の `note_ev_gt` は S1 チップの S2 にだけ 1.3、対照とチップを含まず rank 順 (e) `AttentionHorse` を直接組んだ矛盾(全部品 met で `no_longer`、`current` None で `matches`、`reason` ありで `chip_rule` None、`rule_id` 不一致、部品の順序違い、条件値が定義と違う)が `ValidationError`、builder に同じ矛盾を注入(monkeypatch)すると `unavailable` + WARNING ログで応答は 200 (f) `AttentionHorse.model_json_schema()` の既存 property 名と required 集合が 138 時点の一覧と一致 (g) 既存の 138 テストが無変更で緑、を通す。

## 3. OpenAPI(SC-006・FR-014)

```bash
scripts/stack.sh start api        # または既存の API を使う
(cd front && bash scripts/gen-types.sh)
(cd admin && bash scripts/gen-types.sh)
git diff --stat front/openapi.json admin/openapi.json front/src/api/schema.d.ts admin/src/api/schema.d.ts
cmp front/openapi.json admin/openapi.json
pnpm -C front check:openapi && pnpm -C admin check:openapi
```

```bash
git diff --numstat front/openapi.json    # 削除行が 0(純追加)
```

期待: 差分は `components/schemas` の追加と `AttentionHorse.reason` のみ(削除行 0)。既存 property の `required` / nullable は不変。path の追加・削除なし。2 ファイルはバイト一致。drift-check 緑。

## 4. front: 照合表と折りたたみ(US1・SC-005・SC-007)

```bash
pnpm -C front test -- AttentionPanel HorseEntriesTable attention
pnpm -C front typecheck && pnpm -C front lint
pnpm -C admin test
```

期待: 緑。追加テストが spec US1 のシナリオ 1〜10(照合表の行数と値・要約文の 4 形・現在値なし=「最新の計算なし」と部品欠損=「値なし」の区別・S3 の 1 行・S2 の一文・不通過タグ付きの「…にも該当」・S5 のみ/取消で表なし・チップと見出しの可視テキストとアクセシブルネームに閾値の数字なし・`<details>` の初期 閉と `summary` クリックで開いた状態での規律と 138 の全開示の残存・`th scope`・`aria-hidden` の記号・`unavailable` の一文・EV の桁数が閾値の表示と同じにならない)を通す。既存の 138 テストは期待文字列の位置変更(折りたたみの中)と `期待回収率` 行の縮小以外は無変更。

## 5. 実画面(SC-001・SC-003)

```bash
scripts/stack.sh status           # api / front が起動していること
```

1. 2026-10 以降の開催日のレース詳細を開き、チップのある馬の行を展開する。
2. 照合表の判断時点の値(期待回収率・単勝・間隔)が、同じ馬の pick の値と一致することを確認する:
   ```bash
   psql "$DATABASE_URL" -c "select rule_id, ens_expected_return, odds_used, days_since_last, computed_at from attention_picks where race_id='<race_id>' and horse_id='<horse_id>' and kind='pick' order by rule_id"
   ```
3. 現在値の列が、同じ馬の `market_ev_predictions`(`model_version='mev-ens15-v1'`)の最新行と一致することを確認する。
4. 要約文が照合表の適合と矛盾しないこと、チップに閾値の数字が無いこと、「検証の根拠」が **閉じていて中身が見えず**、開くと 138 の統計がそのまま見えること(可視性は実ブラウザでしか確認できない)を確認する。読み上げ(VoiceOver)で適合の語が 1 回だけ読まれ、記号が読まれないことを 1 行確認する。
5. 現在値が無いレース(再計算待ち)があれば、全行「最新の計算なし」と `chip_now=unknown` の表示を確認する。

## 6. 回帰(SC-004)

```bash
cd eval && uv run pytest -q
cd ../api && uv run pytest -q
cd ../training && uv run pytest tests/unit/test_attention_picks.py -q   # pick 書込みは無改修(applicable_rules の挙動不変)
cd ../scripts && uv run --project ../training pytest tests/test_freeze_rules_S1_S5.py -q   # 凍結スクリプトは match_mask を呼ぶ(呼び出し経路を踏む)
pnpm -C front test && pnpm -C admin test
```

期待: 全部緑。training の pick 書込みテストは変更なしで緑(= `match_mask` の書き換えが呼び出し側に見えない)。
