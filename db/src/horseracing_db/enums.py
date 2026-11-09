"""Status code system (single source of truth for allowed values).

These mirror data-model.md "状態コード体系". Stored as text + CHECK (not Postgres
ENUM) so values can be extended via a CHECK-replacement migration (research R4).
"""

from __future__ import annotations


class EntryStatus:
    """race_horses.entry_status — 出走状態."""

    STARTED = "started"
    CANCELLED = "cancelled"  # 出走取消
    EXCLUDED = "excluded"  # 競走除外
    ALL = (STARTED, CANCELLED, EXCLUDED)
    #: statuses that mean the horse did NOT run (no race_results row, INV-1).
    NON_STARTERS = (CANCELLED, EXCLUDED)


class ResultStatus:
    """race_results.result_status — 完走状態."""

    FINISHED = "finished"
    STOPPED = "stopped"  # 競走中止
    DISQUALIFIED = "disqualified"  # 失格
    ALL = (FINISHED, STOPPED, DISQUALIFIED)
    #: only this status is included in completion-based aggregates / labels (INV-3).
    COMPLETED = (FINISHED,)


class MappingStatus:
    """id_mappings.mapping_status — ID 対応状態."""

    UNMAPPED = "unmapped"
    MAPPED = "mapped"
    CONFLICT = "conflict"
    REJECTED = "rejected"
    ALL = (UNMAPPED, MAPPED, CONFLICT, REJECTED)


class EntityType:
    """id_mappings.entity_type."""

    HORSE = "horse"
    JOCKEY = "jockey"
    TRAINER = "trainer"
    ALL = (HORSE, JOCKEY, TRAINER)


class Source:
    """Data source identifiers.

    ``JRA_OFFICIAL`` (Feature 176, migration 0023) is the JRA official site route. It is a valid
    value for ingestion_jobs / exotic_odds / race_laps / the provenance tables; ``id_mappings``
    accepts it through the shared CHECK but the official route never writes there (its ids ARE
    the canonical ids — constitution I interpretation recorded in specs/176 plan.md).
    """

    JRA_VAN = "jra_van"
    NETKEIBA = "netkeiba"
    JRA_OFFICIAL = "jra_official"
    #: Feature 178 (migration 0025): the JRDB member-file route, the third writer. Legal on the
    #: same tables as JRA_OFFICIAL and on id_mappings (JRDB jockey/trainer codes are private ids).
    JRDB = "jrdb"
    ALL = (JRA_VAN, NETKEIBA, JRA_OFFICIAL, JRDB)


class JobStatus:
    """ingestion_jobs.status."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    SKIPPED = "skipped"  # added in migration 0004 (e.g. JRA-VAN <2007 files)
    ALL = (QUEUED, RUNNING, SUCCEEDED, FAILED, PARTIAL, SKIPPED)


class AdoptionStatus:
    """model_versions.adoption_status."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    RETIRED = "retired"
    ALL = (CANDIDATE, ACTIVE, RETIRED)


class BetType:
    """recommendations.bet_type — 7 券種 (枠連は対象外)."""

    WIN = "win"  # 単勝
    PLACE = "place"  # 複勝
    QUINELLA = "quinella"  # 馬連
    EXACTA = "exacta"  # 馬単
    WIDE = "wide"  # ワイド
    TRIO = "trio"  # 3連複
    TRIFECTA = "trifecta"  # 3連単
    ALL = (WIN, PLACE, QUINELLA, EXACTA, WIDE, TRIO, TRIFECTA)
    #: exotic bet types (win excluded) — the only ones with a real exotic odds pool (012).
    EXOTIC = (PLACE, QUINELLA, EXACTA, WIDE, TRIO, TRIFECTA)


class CoverageScope:
    """exotic_odds.coverage_scope — 取得グリッドの完全性 (012)."""

    FULL = "full"  # 期待件数テストで完全グリッドを証明
    PARTIAL = "partial"  # 部分取得 (欠損は推定フォールバック)
    ALL = (FULL, PARTIAL)
