"""Feature 109: the frozen buy-pattern family (enumeration + predicates).

A *buy pattern* is a rule that maps (race context, market state, model state) to the set of
horses to bet 100 yen on. This module owns the axes, band edges and predicates
(research D4) and enumerates the family deterministically; the driver freezes the emitted
JSON and hashes it. Predicates NEVER read outcomes (won / finish_order / payout) — a condition
naming one of those fields is rejected at enumeration time (FR-009).

Imports: numpy only. No pandas (the eval environment has none), no betting / training.

Array contract (all numpy, one row per started horse; produced by the driver via
``buy_pattern_gate.derive``):
  track_type (object: '芝'/'ダ'), distance (float), race_class_canon (object or None),
  field_size (float, entered horses), sex_group (object 'F'/'M'/None), is_summer (bool),
  interval_days (float, NaN = first start), prev_finish (float, NaN), tataki_2 (float 0/1/NaN),
  odds (float), q (float), fav_q (float), entropy_band_past (int, -1 = missing),
  p_rank (int), p_over_q (float), p_band_past (int, -1 = missing), ev (float), is_fav (bool).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import numpy as np

from .hashing import stable_hash

INF = float("inf")

#: available_at ordering — a pattern's tag is the latest of its conditions (+ horse rule).
AVAILABLE_AT_ORDER = ("pre_entry", "post_draw", "closing")
HORSE_RULES = ("fav", "cap11", "cap21")
FORBIDDEN_FIELDS = frozenset({"won", "finish_order", "payout", "result_status", "n_winners"})

# ---- band edges (all [lo, hi); research D4) ------------------------------------------------
ODDS_BANDS = (
    ("lt3", 0.0, 3.0),
    ("3-6", 3.0, 6.0),
    ("6-11", 6.0, 11.0),
    ("11-21", 11.0, 21.0),
    ("21-51", 21.0, 51.0),
    ("ge51", 51.0, INF),
)
Q_BANDS = (
    ("ge0.30", 0.30, INF),
    ("0.15-0.30", 0.15, 0.30),
    ("0.05-0.15", 0.05, 0.15),
    ("lt0.05", 0.0, 0.05),
)
PQ_BANDS = (
    ("ge1.5", 1.5, INF),
    ("1.2-1.5", 1.2, 1.5),
    ("1.0-1.2", 1.0, 1.2),
    ("0.8-1.0", 0.8, 1.0),
    ("lt0.8", 0.0, 0.8),
)
DIST_BANDS = (
    ("le1400", 0.0, 1401.0),
    ("1401-1800", 1401.0, 1801.0),
    ("1801-2200", 1801.0, 2201.0),
    ("gt2200", 2201.0, INF),
)
FIELD_BANDS = (("le8", 0.0, 9.0), ("9-13", 9.0, 14.0), ("ge14", 14.0, INF))
INTERVAL_BANDS = (
    ("le13", 0.0, 14.0),
    ("14-27", 14.0, 28.0),
    ("28-69", 28.0, 70.0),
    ("ge70", 70.0, INF),
)
PREVFIN_BANDS = (("1-3", 1.0, 4.0), ("4-5", 4.0, 6.0), ("6-9", 6.0, 10.0), ("ge10", 10.0, INF))
FAVQ_BANDS = (("ge0.50", 0.50, INF), ("0.30-0.50", 0.30, 0.50), ("lt0.30", 0.0, 0.30))
CLASS_CELLS = ("debut", "maiden", "C1", "C2", "C3", "OP")
SUMMER_MONTHS = (6, 7, 8, 9)

_CLASS_CANON = {
    "新馬": "debut",
    "未勝利": "maiden",
    "1勝": "C1",
    "１勝": "C1",
    "500万": "C1",
    "５００万": "C1",
    "2勝": "C2",
    "２勝": "C2",
    "1000万": "C2",
    "１０００万": "C2",
    "3勝": "C3",
    "３勝": "C3",
    "1600万": "C3",
    "１６００万": "C3",
    "ｵｰﾌﾟﾝ": "OP",
    "オープン": "OP",
    "OP": "OP",
    "OP(L)": "OP",
    "L": "OP",
    "Ｇ３": "OP",
    "Ｇ２": "OP",
    "Ｇ１": "OP",
    "G3": "OP",
    "G2": "OP",
    "G1": "OP",
    "GIII": "OP",
    "GII": "OP",
    "GI": "OP",
}


def canon_class(raw) -> str | None:
    """Canonicalise the split spellings of ``race_class`` (098) into the 6 cells; unknown → None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in _CLASS_CANON:
        return _CLASS_CANON[s]
    for k, v in _CLASS_CANON.items():
        if s.startswith(k):
            return v
    return None


