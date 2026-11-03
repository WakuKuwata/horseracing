"""公式 ID → 既存出走行の ID(Feature 176 FR-011a・利用者決定 2026-11-03).

2026 年の新馬は netkeiba 経路が ``nk:X`` で登録しており、公式コード ``X`` をそのまま書くと同じ馬番に
別 ID が並んでレースが隔離される(11/01 は 24 レース中 22)。正規 ``X`` を新たに作れば ``nk:X`` に
ある過去走と履歴が割れる。そこで公式経路だけ、**同じレースの既存出走行**を経由した別名を認める:

* 公式経路は出走行もマスタ行も作らない(出馬表は netkeiba が先・出走行 0 件のレースは見送り)。
* 公式の馬 ``X`` は、同じレースの既存行のうち ID が ``X`` か ``nk:X`` の 1 行にだけ対応づける。
* 既存行が ``nk:X`` なら、正規 ``X`` の馬マスタが無く、``id_mappings`` が判断済み(mapped /
  conflict / rejected)でなく、``nk:X`` の馬名が公式の馬名と名前照合キーで一致するときだけ使う。
* 騎手・調教師は既存行の ID が ``J`` か ``nk:J`` ならそのまま。替わった人は netkeiba 経路が
  選ぶ ID と確実に一致するときだけ書く(承認済み対応 → 正規、対応が未承認で ``nk:J`` だけ
  ある → ``nk:J``)。正規 ``J`` のマスタがあるときは netkeiba 側が名前・生年で同一判定するか
  どうかに依存するので決めない(codex 実装レビュー 2026-11-03)。決まらなければ書かない。

数字の一致を一般の同一性の証明にはしない(同じレースの既存行という文脈に限って使う)。呼び出し側は
``lock_race`` の後(同じトランザクション・同じロック下)で呼ぶ。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from horseracing_db.enums import EntityType, MappingStatus, Source
from horseracing_db.models import Horse, IdMapping, Jockey, RaceHorse, Trainer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import SURROGATE_PREFIX
from .identity import jra_name_key

#: quarantine reasons (Counts keys). ``quarantined_collision`` is bumped alongside each of them so
#: the existing "the race was not written" counter keeps its meaning.
AWAITING = "awaiting_netkeiba_entries"
MISSING = "alias_missing_in_field"
BOTH_IN_RACE = "alias_both_ids_in_race"
NUMBER_MISMATCH = "alias_number_mismatch"
BOTH_EXIST = "alias_both_ids_exist"
PENDING_REKEY = "approved_mapping_pending_rekey"
DISPUTED = "alias_mapping_disputed"
NAME_MISMATCH = "alias_name_mismatch"

_MASTER = {EntityType.JOCKEY: Jockey, EntityType.TRAINER: Trainer}


@dataclass(frozen=True)
class OfficialHorse:
    horse_id: str
    horse_number: int | None  # None = 取消で馬番が空欄(既存行から復元する)
    horse_name: str | None


@dataclass
class HorseAliases:
    """official id → stored row, or the reason the race must not be written."""

    rows: dict[str, RaceHorse] = field(default_factory=dict)
    reason: str | None = None
    problems: list[str] = field(default_factory=list)
    aliased: int = 0  # official X written through an ``nk:X`` row

    def stored_id(self, official_id: str) -> str:
        return self.rows[official_id].horse_id


def _nk(x: str) -> str:
    return f"{SURROGATE_PREFIX}{x}"


def _mapping_status(session: Session, entity_type: str, source_id: str) -> str | None:
    return session.scalar(
        select(IdMapping.mapping_status).where(
            IdMapping.entity_type == entity_type,
            IdMapping.source == Source.NETKEIBA,
            IdMapping.source_id == source_id,
        )
    )


def resolve_horses(
    session: Session,
    stored: dict[str, RaceHorse],
    incoming: Sequence[OfficialHorse],
    *,
    require_all: bool = True,
) -> HorseAliases:
    """Map each official horse to the stored row of the same race (FR-011a §2-§3).

    ``stored`` is ``RaceSnapshot.horses`` read under the race lock. With ``require_all`` (entries,
    results) a horse that has no row is a quarantine; without it (odds) it is just left out of
    ``rows`` — the caller treats it as a partial field. Any other problem always quarantines."""
    out = HorseAliases()
    if not stored:
        out.reason = AWAITING
        return out

    def fail(reason: str, msg: str) -> HorseAliases:
        if out.reason is None:
            out.reason = reason
        out.problems.append(msg)
        return out

    for h in incoming:
        x = h.horse_id
        plain, alias = stored.get(x), stored.get(_nk(x))
        if plain is not None and alias is not None:
            fail(BOTH_IN_RACE, f"{x}: both {x} and {_nk(x)} in the race")
            continue
        rh = plain or alias
        if rh is None:
            if require_all:
                fail(MISSING, f"{x} (馬番 {h.horse_number}) not in the stored field")
            continue
        if h.horse_number is not None and rh.horse_number != h.horse_number:
            fail(
                NUMBER_MISMATCH,
                f"{x}: official 馬番 {h.horse_number}"
                f" vs stored {rh.horse_id} 馬番 {rh.horse_number}",
            )
            continue
        if rh is alias:
            if session.get(Horse, x) is not None:
                fail(BOTH_EXIST, f"{x}: masters {x} and {_nk(x)} both exist")
                continue
            status = _mapping_status(session, EntityType.HORSE, x)
            if status == MappingStatus.MAPPED:
                fail(PENDING_REKEY, f"{x}: approved mapping, {_nk(x)} not re-keyed")
                continue
            if status in (MappingStatus.CONFLICT, MappingStatus.REJECTED):
                fail(DISPUTED, f"{x}: id_mappings is {status}")
                continue
            master = session.get(Horse, _nk(x))
            stored_key = jra_name_key(master.horse_name if master is not None else None)
            if not stored_key or stored_key != jra_name_key(h.horse_name):
                fail(
                    NAME_MISMATCH,
                    f"{x}: stored {_nk(x)} name "
                    f"{master.horse_name if master else None!r} vs official {h.horse_name!r}",
                )
                continue
            out.aliased += 1
        out.rows[x] = rh
    return out


def resolve_person(
    session: Session, entity_type: str, official: str | None, stored: str | None
) -> str | None:
    """The id to write for a jockey / trainer (FR-011a §4), or None = leave the stored value.

    Same person as stored (``J`` / ``nk:J``) → the stored id. Otherwise only an id the netkeiba
    route is certain to produce too (``idmap.resolve_entity``): an approved mapping → its canonical
    id; an unapproved / conflicting one with only the ``nk:J`` master → ``nk:J``. A canonical ``J``
    master makes netkeiba's choice depend on its own name/birth-year check → None."""
    if official is None:
        return None
    if stored in (official, _nk(official)):
        return stored
    row = session.execute(
        select(IdMapping.mapping_status, IdMapping.canonical_id).where(
            IdMapping.entity_type == entity_type,
            IdMapping.source == Source.NETKEIBA,
            IdMapping.source_id == official,
        )
    ).first()
    if row is not None and row[0] == MappingStatus.MAPPED:
        return row[1] or None
    model = _MASTER[entity_type]
    if session.get(model, official) is not None:
        return None  # netkeiba may or may not promote to the canonical id
    if session.get(model, _nk(official)) is not None:
        return _nk(official)
    return None
