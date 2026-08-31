# T029: production pl_topk ゲート合格 → cap 既定 ON(2026-08-31)

064 が事前登録した条件「既定 ON は production pl_topk ゲート合格後」を、
**feature 104 の副産物 = 本番忠実 arm E(pl_topk/rounds 900/OOF isotonic/wmask)の
walk-forward OOF 予測 26,366 レース(2019-2026)** で検証した(当時は十数時間の学習が
必要で未実施のまま 2 年止まっていた。今回は学習ゼロ)。

## 結果(`policy-gate-pl-topk.json`・スクリプト `scripts/policy_gate_pl_topk.py`)
- ev_all(旧既定) ROI **0.7507** [0.716, 0.786] / ev_cap21 **0.8203** [0.796, 0.847]
- **paired(ev_cap21 − ev_all) = +0.0696 CI[+0.0292, +0.1093]**(開催日クラスタ bootstrap)
- 年別 2019-2026 の **8 年すべて改善**(+0.015〜+0.148)
- cap 境界は**排他**: odds < 21.0 が賭け対象

これは**出血削減であって利益ではない**(全政策 ROI<1.0。精度→利益の橋は
accuracy-to-roi-bridge 測定で閉じている)。

## 実装(codex 設計レビュー 3 パス全会一致を反映)
- `DEFAULT_WIN_ODDS_CAP = 21.0`(recommend.py)。CLI 未指定 → 既定 / `--no-win-odds-cap` で
  旧挙動をバイト再現 / 両指定は usage error(優先順位で解決しない)/ 非正値・NaN・Inf 拒否
- **混在ガード(双方向・076 同型)**: 同じ run に別 win policy(cap⇔なし・別 cap 値)が
  あれば生成スキップ `win_policy_conflict`。既存 run は旧 policy のまま = rollout 境界
- **トークン厳密化**: `contains(";oddscap=21.0")` は部分一致で 21.05 にも当たる →
  `token+";" または endswith` の境界つき一致に(`_cap_token_filter`)
- **prospective(065 shadow-log)にも波及**: shadow は本番の影なので同じ既定。既存ログ不変・
  `logic_version` が policy regime を区切る。レーン内混在ガード + **lock key から policy を
  除去**(policy 別 lock だと並行実行が混在を作れる)
- ops / live refresh は無変更(フラグなし起動が新既定を拾う)

## リリース前監査
既存の混在 run は **1 件**(race 202610020412・064 の E2E 残骸・win 8 行中 3 行 capped)。
append-only のため残置。新ガードにより今後は構造的に発生しない。
