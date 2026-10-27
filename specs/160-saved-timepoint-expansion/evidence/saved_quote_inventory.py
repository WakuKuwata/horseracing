"""Read saved archive only; no network, database, final-price parsing, or inference.

The result archive is inventoried by pathname only and never opened. Entry HTML is
checked for literal server-rendered odds spans; linked JS requests are not executed.
"""
from __future__ import annotations
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import re
from collections import Counter
from urllib.parse import urlsplit, parse_qs
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
STAMP=re.compile(r'^(\d{8}T\d{12}Z)(?:-\d+)?\.html\.gz$')
ODDS=re.compile(rb'<span\b[^>]*\bid=["\']odds-1_(\d+)["\'][^>]*>(.*?)</span>',re.I|re.S)
NUM=re.compile(rb'\s*\d+(?:\.\d+)?\s*')

def main():
    race_frame=pd.read_parquet(ROOT/'artifacts/158-prerace-roi-comparison/snapshot/races.parquet')
    race_info=race_frame.set_index('race_id').to_dict('index')
    paths=[]; rows=[]; groups=Counter();group_files=Counter()
    for dependency in [Path(__file__), ROOT/'artifacts/158-prerace-roi-comparison/snapshot/races.parquet']:
        paths.append({'path':str(dependency.relative_to(ROOT)), 'sha256':hashlib.sha256(dependency.read_bytes()).hexdigest()})
    for marker in sorted((ROOT/'artifacts/scrape_archive/race.netkeiba.com').glob('*/url.txt')):
        raw_marker=marker.read_bytes();url=raw_marker.decode().strip();parts=urlsplit(url)
        key=parts.path;files=sorted(marker.parent.glob('*.gz'));groups[key]+=1;group_files[key]+=len(files)
        paths.append({'path':str(marker.relative_to(ROOT)),'sha256':hashlib.sha256(raw_marker).hexdigest()})
        if key!='/race/shutuba.html':continue
        rid=parse_qs(parts.query).get('race_id',[None])[0]
        for file in files:
            raw_gz=file.read_bytes();raw=gzip.decompress(raw_gz)
            stamp=STAMP.fullmatch(file.name)
            captured=(dt.datetime.strptime(stamp.group(1),'%Y%m%dT%H%M%S%fZ').replace(tzinfo=dt.UTC) if stamp else None)
            values=ODDS.findall(raw)
            numerical=[(int(k),v.decode('ascii').strip()) for k,v in values if NUM.fullmatch(v)]
            post=race_info.get(rid,{}).get('post_time')
            rows.append({'race_id':rid,'archive_path':str(file.relative_to(ROOT)),
                'archive_sha256':hashlib.sha256(raw_gz).hexdigest(),'captured_at':captured,
                'timestamp_source':'archive_filename' if captured else None,
                'odds_span_count':len(values),'numeric_win_odds_count':len(numerical),
                'numeric_win_odds_json':json.dumps(numerical),'official_complete_158':rid in race_info,
                'prepost_158':bool(captured<post) if post is not None and captured else None})
    frame=pd.DataFrame(rows)
    frame.to_parquet(OUT/'saved_shutuba_inventory.parquet',index=False)
    paths.extend({'path':r['archive_path'],'sha256':r['archive_sha256']} for r in rows)
    summary={'new_collection':False,'db_access':False,'result_html_opened':False,
       'archive_groups':[{'url_path':key,'markers':groups[key],'gzip_versions':group_files[key]} for key in sorted(groups)],
       'odds_api_archive_groups':groups['/api/api_get_jra_odds.html'],
       'shutuba_versions':len(frame),'shutuba_races':int(frame.race_id.nunique()),
       'with_odds_span':int((frame.odds_span_count>0).sum()),
       'with_any_numeric_win_odds':int((frame.numeric_win_odds_count>0).sum()),
       'with_complete_numeric_win_odds':int(((frame.numeric_win_odds_count==frame.odds_span_count)&(frame.odds_span_count>=2)).sum()),
       'official_complete_158_versions':int(frame.official_complete_158.sum()),
       'official_complete_158_races':int(frame.loc[frame.official_complete_158,'race_id'].nunique()),
       'official_complete_prepost_versions':int(frame.prepost_158.fillna(False).sum()),
       'official_complete_prepost_races':int(frame.loc[frame.prepost_158.fillna(False),'race_id'].nunique()),
       'numeric_versions_by_race':frame.loc[frame.numeric_win_odds_count>0,['race_id','archive_path','captured_at','numeric_win_odds_count']].assign(captured_at=lambda x:x.captured_at.astype(str)).to_dict('records')}
    (OUT/'saved_quote_inventory.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (OUT/'saved_quote_inventory_input_hashes.json').write_text(json.dumps(paths,indent=2)+'\n')
    old=pd.read_parquet(ROOT/'artifacts/roi_explore/missed_20261004/R02_odds_drift/db_chaos.parquet').rename(columns={'snap_id':'chaos_snapshot_id'})
    all_=pd.read_parquet(ROOT/'artifacts/155-saved-odds-settlement-audit/snapshot/chaos.parquet')
    new=pd.read_parquet(ROOT/'artifacts/158-prerace-roi-comparison/snapshot/chaos.parquet')
    def copy_check(a,b):
        merged=a.merge(b,on='chaos_snapshot_id',suffixes=('_old','_new'),how='left',indicator=True)
        shared=merged.loc[merged._merge=='both']
        return {'rows':len(a),'shared_ids':len(shared),'not_in_later':int((merged._merge=='left_only').sum()),
            'shared_equal':{f:int((shared[f+'_old']==shared[f+'_new']).sum()) for f in ['race_id','captured_at','field_json']},
            'shared_races':int(a[a.race_id.isin(b.race_id)].race_id.nunique())}
    copies={'r02_to_155':copy_check(old,all_),'r02_to_158':copy_check(old,new),
        '155_chaos_rows':len(all_),'155_unique_races':int(all_.race_id.nunique()),
        '158_chaos_rows':len(new),'158_unique_races':int(new.race_id.nunique())}
    (OUT/'saved_chaos_copy_check.json').write_text(json.dumps(copies,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
