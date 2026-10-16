"""Read-only query runner: uv run python q.py <sqlfile|->; prints each statement's rows."""
import sys, json
import pandas as pd
from sqlalchemy import create_engine, text
URL = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
eng = create_engine(URL)
src = sys.stdin.read() if sys.argv[1] == "-" else open(sys.argv[1]).read()
stmts = [s.strip() for s in src.split(";;") if s.strip()]
with eng.connect() as con:
    con.execute(text("SET TRANSACTION READ ONLY"))
    for s in stmts:
        title = s.splitlines()[0] if s.startswith("--") else s[:80]
        print("=" * 8, title)
        try:
            df = pd.read_sql(text(s), con)
            with pd.option_context("display.max_rows", 200, "display.width", 250, "display.max_columns", 40):
                print(df.to_string(index=False))
        except Exception as e:
            print("ERR", type(e).__name__, str(e)[:400])
            con.rollback(); con.execute(text("SET TRANSACTION READ ONLY"))
