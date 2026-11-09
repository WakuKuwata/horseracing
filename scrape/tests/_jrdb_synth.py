"""178 integration-test helpers: the public samples shifted to the netkeiba era (2026) so the
production-DB era guard lets them through, plus synthetic BAC/UKC/KYI rows derived from SED."""

from __future__ import annotations

import dataclasses
import datetime
from decimal import Decimal
from pathlib import Path

from horseracing_scrape.jrdb.decode import decode_file
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from horseracing_scrape.jrdb.models import (
    JrdbEntry,
    JrdbFileMeta,
    JrdbHorse,
    JrdbPayouts,
    JrdbPreRace,
    JrdbRaceKey,
    JrdbRaceProgram,
    JrdbResult,
)

PUBLIC = Path(__file__).resolve().parent / "fixtures" / "jrdb" / "public"
YEAR = 2026
DAY = datetime.date(2026, 9, 13)  # the 2008-09-13 sample moved to 2026 (same month/day)
JST = datetime.timezone(datetime.timedelta(hours=9))


def _shift_key(key: JrdbRaceKey) -> JrdbRaceKey:
    return dataclasses.replace(key, yy=YEAR % 100)


def decode_public(name: str, kind: str, *, profile="jrdb_canonical"):
    raw = (PUBLIC / f"{name}.txt").read_bytes()
    meta = JrdbFileMeta(kind, None, datetime.date(2008, 9, 13), "daily", "x", len(raw), 0)
    return decode_file(raw, meta, profile=profile)


def shifted_results(*, profile="jrdb_canonical", races: int | None = None) -> list[JrdbResult]:
    """SED080913 moved to 2026-09-13 (blood numbers moved +18 years so the horses are 2〜6 years old)."""

    out = []
    for r in decode_public("SED080913", "SED", profile=profile).rows:
        yy = (int(r.blood_reg_no[:2]) + 18) % 100
        out.append(
            dataclasses.replace(
                r,
                key=_shift_key(r.key),
                race_date=DAY,
                blood_reg_no=f"{yy:02d}{r.blood_reg_no[2:]}",
            )
        )
    if races is not None:
        keep = sorted({race_id_from_key(r.key, file_year=YEAR) for r in out})[:races]
        out = [r for r in out if race_id_from_key(r.key, file_year=YEAR) in keep]
    return out


