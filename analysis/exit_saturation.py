"""출구 포화 시간 → 대기 시간 추정 (웹 SCN 값의 근거)
사용: python exit_saturation.py inout.parquet docs/schedule.json
- 처리 능력: 차로별 5분 통과 대수의 상위 0.5% 값, 출구 = 차로 합
- 포화 시간: 18~23시에 출구 5분 출차가 처리 능력의 85% 이상인 최장 연속 구간
- 평균 대기 ≈ 포화 시간 / 2"""
import sys, json, pandas as pd
d = pd.read_parquet(sys.argv[1], columns=['출구','출구문','출차'])
d = d[d['출구문'].isin(['동2문','남2문','북2문']) & d['출차'].dt.month.between(1,7)]
d['b'] = d['출차'].dt.floor('5min'); d['day'] = d['출차'].dt.normalize()
lane = d.groupby(['출구','b']).size().groupby(level=0).quantile(.995)
cap = lane.groupby(lambda s: s[:3]).sum().to_dict(); print('출구 5분 처리 능력', cap)
sc = pd.DataFrame(json.load(open(sys.argv[2]))['items'])
aud = sc.groupby('date').audience.max(); aud.index = pd.to_datetime(aud.index)
ev = d[d['출차'].dt.hour.between(18,23)]
out = []
for (day,g), x in ev.groupby(['day','출구문']):
    x = x.groupby('b').size().asfreq('5min', fill_value=0)
    best = run = 0
    for v in (x >= 0.85*cap[g]): run = run+1 if v else 0; best = max(best, run)
    out.append((day, g, best*5))
o = pd.DataFrame(out, columns=['day','gate','sat_min']); o['aud'] = o.day.map(aud).fillna(0)
o['band'] = pd.cut(o.aud, [-1,0,8000,12000,15000,1e6], labels=['공연없음','~8천','8천~1.2만','1.2만~1.5만','1.5만+'])
t = o.groupby(['band','gate'], observed=True).sat_min.agg(['size','median', lambda s: s.quantile(.75), 'max'])
t.columns = ['days','포화_중앙값','포화_상위25%','포화_최대']; print(t)