@dataclass(frozen=True)
class Condition:
    axis: str  #: 'context' | 'market' | 'model'
    field: str
    op: str  #: 'in_band' | 'eq' | 'is_null' | 'is_true' | 'is_false'
    available_at: str
    lo: float | None = None
    hi: float | None = None
    value: Any = None

    def __post_init__(self) -> None:
        if self.field in FORBIDDEN_FIELDS:
            raise ValueError(f"ForbiddenField: predicate must not read {self.field!r}")
        if self.available_at not in AVAILABLE_AT_ORDER:
            raise ValueError(f"bad available_at {self.available_at!r}")
        if self.op not in ("in_band", "eq", "is_null", "is_true", "is_false"):
            raise ValueError(f"bad op {self.op!r}")

    def to_dict(self) -> dict:
        d = {
            "axis": self.axis,
            "field": self.field,
            "op": self.op,
            "available_at": self.available_at,
        }
        if self.op == "in_band":
            d["lo"] = self.lo
            d["hi"] = None if self.hi == INF else self.hi
            d["hi_inclusive"] = False
        if self.op == "eq":
            d["value"] = self.value
        return d

    def evaluate(self, arrays: dict) -> np.ndarray:
        x = arrays[self.field]
        if self.op == "in_band":
            xf = np.asarray(x, dtype=float)
            ok = np.isfinite(xf)
            return ok & (xf >= self.lo) & (xf < self.hi)
        if self.op == "eq":
            xo = np.asarray(x, dtype=object)
            return np.array([(v is not None) and (v == self.value) for v in xo], dtype=bool)
        if self.op == "is_null":
            xf = np.asarray(x, dtype=float)
            return ~np.isfinite(xf)
        xf = np.asarray(x, dtype=float)
        ok = np.isfinite(xf)
        if self.op == "is_true":
            return ok & (xf == 1.0)
        return ok & (xf == 0.0)


@dataclass(frozen=True)
class BuyPattern:
    pattern_id: str
    family: str
    conditions: tuple[Condition, ...]
    horse_rule: str | None
    label_ja: str
    race_level_only: bool = field(default=False)

    @property
    def available_at(self) -> str:
        idx = max([AVAILABLE_AT_ORDER.index(c.available_at) for c in self.conditions] + [0])
        if self.horse_rule is not None:
            idx = max(idx, AVAILABLE_AT_ORDER.index("closing"))
        return AVAILABLE_AT_ORDER[idx]

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "family": self.family,
            "conditions": [c.to_dict() for c in self.conditions],
            "horse_rule": self.horse_rule,
            "label_ja": self.label_ja,
            "available_at": self.available_at,
        }


@dataclass(frozen=True)
class Cell:
    key: str
    label: str
    conditions: tuple[Condition, ...]
    race_level: bool


def _band_cells(
    prefix: str, field_name: str, bands, axis: str, avail: str, race_level: bool, label_fmt: str
) -> list[Cell]:
    out = []
    for name, lo, hi in bands:
        out.append(
            Cell(
                f"{prefix}.{name}",
                label_fmt.format(name),
                (Condition(axis, field_name, "in_band", avail, lo=lo, hi=hi),),
                race_level,
            )
        )
    return out


def context_race_cells() -> list[Cell]:
    cells = [
        Cell(
            "C.track.turf",
            "芝",
            (Condition("context", "track_type", "eq", "pre_entry", value="芝"),),
            True,
        ),
        Cell(
            "C.track.dirt",
            "ダート",
            (Condition("context", "track_type", "eq", "pre_entry", value="ダ"),),
            True,
        ),
    ]
    cells += _band_cells("C.dist", "distance", DIST_BANDS, "context", "pre_entry", True, "距離 {}")
    for c in CLASS_CELLS:
        cells.append(
            Cell(
                f"C.class.{c}",
                f"クラス {c}",
                (Condition("context", "race_class_canon", "eq", "pre_entry", value=c),),
                True,
            )
        )
    cells += _band_cells(
        "C.field", "field_size", FIELD_BANDS, "context", "post_draw", True, "頭数 {}"
    )
    return cells


