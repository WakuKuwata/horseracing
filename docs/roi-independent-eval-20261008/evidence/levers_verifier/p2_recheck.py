import numpy as np, pandas as pd, json
P="/Users/kuwatawaku/workspace/horseracing/artifacts/roi_explore/missed_20261004/R02_odds_drift/pairs.parquet"
df=pd.read_parquet(P)
df["mins"]=df.hours*60
df["ld"]=np.log(df.O_F/df.O_J)
df=df[np.isfinite(df.ld)]
edges=[0,10,30,60,180,600,1440,2000]
df["hb"]=pd.cut(df.mins,edges)
def mad_sd(x): 
    x=np.asarray(x); return 1.4826*np.median(np.abs(x-np.median(x)))
# within-race centering
df["ld_c"]=df.ld-df.groupby("pair_id").ld.transform("mean")
rows=[]
for hb,g in df.groupby("hb",observed=True):
    r={"band":str(hb),"pairs":g.pair_id.nunique(),"races":g.race_id.nunique(),"horses":len(g),
       "sd":g.ld.std(),"mean":g.ld.mean(),"mad_sd":mad_sd(g.ld),"sd_within_race":g.ld_c.std(),
       "sd_B4_20-40":g[g.bJ=="B4_20-40"].ld.std(),"n_B4":int((g.bJ=="B4_20-40").sum()),
       "sd_B1-3_le20":g[g.bJ.isin(["B1_1-5","B2_5-10","B3_10-20"])].ld.std(),
       "sd_B5-6_ge40":g[g.bJ.isin(["B5_40-100","B6_100+"])].ld.std(),
       "sd_S3picks":g[g.J_S3].ld.std(),"n_S3":int(g.J_S3.sum()),
       "EVr_med_all":(g.EVe_F/g.EVe_J).median(),"EVr_med_S3":(g.EVe_F/g.EVe_J)[g.J_S3].median(),
       "src":g.drop_duplicates("pair_id").source.value_counts().to_dict()}
    rows.append(r)
out=pd.DataFrame(rows); pd.set_option("display.width",250); pd.set_option("display.max_columns",30)
print(out.round(3).to_string())
# by source within band
print("\n-- sd by band x source")
print(df.groupby(["hb","source"],observed=True).ld.agg(["std","count"]).round(3))
# sd by judged odds band overall
print("\n-- sd by judged odds band (all time)")
print(df.groupby("bJ").ld.agg(["std","mean","count"]).round(3))
# race coverage near post
one=df.drop_duplicates("pair_id")
print("\nraces within min:",{t:int(one.loc[one.mins<=t,"race_id"].nunique()) for t in [2,5,10,30]})
print("min mins observed:",round(one.mins.min(),2))
# EV ratio medians for S1 picks
print("EVr med S1 picks:",round((df.EVe_F/df.EVe_J)[df.J_S1].median(),3),"S3:",round((df.EVe_F/df.EVe_J)[df.J_S3].median(),3),"all:",round((df.EVe_F/df.EVe_J).median(),3))
# naive log-linear fit sd vs log(mins), extrapolate to 2,3,10 min
m=out[out.band!="(1440, 2000]"]
mid=np.array([5,20,45,120,390,1020]); 
for col in ["sd","mad_sd","sd_within_race","sd_B4_20-40"]:
    b=np.polyfit(np.log(mid),m[col].values,1)
    print(col,"fit slope",round(b[0],3),"pred@2min",round(np.polyval(b,np.log(2)),3),"@3",round(np.polyval(b,np.log(3)),3),"@10",round(np.polyval(b,np.log(10)),3),"@30",round(np.polyval(b,np.log(30)),3))
