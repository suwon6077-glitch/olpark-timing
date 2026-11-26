import pandas as pd, numpy as np, warnings
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GroupKFold
from sklearn.metrics import f1_score, precision_recall_fscore_support, accuracy_score
warnings.filterwarnings('ignore')
T=pd.read_parquet('rf_pred.parquet')
def rf(): return RandomForestClassifier(n_estimators=300,max_depth=14,min_samples_leaf=3,max_features=0.5,class_weight='balanced_subsample',n_jobs=-1,random_state=0)
def mt(y,p):
    pr,rc,f,_=precision_recall_fscore_support(y,p,labels=[0,1,2],zero_division=0)
    return f"acc {accuracy_score(y,p):.3f} mF1 {f1_score(y,p,average='macro'):.3f} 혼잡 P {pr[2]:.3f} R {rc[2]:.3f} F1 {f[2]:.3f}"
wk=T.day.dt.isocalendar().week.values; dg=T.day.dt.strftime('%F').values
sets={'전체(실시간 포함)':['gate','clock','after_end','after_start','show','aud','shows','dow','offday','month','temp','rain','hum','lag_park'],
 '사전:월·날씨·주차 제외':['gate','clock','after_end','after_start','show','aud','shows','dow','offday'],
 '사전+날씨':['gate','clock','after_end','after_start','show','aud','shows','dow','offday','temp','rain','hum']}
for k,F in sets.items():
    for gname,g in [('날짜',dg),('주',wk)]:
        p=np.zeros(len(T),int)
        for a,b in GroupKFold(5).split(T,T.y,g): p[b]=rf().fit(T.iloc[a][F],T.y.iloc[a]).predict(T.iloc[b][F])
        print(k,gname,mt(T.y.values,p),flush=True)
    tr=T[T.month<=5]; te=T[T.month>=6]
    print(k,'시간순',mt(te.y.values,rf().fit(tr[F],tr.y).predict(te[F])),flush=True)
