"""랜덤포레스트 출구 혼잡 분류 — 학습용 표 만들기
입력: inout_janjul.parquet(2025년 1~7월 입출차), linked_hourly.parquet(연계 데이터), schedule.json(공연 일정)
행 단위: 날짜 × 출구(동2·남2·북2) × 10분 구간(17:00~23:50)
정답: 그 10분 동안 출구 출차량 / 출구 처리 능력 → 원활(<50%) · 보통(50~85%) · 혼잡(≥85%)
"""
import json, numpy as np, pandas as pd

GATES = ['동2문', '남2문', '북2문']
d = pd.read_parquet('inout_janjul.parquet', columns=['출구', '출구문', '출차'])
d = d[d['출구문'].isin(GATES) & (d['출차'].dt.year == 2025)]
print('출차 기록(1~7월, 3개 출구):', len(d))

# 처리 능력: 차로별 10분 통과 대수 상위 0.5% 값의 합 (CMB 무인정산 차로 제외)
lanes = d[~d['출구'].str.contains('CMB')]
lane_cap = lanes.groupby(['출구', lanes['출차'].dt.floor('10min')]).size().groupby(level=0).quantile(.995)
cap = lane_cap.groupby(lambda s: s[:3]).sum().to_dict()
print('출구 10분 처리 능력:', cap)

d['day'] = d['출차'].dt.normalize()
d['bin'] = ((d['출차'] - d['day']).dt.total_seconds() // 600).astype(int)   # 0..143
ev = d[d['bin'] >= 17 * 6]
cnt = ev.groupby(['day', '출구문', 'bin']).size().rename('n')
days = pd.date_range('2025-01-01', '2025-07-31', freq='D')
idx = pd.MultiIndex.from_product([days, GATES, range(17 * 6, 24 * 6)], names=['day', '출구문', 'bin'])
T = cnt.reindex(idx, fill_value=0).reset_index()
T['cap'] = T['출구문'].map(cap)
T['ratio'] = T.n / T.cap
T['y'] = pd.cut(T.ratio, [-1, .5, .85, 99], labels=[0, 1, 2]).astype(int)   # 0 원활, 1 보통, 2 혼잡

# 공연 일정(연계 데이터에서 만든 schedule.json): 그날 관람객, 저녁 공연 시작·종료 시각
S = pd.DataFrame(json.load(open('docs/schedule.json'))['items'])
S = S[~S.get('est', pd.Series(False, index=S.index)).fillna(False).astype(bool)]
S['day'] = pd.to_datetime(S.date)
g = S.groupby('day').agg(aud=('audience', 'max'), start_h=('from', 'max'), end_h=('to', 'max'), shows=('title', 'nunique'))
T = T.merge(g, left_on='day', right_index=True, how='left')
T['show'] = T.aud.notna().astype(int)
T['aud'] = T.aud.fillna(0)
T['shows'] = T.shows.fillna(0)
T['clock'] = T.bin * 10                           # 하루 중 분
T['after_end'] = np.where(T.show == 1, T.clock - T.end_h * 60, -999)   # 공연 종료 후 지난 분(예정 기준)
T['after_start'] = np.where(T.show == 1, T.clock - T.start_h * 60, -999)

# 날씨·휴일: 연계 데이터 1시간 값
L = pd.read_parquet('linked_hourly.parquet')
L['day'] = L.시각.dt.normalize(); L['h'] = L.시각.dt.hour
HOL = pd.to_datetime(['2025-01-01', '2025-01-27', '2025-01-28', '2025-01-29', '2025-01-30', '2025-03-01', '2025-03-03',
                      '2025-05-05', '2025-05-06', '2025-06-03', '2025-06-06'])
off = L.groupby('day').휴일.apply(lambda s: (s.fillna('').str.strip() != '').any())
T['h'] = T.bin // 6
W = L[['day', 'h', '기온', '강수량', '습도']].drop_duplicates(['day', 'h'])
T = T.merge(W, on=['day', 'h'], how='left')
T['temp'] = T.groupby('day')['기온'].transform(lambda s: s.ffill().bfill())
T['rain'] = T['강수량'].fillna(0)
T['hum'] = T.groupby('day')['습도'].transform(lambda s: s.ffill().bfill())
T['dow'] = T.day.dt.dayofweek
T['offday'] = ((T.dow >= 5) | T.day.isin(HOL) | T.day.map(off).fillna(False)).astype(int)
T['month'] = T.day.dt.month
T['gate'] = T['출구문'].map({g: i for i, g in enumerate(GATES)})
T = T.drop(columns=['기온', '강수량', '습도'])
T.to_parquet('rf_table.parquet')
print('행 수:', len(T), '날짜:', T.day.nunique(), '공연일:', T[T.show == 1].day.nunique())
print('클래스 비율 전체:', T.y.value_counts(normalize=True).sort_index().round(3).to_dict())
print('클래스 비율 공연일:', T[T.show == 1].y.value_counts(normalize=True).sort_index().round(3).to_dict())
print(T.groupby(['출구문', 'y']).size().unstack())