def context_horse_cells() -> list[Cell]:
    cells = []
    for sex, sex_ja in (("F", "牝馬"), ("M", "牡セ")):
        for summer, s_ja, op in ((True, "夏", "is_true"), (False, "非夏", "is_false")):
            cells.append(
                Cell(
                    f"C.sexseason.{sex}_{'summer' if summer else 'nonsummer'}",
                    f"{sex_ja}×{s_ja}",
                    (
                        Condition("context", "sex_group", "eq", "pre_entry", value=sex),
                        Condition("context", "is_summer", op, "pre_entry"),
                    ),
                    False,
                )
            )
    cells += _band_cells(
        "C.interval", "interval_days", INTERVAL_BANDS, "context", "pre_entry", False, "間隔 {} 日"
    )
    cells.append(
        Cell(
            "C.interval.first_start",
            "初出走",
            (Condition("context", "interval_days", "is_null", "pre_entry"),),
            False,
        )
    )
    cells += _band_cells(
        "C.prevfin", "prev_finish", PREVFIN_BANDS, "context", "pre_entry", False, "前走 {} 着"
    )
    return cells


def market_horse_cells() -> list[Cell]:
    cells = _band_cells("M.odds", "odds", ODDS_BANDS, "market", "closing", False, "オッズ {}")
    cells += _band_cells("M.q", "q", Q_BANDS, "market", "closing", False, "市場勝率 {}")
    return cells


def market_race_cells() -> list[Cell]:
    cells = _band_cells("M.favq", "fav_q", FAVQ_BANDS, "market", "closing", True, "1 番人気 q {}")
    for t in range(3):
        cells.append(
            Cell(
                f"M.entropy.t{t}",
                f"市場エントロピー 3 分位 {t}",
                (Condition("market", "entropy_band_past", "eq", "closing", value=t),),
                True,
            )
        )
    return cells


def model_cells() -> list[Cell]:
    cells = [
        Cell(
            "X.rank.1",
            "モデル 1 位",
            (Condition("model", "p_rank", "in_band", "closing", lo=1.0, hi=2.0),),
            False,
        ),
        Cell(
            "X.rank.2-3",
            "モデル 2〜3 位",
            (Condition("model", "p_rank", "in_band", "closing", lo=2.0, hi=4.0),),
            False,
        ),
        Cell(
            "X.rank.ge4",
            "モデル 4 位以下",
            (Condition("model", "p_rank", "in_band", "closing", lo=4.0, hi=INF),),
            False,
        ),
    ]
    cells += _band_cells("X.pq", "p_over_q", PQ_BANDS, "model", "closing", False, "p/q {}")
    for t, lab in ((0, "上位 10%"), (1, "10〜25%"), (2, "25% 未満")):
        cells.append(
            Cell(
                f"X.pband.{['top10', '10-25', 'rest'][t]}",
                f"厳密過去分位 {lab}",
                (Condition("model", "p_band_past", "eq", "closing", value=t),),
                False,
            )
        )
    cells.append(
        Cell(
            "X.ev_ge_1",
            "EV ≥ 1.0",
            (Condition("model", "ev", "in_band", "closing", lo=1.0, hi=INF),),
            False,
        )
    )
    return cells


def folklore_factors() -> list[Cell]:
    return [
        Cell(
            "fs",
            "牝馬×夏",
            (
                Condition("context", "sex_group", "eq", "pre_entry", value="F"),
                Condition("context", "is_summer", "is_true", "pre_entry"),
            ),
            False,
        ),
        Cell(
            "nt",
            "叩き 2 走目でない",
            (Condition("context", "tataki_2", "is_false", "pre_entry"),),
            False,
        ),
        Cell(
            "iv",
            "間隔 28〜69 日",
            (Condition("context", "interval_days", "in_band", "pre_entry", lo=28.0, hi=70.0),),
            False,
        ),
        Cell(
            "pf",
            "前走 6〜9 着",
            (Condition("context", "prev_finish", "in_band", "pre_entry", lo=6.0, hi=10.0),),
            False,
        ),
    ]


_EV_COND = Condition("model", "ev", "in_band", "closing", lo=1.0, hi=INF)
_H_LABEL = {"fav": "1 番人気", "cap11": "11 倍未満の全馬", "cap21": "21 倍未満の全馬"}


def _pattern(
    pid: str, family: str, conds, horse_rule, label: str, race_level_only: bool
) -> BuyPattern:
    if race_level_only and horse_rule is None:
        raise ValueError(f"{pid}: race-level-only pattern needs a horse rule")
    return BuyPattern(pid, family, tuple(conds), horse_rule, label, race_level_only)


@dataclass(frozen=True)
class Family:
    patterns: tuple[BuyPattern, ...]
    controls: tuple[BuyPattern, ...]
    control_aliases: dict

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "n_patterns": len(self.patterns),
            "patterns": [p.to_dict() for p in self.patterns],
            "controls": [c.to_dict() for c in self.controls],
            "control_aliases": dict(self.control_aliases),
        }

    def hash(self) -> str:
        return stable_hash(self.to_dict())


