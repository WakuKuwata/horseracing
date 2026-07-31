# 実装契約 — 導出層較正診断(2-3)

事前登録: [prereg-joint-calibration.md](prereg-joint-calibration.md) **rev2**。
この契約は **3 者が並列実装するための凍結インターフェース**。勝手に変えない。
矛盾を見つけたら実装せず報告すること(事前登録が正本)。

## ファイル所有権(重複禁止)

| 担当 | 触ってよいファイル |
|---|---|
| **A** | `eval/src/horseracing_eval/joint_calibration.py` のみ |
| **B** | `training/src/horseracing_training/joint_calibration_run.py` と `training/src/horseracing_training/cli.py` のみ |
| **C** | `eval/tests/unit/test_joint_calibration.py` のみ |

他人のファイルを編集しない。既存モジュールの変更も禁止(必要なら報告)。

## 凍結定数(A が定義し、B/C は import する)

```python
CONTRACT_VERSION = "joint-calibration-v1"

#: 実現組合せが一意な券種だけが NLL の対象。ワイドは当たりが 3 つあるので入らない。
BET_TYPES_NLL: tuple[str, ...] = ("exacta", "quinella", "trio", "trifecta")

#: reliability のみ対象。N>=8 限定(N=5-7 はエンジンと精算が食い違う=別所見)。
WIDE_MIN_FIELD = 8

#: 事前登録の対数等間隔ビン。0 から 1 を隙間なく覆う(下側 [0, 1e-6) を含む)。
BIN_EDGES: tuple[float, ...] = (1e-6, 1e-5, 1e-4, 1e-3, 3e-3, 1e-2, 3e-2, 1e-1, 3e-1, 1.0)

FIELD_BUCKETS: tuple[str, ...] = ("<=7", "8-11", "12-15", "16+")

#: 主 arm。0.75/0.70 を主とベースラインに二重掲載しない。
MARKET_LAMBDA2, MARKET_LAMBDA3 = 0.75, 0.70
ARMS: tuple[str, ...] = ("identity", "market_current", "indep_normalized", "uniform")

#: §7 選択部分集合エンドポイントの閾値(事前固定)
SELECT_THRESHOLDS: tuple[float, ...] = (1.0, 1.5)

BOOTSTRAP_B, BOOTSTRAP_SEED = 2000, 20260731
```

## データ契約(B が作り、A が受ける)

```python
@dataclass(frozen=True)
class JointCalibRace:
    race_id: str
    day: str                          # ISO 日付。開催日クラスタ bootstrap の単位
    numbers: tuple[int, ...]          # 最終 started の馬番・昇順
    q: tuple[float, ...]              # numbers に整列・Σ=1(B が正規化済みで渡す)
    top3: tuple[int, int, int]        # 実現 1・2・3 着の馬番
    #: US2 専用。bet_type -> 正準キー -> 実オッズ(ワイドは下限)。無ければ None
    grid: dict[str, dict[tuple[int, ...], float]] | None = None
```

**不変条件(A は fail-closed で検証する)**: `len(numbers) == len(q)`、`numbers` は昇順かつ重複なし、
`abs(sum(q) - 1) < 1e-9`、`q` は全て有限かつ正、`top3` の 3 頭は相異なり全て `numbers` に含まれる。
破れたら `JointCalibrationError` を送出する(黙って落とさない)。

**正準キー**: 順序付き=馬番の tuple(着順のまま)。順序なし=**昇順 tuple**(frozenset ではない。
JSON 化と決定論のため)。`exacta=(1着,2着)` / `quinella=tuple(sorted(...))` /
`trifecta=(1,2,3着)` / `trio=tuple(sorted(...))` / `wide=tuple(sorted(pair))`。

## A の公開 API

```python
class JointCalibrationError(RuntimeError): ...

def stage_losses(race, *, lambda2: float, lambda3: float) -> tuple[float, float, float]:
    """(L1, L2, L3)。事前登録 §2 の式そのまま。
       L1 = -log q_{r1}
       L2 = -log( q_{r2}^λ2 / Σ_{h≠r1} q_h^λ2 )
       L3 = -log( q_{r3}^λ3 / Σ_{h∉{r1,r2}} q_h^λ3 )
       λ=1 のとき L2/L3 は素の逐次 softmax に一致すること。"""

def normalized_independent(q, numbers, k: int, *, ordered: bool) -> dict[tuple[int, ...], float]:
    """事前登録 §5。ordered=True は Z2/Z3 正規化した順列分布、False は 2倍/6倍係数つき組合せ分布。
       いずれも Σ=1 になること(これがベースラインの要件)。"""

def bet_type_distributions(race, *, arm: str) -> dict[str, dict[tuple[int, ...], float]]:
    """arm ごとに bet_type -> 正準キー -> 確率。
       identity        : joint_probabilities(q, stage_discount=None)
       market_current  : joint_probabilities(q, stage_discount=StageDiscount(0.75, 0.70))
       indep_normalized: normalized_independent
       uniform         : そのレースの全組合せに 1/K
       ワイドは N>=8 のときのみ含め、**総質量 3 のまま**(1 に正規化しない)。"""

def realized_keys(bet_type: str, top3) -> tuple[tuple[int, ...], ...]:
    """当たりキー。ワイド以外は長さ 1、ワイドは長さ 3。"""

def evaluate(races, *, arms=ARMS, b=BOOTSTRAP_B, seed=BOOTSTRAP_SEED) -> dict:
    """全指標を計算して JSON 化可能な dict を返す。最上位キー:
       instrument_contract / provenance / stage_losses / bet_type_nll /
       reliability / selected_subset / wide_inclusion / field_size_mismatch_note"""
```