def shifted_programs(results: list[JrdbResult]) -> list[JrdbRaceProgram]:
    """One BAC row per race, from the SED race header (BAC080913 is the same day)."""

    by_rid: dict[str, JrdbResult] = {}
    for r in results:
        by_rid.setdefault(race_id_from_key(r.key, file_year=YEAR), r)
    programs = []
    for _rid, r in sorted(by_rid.items()):
        programs.append(
            JrdbRaceProgram(
                key=r.key,
                race_date=DAY,
                post_time=datetime.time(9 + r.key.r // 2, 30 if r.key.r % 2 else 0),
                distance=r.distance,
                track_code=r.track_code,
                turn_code=r.turn_code,
                inout_code=r.inout_code,
                shubetsu_code=r.shubetsu_code,
                joken_code=r.joken_code,
                kigo_code=r.kigo_code,
                juryo_code=r.juryo_code,
                grade_code=r.grade_code,
                race_name=r.race_name,
                head_count=r.head_count,
                race_name_short=r.race_name_short,
                race_name_9=None,
                data_kubun="3",
                prizes_man=(500, 200, 130, 75, 50),
                sale_flags="11011111",
            )
        )
    return programs


def entries_from_results(results: list[JrdbResult]) -> list[JrdbEntry]:
    """KYI rows as the 前日 file would carry them (no body weight, no odds)."""

    return [
        JrdbEntry(
            key=r.key,
            horse_number=r.horse_number,
            blood_reg_no=r.blood_reg_no,
            horse_name=r.horse_name,
            jockey_name=r.jockey_name,
            carried_weight=r.jockey_weight,
            apprentice_code=None,
            trainer_name=r.trainer_name,
            trainer_affiliation=None,
            frame=(r.horse_number + 1) // 2,
            jockey_code=r.jockey_code,
            trainer_code=r.trainer_code,
            weight=None,
            weight_diff=None,
            cancelled=r.abnormal_code == 1,
            sex_code=1,
            owner_name=None,
        )
        for r in results
    ]


def horses_from_results(results: list[JrdbResult]) -> list[JrdbHorse]:
    seen = set()
    out = []
    for r in results:
        if r.blood_reg_no in seen:
            continue
        seen.add(r.blood_reg_no)
        hid = horse_id_from_blood_no(r.blood_reg_no, race_year=YEAR)
        out.append(
            JrdbHorse(
                blood_reg_no=r.blood_reg_no,
                horse_name=r.horse_name,
                sex_code=1,
                sire_name="父" + r.horse_name[:3],
                dam_name="母" + r.horse_name[:3],
                damsire_name="母父" + r.horse_name[:3],
                birth_date=datetime.date(int(hid[:4]), 4, 1),
                owner_name="馬主　太郎氏",
                breeder_name="生産　牧場",
                retired=False,
                data_date=DAY,
                sire_line_code="1104",
                damsire_line_code="1102",
            )
        )
    return out


def payouts_from_results(results: list[JrdbResult]) -> list[JrdbPayouts]:
    """HJC rows: 単勝 = 確定オッズ × 100 of the winner, 複勝 for 1〜3, 馬連/馬単/3連複/3連単 from
    the first three finishers (synthetic yen)."""

    by_rid: dict[str, list[JrdbResult]] = {}
    for r in results:
        by_rid.setdefault(race_id_from_key(r.key, file_year=YEAR), []).append(r)
    out = []
    for _rid, rows in sorted(by_rid.items()):
        top = sorted((r for r in rows if r.finish_order), key=lambda r: r.finish_order)[:3]
        if len(top) < 3 or top[0].final_win_odds is None:
            continue
        a, b, c = (t.horse_number for t in top)
        win_yen = int(top[0].final_win_odds * 100)
        out.append(
            JrdbPayouts(
                key=top[0].key,
                win=((a, win_yen),),
                place=tuple((t.horse_number, 150 + 10 * i) for i, t in enumerate(top)),
                quinella=(((min(a, b), max(a, b)), 1420),),
                wide=(((min(a, b), max(a, b)), 530), ((min(a, c), max(a, c)), 2130)),
                exacta=(((a, b), 2920),),
                trio=((tuple(sorted((a, b, c))), 8550),),
                trifecta=(((a, b, c), 39040),),
            )
        )
    return out


def prerace_from_results(results: list[JrdbResult]) -> list[JrdbPreRace]:
    return [
        JrdbPreRace(
            key=r.key,
            horse_number=r.horse_number,
            cancelled=r.abnormal_code == 1,
            jockey_code=r.jockey_code,
            jockey_name=r.jockey_name,
            carried_weight=r.jockey_weight,
            apprentice_code=None,
            going_code=r.going_code,
            weather_code=r.weather_code,
            weight=r.weight,
            weight_diff=r.weight_diff,
            post_time=None,
        )
        for r in results
    ]


def table_snapshot(session, table: str, pk: tuple[str, ...], where: str = "", params=None):
    """{pk tuple: {col: value}} and {pk tuple: xmin} for product-value / unneeded-write checks."""

    from sqlalchemy import text

    rows = (
        session.execute(text(f"SELECT *, xmin::text AS _xmin FROM {table} {where}"), params or {})
        .mappings()
        .all()
    )
    values = {}
    xmins = {}
    for r in rows:
        key = tuple(tuple(r[k]) if isinstance(r[k], list) else r[k] for k in pk)
        d = {k: v for k, v in dict(r).items() if k not in ("_xmin", "created_at", "updated_at")}
        values[key] = d
        xmins[key] = r["_xmin"]
    return values, xmins


def win_odds_of(results: list[JrdbResult]) -> dict[tuple[str, str], Decimal | None]:
    return {
        (
            race_id_from_key(r.key, file_year=YEAR),
            horse_id_from_blood_no(r.blood_reg_no, race_year=YEAR),
        ): r.final_win_odds
        for r in results
    }


# --------------------------------------------------------------------------- raw-byte shifting

_TERMINATOR = {"CRLF": b"\r\n", "LF": b"\n", "LFLF": b"\n\n", "mixed": b"\r\n", "none": b"\r\n"}


def shift_raw(kind: str, raw: bytes, *, year_from: int = 2008, year_to: int = 2026) -> bytes:
    """Move a public sample's RAW records to another year, touching only the ASCII digit fields
    (race-key ``yy``, ``race_date``, and the blood number's 2-digit birth year) so the file still
    decodes under its layout. Used to build netkeiba-era archive files for the repair tests."""

    from horseracing_scrape.jrdb.decode import split_records
    from horseracing_scrape.jrdb.layout import layout_for

    layout = layout_for(kind)
    fields = {f.name: f for f in layout.fields}
    records, _rejected, framing = split_records(raw, layout.data_length)
    delta = year_to - year_from
    out = bytearray()
    for _line_no, _record_no, data in records:
        rec = bytearray(data)

        def _patch(name: str, fn, rec=rec):
            f = fields.get(name)
            if f is None:
                return
            a, b = f.start - 1, f.start - 1 + f.length
            text = rec[a:b].decode("ascii")
            new = fn(text)
            assert len(new) == len(text)
            rec[a:b] = new.encode("ascii")

        _patch("yy", lambda t: f"{(int(t) + delta) % 100:02d}" if t.strip() else t)
        _patch(
            "race_date",
            lambda t: f"{int(t[:4]) + delta:04d}{t[4:]}" if t.strip() and t[:4].isdigit() else t,
        )
        _patch(
            "blood_reg_no",
            lambda t: f"{(int(t[:2]) + delta) % 100:02d}{t[2:]}" if t[:2].isdigit() else t,
        )
        _patch(
            "birth_date",
            lambda t: f"{int(t[:4]) + delta:04d}{t[4:]}" if t.strip() and t[:4].isdigit() else t,
        )
        out += bytes(rec) + _TERMINATOR.get(framing.terminator, b"\r\n")
    return bytes(out)


def public_sample(kind: str) -> Path:
    """The one public sample of this kind (they are different days: BAC/SED/SRB 2008-09-13, HJC
    2024-09-29, KYI 2015-07-12, TYB 2021-09-12, UKC 2002-09-08)."""

    return next(PUBLIC.glob(f"{kind}*.txt"))


def write_shifted_archive(dest: Path, years: dict[str, int]) -> dict[str, Path]:
    """``dest/<KIND>yymmdd.txt`` for each kind moved to its target year (the file's own year is
    read from its name); returns the paths."""

    from horseracing_scrape.jrdb.files import parse_file_name

    dest.mkdir(parents=True, exist_ok=True)
    out = {}
    for kind, year_to in years.items():
        src = public_sample(kind)
        _k, _v, day, _var = parse_file_name(src.name, reference=datetime.date(2010, 7, 1))
        path = dest / f"{kind}{year_to % 100:02d}{day.strftime('%m%d')}.txt"
        path.write_bytes(shift_raw(kind, src.read_bytes(), year_from=day.year, year_to=year_to))
        out[kind] = path
    return out


def passing_parity_report(
    rates: dict[str, float | None] | None = None, *, year: int = 2008
) -> dict:
    """A stage-1 report shaped like ``parity.build_parity`` output with one segment per column
    at the given rate (default: every column at 1.0; ``None`` = no compared cell)."""

    from horseracing_scrape.jrdb import parity

    rates = rates or {}
    cols = {}
    for name, rule in parity.COLUMN_RULES.items():
        rate = rates.get(name, 1.0)
        n = 0 if rate is None else 1000
        cols[name] = {
            "meaning": rule["meaning"],
            "threshold": rule["threshold"],
            "rate": rate,
            "by_segment": {"all": {"n": n, "agree": int(n * (rate or 0)), "rate": rate}},
        }
    return {"year": year, "columns": cols}


# --------------------------------------------------------------------------- synthetic raw records


def encode_field(f, value) -> bytes:
    """The inverse of ``decode_field`` for the pictures the synthetic files need: blanks for
    None, cp932 left-justified text for ``X``, zero-padded digits for ``9`` (``scale`` applied),
    right-justified digits for ``Z``, and the yyyymmdd / flag / signed codecs."""

    n = f.length
    if value is None:
        return b" " * n
    if f.codec == "yyyymmdd":
        return value.strftime("%Y%m%d").encode("ascii")
    if f.codec == "flag":
        return (b"1" if value else b"0").ljust(n, b" ")
    if f.codec == "signed":
        v = int(value)
        body = str(abs(v))
        sign = "-" if v < 0 else " "
        return (sign + body.rjust(n - 1)).encode("ascii")
    if f.type == "F":  # one hex digit (開催日)
        return str(value).encode("ascii").ljust(n, b" ")
    if f.codec == "code" or f.type == "X":
        text = str(value)
        if f.fmt and set(f.fmt) == {"9"} and text.isdigit():
            text = text.zfill(n)
        b = text.encode("cp932")
        assert len(b) <= n, (f.name, value)
        return b.ljust(n, b" ")
    if f.type == "9":
        v = int(round(float(value) * f.scale))
        return str(v).zfill(n).encode("ascii")
    if f.type == "Z":
        return str(int(value)).rjust(n).encode("ascii")
    raise ValueError(f"no encoder for {f}")


def patch_record(kind: str, template: bytes, values: dict) -> bytes:
    """Copy a real record and overwrite the named fields (``values`` may name race-key parts
    ``venue``/``yy``/``kai``/``day_hex``/``r``)."""

    from horseracing_scrape.jrdb.layout import layout_for

    layout = layout_for(kind)
    rec = bytearray(template)
    fields = {f.name: f for f in layout.fields}
    for name, value in values.items():
        f = fields[name]
        rec[f.slice] = encode_field(f, value)
    assert len(rec) == layout.data_length
    return bytes(rec)


def _template(kind: str) -> bytes:
    from horseracing_scrape.jrdb.decode import split_records
    from horseracing_scrape.jrdb.layout import layout_for

    records, _rej, _fr = split_records(
        public_sample(kind).read_bytes(), layout_for(kind).data_length
    )
    return records[0][2]


def _key_values(key: JrdbRaceKey) -> dict:
    return {"venue": key.venue, "yy": key.yy, "kai": key.kai, "day_hex": key.day_hex, "r": key.r}


def synth_kyi_raw(results: list[JrdbResult]) -> bytes:
    """A KYI file (前日 出走表) for the SED rows: one record per horse, the rest of each record
    copied from the real KYI sample."""

    tpl = _template("KYI")
    out = bytearray()
    for r in results:
        out += (
            patch_record(
                "KYI",
                tpl,
                {
                    **_key_values(r.key),
                    "horse_number": r.horse_number,
                    "blood_reg_no": r.blood_reg_no,
                    "horse_name": r.horse_name,
                    "jockey_name": r.jockey_name,
                    "trainer_name": r.trainer_name,
                    "jockey_code": r.jockey_code,
                    "trainer_code": r.trainer_code,
                    "carried_weight": r.jockey_weight,
                    "frame": (r.horse_number + 1) // 2 if r.horse_number <= 16 else 8,
                    "weight": None,
                    "weight_diff": None,
                    "cancelled": r.abnormal_code == 1,
                    "sex_code": 1,
                },
            )
            + b"\r\n"
        )
    return bytes(out)


def synth_ukc_raw(results: list[JrdbResult], *, race_year: int) -> bytes:
    """A UKC file for the SED horses: names / birth dates consistent with the blood numbers; the
    pedigree names, owner, breeder and the line codes stay the real sample's (non-NULL)."""

    tpl = _template("UKC")
    out = bytearray()
    seen = set()
    for r in results:
        if r.blood_reg_no in seen:
            continue
        seen.add(r.blood_reg_no)
        hid = horse_id_from_blood_no(r.blood_reg_no, race_year=race_year)
        out += (
            patch_record(
                "UKC",
                tpl,
                {
                    "blood_reg_no": r.blood_reg_no,
                    "horse_name": r.horse_name,
                    "sex_code": 1,
                    "birth_date": datetime.date(int(hid[:4]), 4, 1),
                    "retired": False,
                },
            )
            + b"\r\n"
        )
    return bytes(out)


def synth_hjc_raw(results: list[JrdbResult]) -> bytes:
    """An HJC file for the SED races: 単勝 = winner × 確定オッズ×100, 複勝 for 1〜3; every other
    bet type blank."""

    from horseracing_scrape.jrdb.layout import layout_for

    tpl = _template("HJC")
    layout = layout_for("HJC")
    blank_all = {
        f.name: None
        for f in layout.fields
        if f.name.split("_")[0] not in ("venue", "yy", "kai", "day", "r", "reserved")
    }
    by_rid: dict[str, list[JrdbResult]] = {}
    for r in results:
        by_rid.setdefault(race_id_from_key(r.key, file_year=r.race_date.year), []).append(r)
    out = bytearray()
    for _rid, rows in sorted(by_rid.items()):
        top = sorted((r for r in rows if r.finish_order), key=lambda r: r.finish_order)[:3]
        if len(top) < 3 or top[0].final_win_odds is None:
            continue
        values = {**blank_all, **_key_values(top[0].key)}
        values["win_1_number"] = f"{top[0].horse_number:02d}"
        values["win_1_yen"] = int(top[0].final_win_odds * 100)
        for i, t in enumerate(top, start=1):
            values[f"place_{i}_number"] = f"{t.horse_number:02d}"
            values[f"place_{i}_yen"] = 150 + 10 * i
        out += patch_record("HJC", tpl, values) + b"\r\n"
    return bytes(out)


def write_public_day_archive(dest: Path) -> dict[str, Path]:
    """The 2008-09-13 public day as a complete archive folder: the real BAC/SED/SRB plus a
    synthesized KYI/UKC/HJC that match them (the public KYI/UKC/HJC samples are other days)."""

    dest.mkdir(parents=True, exist_ok=True)
    sed_rows = decode_public("SED080913", "SED").rows
    paths = {}
    for kind in ("BAC", "SED", "SRB"):
        src = public_sample(kind)
        paths[kind] = dest / src.name
        paths[kind].write_bytes(src.read_bytes())
    paths["KYI"] = dest / "KYI080913.txt"
    paths["KYI"].write_bytes(synth_kyi_raw(sed_rows))
    paths["UKC"] = dest / "UKC080913.txt"
    paths["UKC"].write_bytes(synth_ukc_raw(sed_rows, race_year=2008))
    paths["HJC"] = dest / "HJC080913.txt"
    paths["HJC"].write_bytes(synth_hjc_raw(sed_rows))
    return paths
