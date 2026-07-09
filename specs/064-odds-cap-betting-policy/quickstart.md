# Quickstart: odds-cap betting policy + honest display

前提: ローカル DB(docker-postgres-1・horseracing DB)、backfill 済み予測、API :8000、front dev。

## 1. バイト同等(cap 無効=既定)

```
betting recommend-serve --race-id <RID>            # cap 未指定=現行
```
- 期待: recommendation 行・`logic_version` が現行(064 前)と完全一致(`test_win_odds_cap_none_is_byte_identical_select_ev_bets` 緑)。

## 2. odds-cap 有効の選定

```
betting recommend-serve --race-id <RID> --win-odds-cap 21
```
- 期待: オッズ 21+ の馬が win 推奨に現れない。cap 内馬の `pseudo_odds`(=1/p)・`pseudo_roi`(=EV−1)が cap 無効時と一致(分母保持=win_prob 不変)。`logic_version` に `;oddscap=21.0`。全馬 21+ のレースは推奨ゼロで正常終了。

## 3. 採用ゲート(proxy で素振り → production で最終確認)

```
# 高速 proxy(binary)で素振り
training policy-gate-eval --from 2008-01-01 --to 2026-07-31 --first-valid-year 2008 --cap 21 \
  --objective binary --calibration isotonic --target-encode jockey_id,trainer_id
# 忠実版(production 構成・長時間)
training policy-gate-eval ... --objective pl_topk ...
```
- 期待レポート: 現行 EV policy vs cap policy の recovery/hit/skip/maxDD/losing_streak + fold別・odds帯別・log growth。`adopted` = cap が現行比で recovery 改善 かつ 過半 fold 改善 かつ 最悪 fold 非悪化。closing-oracle 注記が付く。
- 検証済み参考値(proxy): 現行 ×0.721 → cap<21 ×0.818・19/19 年で現行超え。

## 4. 表示(正直な意思決定支援)

- rec panel の単勝過去実績サマリに **no-bet ×1.00** と **本命ベタ基準** が併置され、回収<1 が中立表示(損益色/利益語/ソートなし)。
- odds帯別 realized 回収が出て大穴帯の出血が見える。
- 中立注記(市場超過の再現優位なし)が常時表示。
- 推奨ゼロのレースで「見送り(理由)」が出る。
- 疑似値は必ずラベル・realized と別グループ(不変テスト緑)。

## 5. 既定切替(ゲート合格後)

- US3 ゲート合格を確認してから、orchestration/serving/CLI の既定 `win_odds_cap` を `21` に変更(それまでは opt-in)。
- 切替後も cap 無効指定で現行挙動を再現できること(後方互換)。

## 合格判定(Definition of Done 抜粋)

- SC-002 バイト同等(cap 無効)緑。SC-001 ゲート(production 構成)で cap が現行の出血を fold 安定的に下回る。SC-003 lv から cap 再現可。SC-004 表示で回収<1/no-bet 最適/優位なしが常時可視。SC-005 leak-guard 緑・selection results 非参照・api betting 非 import。
