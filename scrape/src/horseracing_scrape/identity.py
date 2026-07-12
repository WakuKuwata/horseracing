"""Identity-resolution pure functions (Feature 067).

netkeiba の ``source_id`` が既存の JRA-VAN canonical id と**同一採番**のとき、名前(馬は生年も)
照合を裏取りに同一個体と判定する。これは「番号だけ」「名前だけ」の推測結合(禁止, 憲法 I)では
なく、公式 ID(馬=血統登録番号 / 騎手・調教師=免許番号)の構造的同一 + 裏取り。曖昧は conflict、
照合情報が欠損する場合は unmapped(insufficient_evidence)に留める(欠損 ≠ 矛盾, codex#5)。

副作用なし・決定論。DB は読まない(canonical 行は呼び出し側が供給する)。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from horseracing_db.enums import EntityType, MappingStatus

#: 騎手/調教師の netkeiba 短縮名に付く見習い/斤量マーカー(先頭に付与される)。
_MARKERS = "△▲☆★◇◆*＊"


def normalize_name(name: str | None) -> str:
    """NFKC 正規化 + 前後空白除去(空/None は空文字)。"""
    if name is None:
        return ""
    return unicodedata.normalize("NFKC", name).strip()


def strip_markers(name: str | None) -> str:
    """NFKC 正規化後、先頭の見習い/斤量マーカーを除去(騎手/調教師名の照合用)。"""
    return normalize_name(name).lstrip(_MARKERS).strip()


@dataclass(frozen=True)
class Resolution:
    """identity 照合の結果(永続化しない値オブジェクト)。"""

    status: str  # MappingStatus.MAPPED / CONFLICT / UNMAPPED
    canonical_id: str | None
    reason: str  # id_mappings.resolution_note に転記する監査文字列


def _canonical_name(entity_type: str, canonical_row) -> str | None:
    if entity_type == EntityType.HORSE:
        return getattr(canonical_row, "horse_name", None)
    if entity_type == EntityType.JOCKEY:
        return getattr(canonical_row, "jockey_name", None)
    if entity_type == EntityType.TRAINER:
        return getattr(canonical_row, "trainer_name", None)
    return None


def classify_identity(
    *,
    entity_type: str,
    source_id: str,
    candidate_name: str | None,
    canonical_row,
    candidate_birth_year: int | None = None,
) -> Resolution:
    """netkeiba サロゲート候補を canonical と照合し mapped/conflict/unmapped を判定する。

    ``canonical_row`` は ``source_id`` と同一 id を持つ既存マスタ行(horse/jockey/trainer)または
    ``None``(番号一致する canonical が存在しない=真の未マッピング)。
    """
    if canonical_row is None:
        return Resolution(
            MappingStatus.UNMAPPED, None, f"no_canonical:{entity_type};id={source_id}"
        )

    canonical_id = source_id  # canonical マスタは id == source_id で引かれている
    canon_raw = _canonical_name(entity_type, canonical_row)
    if entity_type == EntityType.HORSE:
        cand = normalize_name(candidate_name)
        canon = normalize_name(canon_raw)
    else:
        cand = strip_markers(candidate_name)
        canon = strip_markers(canon_raw)

    if not cand or not canon:
        return Resolution(
            MappingStatus.UNMAPPED, None, f"insufficient:{entity_type};name_missing"
        )

    if entity_type == EntityType.HORSE:
        if cand != canon:
            return Resolution(
                MappingStatus.CONFLICT, None, f"conflict:horse;name({cand}!={canon})"
            )
        canon_by = getattr(canonical_row, "birth_year", None)
        if candidate_birth_year is None or canon_by is None:
            return Resolution(
                MappingStatus.UNMAPPED, None, "insufficient:horse;birth_year_missing"
            )
        if candidate_birth_year != canon_by:
            return Resolution(
                MappingStatus.CONFLICT,
                None,
                f"conflict:horse;birth_year({candidate_birth_year}!={canon_by})",
            )
        return Resolution(
            MappingStatus.MAPPED,
            canonical_id,
            "identity:horse;id==canonical;name=exact;birth=match",
        )

    # jockey / trainer: マーカー除去後の双方向 prefix 一致(短縮名の裏取り)
    if canon.startswith(cand) or cand.startswith(canon):
        return Resolution(
            MappingStatus.MAPPED,
            canonical_id,
            f"identity:{entity_type};id==canonical;name=prefix",
        )
    return Resolution(
        MappingStatus.CONFLICT, None, f"conflict:{entity_type};prefix_fail({cand}!={canon})"
    )
