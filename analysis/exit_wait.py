"""출구별 대기 시간 추정 (누적곡선 방식)
- 공연일 저녁, 3개 주출구(동2·남2·북2)의 5분 단위 출차를 공연 종료 시점(t0) 기준으로 정렬
- t0 = 19:30 이후 주출구 합계가 그날 저녁 최대치의 25%를 처음 넘는 5분 구간
- 수요곡선 F: 소규모 공연일(대기가 거의 없는 날)의 출구별 누적 출차 비율
- 대규모 공연일의 출차곡선 D와 F의 수평 거리 = 출구 도착 후 대기 시간
샘플링 비율과 무관하도록 모든 곡선을 그날 그 출구 총량으로 정규화한다."""
import pandas as pd, numpy as np, json, sys
INOUT = sys.argv[1] if len(sys.argv)>1 else 'inout2025_partial.parquet'
SCHED = sys.argv[2] if len(sys.argv)>2 else '/home/claude/olpark-repo/docs/schedule.json'
OUT = sys.argv[3] if len(sys.argv)>3 else 'exit_wait.json'
MAIN = ['동2문','남2문','북2문']
d = pd.read_parquet(INOUT) if INOUT.endswith('.parquet') else pd.read_csv(INOUT, parse_dates=['입차','출차'])
d = d[d['출구문'].isin(MAIN)].copy()
d['day'] = d['출차'].dt.normalize()
sc = pd.DataFrame(json.load(open(SCHED))['items'])
aud = sc.groupby('date').audience.max(); aud.index = pd.to_datetime(aud.index)
lateshow = sc[sc['from']>=16].date.unique()  # 저녁 공연이 있는 날
days = [pd.Timestamp(x) for x in lateshow if pd.Timestamp(x) in set(d.day)]
BIN = 5; REL = np.arange(-30, 151, BIN)          # t0 기준 -30~150분
rows = []
for day in days:
    x = d[(d.day==day)&(d['출차']>=day+pd.Timedelta(hours=18))]
    if len(x) < 300: continue
    b = ((x['출차']-day).dt.total_seconds()//(BIN*60)).astype(int)
    tot = b.value_counts().sort_index()
    after = tot[tot.index >= (19*60+30)//BIN]
    if after.empty: continue
    t0 = after[after >= 0.25*after.max()].index.min()
    for g in MAIN:
        bg = b[x['출구문']==g]; c = bg.value_counts()
        series = np.array([c.get(t0+r//BIN,0) for r in REL], float)
        n = series[(REL>=0)&(REL<=120)].sum()
        if n < 60: continue
        rows.append(dict(day=day, gate=g, audience=aud.get(day,np.nan), t0_min=t0*BIN, n=n,
                         peak_share=series.max()/series[REL>=0].sum(), counts=series))
R = pd.DataFrame(rows)
R['band'] = pd.cut(R.audience,[0,8000,12000,15000,1e6],labels=['~8천','8천~1.2만','1.2만~1.5만','1.5만+'])
print(R.groupby(['gate','band'],observed=True).agg(days=('day','nunique'),t0=('t0_min','median'),peak=('peak_share','mean')).round(3))
def cum(counts):
    s = counts[REL>=0]; c = np.r_[0, np.cumsum(s)]; return c/c[-1]
T = np.r_[REL[REL>=0], REL[-1]+BIN]          # 곡선 꼭짓점 시각
res = {}
for g in MAIN:
    Rg = R[R.gate==g]
    F = np.mean([cum(c) for c in Rg[Rg.band=='~8천'].counts], axis=0)   # 수요(대기 없는 날)
    res[g] = {}
    for band in ['8천~1.2만','1.2만~1.5만','1.5만+']:
        sub = Rg[Rg.band==band]
        if len(sub) < 3: continue
        D = np.mean([cum(c) for c in sub.counts], axis=0)
        # 출구 도착 시각 s(분) → 도착 순번 F(s) → 같은 순번이 빠져나가는 시각 D^{-1}
        waits = {}
        for s in range(0, 91, 5):
            q = np.interp(s, T, F); out = np.interp(q, D, T)
            waits[s] = round(max(0.0, out - s), 1)
        res[g][band] = dict(days=int(sub.day.nunique()), wait=waits)
json.dump(res, open(OUT,'w'), ensure_ascii=False, indent=1)
for g in res:
    for band,v in res[g].items():
        print(g, band, v['days'], {k:v['wait'][k] for k in (0,10,20,30,45,60,90)})

# 공연 종료 후 출차가 빠지는 속도: 종료 후 120분 출차 중 50%·80%가 나간 시각, 출구 비중
summ = {}
for g in MAIN:
    for band in ['~8천','8천~1.2만','1.2만~1.5만','1.5만+']:
        sub = R[(R.gate==g)&(R.band==band)]
        if len(sub)<3: continue
        D = np.mean([cum(c) for c in sub.counts], axis=0)
        summ.setdefault(band,{})[g] = dict(t50=round(float(np.interp(.5,D,T))), t80=round(float(np.interp(.8,D,T))))
share = R.groupby(['band','gate'],observed=True).n.sum().unstack(); share = share.div(share.sum(axis=1),axis=0).round(3)
for band in summ:
    for g in summ[band]: summ[band][g]['share'] = float(share.loc[band,g])
print(json.dumps(summ, ensure_ascii=False, indent=1))
json.dump(dict(wait=res, clear=summ), open(OUT,'w'), ensure_ascii=False, indent=1)
