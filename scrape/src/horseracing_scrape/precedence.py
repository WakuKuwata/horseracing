"""二重情報源(netkeiba / JRA 公式)の書込み規則 — 純関数(Feature 176, contracts/precedence.md).

ここは**両 writer 共通の正本**。DB は読まない・書かない。``upsert.py`` が ``races`` 行を
``FOR UPDATE`` で取り、ロック下で再読込した状態をこれらの関数に渡し、返った判定どおりに値と来歴を
同一 savepoint で書く。netkeiba 経路(``upsert_entries`` / ``update_odds`` …)と公式経路
(``jra/upsert_jra.py``)はこの同じ関数を呼ぶので、「どちらの経路が後から来ても」規則が守られる。

用語: ``stored`` = ロック下で再読込した DB の現在値、``incoming`` = 今回の入力。
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from horseracing_db.enums import EntryStatus, Source

JRA_OFFICIAL = "jra_official"  # == Source.JRA_OFFICIAL once db enums gain it (Phase 3)

Decision = Literal["write", "keep", "reject"]

# ---------------------------------------------------------------------------
# §2 衝突検査
# ---------------------------------------------------------------------------

COLLISION_NUMBER = "collision:number"
COLLISION_ID = "collision:id"
COLLISION_EXISTING_DUP = "collision:existing_dup"
COLLISION_INPUT_DUP = "collision:input_dup"
COLLISION_NULL_NUMBER = "collision:null_number"
COLLISION_SURPLUS = "collision:surplus"


@dataclass(frozen=True)
class EntryRow:
    """The three facts the collision check needs about one race_horses row (existing or
    incoming)."""

    horse_id: str
    horse_number: int | None
    entry_status: str

    @property
    def started(self) -> bool:
        return self.entry_status == EntryStatus.STARTED


@dataclass(frozen=True)
class Collision:
    kind: str
    horse_number: int | None = None
    existing_horse_id: str | None = None
    incoming_horse_id: str | None = None


@dataclass(frozen=True)
class CollisionReport:
    collisions: tuple[Collision, ...] = ()
    merge_candidates: tuple[tuple[str, str], ...] = ()
    approved_mapping_pending_rekey: tuple[tuple[str, str], ...] = ()

    @property
    def quarantined(self) -> bool:
        return bool(self.collisions)

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({c.kind for c in self.collisions}))


def check_entry_collisions(
    existing: Iterable[EntryRow], incoming: Iterable[EntryRow]
) -> CollisionReport:
    """contracts/precedence.md §2. One hit → the whole race is quarantined by the caller.

    * 同じ馬番に別の horse_id(既存 vs 入力)
    * 同じ horse_id に別の馬番(取消で None になった馬番は除く)
    * 既存行の中に重複馬番(started 同士)
    * 入力の中に重複馬番・重複 horse_id
    * 馬番 NULL の既存 started 行
    * 入力に無い既存 started 行(余剰)
    """
    existing = tuple(existing)
    incoming = tuple(incoming)
    out: list[Collision] = []

    # input duplicates
    seen_num: dict[int, str] = {}
    seen_id: set[str] = set()
    for row in incoming:
        if row.horse_id in seen_id:
            out.append(
                Collision(COLLISION_INPUT_DUP, row.horse_number, incoming_horse_id=row.horse_id)
            )
        seen_id.add(row.horse_id)
        if row.horse_number is not None:
            if row.horse_number in seen_num and seen_num[row.horse_number] != row.horse_id:
                out.append(
                    Collision(
                        COLLISION_INPUT_DUP,
                        row.horse_number,
                        existing_horse_id=seen_num[row.horse_number],
                        incoming_horse_id=row.horse_id,
                    )
                )
            seen_num.setdefault(row.horse_number, row.horse_id)

    # existing hygiene (started rows only)
    ex_started = [r for r in existing if r.started]
    ex_by_num: dict[int, str] = {}
    for row in ex_started:
        if row.horse_number is None:
            out.append(Collision(COLLISION_NULL_NUMBER, None, existing_horse_id=row.horse_id))
            continue
        if row.horse_number in ex_by_num and ex_by_num[row.horse_number] != row.horse_id:
            out.append(
                Collision(
                    COLLISION_EXISTING_DUP,
                    row.horse_number,
                    existing_horse_id=ex_by_num[row.horse_number],
                    incoming_horse_id=row.horse_id,
                )
            )
        ex_by_num.setdefault(row.horse_number, row.horse_id)

    ex_by_id: dict[str, EntryRow] = {r.horse_id: r for r in existing}
    ex_all_by_num: dict[int, str] = {
        r.horse_number: r.horse_id for r in existing if r.horse_number is not None
    }
    for row in incoming:
        # 同じ馬番に別の horse_id
        if row.horse_number is not None:
            other = ex_all_by_num.get(row.horse_number)
            if other is not None and other != row.horse_id:
                out.append(Collision(COLLISION_NUMBER, row.horse_number, other, row.horse_id))
        # 同じ horse_id に別の馬番(どちらかが None=取消で失われた馬番 は除く)
        ex = ex_by_id.get(row.horse_id)
        if (
            ex is not None
            and ex.horse_number is not None
            and row.horse_number is not None
            and ex.horse_number != row.horse_number
        ):
            out.append(Collision(COLLISION_ID, ex.horse_number, ex.horse_id, row.horse_id))

    # 入力に無い既存 started 行(余剰)
    incoming_ids = {r.horse_id for r in incoming}
    for row in ex_started:
        if row.horse_id not in incoming_ids:
            out.append(
                Collision(COLLISION_SURPLUS, row.horse_number, existing_horse_id=row.horse_id)
            )

    return CollisionReport(collisions=tuple(out))


@dataclass(frozen=True)
class HorseIdentity:
    horse_id: str
    name_key: str
    birth_year: int | None


def pair_merge_candidates(
    existing: Iterable[HorseIdentity],
    incoming: Iterable[HorseIdentity],
    *,
    approved: Iterable[tuple[str, str]] = (),
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """(nk: id, 公式 id) の対を 名前 key + 生年の一致で列挙する(報告専用・書かない).

    ``approved`` に (nk: id, canonical) が含まれる対は ``approved_mapping_pending_rekey`` に分ける。
    どちらも本 feature では書かない(再キーは別 spec・FR-011/FR-024)。生年が片方でも None の対は
    候補にしない(067 の ``classify_identity`` と同じ=欠損は矛盾ではないが証拠でもない)。
    """
    approved_set = set(approved)
    merge: list[tuple[str, str]] = []
    pending: list[tuple[str, str]] = []
    canonical = [r for r in incoming if not r.horse_id.startswith("nk:")]
    surrogates = [r for r in existing if r.horse_id.startswith("nk:")]
    for c in canonical:
        if not c.name_key or c.birth_year is None:
            continue
        for s in surrogates:
            if s.name_key == c.name_key and s.birth_year == c.birth_year:
                pair = (s.horse_id, c.horse_id)
                (pending if pair in approved_set else merge).append(pair)
    return tuple(merge), tuple(pending)


# ---------------------------------------------------------------------------
# §3 項目別遷移表
# ---------------------------------------------------------------------------

GROUP_ENTRY_STATUS = "entry_status"
GROUP_JOCKEY = "jockey"
GROUP_TRAINER = "trainer"
GROUP_ENTRY_STATIC = "entry_static"
GROUP_BODY_WEIGHT = "body_weight"
GROUP_RACE_STATIC = "race_static"
GROUP_POST_TIME = "post_time"

HORSE_GROUPS = (
    GROUP_ENTRY_STATIC,
    GROUP_JOCKEY,
    GROUP_TRAINER,
    GROUP_BODY_WEIGHT,
    GROUP_ENTRY_STATUS,
)
RACE_GROUPS = (GROUP_RACE_STATIC, GROUP_POST_TIME)

PAGE_ENTRIES = "entries"
PAGE_CHANGES = "changes"
PAGE_RESULTS = "results"
PAGE_ODDS = "odds"

_STATIC_GROUPS = frozenset({GROUP_TRAINER, GROUP_ENTRY_STATIC, GROUP_RACE_STATIC, GROUP_POST_TIME})
_TIMED_GROUPS = frozenset({GROUP_JOCKEY, GROUP_BODY_WEIGHT})


@dataclass(frozen=True)
class TransitionContext:
    incoming_source: str
    incoming_observed_at: datetime.datetime | None = None
    incoming_page_kind: str = PAGE_ENTRIES
    stored_source: str | None = None
    stored_observed_at: datetime.datetime | None = None


@dataclass(frozen=True)
class Transition:
    decision: Decision
    #: True when a stored value exists, differs from the incoming one, and the group is static
    #: (entry_static / trainer / race_static / post_time). Counted as ``static_mismatch`` whether
    #: the decision was keep (netkeiba vs official-owned) or write (official correcting).
    mismatch: bool = False
    reason: str = ""


def _is_pair(value) -> bool:
    return isinstance(value, tuple) and len(value) == 2


def field_transition(group: str, stored, incoming, ctx: TransitionContext) -> Transition:
    """contracts/precedence.md §3. ``body_weight`` values are (weight, weight_diff) pairs."""
    if group == GROUP_BODY_WEIGHT:
        if incoming is None or (_is_pair(incoming) and incoming[0] is None and incoming[1] is None):
            return Transition("keep", reason="missing")
        if not _is_pair(incoming):
            raise ValueError("body_weight incoming must be a (weight, weight_diff) pair")
    elif incoming is None:
        return Transition("keep", reason="missing")

    if group == GROUP_ENTRY_STATUS:
        return _entry_status_transition(stored, incoming, ctx)

    if group in _TIMED_GROUPS:
        if (
            ctx.incoming_observed_at is not None
            and ctx.stored_observed_at is not None
            and ctx.incoming_observed_at < ctx.stored_observed_at
        ):
            return Transition("reject", reason="older_observation")
        return Transition("write")

    if group in _STATIC_GROUPS:
        differs = stored is not None and stored != incoming
        if ctx.stored_source == JRA_OFFICIAL and ctx.incoming_source == Source.NETKEIBA:
            return Transition("keep", mismatch=differs, reason="official_owned")
        return Transition("write", mismatch=differs)

    raise ValueError(f"unknown field group: {group!r}")


def _entry_status_transition(stored, incoming, ctx: TransitionContext) -> Transition:
    if stored is None:
        return Transition("write")
    if stored == incoming:
        return Transition("keep", reason="unchanged")
    if stored == EntryStatus.STARTED and incoming in EntryStatus.NON_STARTERS:
        return Transition("write")
    if stored in EntryStatus.NON_STARTERS and incoming == EntryStatus.STARTED:
        return Transition("reject", reason="started_regression")  # どの経路からも拒否
    if stored in EntryStatus.NON_STARTERS and incoming in EntryStatus.NON_STARTERS:
        if ctx.incoming_page_kind in (PAGE_CHANGES, PAGE_RESULTS):
            return Transition("write", reason="correction")
        return Transition("keep", reason="entries_cannot_correct")
    return Transition("reject", reason="unknown_status")


# ---------------------------------------------------------------------------
# §4 オッズの共通契約
# ---------------------------------------------------------------------------

KIND_WIN = "win"
KIND_PLACE = "place"

OddsAction = Literal["write", "skip", "settled_fill"]


@dataclass(frozen=True)
class OddsSnapshot:
    """Either the stored state (``source`` = provenance source, None when unknown) or the input."""

    official_at: datetime.datetime | None
    source: str | None
    values: Mapping[int, object] = field(default_factory=dict)  # horse_number -> value


@dataclass(frozen=True)
class OddsDecision:
    action: OddsAction
    reason: str
    adopted_source: str | None = None
    conflict: bool = False


def odds_decision(
    kind: str,
    stored: OddsSnapshot,
    incoming: OddsSnapshot,
    started: Iterable[int],
    *,
    has_results: bool,
) -> OddsDecision:
    """contracts/precedence.md §4, evaluated top to bottom.

    ``adopted_source`` is the provenance source to record when the action is ``write``: on a
    same-time same-value confirmation the official priority is preserved (public→netkeiba does
    NOT hand the adoption back to netkeiba; codex Q2-5).
    """
    if kind not in (KIND_WIN, KIND_PLACE):
        raise ValueError(f"unknown odds kind: {kind!r}")
    src = incoming.source
    if has_results:
        if kind == KIND_WIN and src == Source.NETKEIBA:
            return OddsDecision("settled_fill", "results_settled")
        return OddsDecision("skip", "results_settled")

    started_set = set(started)
    if not started_set or not started_set <= set(incoming.values):
        return OddsDecision("skip", "partial_field")

    if incoming.official_at is None and stored.official_at is not None:
        return OddsDecision("skip", "no_official_at")

    if stored.official_at is not None and incoming.official_at is not None:
        if incoming.official_at < stored.official_at:
            return OddsDecision("skip", "stale")
        if incoming.official_at == stored.official_at:
            same = all(stored.values.get(n) == incoming.values.get(n) for n in started_set)
            if same:
                adopted = JRA_OFFICIAL if JRA_OFFICIAL in (stored.source, src) else src
                return OddsDecision("write", "same_time_same_value", adopted_source=adopted)
            if src == JRA_OFFICIAL:
                return OddsDecision(
                    "write", "same_time_conflict", adopted_source=src, conflict=True
                )
            if stored.source == JRA_OFFICIAL:
                return OddsDecision("skip", "same_time_conflict", conflict=True)
            return OddsDecision("write", "same_time_both_netkeiba", adopted_source=src)

    # newer, or nothing stored yet (stored.official_at None)
    return OddsDecision("write", "newer", adopted_source=src)


# ---------------------------------------------------------------------------
# §6 払戻(券種ごとの集合)
# ---------------------------------------------------------------------------

PayoutAction = Literal["replace", "noop", "report", "skip"]


@dataclass(frozen=True)
class PayoutDecision:
    action: PayoutAction
    reason: str
    to_delete: tuple = ()
    to_update: tuple = ()
    to_confirm: tuple = ()  # same value: source/provenance only


def payout_block_decision(
    state: str,
    stored_rows: Mapping[object, object],
    incoming_lines: Mapping[object, object],
    *,
    stored_source: str | None,
    incoming_source: str,
) -> PayoutDecision:
    """contracts/precedence.md §6. Keys are selection tuples (or horse_number for 単勝)."""
    if stored_source == JRA_OFFICIAL and incoming_source == Source.NETKEIBA:
        return PayoutDecision("skip", "skipped_official_payouts")
    if state == "complete":
        if not incoming_lines:
            return PayoutDecision("skip", "complete_but_empty")
        to_delete = tuple(k for k in stored_rows if k not in incoming_lines)
        to_update = tuple(k for k, v in incoming_lines.items() if stored_rows.get(k, _MISSING) != v)
        to_confirm = tuple(
            k for k, v in incoming_lines.items() if stored_rows.get(k, _MISSING) == v
        )
        return PayoutDecision("replace", "complete", to_delete, to_update, to_confirm)
    if state == "not_published":
        return PayoutDecision("noop", "not_published")
    if state in ("not_sold", "refund_only"):
        return PayoutDecision("report", state)
    if state == "parse_failed":
        return PayoutDecision("skip", "parse_failed")
    raise ValueError(f"unknown payout block state: {state!r}")


class _Missing:
    pass


_MISSING = _Missing()
