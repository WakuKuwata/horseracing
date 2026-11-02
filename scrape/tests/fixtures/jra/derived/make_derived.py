"""Regenerate the structure-change fixtures from the raw ones (176 T007).

Run: ``python3 scrape/tests/fixtures/jra/derived/make_derived.py`` (repo root). Each derived file
is the raw page decoded with cp932, surgically altered, and re-encoded — so the parser sees the
same bytes shape as a real response. The manifest's ``derived`` list is rewritten.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent


def _load(name: str) -> str:
    return (BASE / name).read_bytes().decode("cp932")


def main() -> None:
    out = []

    def emit(name: str, src: str, text: str, note: str) -> None:
        data = text.encode("cp932")
        (HERE / name).write_bytes(data)
        out.append(
            {
                "file": f"derived/{name}",
                "derived_from": src,
                "raw_sha256": hashlib.sha256(data).hexdigest(),
                "charset": "shift_jis",
                "note": note,
            }
        )

    src = "entries_tokyo_20261101.sjis.html"
    t = _load(src)
    t2 = re.sub(r'<th class="h_weight" scope="col">馬体重</th>\s*', "", t)
    t2 = re.sub(r'<td class="h_weight">.*?</td>\s*', "", t2, flags=re.S)
    assert "h_weight" not in t2
    emit(
        "entries_missing_h_weight.sjis.html",
        src,
        t2,
        "出馬表から馬体重列(th/td.h_weight)を削除=列欠落で ParseError",
    )
    t3 = t.replace("2026年11月1日（日曜） 4回東京11日", "2026年11月2日（月曜） 4回東京12日")
    assert t3 != t
    emit(
        "entries_other_day.sjis.html",
        src,
        t3,
        "出馬表の全見出しを別日(4回東京12日・11/2)に書換え=expected_date 不一致で parse_failed",
    )

    src = "results_tokyo_20261031.sjis.html"
    r = _load(src)
    r2 = r.replace("2026年10月31日（土曜） 4回東京10日", "2026年11月1日（日曜） 4回東京11日")
    assert r2 != r
    emit(
        "results_other_day.sjis.html",
        src,
        r2,
        "結果の全見出しを別日に書換え=expected_date 不一致で parse_failed",
    )

    src = "payouts_tokyo_20261031.sjis.html"
    p = _load(src)
    m = re.search(r'(<li class="trio">.*?<div class="line">.*?</div>\s*</div>)(\s*</dd>)', p, re.S)
    assert m
    line = re.search(r'<div class="line">.*?</div>\s*</div>', m.group(1), re.S).group(0)
    p2 = p[: m.end(1)] + line + p[m.end(1) :]
    emit(
        "payouts_duplicate_combo.sjis.html",
        src,
        p2,
        "1R 3連複ブロックの組番行を複製=組番重複で parse_failed(その券種のみ)",
    )

    src = "odds_tokyo11r_20261101_0822.sjis.html"
    o = _load(src)
    o2 = re.sub(r'<div class="cell time"><strong>8時22分現在オッズ</strong></div>', "", o)
    assert o2 != o
    emit(
        "odds_missing_time.sjis.html",
        src,
        o2,
        "単複オッズの「HH時MM分現在オッズ」セルを削除=official_at 不明で ParseError",
    )

    mf_path = BASE / "manifest.json"
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    mf["derived"] = out
    mf_path.write_text(json.dumps(mf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n".join(e["file"] for e in out))


if __name__ == "__main__":
    main()