## 統計の要件(A・**ここが一番間違えやすい**)

- **開催日クラスタ bootstrap**。`eval/bootstrap.py` の既存関数を使う。新しい bootstrap を書かない
- **replicate の中で分母・ビン和・比を再計算**する。点推定を取り出してから区間だけ作らない
- **全 arm を同じ抽出で動かす**(paired)。arm ごとに別 seed で回さない
- reliability は**セル加重(micro)**。1レース1標本の NLL とは別 estimand なので出力にそう書く。
  レース正規化版のギャップも併記
- **Wilson は使わない**(セルはレース・和制約・頭数を共有し独立でない)
- 空ビン・実現ゼロビンは **NaN を出す。0 で埋めない**
- 券種間・頭数間の比較は**一様超過** `NLL - log K_r` で行う(生 NLL を並べない)

## B の責務(DB → JointCalibRace)

窓 **2019-01-01..2026-07-12**。fail-closed で以下を**相互排他に**数え、出力に全件記録する:

`partial_or_invalid_odds` / `top3_dead_heat` / `missing_or_duplicate_rank` /
`result_horse_not_started` / `duplicate_horse_number` / `incomplete_grid` / `unsupported_wide_field`

- オッズ無効の定義: `None` / `<=0` / NaN / inf / **999.9 番兵** / 範囲外。
  **最終 started の 1 頭でも無効ならレースごと除外**(部分フィールドを再正規化しない)
- **上位3着の同着は除外。4着以下の同着は残す**
- 頭数バケットは**最終 starters** で決める
- US2: `exotic_quotes` から馬連・ワイド・三連複のグリッド。ワイドは**下限**。
  取消で started に無い馬を含む価格行は落として数える。
  **λ fit に使った 667 レースを除いた 334 レースのみ**を使う。membership が正確に再構成
  できなければ `us2_scope="developmental_all_1001"` と記録して全件を使う
- **netkeiba へのアクセスは一切禁止**。ローカル DB のみ

CLI: `training joint-calibration --from --to --seed --bootstrap-b --json`。
既存サブコマンドの実装(`cross_pool_win_run.py` / `_cross_pool_win`)と同じ形にそろえる。

## C の責務(事前登録から直接テストを書く)

**A の実装を読まずに、事前登録と本契約だけからテストを書くこと。** 実装に合わせない。

必須(codex レビューが挙げた不変条件):

1. 一様 N=8: 4 券種は Σ=1。**ワイドは Σ=3** で各ペア `3/C(8,2)`
2. N=7: エンジンのワイド(上位3着包含)と精算(上位2着)の**食い違いを実証**する
3. `馬単 NLL == L1+L2`、`三連単 NLL == L1+L2+L3`(λ 込みで厳密に)
4. 正規化独立積が厳密に Σ=1、順序なしの 2倍/6倍係数が正しい
5. 正例はカテゴリ型で**ちょうど 1**、ワイドは N>=8 で **3**
6. ビン境界が 0〜1 を**隙間なく重複なく**覆う
7. 空ビン・実現ゼロビンが**事前登録どおり NaN**
8. bootstrap が開催日を再抽出し**replicate 内で分母を再計算**、arm 比較は**同一抽出**
9. 頭数可変の fixture で micro 加重と race 加重が**別の値**になることを示す
10. 上位3着同着は除外・4着同着は残す
11. オッズ無効(None/0/負/NaN/inf/999.9)が**全ケース fail-closed**
12. **結果を変えても予測・ビン・マスク・入力確率が一切変わらない**(リーク境界)
13. λ arm が厳密値。049/084 の λ が紛れ込まないことを固定
14. `JointCalibRace` の不変条件違反が `JointCalibrationError` になる

テストはネットワークを使わない。DB も使わない(合成 fixture のみ)。
