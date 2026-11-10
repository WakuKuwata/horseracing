"""Value objects decoded from JRDB files (Feature 178, data-model §5). They know nothing about
the database.

Design rules (憲法 II / V, contracts/files-and-ids.md §2–§4):

* Only the columns this feature adopts are present. JRDB's own indices (ＩＤＭ・各指数),
  comments, 基準オッズ, 10 時オッズ and the TYB odds snapshot are not attributes here — the
  layout marks them ``target="skip"`` and the decoder never reads them.
* The four columns whose JRDB definition differs from the JRA-VAN column of the same name
  (``first_3f`` outside 1200 m, ``corner_orders``, ``running_style``, ``sire_line/damsire_line``)
  are ``canonical_only``: under ``profile="current"`` the decoder leaves them ``None``.
* Blank (space-filled) fields are ``None``; 0.1-unit numbers are ``Decimal`` already divided.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

#: JRA venues 01〜10 (JRDB 場コード) → the same two-digit code the DB stores in races.venue_code.
JRA_VENUE_CODES: frozenset[str] = frozenset(f"{i:02d}" for i in range(1, 11))

#: Kinds this feature decodes (the layout registry keys). KSA/CSA share KZA/CZA's layout.
DECODED_KINDS: tuple[str, ...] = (
    "BAC",
    "KYI",
    "UKC",
    "KZA",
    "KSA",
    "CZA",
    "CSA",
    "TYB",
    "HJC",
    "SED",
    "SRB",
)

Profile = str  # "current" | "jrdb_canonical" (validated in profile.py)


@dataclass(frozen=True)
class JrdbFileMeta:
    """What a file *is*, decided from its name and bytes (files.py) — the decoder's input."""

    kind: str  # BAC / KYI / UKC / KZA / KSA / CZA / CSA / TYB / HJC / SED / SRB
    version: str | None  # layout version to use (None = the kind's current layout)
    data_date: datetime.date
    variant: str  # daily / venue_e / venue_w / venue_l / archive
    sha256: str
    size: int
    record_length: int  # bytes per record INCLUDING the 2-byte terminator (the doc's レコード長)
    #: SED/SEC only: "sokuho" (当日 18:00 の速報版) or "formal" (木曜の正式版); None elsewhere.
    #: Decided from the fetch date (files.sed_edition), never from Last-Modified (D9).
    edition: str | None = None


@dataclass(frozen=True)
class JrdbRaceKey:
    """レースキー 8 文字: 場 2・年 2・回 1・日 1 (16 進)・R 2 (contracts §3)."""

    venue: str
    yy: int
    kai: int
    day_hex: str
    r: int

    def __post_init__(self) -> None:
        # the same grammar for both entry points (ids.key_from_text and the decoder) — codex D20 #12
        if not (len(self.venue) == 2 and self.venue.isdigit()):
            raise ValueError("race key venue must be two ASCII digits")
        if not 0 <= self.yy <= 99 or not 0 <= self.kai <= 9 or not 0 <= self.r <= 99:
            raise ValueError("race key part out of its picture range")
        if not (len(self.day_hex) == 1 and self.day_hex in "123456789abcdef"):
            raise ValueError("race key day must be one hex digit 1-f")

    @property
    def key8(self) -> str:
        return f"{self.venue}{self.yy:02d}{self.kai}{self.day_hex}{self.r:02d}"

    @property
    def is_jra(self) -> bool:
        return self.venue in JRA_VENUE_CODES


@dataclass(frozen=True)
class JrdbRaceProgram:
    """BAC 番組 1 行 (レース)."""

    key: JrdbRaceKey
    race_date: datetime.date
    post_time: datetime.time | None
    distance: int | None
    track_code: int | None  # 1 芝 / 2 ダート / 3 障害
    turn_code: int | None  # 1 右 / 2 左 / 3 直 / 9 他
    inout_code: int | None
    shubetsu_code: int | None  # 種別 11/12/13/14/20/99
    joken_code: str | None  # 条件 04/05/08/09/10/15/16/A1/A2/A3/OP
    kigo_code: str | None  # 記号 3 桁
    juryo_code: int | None  # 重量 1 ハンデ 2 別定 3 馬齢 4 定量
    grade_code: int | None  # 1 G1 … 6 L
    race_name: str | None
    head_count: int | None
    race_name_short: str | None
    race_name_9: str | None
    data_kubun: str | None  # 1 特別登録 / 2 想定確定 / 3 前日
    prizes_man: tuple[int | None, int | None, int | None, int | None, int | None]  # 1〜5 着 (万円)
    sale_flags: str | None  # 馬券発売フラグ 16 桁 (単複枠馬連馬単ワイド3複3単+予備)


@dataclass(frozen=True)
class JrdbEntry:
    """KYI 競走馬 1 行 (レース×馬) — adopted columns only."""

    key: JrdbRaceKey
    horse_number: int
    blood_reg_no: str
    horse_name: str | None
    jockey_name: str | None
    carried_weight: Decimal | None  # kg
    apprentice_code: int | None  # 見習い区分 1/2/3/4/9
    trainer_name: str | None
    trainer_affiliation: str | None
    frame: int | None
    jockey_code: str | None  # JRDB 独自 5 桁 — mapped through id_mappings, never used as JRA id
    trainer_code: str | None
    weight: int | None  # 枠確定馬体重 (前日版では空のことが多い)
    weight_diff: int | None
    cancelled: bool
    sex_code: int | None  # 1 牡 2 牝 3 セン
    owner_name: str | None


