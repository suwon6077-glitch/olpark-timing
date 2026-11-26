"""랜덤포레스트 출구 혼잡 분류 — 비교·튜닝·해석
검증: 날짜 단위 5겹 교차검증(GroupKFold). 같은 날의 구간이 학습과 평가에 섞이지 않게 한다.
튜닝: 바깥 5겹마다 학습 데이터 안에서 다시 날짜 단위 3겹으로 하이퍼파라미터를 고른다(중첩 교차검증).
"""
import json, warnings, numpy as np, pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import make_pipeline
from sklearn.metrics import f1_score, accuracy_score, precision_recall_fscore_support, confusion_matrix
from sklearn.inspection import permutation_importance
warnings.filterwarnings('ignore')

T0 = pd.read_parquet('rf_table.parquet')
# 직전 1시간 공원 전체 주차대수(연계 데이터; 서비스에서는 실시간 주차현황 API로 대신함)
Lk = pd.read_parquet('linked_hourly.parquet'); Lk['day'] = Lk.시각.dt.normalize(); Lk['h'] = Lk.시각.dt.hour
P = Lk.drop_duplicates(['day', 'h']).set_index(['day', 'h']).주차대수
T0['lag_park'] = [P.get((d, h - 1), np.nan) for d, h in zip(T0.day, T0.h)]
T0['lag_park'] = T0.lag_park.fillna(-1)
# 30분 창: 그 30분 안에서 가장 붐빈 10분의 출차량/처리 능력으로 등급을 정한다
T0['w'] = T0.bin // 3
KEEP = ['gate', 'clock', 'after_end', 'after_start', 'show', 'aud', 'shows', 'dow', 'offday', 'month', 'temp', 'rain', 'hum', 'lag_park', 'h']
T = T0.groupby(['day', '출구문', 'w']).agg(ratio=('ratio', 'max'), n=('n', 'sum'), **{c: (c, 'first') for c in KEEP}).reset_index()
T['y'] = pd.cut(T.ratio, [-1, .5, .85, 99], labels=[0, 1, 2]).astype(int)
F = ['gate', 'clock', 'after_end', 'after_start', 'show', 'aud', 'shows', 'dow', 'offday', 'month', 'temp', 'rain', 'hum', 'lag_park']
NAMES = {'gate': '출구', 'clock': '시각(10분)', 'after_end': '공연 종료 후 지난 시간', 'after_start': '공연 시작 후 지난 시간',
         'show': '공연 여부', 'aud': '그날 관람객 수', 'shows': '그날 공연 수', 'dow': '요일', 'offday': '주말·공휴일',
         'month': '월', 'temp': '기온', 'rain': '강수량', 'hum': '습도', 'lag_park': '직전 1시간 주차대수'}
X, y, grp = T[F], T.y.values, T.day.dt.strftime('%Y-%m-%d').values
outer = GroupKFold(n_splits=5)

def logit():
    ct = ColumnTransformer([('oh', OneHotEncoder(handle_unknown='ignore'), ['gate', 'dow', 'month']),
                            ('sc', StandardScaler(), [f for f in F if f not in ('gate', 'dow', 'month')])])
    return make_pipeline(ct, LogisticRegression(max_iter=2000, class_weight='balanced'))

GRID = [dict(max_depth=d, min_samples_leaf=l, max_features=m)
        for d in (None, 14) for l in (3, 10) for m in ('sqrt', 0.5)]

def rf(**kw):
    return RandomForestClassifier(n_estimators=300, class_weight='balanced_subsample', n_jobs=-1, random_state=0, **kw)

def tune(Xtr, ytr, gtr):
    best, bs = None, -1
    for p in GRID:
        sc = []
        for a, b in GroupKFold(n_splits=3).split(Xtr, ytr, gtr):
            m = RandomForestClassifier(n_estimators=120, class_weight='balanced_subsample', n_jobs=-1, random_state=0, **p)
            m.fit(Xtr.iloc[a], ytr[a]); sc.append(f1_score(ytr[b], m.predict(Xtr.iloc[b]), average='macro'))
        if np.mean(sc) > bs: best, bs = p, np.mean(sc)
    return best

pred = {k: np.zeros(len(T), int) for k in ['다수 클래스', '로지스틱 회귀', '의사결정나무', '랜덤포레스트']}
proba = np.zeros((len(T), 3)); chosen = []; imps = []
for k, (tr, te) in enumerate(outer.split(X, y, grp)):
    Xtr, Xte, ytr = X.iloc[tr], X.iloc[te], y[tr]
    pred['다수 클래스'][te] = np.bincount(ytr).argmax()
    pred['로지스틱 회귀'][te] = logit().fit(Xtr, ytr).predict(Xte)
    pred['의사결정나무'][te] = DecisionTreeClassifier(max_depth=8, min_samples_leaf=20, class_weight='balanced', random_state=0).fit(Xtr, ytr).predict(Xte)
    p = tune(Xtr, ytr, grp[tr]); chosen.append(p)
    m = rf(**p).fit(Xtr, ytr)
    pred['랜덤포레스트'][te] = m.predict(Xte); proba[te] = m.predict_proba(Xte)
    pi = permutation_importance(m, Xte, y[te], scoring='f1_macro', n_repeats=3, random_state=0, n_jobs=-1)
    imps.append(pi.importances_mean)
    print(f'fold {k+1}: {p}', flush=True)

def metrics(mask):
    out = {}
    for k, p in pred.items():
        pr, rc, f1, _ = precision_recall_fscore_support(y[mask], p[mask], labels=[0, 1, 2], zero_division=0)
        out[k] = {'정확도': round(accuracy_score(y[mask], p[mask]), 3), '매크로F1': round(f1_score(y[mask], p[mask], average='macro'), 3),
                 '혼잡_정밀도': round(pr[2], 3), '혼잡_재현율': round(rc[2], 3), '혼잡_F1': round(f1[2], 3)}
    return out

ev = (T.show == 1) & (T.clock >= 18 * 60)
print('행', len(T), '클래스', np.bincount(y))
res = {'전체': metrics(np.ones(len(T), bool)), '공연일 18시 이후': metrics(ev.values)}
cm = confusion_matrix(y, pred['랜덤포레스트'], labels=[0, 1, 2]).tolist()
cm_ev = confusion_matrix(y[ev.values], pred['랜덤포레스트'][ev.values], labels=[0, 1, 2]).tolist()
imp = pd.Series(np.mean(imps, axis=0), index=[NAMES[f] for f in F]).sort_values(ascending=False)
print(json.dumps(res, ensure_ascii=False, indent=1)); print('혼동행렬 전체', cm); print('혼동행렬 공연일 저녁', cm_ev)
print(imp.round(4).to_string())
T['rf_pred'] = pred['랜덤포레스트']; T[['p0', 'p1', 'p2']] = proba
T.to_parquet('rf_pred.parquet')
import pickle; pickle.dump(rf(**chosen[0]).fit(X, y), open('rf_final.pkl', 'wb'))
json.dump({'metrics': res, 'cm': cm, 'cm_event': cm_ev, 'importance': imp.round(4).to_dict(), 'chosen': [str(c) for c in chosen],
           'rows': len(T), 'days': int(T.day.nunique()), 'show_days': int(T[T.show == 1].day.nunique()),
           'class_share': T.y.value_counts(normalize=True).sort_index().round(4).tolist()},
          open('rf_results.json', 'w'), ensure_ascii=False, indent=1)