def enumerate_family() -> Family:
    """Deterministic enumeration of the 393-pattern family (research D4)."""
    pats: list[BuyPattern] = []
    c_race = context_race_cells()
    c_horse = context_horse_cells()
    m_horse = market_horse_cells()
    m_race = market_race_cells()
    x_cells = model_cells()

    # C single (horse-level, self-selecting): 13
    for c in c_horse:
        pats.append(_pattern(c.key, "C", c.conditions, None, c.label, False))
    # C x H: (15 + 13) x 3 = 84
    for c in c_race + c_horse:
        for h in HORSE_RULES:
            pats.append(
                _pattern(
                    f"{c.key}|H.{h}",
                    "C×H",
                    c.conditions,
                    h,
                    f"{c.label} かつ {_H_LABEL[h]}",
                    c.race_level,
                )
            )
    # C x EV: 28
    for c in c_race + c_horse:
        pats.append(
            _pattern(
                f"{c.key}|X.ev_ge_1",
                "C×EV",
                c.conditions + (_EV_COND,),
                None,
                f"{c.label} かつ EV ≥ 1.0",
                False,
            )
        )
    # M single (horse-level): 10
    for m in m_horse:
        pats.append(_pattern(m.key, "M", m.conditions, None, m.label, False))
    # M race-level x H: 18
    for m in m_race:
        for h in HORSE_RULES:
            pats.append(
                _pattern(
                    f"{m.key}|H.{h}", "M×H", m.conditions, h, f"{m.label} かつ {_H_LABEL[h]}", True
                )
            )
    # X single: 12
    for x in x_cells:
        pats.append(_pattern(x.key, "X", x.conditions, None, x.label, False))
    # M x X: (10 + 6) x 12 = 192
    for m in m_horse + m_race:
        for x in x_cells:
            pats.append(
                _pattern(
                    f"{m.key}|{x.key}",
                    "M×X",
                    m.conditions + x.conditions,
                    None,
                    f"{m.label} かつ {x.label}",
                    False,
                )
            )
    # F: 15 subsets x 3 H, minus the 9 that coincide with C x H = 36
    factors = folklore_factors()
    dup_single = {"fs", "iv", "pf"}
    for r in range(1, 5):
        for combo in combinations(factors, r):
            keys = [c.key for c in combo]
            if len(keys) == 1 and keys[0] in dup_single:
                continue
            conds = tuple(cond for c in combo for cond in c.conditions)
            label = " かつ ".join(c.label for c in combo)
            for h in HORSE_RULES:
                pats.append(
                    _pattern(
                        f"F.{'+'.join(keys)}|H.{h}",
                        "F",
                        conds,
                        h,
                        f"{label} かつ {_H_LABEL[h]}",
                        False,
                    )
                )

    ids = [p.pattern_id for p in pats]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate pattern ids")

    controls = (
        BuyPattern("no_bet", "control", (), None, "買わない(会計上の基準点 1.00)"),
        BuyPattern("favorite", "control", (), "fav", "1 番人気のみ"),
        BuyPattern("cap11_all", "control", (), "cap11", "11 倍未満の全馬"),
        BuyPattern("cap21_all", "control", (), "cap21", "21 倍未満の全馬"),
    )
    return Family(tuple(pats), controls, {"ev1_all": "X.ev_ge_1"})


def mask_for(pattern: BuyPattern, arrays: dict) -> np.ndarray:
    """Boolean selection mask over the rows; missing predicate inputs → False."""
    n = len(arrays["odds"])
    if pattern.pattern_id == "no_bet":
        return np.zeros(n, dtype=bool)
    m = np.ones(n, dtype=bool)
    for c in pattern.conditions:
        m &= c.evaluate(arrays)
    if pattern.horse_rule == "fav":
        m &= np.asarray(arrays["is_fav"], dtype=bool)
    elif pattern.horse_rule == "cap11":
        odds = np.asarray(arrays["odds"], dtype=float)
        m &= np.isfinite(odds) & (odds < 11.0)
    elif pattern.horse_rule == "cap21":
        odds = np.asarray(arrays["odds"], dtype=float)
        m &= np.isfinite(odds) & (odds < 21.0)
    return m


def pattern_from_dict(d: dict) -> BuyPattern:
    conds = []
    for c in d["conditions"]:
        conds.append(
            Condition(
                c["axis"],
                c["field"],
                c["op"],
                c["available_at"],
                lo=c.get("lo"),
                hi=(INF if c.get("hi") is None else c["hi"]),
                value=c.get("value"),
            )
        )
    return BuyPattern(
        d["pattern_id"],
        d["family"],
        tuple(conds),
        d.get("horse_rule"),
        d.get("label_ja", ""),
        race_level_only=False,
    )
