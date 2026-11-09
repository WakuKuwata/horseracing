"""JRDB (有料・Shift_JIS 固定長) を第 3 の情報源として読む (Feature 178).

決定論の純関数(layout → decode → ids/profile)と、会員ページからの取得(fetch)、ファイル版の
管理(files)、段 1 の parity 報告(parity)に分かれる。書込み(upsert_jrdb)は 176 の規則
`precedence.py` と来歴 2 表を共用し、`profile=current|jrdb_canonical` を必須に取る(D7a)。

会員配信ファイル・会員 URL・認証情報は repo に置かない(FR-022)。この package の fixture は
ログイン不要の公開サンプル(`https://jrdb.com/program/data.html` から辿れる配布物)だけ。
"""
