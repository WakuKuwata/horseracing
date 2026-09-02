"""Read-side assembly for purchase records (Feature 106): fold + per-bet settlement.

Read-time computation only (research D3) — nothing here is persisted, so a correction, a voided
row or a dividend arriving later is reflected on the next GET without any stored state to
invalidate. Settlement uses OFFICIAL results for the hit decision (011 semantics via
``settlement.is_hit``) and official prices first; a hit whose official dividend is absent is
settled on the 010 estimated odds and MARKED (clarify Q2 = B: counted, double-pseudo badged,
auto-replaced when the real dividend arrives).
"""

from __future__ import annotations

from dataclasses import dataclass

from horseracing_db.models import ExoticOdds, PurchaseRecord, RaceHorse, RaceResult
from horseracing_probability.market_odds import (
    MarketOddsError,
    default_market_stage_discount,
    estimate_market_odds,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .purchase_fold import EffectiveRecord, fold_purchase_records
from .settlement import is_hit

#: settlement states (contracts/recording-and-comparison.md)
PENDING = "pending"
SETTLED_REAL = "settled_real"
SETTLED_ESTIMATED = "settled_estimated"
REFUNDED = "refunded"
UNSETTLEABLE = "unsettleable"   # win with no official odds (U4: the 010 estimate is win-derived)


@dataclass(frozen=True)
class SettledBet:
    bet_type: str
    selection: list[int]
    amount_yen: int
    status: str
    hit: bool | None            #: None while pending / refunded / unsettleable
    payout_yen: float | None    #: gross return (0 for a settled miss)
    is_estimated: bool          #: True only for SETTLED_ESTIMATED (double-pseudo)


def race_context(session: Session, race_id: str) -> dict:
    """Number-keyed result context for one race (read-only)."""
    rows = session.execute(
        select(RaceHorse.horse_number, RaceHorse.entry_status, RaceHorse.odds,
               RaceResult.finish_order, RaceResult.result_status)
        .join(RaceResult,
              (RaceResult.race_id == RaceHorse.race_id)
              & (RaceResult.horse_id == RaceHorse.horse_id), isouter=True)
        .where(RaceHorse.race_id == race_id)
    ).all()
    finish: dict[int, int] = {}
    statuses: dict[int, str | None] = {}
    win_odds: dict[int, float] = {}
    n_started = 0
    has_results = False
    for number, entry_status, odds, order, result_status in rows:
        if number is None:
            continue
        n = int(number)
        entry = getattr(entry_status, "value", entry_status)
        if entry == "started":
            n_started += 1
            if odds is not None:
                win_odds[n] = float(odds)
        statuses[n] = getattr(result_status, "value", result_status)
        if result_status is not None:
            has_results = True
        if getattr(result_status, "value", result_status) == "finished" and order is not None:
            finish[n] = int(order)
    dividends: dict[tuple[str, tuple[int, ...]], float] = {}
    for x in session.scalars(select(ExoticOdds).where(ExoticOdds.race_id == race_id)):
        bt = getattr(x.bet_type, "value", x.bet_type)
        dividends[(str(bt), tuple(int(i) for i in x.selection))] = float(x.odds)
    return {"finish": finish, "statuses": statuses, "win_odds": win_odds,
            "n_started": n_started, "has_results": has_results, "dividends": dividends}


def _estimated_multiplier(ctx: dict, bet_type: str, selection: list[int]) -> float | None:
    """010 estimated odds for one selection (win-odds derived; None when unavailable)."""
    if not ctx["win_odds"] or len(ctx["win_odds"]) < 2:
        return None
    canon = {str(n): o for n, o in ctx["win_odds"].items()}
    try:
        # same estimator configuration as the /odds display (049 fitted stage discount) so a
        # settled_estimated payout matches the estimated odds the product showed
        eo = estimate_market_odds(
            canon, field_size=len(canon), stage_discount=default_market_stage_discount()
        )
    except MarketOddsError:
        return None
    key_o = tuple(str(n) for n in selection)
    key_u = frozenset(str(n) for n in selection)
    table = {"place": eo.place, "exacta": eo.exacta, "quinella": eo.quinella,
             "wide": eo.wide, "trio": eo.trio, "trifecta": eo.trifecta}.get(bet_type)
    if table is None:
        return None
    if bet_type == "place":
        v = table.get(str(selection[0]))
    elif bet_type in ("exacta", "trifecta"):
        v = table.get(key_o)
    else:
        v = table.get(key_u)
    return float(v) if v is not None else None


def settle_bet(ctx: dict, bet: dict) -> SettledBet:
    bet_type = str(bet["bet_type"])
    selection = [int(x) for x in bet["selection"]]
    amount = int(bet["amount_yen"])
    if not ctx["has_results"]:
        return SettledBet(bet_type, selection, amount, PENDING, None, None, False)
    # 返還: a selected number with no result row at all (取消・除外 — never ran)
    if any(n not in ctx["statuses"] or ctx["statuses"][n] is None for n in selection):
        return SettledBet(bet_type, selection, amount, REFUNDED, None, float(amount), False)
    hit = is_hit(bet_type, selection, ctx["finish"], ctx["n_started"])
    if hit is None:   # place with <=4 starters — not on sale; treat as refunded
        return SettledBet(bet_type, selection, amount, REFUNDED, None, float(amount), False)
    if not hit:
        return SettledBet(bet_type, selection, amount, SETTLED_REAL, False, 0.0, False)
    if bet_type == "win":
        odds = ctx["win_odds"].get(selection[0])
        if odds is None:   # U4: win estimate is itself win-odds derived — cannot fall back
            return SettledBet(bet_type, selection, amount, UNSETTLEABLE, True, None, False)
        return SettledBet(bet_type, selection, amount, SETTLED_REAL, True,
                          amount * float(odds), False)
    div = ctx["dividends"].get((bet_type, tuple(selection)))
    if div is not None:
        return SettledBet(bet_type, selection, amount, SETTLED_REAL, True, amount * div, False)
    est = _estimated_multiplier(ctx, bet_type, selection)
    if est is None:
        return SettledBet(bet_type, selection, amount, UNSETTLEABLE, True, None, False)
    return SettledBet(bet_type, selection, amount, SETTLED_ESTIMATED, True, amount * est, True)


def load_race_rows(session: Session, date_from, date_to) -> dict[str, list[PurchaseRecord]]:
    from horseracing_db.models import Race
    rows = session.scalars(
        select(PurchaseRecord)
        .join(Race, Race.race_id == PurchaseRecord.race_id)
        .where(Race.race_date >= date_from, Race.race_date <= date_to)
        .order_by(PurchaseRecord.recorded_at, PurchaseRecord.purchase_record_id)
    ).all()
    by_race: dict[str, list[PurchaseRecord]] = {}
    for r in rows:
        by_race.setdefault(r.race_id, []).append(r)
    return by_race


def effective(session: Session, date_from, date_to) -> dict[str, EffectiveRecord | None]:
    return {rid: fold_purchase_records(rows)
            for rid, rows in load_race_rows(session, date_from, date_to).items()}


# --- comparison assembly (US2) -------------------------------------------------------------

#: fixed disclosure note keys (contract: notes carries stable phrasing keys, front owns the copy)
COMPARISON_NOTES = [
    "counterfactual_snapshot",   # 政策線は凍結スナップショット精算=反実仮想(075 命名)
    "pre_tax",                   # 税引前
    "asymmetric_scope",          # 実購入=全券種 / 政策線=単勝のみ(clarify Q1)
    "coverage_denominator_all_races",  # 記録率の分母=期間内の全開催レース(clarify Q3)
]

ESTIMATOR_PROVENANCE = (
    "estimate_market_odds (010, win-odds derived, 049 market stage discount, double-pseudo)"
)


def _snapshot_win_bets(snapshot: dict | None) -> list[dict] | None:
    """Win bets from a presented snapshot; tolerant of camel/snake key spelling.

    Returns None when the snapshot itself is absent (freeform / presentation_unavailable):
    "no snapshot" is UNKNOWN, not zero (codex Q2), so the policy point must be null there.
    """
    if snapshot is None:
        return None
    bets = snapshot.get("bets")
    if not isinstance(bets, list):
        return None
    out = []
    for b in bets:
        if not isinstance(b, dict):
            continue
        bt = b.get("bet_type", b.get("betType"))
        if bt != "win":
            continue
        out.append({
            "selection": b.get("selection") or [],
            "amount_yen": b.get("amount_yen", b.get("amountYen")),
            "odds_used": b.get("odds_used", b.get("oddsUsed")),
        })
    return out


def settle_policy_net(ctx: dict, snapshot: dict | None) -> float | None:
    """Counterfactual-snapshot net for the policy line (win-only, FROZEN odds).

    None = not computable (no snapshot, missing frozen odds/amount, or race not settled).
    Zero win bets in a snapshot is a VERIFIED zero (skip / no_recommendation) and returns 0.0.
    """
    bets = _snapshot_win_bets(snapshot)
    if bets is None or not ctx["has_results"]:
        return None
    net = 0.0
    for b in bets:
        sel = [int(x) for x in b["selection"]]
        if len(sel) != 1 or b["amount_yen"] is None:
            return None
        amount = int(b["amount_yen"])
        n = sel[0]
        if n not in ctx["statuses"] or ctx["statuses"][n] is None:
            continue   # 返還 — stake back, net 0
        hit = is_hit("win", sel, ctx["finish"], ctx["n_started"])
        if hit:
            if b["odds_used"] is None:
                return None   # frozen odds missing — cannot value the counterfactual
            net += amount * float(b["odds_used"]) - amount
        else:
            net -= amount
    return net


@dataclass
class RaceComparison:
    race_id: str
    race_date: object
    actual_net_yen: float | None      #: None while the race is pending/unsettleable
    policy_net_yen: float | None      #: None when the policy line is not computable
    pending: bool
    pending_amount_yen: int
    n_pending_bets: int
    n_estimated: int
    estimated_amount_yen: int
    was_post_hoc: bool
    n_corrections: int
    presentation_unavailable: bool


def compare_race(ctx: dict, eff: EffectiveRecord, race_id: str, race_date,
                 *, scope: str, snapshot: dict | None, base_kind: str) -> RaceComparison:
    """One race's comparison row.

    ``snapshot``/``base_kind`` come from the BASE row (corrections never re-present, so the
    fold's current row may be a correction that carries no snapshot of its own).
    """
    bets = eff.bets or []
    if scope == "win_only":
        bets = [b for b in bets if str(b.get("bet_type")) == "win"]
    settled = [settle_bet(ctx, b) for b in bets]
    open_bets = [s for s in settled if s.status in (PENDING, UNSETTLEABLE)]
    pending = bool(open_bets)
    actual_net: float | None = None
    n_est = 0
    est_amount = 0
    if not pending:
        actual_net = 0.0
        for s in settled:
            if s.status == REFUNDED:
                continue
            actual_net += (s.payout_yen or 0.0) - s.amount_yen
            if s.is_estimated:
                n_est += 1
                est_amount += s.amount_yen
    return RaceComparison(
        race_id=race_id, race_date=race_date,
        actual_net_yen=actual_net,
        policy_net_yen=None if snapshot is None else settle_policy_net(ctx, snapshot),
        pending=pending,
        pending_amount_yen=sum(s.amount_yen for s in open_bets),
        n_pending_bets=len(open_bets),
        n_estimated=n_est, estimated_amount_yen=est_amount,
        was_post_hoc=not eff.result_pending_at_record,
        n_corrections=eff.n_corrections,
        presentation_unavailable=(base_kind == "presentation_unavailable"),
    )
