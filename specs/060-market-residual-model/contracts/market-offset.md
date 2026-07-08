# Contract: market offset(060)

API/OpenAPI/DB スキーマの変更なし。本 feature の「契約」は以下の内部不変条件。

## INV-M1: offset の定義同一性

学習(fobj)・calib held-out・eval predict・serving predict の全経路で
`offset_i = log(clip(q_i, 1e-6, 1))`, `q_i = (1/odds_i)/Σ_j(1/odds_j)` が同一実装
(`training/market_offset.py` の純関数)から供給される。経路ごとの再実装禁止。

## INV-M2: 等価性(加算漏れ検出)

情報ゼロ特徴で学習した market_offset モデルの正規化前 softmax 出力は、
同一レースの q(clip 済み・再正規化)と一致する(校正 identity 時)。
= predict 側 offset 加算漏れ・fobj 側 offset 適用漏れを機械検出する恒久テスト。

## INV-M3: default byte-parity

market_offset を有効化しない全経路(既存 objective/binary/cond_logit/pl_topk、
offsets=None)は本変更前とバイト一致。既存モデル(metadata に market_offset キー無し)の
serving 予測はバイト一致。

## INV-M4: fail-closed

- 学習/評価: started 行に 1 頭でも無効オッズ(null / ≤0 / 非数)のレースは母集団から除外し件数を報告
- serving: 同条件で typed skip(offset なし縮退予測・部分補完は禁止)

## INV-M5: リーク境界(挙動型)

- 他レース・未来レースのオッズ変更は対象レースの予測を変えない
- レース結果の変更は予測を変えない
- 対象レース自身のオッズ変更は予測を変える(正の対照)

## INV-M6: 監査

market_offset モデルの予測 logic_version は `mkt=logq` を含む。
metadata.market_offset(kind/source/q_clip/limitation)から定義を復元できる。

## 事前登録ゲート(変更禁止)

母集団 = 全 fold のオッズ完全カバーレース(3 者共通):

1. win LogLoss(candidate) < win LogLoss(q 単体 baseline) — MUST
2. win LogLoss(candidate) < win LogLoss(lgbm-058-acc 構成の同母集団再評価) — MUST
3. top2/top3 LogLoss(candidate) ≤ q 単体 baseline(非悪化) — MUST

全通過 → `lgbm-060-mkt` candidate 登録(非 active・自動昇格なし)。不通過 → 登録なし。

## Spike go/no-go(事前登録)

直近 3-4 fold で candidate win LogLoss < q baseline(平均)。
no-go 時は γ 事前補正(research D2 フォールバック)を 1 回だけ試行、なお負ければ中断・記録。
