# Contract: 結合確率エンジン

`horseracing_probability.engine` / `consistency` の契約。

## エンジン

```python
@dataclass(frozen=True)
class JointProbabilities:
    win: dict[str, float]                       # horse_id -> P(1着)
    place: dict[str, float] | None              # horse_id -> P(複勝圏)。≤4頭は None
    exacta: dict[tuple[str, str], float]        # (i,j) ordered -> P(馬単)
    quinella: dict[frozenset[str], float]       # {i,j} -> P(馬連)
    wide: dict[frozenset[str], float] | None    # {i,j} -> P(ワイド)。N<3 は None
    trifecta: dict[tuple[str, str, str], float] # (i,j,k) ordered -> P(三連単)。N<3 は空
    trio: dict[frozenset[str], float]           # {i,j,k} -> P(三連複)。N<3 は空

def joint_probabilities(
    win_probs: dict[str, float], *, field_size: int | None = None, eps: float = 1e-9
) -> JointProbabilities:
    # 1. 入力 = 出走母集団の単勝確率(取消・除外の除去は呼び出し側責務。エンジンは entry_status を持たない)
    # 2. Σ=1 再正規化 -> [eps,1-eps] clip -> 再正規化 (INV-J1、再正規化は PL 分母計算より前)
    # 3. PL 派生: exacta/trifecta、無順序=順序和、wide=trio 第3頭和、place=harville top-N(N 依存)
    # 返り値は INV-J2..J7 を満たす
```

## 整合性検査

```python
DEFAULT_TOL = {"sum": 1e-6, "marginal": 1e-6}   # golden 比較は atol=1e-9(SC-001)

def check_joint_consistency(jp: JointProbabilities, win_probs: dict[str,float],
                            tol: dict | None = None) -> None:
    # Σexacta=1, Σtrifecta=1, quinella=exacta双方向和, wide>=quinella,
    # trifecta周辺=harville top3, exacta周辺=harville top2(非退化), place∈[0,1], 単調
    # 違反は JointConsistencyError(fail-fast)
```

## 保証(テストで検証)

- N=3/4・一様の手計算 golden と全券種が許容内一致(SC-001)。
- INV-J2..J5 の整合性不変条件をすべて満たす(SC-002)。
- 取消・除外除去後に残存馬で再正規化してから派生、取消馬の確率 0(SC-003)。
- 端点でゼロ割・範囲逸脱なし、決定論(SC-004)。
- 複勝の頭数依存・小頭数縮退・該当なし(None/空)が規則どおり(SC-006)。
- 確率導出は結果/オッズ非参照(SC-007)。