@dataclass(frozen=True)
class JrdbHorse:
    """UKC 馬基本 1 行 (馬)."""

    blood_reg_no: str
    horse_name: str | None
    sex_code: int | None
    sire_name: str | None
    dam_name: str | None
    damsire_name: str | None
    birth_date: datetime.date | None
    owner_name: str | None
    breeder_name: str | None
    retired: bool
    data_date: datetime.date | None
    sire_line_code: str | None = None  # canonical_only (JRDB 系統体系・DB とは別定義)
    damsire_line_code: str | None = None  # canonical_only
    #: (sire, damsire) JRDB line codes kept under EVERY profile ONLY as translation inputs
    #: (vocab.py → the current DB's families); never written as such (2026-11-10)
    line_codes: tuple[str | None, str | None] | None = None


@dataclass(frozen=True)
class JrdbPerson:
    """KZA/KSA (騎手) ・ CZA/CSA (調教師) 1 行 — the material for id_mappings (D3)."""

    kind: str  # "jockey" | "trainer"
    code: str  # JRDB 5 桁
    retired: bool
    retired_date: datetime.date | None
    name: str | None
    kana: str | None
    short_name: str | None
    affiliation_code: int | None  # 1 関東 2 関西 3 他
    region_name: str | None
    birth_date: datetime.date | None
    first_license_year: int | None
    data_date: datetime.date | None
    apprentice_code: int | None = None  # jockey only
    stable_trainer_code: str | None = None  # jockey only


@dataclass(frozen=True)
class JrdbPreRace:
    """TYB 直前情報 1 行 (レース×馬). No odds: the TYB snapshot is never written (contract §3)."""

    key: JrdbRaceKey
    horse_number: int
    cancelled: bool
    jockey_code: str | None
    jockey_name: str | None
    carried_weight: Decimal | None
    apprentice_code: int | None
    going_code: int | None
    weather_code: int | None
    weight: int | None
    weight_diff: int | None
    post_time: datetime.time | None


@dataclass(frozen=True)
class JrdbResult:
    """SED 成績 1 行 (レース×馬)."""

    key: JrdbRaceKey
    horse_number: int
    blood_reg_no: str
    race_date: datetime.date
    horse_name: str | None
    distance: int | None
    track_code: int | None
    turn_code: int | None
    inout_code: int | None
    going_code: int | None  # 10〜42
    shubetsu_code: int | None
    joken_code: str | None
    kigo_code: str | None
    juryo_code: int | None
    grade_code: int | None
    race_name: str | None
    head_count: int | None
    race_name_short: str | None
    finish_order: int | None  # 0/None when abnormal
    abnormal_code: int | None  # 0 なし 1 取消 2 除外 3 中止 4 失格 5 降着 6 再騎乗
    finish_time: datetime.timedelta | None
    jockey_weight: Decimal | None
    jockey_name: str | None
    trainer_name: str | None
    final_win_odds: Decimal | None  # 正式版のみ・速報版は None
    final_win_popularity: int | None
    finish_time_diff: Decimal | None  # seconds (1(2)着タイム差)
    last_3f: Decimal | None  # seconds
    jockey_code: str | None
    trainer_code: str | None
    weight: int | None
    weight_diff: int | None
    weather_code: int | None
    win_payout_yen: int | None  # 払戻 単勝 (= 確定オッズ×100; audit material)
    place_payout_yen: int | None
    post_time: datetime.time | None  # 第 4 版a (2022-08) 以降・それ以前は None
    first_3f: Decimal | None = None  # canonical_only
    corner_orders: tuple[int | None, int | None, int | None, int | None] | None = (
        None  # canonical_only
    )
    running_style_code: str | None = None  # canonical_only
    #: (JRDB style code, stored corner positions) kept under EVERY profile ONLY as the inputs of
    #: the running-style translation (vocab.py → a JRA-VAN label); never written as such
    style_inputs: tuple[str | None, tuple[int, ...]] | None = None


@dataclass(frozen=True)
class JrdbRaceResult:
    """SRB 成績レース 1 行 (レース): 先頭馬のハロンタイムとトラックバイアス."""

    key: JrdbRaceKey
    lap_times: tuple[Decimal, ...]  # seconds, leading-horse 1F splits, blanks dropped from the tail
    track_bias: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class JrdbPayouts:
    """HJC 払戻 1 行 (レース). 枠連 is not read (DB has no bet type for it)."""

    key: JrdbRaceKey
    win: tuple[tuple[int, int], ...]  # (horse_number, yen)
    place: tuple[tuple[int, int], ...]
    quinella: tuple[tuple[tuple[int, int], int], ...]  # ((a, b), yen)
    wide: tuple[tuple[tuple[int, int], int], ...]
    exacta: tuple[tuple[tuple[int, int], int], ...]
    trio: tuple[tuple[tuple[int, int, int], int], ...]
    trifecta: tuple[tuple[tuple[int, int, int], int], ...]
