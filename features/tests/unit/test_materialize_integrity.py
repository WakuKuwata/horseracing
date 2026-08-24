"""parquet の完全性と、それを作ったコードの同一性(2026-08-23)。

manifest は `content_hash` を書いていたが、読み込み側は**一度も照合していなかった**。
`source_fingerprint` が見るのは DB の入力であってファイルではないので、切り詰められた
parquet も、書きかけの parquet も、手で編集された parquet も全ての検査を素通りした。
行が欠けても builder の左結合はそれを NaN の履歴に変えるだけで、エラーにはならない。

さらに、**コード**の同一性を見る検査がどこにも無かった。`jockey_recent_win_rate` の
行順依存を直したとき(同日)その列の 12.21% の値が変わったが、DB の入力は 1 行も
変わっていないので、ディスク上の parquet は静かに古い値のままだった。

読み込み時の照合は**ファイルのバイト列**に対して行う。実測(実 307MB parquet):
read_parquet 0.3s / file sha256 0.1s / 値ベース _hash_frame 30.0s。
値ハッシュは読み込み全体の 100 倍で、毎回照合すると materialize の意義が消える。
"""

from __future__ import annotations

import json

import pytest

from horseracing_features.builder import assemble_feature_matrix
from horseracing_features.materialize import (
    MaterializationError,
    read_manifest,
    read_materialized,
    write_materialized,
)
from tests._frames import make_frames

_SPECS = [
    {"race_id": "200801010101", "race_date": "2008-01-01", "horses": [
        {"horse_id": "H", "horse_number": 1, "finish_order": 1},
        {"horse_id": "X", "horse_number": 2, "finish_order": 2}]},
    {"race_id": "200802010101", "race_date": "2008-02-01", "horses": [
        {"horse_id": "H", "horse_number": 1, "finish_order": 2},
        {"horse_id": "X", "horse_number": 2, "finish_order": 1}]},
]


def _built(tmp_path):
    frames = make_frames(_SPECS)
    path = tmp_path / "f.parquet"
    write_materialized(path, frames)
    return frames, path


def test_manifest_records_both_hashes(tmp_path):
    _, path = _built(tmp_path)
    m = read_manifest(path)
    assert m.parquet_sha256 and len(m.parquet_sha256) == 64
    assert m.feature_code_hash and len(m.feature_code_hash) == 64


def test_a_healthy_parquet_reads(tmp_path):
    frames, path = _built(tmp_path)
    df, _ = read_materialized(path)
    assert len(df) > 0
    assemble_feature_matrix(frames, use_materialized=True, materialized_path=path)


def test_a_truncated_parquet_is_caught(tmp_path):
    """切り詰めは実際に起きる(書き込み中のクラッシュ・ディスク満杯・部分コピー)."""
    _, path = _built(tmp_path)
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(MaterializationError, match="integrity check failed"):
        read_materialized(path)


def test_an_edited_parquet_is_caught(tmp_path):
    """1 バイト書き換えでも落ちること(値ではなくバイト列を見ているので確実)."""
    _, path = _built(tmp_path)
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    path.write_bytes(bytes(raw))
    with pytest.raises(MaterializationError, match="integrity check failed"):
        read_materialized(path)


def test_a_manifest_without_an_integrity_hash_is_caught(tmp_path):
    """完全性ハッシュを持たない旧 manifest は素通りさせない(fingerprint_algo と同じ扱い)."""
    _, path = _built(tmp_path)
    mpath = tmp_path / "f.manifest.json"
    raw = json.loads(mpath.read_text(encoding="utf-8"))
    del raw["parquet_sha256"]
    mpath.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MaterializationError, match="integrity check failed"):
        read_materialized(path)


def test_feature_code_change_invalidates_the_cache(tmp_path):
    """DB の入力が 1 行も変わらなくても、特徴コードが変われば cache は無効。

    これが今日の実例を止める石。`source_fingerprint` は DB を見るので、コード由来の
    陳腐化には原理的に反応できない。
    """
    frames, path = _built(tmp_path)
    mpath = tmp_path / "f.manifest.json"
    raw = json.loads(mpath.read_text(encoding="utf-8"))
    raw["feature_code_hash"] = "0" * 64  # 別のコードで作られた cache を模す
    mpath.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MaterializationError, match="feature code changed"):
        assemble_feature_matrix(frames, use_materialized=True, materialized_path=path)


def test_code_check_also_runs_when_fingerprint_verification_is_skipped(tmp_path):
    """verify-once の backfill 経路でもコード同一性は毎回見る。

    `skip_fingerprint_verify` が飛ばすのは DB との突き合わせだけで、
    「このコードが作った cache か」は毎回問われなければならない。
    """
    frames, path = _built(tmp_path)
    mpath = tmp_path / "f.manifest.json"
    raw = json.loads(mpath.read_text(encoding="utf-8"))
    raw["feature_code_hash"] = "0" * 64
    mpath.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MaterializationError, match="feature code changed"):
        assemble_feature_matrix(
            frames, use_materialized=True, materialized_path=path,
            skip_fingerprint_verify=True,
        )


# --- コード hash は散文を無視し、ロジックだけを見る(2026-08-24) -------------------------------
#
# 最初の実装はソースのバイト列を hash していた。その結果、registry.py にコメントを 18 行足した
# だけの docs コミットが数時間後に 307MB のキャッシュを丸ごと無効化した(実際に `live refresh`
# が MaterializationError で落ちて発覚)。散文で狼少年になる検査は迂回されるようになり、
# 検査が無いより悪い。AST(docstring 除去済み)を hash する形に変えてある。

def test_prose_does_not_invalidate_the_cache(tmp_path, monkeypatch):
    """コメント・空行・docstring の変更では hash が動かないこと."""
    import ast

    from horseracing_features import materialize as m

    src_plain = "X = 1\ndef f():\n    return X + 2\n"
    src_prose = (
        '"""module docstring."""\n'
        "# 長い説明コメント\n"
        "\n"
        "X = 1\n"
        "def f():\n"
        '    """まったく違う説明。"""\n'
        "    # さらにコメント\n"
        "    return X + 2\n"
    )
    def h(src):
        return ast.dump(m._strip_docstrings(ast.parse(src)))

    assert h(src_plain) == h(src_prose)


def test_a_logic_change_does_invalidate_the_cache():
    """式・定数・並べ替えキーが変われば hash は動くこと(狼少年の逆で、見逃してはならない)."""
    import ast

    from horseracing_features import materialize as m

    def h(src):
        return ast.dump(m._strip_docstrings(ast.parse(src)))

    assert h("def f():\n    return X + 2\n") != h("def f():\n    return X + 3\n")
    assert h('s = ["a", "b"]\n') != h('s = ["a"]\n')


def test_an_algo_change_is_reported_as_such(tmp_path):
    """アルゴリズムが変わったときに「コードが変わった」と誤報しないこと."""
    import json

    _, path = _built(tmp_path)
    mpath = tmp_path / "f.manifest.json"
    raw = json.loads(mpath.read_text(encoding="utf-8"))
    raw["feature_code_hash_algo"] = "byte-v0"
    mpath.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MaterializationError, match="algo mismatch"):
        assemble_feature_matrix(
            make_frames(_SPECS), use_materialized=True, materialized_path=path,
        )
