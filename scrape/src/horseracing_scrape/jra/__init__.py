"""JRA 公式サイト(www.jra.go.jp/JRADB)を第 2 の情報源として読む経路(Feature 176).

netkeiba 経路と並走する。書込みは ``horseracing_scrape.upsert`` の共通 writer を通し、二重情報源の
規則(``horseracing_scrape.precedence``)は両経路に同じ形で課される。取得は ``JraFetcher``(POST
cname・cp932)で、共有スロット(``politeness``)と robots キャッシュだけを netkeiba 経路と共有する。
``HttpFetcher`` は触らない。
"""
