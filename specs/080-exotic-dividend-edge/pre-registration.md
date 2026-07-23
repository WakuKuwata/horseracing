# Exotic Edge Measurement — Pre-Registration (Feature 080 · US3 / T020)

**Status**: FROZEN(結果を見る前に固定)
**Created**: 2026-07-23
**Rule**: この文書は実配当を測定する**前**に確定する。測定後に条件を変えない(憲法 III)。追記は append-only(過去 verdict を遡及変更しない)。数値を見てから n_min / baseline / 補正法を動かすことは禁止。

---

## 0. 目的と成功の定義(honest bar)

- 測るもの: WIN より非効率と期待される exotic 市場で、009 joint EV(モデル p 由来)が**実配当を上回る edge を持つか**。
- 成功 = 各券種で **baseline を超え、多重比較補正後も有意**(市場超過が真のバー)。**ROI>1.0 単独では成功としない**(控除率逆風下で ROI>1.0 は稀・かつ baseline 未超過なら再現しない)。
- **null 結果も成功**(feature の目的は「儲ける」でなく「edge の有無を正直に測れる状態」)。
- edge の有無は測定結果であって feature の成否ではない。

## 1. 対象券種(個別に測る・束ねない)

place / quinella / wide / exacta / trio / trifecta を**各券種独立**に測定・判定する。
理由: 049 で「束ねたゲートは trio の悪化を place が隠す」を確認済み。券種間で控除率も分散も違う。

## 2. probability と odds(p≠q)

- probability = **P_model = 009 joint(active model=lgbm-065 の win p 由来)**。q(市場 vote-share)をモデル確率に使わない。
- odds/payout = **実 exotic 配当優先**。実配当が無い券種/レースは 010 推定オッズ(double-pseudo)で**分離ラベル**し、主判定には実配当のみを使う。
- EV = P_model(combo) × payout(combo)。selection は結果を読まない(009/010/011 既存不変式)。

## 3. baseline(同条件)

各券種で 2 つ:
- **lowest-O_est(人気筋)**: その券種で推定オッズが最小=市場が最も本命視する組合せを同数選ぶ。
- **uniform**: その券種の候補組合せから無作為/一様に同数選ぶ。
成功条件は「モデル EV 選抜が両 baseline を上回る」。

## 4. 採点規則(既存 011/012 準拠)

- exacta/trifecta = ordered 一致、quinella/trio = set 一致、wide/place = inclusion + 009 field ルール。
- place/wide の複数当選は bet-level で採点(複数払戻を正しく合算)。
- payout = 実配当(該当時)、stake=flat(EV 選抜)。

## 5. 最小サンプル数 n_min(券種別・FROZEN)

n = 主系列(prospective)で EV≥threshold により選抜され採点された **bet 数**。組合せ数と payout 分散が大きい券種ほど大きく設定。

| 券種 | n_min(scored bets) | 根拠(事前) |
|---|---|---|
| place | 500 | 低分散・高頻度 |
| quinella | 500 | 中分散 |
| wide | 500 | 中分散・複数当選 |
| exacta | 700 | やや高分散 |
| trio | 1000 | 高分散 |
| trifecta | 1500 | 最高分散(稀な大配当が支配) |

- さらに **全体ゲート**: 主系列で実配当を持つ settled レースが **300 未満**の間は、全券種 verdict=**NO_DECISION**(母集団が薄すぎる)。
- n<n_min の券種は個別に **NO_DECISION**(edge を主張しない)。

## 6. 信頼区間・有意性

- **開催日クラスタ bootstrap**(race-day cluster、i.i.d. リサンプル禁止)、resamples=2000、**seed=20260723**(固定)。
- 各券種で「モデル − baseline」の realized ROI 差の 95% CI を出す。CI 下限 > 0 を有意の必要条件とする。

## 7. 多重比較補正

6 券種(×評価窓が複数なら窓数)にわたる偽陽性を **Holm–Bonferroni**(family = 全券種×窓)で補正。
補正後も CI 下限>0 かつ p<補正 α の券種のみ ADOPT候補。事前に family サイズを窓確定時に固定。

## 8. 収集系列(主/補)

- **主 = prospective**(feature 稼働後に前向き収集した実配当)。closing 楽観バイアスなし。**判定はこの系列のみ**。
- **補 = netkeiba cache backfill**(過去 result cache に既在の実配当)。in-sample 寄り=**別ラベル・診断のみ**、主判定に混ぜない。

## 9. OOS / overfit ガード

- in-sample の見かけ edge を walk-forward / 時系列 OOS(前半で選抜規則固定→後半で検証)で確認。OOS で崩れれば **REJECT**。
- 過去の当たり穴目を拾う overfit を防ぐため、選抜閾値・券種・baseline は本文書で固定(結果後に選び直さない)。

## 10. 控除率(logic_version へ記録)

JRA 既定: place 20% / quinella 22.5% / wide 22.5% / exacta 25% / trio 25% / trifecta 27.5%。
edge run の logic_version に控除率・評価窓・seed・n_min・baseline 種別・多重比較補正法・収集系列を記録(憲法 V・再現性)。

## 11. verdict(三値・遡及変更しない)

- **NO_DECISION**: n<n_min または全体<300 races(前向き収集初期の既定)。
- **REJECT**: baseline 未超過、または多重比較補正後に非有意、または OOS 崩壊。
- **ADOPT候補**: 全条件満+OOS 維持。**それでも実運用ベッティングは別 feature**(本 feature は測定のみ)。

## 12. 評価窓

初回測定は「feature 稼働日 〜 測定実行日」の全 prospective 期間(単一窓)。複数窓に分ける場合は family サイズ(§7)を窓確定時に事前固定してから測定する(窓を結果に合わせて選ばない)。
