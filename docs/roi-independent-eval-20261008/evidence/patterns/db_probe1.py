import pandas as pd
from sqlalchemy import create_engine, text
eng=create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
def q(sql, **kw):
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return pd.read_sql(text(sql), c, params=kw)
pd.set_option('display.width',250); pd.set_option('display.max_columns',50); pd.set_option('display.max_rows',200)
print(q("select table_name from information_schema.tables where table_schema='public' order by 1"))
for t in ['chaos_snapshots','attention_picks','attention_race_scans','official_win_payouts','exotic_odds','purchase_records','fetch_throttle_state','market_ev_predictions','recommendations']:
    print('=== ',t)
    print(q("select column_name,data_type from information_schema.columns where table_name=:t order by ordinal_position", t=t))
