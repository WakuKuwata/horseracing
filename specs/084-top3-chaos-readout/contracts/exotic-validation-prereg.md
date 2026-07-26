# 080 実配当による S の構成概念妥当性検証 — 事前登録

**Status**: FROZEN（実配当 0 行の時点で固定）  
**Registered**: 2026-07-26  
**Feature / task**: 084 US5 / T080  
**対象 artifact**: `chaosbands-v1`  
**Artifact digest**:
`f190e65cb9bb2d59d27982c8721f8f8e65e6c31e5b53d65d367b7ca569b72782`  
**Fit / validity**: `fit_through=2023-12-31`, `valid_from=2024-01-01`

この文書は feature 080 が `exotic_odds` を生成する前に、v1 の実配当検証を固定する。
結果を見た後に endpoint、閾値、除外、score、band edge を変更しない。

## 1. 問いと endpoint の優先順位

検証する問いは、凍結 snapshot から得た
`S = 1〜3着馬の popularity 順位合計` が、実際の上位3着向け配当と平均的に正の関係を持つかである。

1. **PRIMARY: 三連複（trio）**。三連複は上位3頭の**順序を問わない集合**への払戻であり、
   順序非感応な S の構成と一致する。v1 の構成概念妥当性の判定は三連複だけが支配する。
2. **SECONDARY: 三連単（trifecta）**。三連単は同じ3頭でも着順により配当が変わる。
   S が持たない order effect を導入するため、副次的な外的妥当性の確認に限る。
   三連単が良くても三連複の失敗を救済しない。

## 2. S が表すもの、表さないもの

S の正式名は **`top3_popularity_composition_proxy_v1`** である。S は人気順位という序数を足した、
順序非感応の **PROXY** であり、配当の ground truth ではない。順位間隔が等しいことも仮定しない。

同じ S でも実配当は大きく異なり得る。たとえば `{1, 2, 17}` と `{5, 7, 8}` はともに
S=20 だが、市場支持率、組合せ需要、控除後の払戻は同じではない。本検証が支持されても
「S から個別レースの配当を復元できる」とは主張しない。

## 3. Cohort、分析単位、分母

- 主系列は feature 080 の収集開始後に**前向きに**確定した JRA レースだけとする。
  登録日前の race_date を持つ cache/backfill 行は discovery/diagnostic と表示し、主判定に混ぜない。
- 分析単位は 1 レース。084 の `status='active'` かつ
  `capture_strength='confirmatory'` の凍結 snapshot を 1 行だけ用い、S は必ずその field の
  popularity から算出する。現在の `race_horses.popularity` は使わない。
- **full target denominator** は収集期間内の全確定レース（started が4頭以上）であり、
  snapshot や配当が存在するレースだけを分母にしてはならない。
- full target denominator から、結果不成立、3頭未満、top-3 が一意でない同着を理由別に示す。
  payout coverage の分母は、それら事前規定の outcome-invalid レースだけを除いた全レースとし、
  `exotic_odds` が存在するレースだけへ縮めない。
- trio / trifecta ごとに、正の払戻を一意に結合できたレース数、欠損数、重複・非正値・
  selection 不一致数を報告する。

## 4. 配当の単位と連続 metric

`exotic_odds.odds` は払戻倍率（払戻金円 / 100）なので、分析値は
`D = 100 × exotic_odds.odds`、単位を **100円投票あたりの円**とする。
主 metric は自然対数 `Y = ln(D)` である。

- `D <= 0`、非有限値、winning selection と結合できない値は 0 や epsilon に置換せず missing/data
  error として監査する。
- 上限 cap、winsorize、会場内の再尺度化は行わない。
- 円の生値について中央値と四分位範囲も記述するが、判定は log dividend に固定する。

## 5. 固定 estimand と判定

各券種について次を計算する。

```text
Δ_bet = mean(ln(D_bet) | S >= 20) - mean(ln(D_bet) | S < 20)
```

`S>=20` は v1 で既に凍結した fit-window 約90パーセンタイル閾値であり、配当を見て選び直さない。
補助表として `S<=10` / `11<=S<20` / `S>=20` の n、円配当中央値、平均 log 配当を出す。
S の値別または band 別の図と rank correlation は診断専用で、主判定を置き換えない。

三連複の判定は次の3値とする。

- **SUPPORTED**: データ充足条件を満たし、`Δ_trio` の95% CI下限が 0 より大きい。
- **NOT_SUPPORTED**: データ充足条件を満たすが、上記条件を満たさない。
- **NO_DECISION**: 最小件数、開催日数、または coverage gate が未達。

三連単にも同じ差と3値を報告するが、常に `SECONDARY` と明記し artifact の昇格判断に使わない。

## 6. 最小件数、coverage、missingness

券種ごとの判定には、正の払戻と結合できた `S>=20` レース **100件以上**、
`S<20` レース **100件以上**、かつ **60開催日以上**を必要とする。
未達なら効果の方向にかかわらず `NO_DECISION` とする。

confirmatory verdict には、その券種の payout coverage が outcome-valid denominator の
**95%以上**で、`S>=20` / `S<20` の各群でも95%以上であることを要求する。未達なら
complete-case の結果は診断として示すだけで `NO_DECISION` とする。

missingness audit では、少なくとも次を件数・率で示す。

- snapshot 無し / weak / unknown、invalid popularity、結果不成立、同着、3頭未満
- trio / trifecta 行無し、非正値、重複、winning selection 不一致
- S 群、開催日、会場、頭数、capture horizon ごとの coverage

## 7. 同着規約

top-3 の3頭または順序が一意にならない同着は、084 の outcome 契約どおり主・副分析とも
`dead_heat` として除外する。同着で複数の払戻行があっても、平均・最大の選択やレースの複製は
行わない。top-3 に影響しない4着以下の同着は除外理由にしない。除外レースは full target
denominator と missingness audit には残す。

## 8. 信頼区間

95% CI は**開催日をクラスタ**として日単位で再標本化する cluster bootstrap
（2,000 resamples、`seed=20260726`）で求める。同日のレースを独立に resample する
i.i.d. CI は使わない。主判定は三連複1本なので、三連単の CI は secondary と表示し、
三連単の結果から主判定を選択し直さない。

## 9. 事前 survey の文脈と禁止 endpoint

[research.md](../research.md) に記録済みの web survey では、JRA 三連単の万馬券率は
**71〜77%**、会場別の三連単配当中央値は **18,500〜32,655円**である。したがって
「三連単が10,000円を超えたか」は陽性が大半を占めて識別力がほぼなく、endpoint、成功条件、
代替判定のいずれにも**使用禁止**とする。会場中央値も記述的な外部文脈であり、結果後の cutoff
候補にはしない。

## 10. 失敗は記録し、v1 を修理しない

**Failure is recorded, not repaired.** `NOT_SUPPORTED` は v1 の score や quintile edge を
切り直す許可ではない。v1 の S、閾値20、band edge、fit cutoff、artifact は不変のまま、
digest とともに失敗結果を保存する。

配当を見て score または edge を再設計する場合、それは必ず **v2** とする。v2 は新しい
fit cutoff と新しい artifact digest/version を持ち、v2 の公開後に始まる**さらに未使用の**
confirmation cohort で再確認しなければならない。ART-6 に従い、promotion は既存 artifact の
書換えではなく**新 artifact version の publish のみ**で行う。
