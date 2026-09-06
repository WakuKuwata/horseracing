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

---

## 測定記録 1(2026-09-02・初回・append-only)

**window = 2026-07-23..2026-09-02(主系列=prospective)・lv は実行出力に完全記録**
(seed=20260723・b=2000・alpha=0.05・n_min/控除率=本文書の凍結値・baseline 両方実行)

**主判定: 全券種 NO_DECISION**(§11 の「前向き収集初期の既定」どおり)

| 券種 | n(scored bets) | n_min | 判定 |
|---|---|---|---|
| place | 423 | 500 | NO_DECISION |
| quinella | 426 | 500 | NO_DECISION |
| wide | 428 | 500 | NO_DECISION |
| exacta | 427 | 700 | NO_DECISION |
| trio | 427 | 1000 | NO_DECISION |
| trifecta | 427 | 1500 | NO_DECISION |

- 全体ゲート(settled 実配当レース ≥300)は **428 レース/12 開催日で通過**。券種別 n_min のみ未達
- 蓄積の実態: exotic_odds 3,135 レースのうち**窓内 prospective は 428 レース**。残り 2,707
  レース(2025-01-05..2026-07-19)は result ページ再取得(通過順 backfill 等)への相乗りで
  貯まった**キャッシュ由来=補系列**であり、§8 どおり主判定に混ぜていない
- point diff は NO_DECISION のため CI なし・edge を主張しない(数値は verdict 出力に記録)
- **n_min 到達見込み**(週末 2 開催日・約 70 bets/券種/週末の実測ペース):
  place/quinella/wide ≈ +1〜2 週末 / exacta ≈ +4 週末 / trio ≈ 約 2 ヶ月 / trifecta ≈ 約 3.5 ヶ月

**divergence 診断(採否バーでない)**: 窓内 428-1,284 pairs。log(実/推定) median は
−0.04(place)〜−0.12(trifecta)= 010 推定はやや過大方向、MAE 0.21〜0.45・P90 0.46〜1.00 で
券種の組合せ数が増えるほど乖離が大きい(double-pseudo の想定どおり)。coverage は
place 0.225 / trifecta 0.000 = 実配当は勝ち組合せのみ保存の構造による(全グリッドではない)。

**次の測定**: 週末運用の継続で n_min 到達後に同一凍結条件で再実行(append-only で本節に追記)。
補系列(cache)の診断ランは別途実施・別ラベル(判定に不使用)。

## 診断記録 1(2026-09-02・補系列=cache・判定に不使用・append-only)

**これは verdict ではない**(§8: 補系列は診断のみ)。窓 2025-01-05..2026-07-19・
2,657〜2,705 bets/券種・169 開催日・凍結パラメータ同一。

| 券種 | vs lowest_oest(本命筋) | vs uniform |
|---|---|---|
| **place** | **+0.101 CI[+0.045,+0.158] p_adj≈0 = 唯一の有意超過** | +0.208 有意 |
| quinella | +0.19 CI 跨ぎ | +0.48 有意 |
| wide | +0.06 CI 跨ぎ | +0.36 有意 |
| exacta | +0.32 CI 跨ぎ | +0.78 有意 |
| trio | +0.65 CI[−0.016,+1.47] 跨ぎ | +1.18 有意 |
| trifecta | +0.02 CI 跨ぎ | +0.52 跨ぎ |

**限界(この数値を信じ込まない理由)**:
- 補系列は**非ランダム部分標本**: result ページ再取得(通過順 backfill 等)の対象になった
  レースだけが配当を持つ(2025 年の月次被覆 ~15%・選択機構は結果と無相関の保証なし)
- uniform 超えは「ランダムよりまし」であり成功バーではない(§3: 両 baseline 超過が条件)
- **place の本命筋超えは仮説として有望**だが、判定は主系列(prospective)の n_min 到達後の
  再測定でのみ行う。place は n_min=500 に最初に到達する券種(+1〜2 週末)なので、
  この診断は「最初の判定券種と診断の有望券種が一致している」という追い風にとどめる
- 過去の関連実測との整合: [[cross-pool-place-result]](複勝の人気側に構造は実在するが
  相対 +7% で必要 +25% に桁不足)— 本診断の +10% は同じ帯であり、**絶対 ROI>1.0 とは別問題**

---

## 測定記録 2(2026-09-06・週末 9/5-6 込み・append-only)

**window = 2026-07-23..2026-09-06(主系列=prospective)・lv は実行出力に完全記録**
(seed=20260723・b=2000・alpha=0.05・baseline=lowest_oest・n_min/控除率=本文書の凍結値)

**主判定: 全券種 NO_DECISION**(券種別 n_min 未達・全体ゲート 14 開催日は通過)

| 券種 | n(scored bets) | n_min | 判定 | 前回比 |
|---|---|---|---|---|
| place | **493** | 500 | NO_DECISION | +70 |
| quinella | **498** | 500 | NO_DECISION | +72 |
| wide | **499** | 500 | NO_DECISION | +71 |
| exacta | 499 | 700 | NO_DECISION | +72 |
| trio | 499 | 1000 | NO_DECISION | +72 |
| trifecta | 499 | 1500 | NO_DECISION | +72 |

- **place/quinella/wide は n_min まで 1〜7 bet**。9/6 に 36 レース中 29 レースで買い目が出なかった
  (refresh が単勝発売前に走り `no win odds` で recommend が skip・オッズ自体は夕方の結果ページ経由で
  今は全レース保持)ため、本来なら今週末で 3 券種とも n_min を越えていた。**前向き買い目は閉鎖
  オッズで遡及生成できない**(065 の楽観バイアス)ので、この 29 レース分は恒久損失
- 点推定は NO_DECISION のため**解釈しない**(§5・§11)。次の開催日 1 日分で place/quinella/wide は
  n_min を越える見込み
- 手続き注記: 計器は `betting exotic-gate --from 2026-07-23 --to <日付>`・窓の終端は測定実行日
