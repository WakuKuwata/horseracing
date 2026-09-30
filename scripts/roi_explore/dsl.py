"""パターン DSL(docs/roi-pattern-exploration-20260923/design.md §2)の検証とマスク化。numpy のみ。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import combinations, permutations

import numpy as np

RACE_FIELDS = {
    "year", "month", "dow", "venue_code", "race_number", "is_last_race", "is_first_race", "distance",
    "dist_band", "track_type", "going", "weather", "race_class_canon", "grade", "is_graded",
    "prize_money", "field_size", "n_fav_under_2", "n_odds_under_10", "fav_odds", "second_odds",
    "odds_gap12", "fav_q", "q_entropy_norm", "day_prev_n", "day_prev_fav_win_rate",
    "day_prev_winner_pop_mean", "day_prev_winner_style_front_share", "p_gap12", "model_fav_is_market_fav",
}
HORSE_FIELDS = {
    "odds", "q", "popularity", "odds_rank", "q_share_of_fav", "is_fav",
    "sex", "age", "frame", "horse_number", "weight", "weight_diff", "jockey_weight", "jockey_id",
    "trainer_id", "sire_line", "damsire_line", "is_debut", "career_starts",
    "career_wins", "career_win_rate", "career_top3_rate", "prev_finish", "prev2_finish", "prev3_finish",
    "avg_last3_finish", "best_finish_last5", "wins_last5", "top3_last5", "prev_popularity", "prev_odds",
    "prev_q", "prev_finish_pct", "prev_beat_market", "prev_field_size", "prev_distance",
    "prev_track_type", "prev_venue_code", "prev_class_canon", "dist_change", "class_change",
    "days_since_last", "tataki_2", "prev_weight", "weight_change_vs_prev", "prev_running_style",
    "prev_last3f_rank", "prev_margin_sec", "last_won", "jockey_change",
    "jockey_win_rate_365", "jockey_starts_365", "jockey_win_rate_all", "jockey_starts_all",
    "trainer_win_rate_365", "trainer_starts_365", "trainer_win_rate_all", "trainer_starts_all",
    "combo_starts_all", "combo_win_rate_all", "combo_starts_365", "combo_win_rate_365",
    "jockey_wins_today_before", "jockey_rides_today_before",
    "jockey_excess_365", "trainer_excess_365", "jockey_excess_all", "trainer_excess_all",
    "p", "p_top2", "p_top3", "p_rank", "ev", "p_over_q",
    "place_odds_low", "place_odds_high",
}
MODEL_FIELDS = {"p", "p_top2", "p_top3", "p_rank", "ev", "p_over_q", "p_gap12", "model_fav_is_market_fav"}
PLACE_PRICE_FIELDS = {"place_odds_low", "place_odds_high"}
FORBIDDEN = {"won", "finish_order", "n_winners", "place_hit", "dead_heat", "race_ok", "in_bundle",
             "finish_time", "margin_sec", "last_3f", "running_style", "last3f_rank"}
ALL_FIELDS = RACE_FIELDS | HORSE_FIELDS
STRING_FIELDS = {"venue_code", "dist_band", "track_type", "going", "weather", "race_class_canon", "grade",
                 "sex", "jockey_id", "trainer_id", "sire_line", "damsire_line", "prev_track_type",
                 "prev_venue_code", "prev_class_canon", "prev_running_style"}
OPS = {"eq", "ne", "in", "not_in", "ge", "gt", "le", "lt", "in_band", "is_true", "is_false", "isnull", "notnull"}
BET_TYPES = ("win", "place", "quinella", "wide", "exacta", "trio", "trifecta")
STAKES = ("flat", "inverse_odds", "edge")
UNORDERED = {"place", "quinella", "wide", "trio"}
COMBO_SIZE = {"place": 1, "quinella": 2, "wide": 2, "exacta": 2, "trio": 3, "trifecta": 3}


class DSLError(ValueError):
    pass


@dataclass(frozen=True)
class Cond:
    field: str
    op: str
    value: object = None
    lo: float | None = None
    hi: float | None = None

    @staticmethod
    def parse(d: dict, scope: str) -> "Cond":
        f = d.get("field"); op = d.get("op")
        if f in FORBIDDEN:
            raise DSLError(f"forbidden (result) field: {f}")
        if f not in ALL_FIELDS:
            raise DSLError(f"unknown field: {f}")
        if op not in OPS:
            raise DSLError(f"unknown op: {op}")
        if scope == "race" and f not in RACE_FIELDS:
            raise DSLError(f"horse-level field in race_filter: {f}")
        if op in ("in", "not_in"):
            v = d.get("value")
            if not isinstance(v, list) or not v:
                raise DSLError(f"{op} needs a non-empty list value ({f})")
            return Cond(f, op, tuple(v))
        if op == "in_band":
            lo, hi = d.get("lo"), d.get("hi")
            if lo is None or hi is None:
                raise DSLError(f"in_band needs lo/hi ({f})")
            return Cond(f, op, None, float(lo), float(hi))
        if op in ("eq", "ne", "ge", "gt", "le", "lt"):
            if "value" not in d:
                raise DSLError(f"{op} needs value ({f})")
            return Cond(f, op, d["value"])
        return Cond(f, op)

    def eval(self, arr: dict) -> np.ndarray:
        x = arr[self.field]
        op = self.op
        if self.field in STRING_FIELDS:
            xs = x
            if op == "eq":
                return xs == str(self.value)
            if op == "ne":
                return (xs != str(self.value)) & (xs != None)  # noqa: E711
            if op == "in":
                return np.isin(xs, [str(v) for v in self.value])
            if op == "not_in":
                return ~np.isin(xs, [str(v) for v in self.value]) & (xs != None)  # noqa: E711
            if op == "isnull":
                return xs == None  # noqa: E711
            if op == "notnull":
                return xs != None  # noqa: E711
            raise DSLError(f"op {op} not valid for string field {self.field}")
        xf = x.astype(float)
        ok = ~np.isnan(xf)
        if op == "eq":
            return ok & (xf == float(self.value))
        if op == "ne":
            return ok & (xf != float(self.value))
        if op == "in":
            return ok & np.isin(xf, [float(v) for v in self.value])
        if op == "not_in":
            return ok & ~np.isin(xf, [float(v) for v in self.value])
        if op == "ge":
            return ok & (xf >= float(self.value))
        if op == "gt":
            return ok & (xf > float(self.value))
        if op == "le":
            return ok & (xf <= float(self.value))
        if op == "lt":
            return ok & (xf < float(self.value))
        if op == "in_band":
            return ok & (xf >= self.lo) & (xf < self.hi)
        if op == "is_true":
            return ok & (xf == 1.0)
        if op == "is_false":
            return ok & (xf == 0.0)
        if op == "isnull":
            return ~ok
        if op == "notnull":
            return ok
        raise DSLError(op)


@dataclass(frozen=True)
class Pattern:
    pattern_id: str
    family: str
    label_ja: str
    bet_type: str
    race_filter: tuple
    horse_filter: tuple
    select_rule: str          # all | top_k
    select_by: str | None
    select_k: int
    select_asc: bool
    stake: str
    combo_type: str | None    # None | box | axis
    source: str = ""
    notes: str = ""

    @property
    def uses_model(self) -> bool:
        fs = {c.field for c in self.race_filter} | {c.field for c in self.horse_filter}
        if self.select_by:
            fs.add(self.select_by)
        return bool(fs & MODEL_FIELDS) or self.stake == "edge"

    @property
    def uses_place_price(self) -> bool:
        fs = {c.field for c in self.race_filter} | {c.field for c in self.horse_filter}
        if self.select_by:
            fs.add(self.select_by)
        return bool(fs & PLACE_PRICE_FIELDS)

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id, "family": self.family, "label_ja": self.label_ja,
            "bet_type": self.bet_type,
            "race_filter": [c.__dict__ for c in self.race_filter],
            "horse_filter": [c.__dict__ for c in self.horse_filter],
            "select": {"rule": self.select_rule, "by": self.select_by, "k": self.select_k, "asc": self.select_asc},
            "stake": self.stake, "combo": self.combo_type, "source": self.source, "notes": self.notes,
        }


def parse_pattern(d: dict, source: str = "") -> Pattern:
    pid = d.get("pattern_id")
    if not pid or not isinstance(pid, str):
        raise DSLError("missing pattern_id")
    bt = d.get("bet_type", "win")
    if bt not in BET_TYPES:
        raise DSLError(f"bad bet_type {bt}")
    rf = tuple(Cond.parse(c, "race") for c in (d.get("race_filter") or []))
    hf = tuple(Cond.parse(c, "horse") for c in (d.get("horse_filter") or []))
    sel = d.get("select") or {"rule": "all"}
    rule = sel.get("rule", "all")
    by = sel.get("by"); k = int(sel.get("k", 1) or 1); asc = bool(sel.get("asc", False))
    if rule == "top_k":
        if by not in ALL_FIELDS or by in STRING_FIELDS:
            raise DSLError(f"top_k needs numeric by field (got {by})")
        if by in FORBIDDEN:
            raise DSLError("forbidden by field")
        if k < 1 or k > 8:
            raise DSLError(f"bad k {k}")
    elif rule == "all":
        by = None; k = 0
    else:
        raise DSLError(f"bad select rule {rule}")
    stake = d.get("stake", "flat")
    if stake not in STAKES:
        raise DSLError(f"bad stake {stake}")
    combo = d.get("combo")
    ctype = None
    if bt != "win":
        ctype = (combo or {}).get("type", "box") if isinstance(combo, dict) else (combo or "box")
        if ctype not in ("box", "axis"):
            raise DSLError(f"bad combo type {ctype}")
        if stake != "flat":
            stake = "flat"
    if not rf and not hf and rule == "all" and bt == "win":
        raise DSLError("empty pattern (bets on every horse)")
    return Pattern(pid, str(d.get("family", "")), str(d.get("label_ja", pid)), bt, rf, hf, rule, by, k, asc,
                   stake, ctype, source, str(d.get("notes", "") or ""))


def load_pattern_files(paths) -> tuple[list[Pattern], list[dict]]:
    pats, rejects, seen = [], [], set()
    for path in paths:
        try:
            obj = json.loads(open(path).read())
        except Exception as e:  # noqa: BLE001
            rejects.append({"file": str(path), "error": f"json: {e}"})
            continue
        items = obj.get("patterns", obj) if isinstance(obj, dict) else obj
        for d in items:
            try:
                p = parse_pattern(d, source=str(path).rsplit("/", 1)[-1])
            except (DSLError, TypeError, ValueError, AttributeError) as e:
                rejects.append({"file": str(path), "pattern_id": str(d.get("pattern_id") if isinstance(d, dict) else d)[:80],
                                "error": str(e)})
                continue
            if p.pattern_id in seen:
                rejects.append({"file": str(path), "pattern_id": p.pattern_id, "error": "duplicate id"})
                continue
            seen.add(p.pattern_id)
            pats.append(p)
    return pats, rejects


# ---------------------------------------------------------------------------------------------
# mask / selection
# ---------------------------------------------------------------------------------------------


def base_mask(p: Pattern, arr: dict) -> np.ndarray:
    m = np.ones(len(arr["race_id"]), dtype=bool)
    for c in p.race_filter:
        m &= c.eval(arr)
    for c in p.horse_filter:
        m &= c.eval(arr)
    return m


def rank_within_race(idx: np.ndarray, key: np.ndarray, race_idx: np.ndarray, tiebreak: np.ndarray):
    """Return (sorted_idx, rank) — rank is 0-based position within race by key asc, tiebreak asc."""
    order = np.lexsort((tiebreak[idx], key, race_idx[idx]))
    sidx = idx[order]
    r = race_idx[sidx]
    start = np.r_[True, r[1:] != r[:-1]]
    pos = np.arange(len(r))
    grp_start = np.maximum.accumulate(np.where(start, pos, 0))
    return sidx, pos - grp_start


def select(p: Pattern, arr: dict, m: np.ndarray, *, need_rank: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Apply select rule. Returns (mask, rank) where rank is 0-based within-race order (axis = 0).

    For rule=all the within-race order is by odds asc (most backed first)."""
    race_idx = arr["_race_idx"]; hn = arr["horse_number"].astype(float)
    idx = np.flatnonzero(m)
    rank = np.full(len(m), -1, dtype=np.int32)
    if p.select_rule == "all" and not need_rank:
        return m, rank
    if p.select_rule == "all":
        key = arr["odds"].astype(float)[idx]
        sidx, r = rank_within_race(idx, key, race_idx, hn)
        rank[sidx] = r
        return m, rank
    by = arr[p.select_by].astype(float)
    keep = idx[~np.isnan(by[idx])]
    key = by[keep] if p.select_asc else -by[keep]
    sidx, r = rank_within_race(keep, key, race_idx, hn)
    chosen = sidx[r < p.select_k]
    out = np.zeros_like(m); out[chosen] = True
    rank[chosen] = r[r < p.select_k]
    return out, rank


def combos_for_race(horses: list[int], bet_type: str, combo_type: str) -> list[tuple]:
    """horses ordered by rank (axis first). Returns selection tuples (sorted for unordered types)."""
    size = COMBO_SIZE[bet_type]
    if bet_type == "place":
        return [(h,) for h in horses]
    if len(horses) < size:
        return []
    if combo_type == "box":
        if bet_type in UNORDERED:
            return [tuple(sorted(c)) for c in combinations(horses, size)]
        return list(permutations(horses, size))
    # axis: first horse fixed, rest as targets
    ax, rest = horses[0], horses[1:]
    if size == 2:
        if bet_type in UNORDERED:
            return [tuple(sorted((ax, t))) for t in rest]
        return [(ax, t) for t in rest]
    if bet_type in UNORDERED:
        return [tuple(sorted((ax,) + c)) for c in combinations(rest, 2)]
    return [(ax,) + c for c in permutations(rest, 2)]
