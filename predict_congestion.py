"""
다가오는 공연일의 출구별 혼잡 예측 → docs/congestion.json  (랜덤포레스트, 공연 전에 알 수 있는 정보만 사용)

학습: analysis/rf_train.csv
  2025년 1~7월 동·남·북2문 × 저녁 17~24시 30분 구간 8,904개. 정답 y = 0 원활 · 1 보통 · 2 혼잡
  (그 30분 중 가장 붐빈 10분의 출차 대수 ÷ 출구 10분 처리 능력: 50% 미만 / 50~85% / 85% 이상)
  analysis/rf_build.py · rf_final.py 로 만든 표에서 공연 전에 알 수 있는 9개 변수만 남긴 것
입력: docs/schedule.json 의 다가오는 공연(est=true: 올림픽공원 홈페이지·KOPIS·직접 입력)
출력: 날짜마다 출구 3곳 × 30분 구간 14개의 등급 확률과 예상 대기(분)

등급(cls)은 검증한 그대로 모델이 가장 높게 본 등급이다. 다만 학습 때 드문 등급에 가중치를 줘서
모델 확률은 혼잡을 실제보다 약 2배 높게 본다. 그래서 확률(p)과 예상 대기에는 등급별 실제 비율의 제곱근을 곱해
다시 맞춘 값을 쓴다(날짜 단위 5겹 검증에서 로그 손실이 가장 작고 평균 예상 대기가 실제와 맞는 보정).

예상 대기 = P(원활)×1 + P(보통)×5 + P(혼잡)×13 분.
  등급별 대기는 1~7월 입출차에서 출구가 처리 능력 한계로 출차한 연속 시간(포화 시간)의 절반:
  보통 수준(관람객 1.2만~1.5만 명 날 출구 포화 10분) → 5분, 혼잡 수준(1.5만 명 이상 날 남2문 25분) → 13분.

매번 학습부터 다시 하므로(약 10초) 모델 파일을 저장소에 두지 않는다. random_state를 고정해 결과는 같다.
사용법: python predict_congestion.py docs/schedule.json analysis/rf_train.csv docs/congestion.json [--days 60]
"""
import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

KST = timezone(timedelta(hours=9))
GATES = ["동2문", "남2문", "북2문"]          # gate 코드 0, 1, 2 (학습 표와 같은 순서)
FEATURES = ["gate", "clock", "after_end", "after_start", "show", "aud", "shows", "dow", "offday"]
WINDOWS = [17 * 60 + 30 * k for k in range(14)]  # 17:00 ~ 23:30 시작 30분 구간
WAIT = [1, 5, 13]                                # 원활 · 보통 · 혼잡 예상 대기(분)
LABEL = ["원활", "보통", "혼잡"]
# 학습과 같은 설정(중첩 교차검증에서 가장 많이 고른 값)
PARAMS = dict(n_estimators=300, max_depth=14, min_samples_leaf=3, max_features=0.5,
              class_weight="balanced_subsample", n_jobs=-1, random_state=0)
# 날짜 단위·주 단위·시간 순 검증 결과 (analysis/rf_advance_eval.py)
CV = {"매크로F1": {"날짜 단위 5겹": 0.609, "주 단위 5겹": 0.600, "시간 순(1~5월→6~7월)": 0.627},
      "혼잡F1": {"날짜 단위 5겹": 0.403, "주 단위 5겹": 0.382, "시간 순(1~5월→6~7월)": 0.387},
      "단순 기준 매크로F1(시간 순)": 0.462}


def holidays_kr(years):
    try:
        import holidays
        h = holidays.KR(years=years)
        return {d for d in h}
    except Exception:  # 패키지가 없으면 주말만 쉬는 날로 본다
        return set()


def day_features(items):
    """그날 공연들 → 학습 표와 같은 정의의 하루 변수"""
    aud = max(int(x.get("audience") or 0) for x in items)
    start_h = max(int(x.get("from") or 0) for x in items)   # 그날 가장 늦게 시작하는 공연의 시작 시(時)
    end_h = max(int(x.get("to") or 0) for x in items)       # 그날 가장 늦게 끝나는 공연의 종료 시(時)
    shows = len({x.get("title") for x in items})
    return aud, start_h, end_h, shows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("schedule")
    ap.add_argument("train")
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()

    T = pd.read_csv(a.train)
    model = RandomForestClassifier(**PARAMS).fit(T[FEATURES], T["y"])
    prior = np.bincount(T["y"], minlength=3) / len(T)
    print(f"학습 {len(T)}구간, 등급 {np.bincount(T['y']).tolist()}")

    S = json.load(open(a.schedule, encoding="utf-8"))
    today = datetime.now(KST).date()
    last = today + timedelta(days=a.days)
    by_day = defaultdict(list)
    for x in S.get("items", []):
        if x.get("est") and today.isoformat() <= x["date"] <= last.isoformat():
            by_day[x["date"]].append(x)
    hol = holidays_kr({today.year, last.year})

    rows, keys = [], []
    for d, items in sorted(by_day.items()):
        dd = date.fromisoformat(d)
        aud, start_h, end_h, shows = day_features(items)
        offday = int(dd.weekday() >= 5 or dd in hol)
        for g in range(3):
            for c in WINDOWS:
                rows.append([g, c, c - end_h * 60, c - start_h * 60, 1, aud, shows, dd.weekday(), offday])
                keys.append((d, g, c))
    out = {"updated": datetime.now(KST).isoformat(timespec="minutes"),
           "model": {"name": "랜덤포레스트 사전 예측 (공연 전에 알 수 있는 정보만 사용)",
                     "features": ["출구", "시각", "공연 종료 후 지난 시간", "공연 시작 후 지난 시간", "공연 여부",
                                  "그날 관람객 수", "그날 공연 수", "요일", "주말·공휴일"],
                     "train": "2025년 1~7월 입출차 8,904구간", "cv": CV},
           "waitByClass": dict(zip(LABEL, WAIT)),
           "note": "p는 실제 비율로 다시 맞춘 확률, cls는 모델이 고른 등급, wait는 p로 계산한 예상 대기(분)",
           "days": {}}
    if rows:
        P = model.predict_proba(pd.DataFrame(rows, columns=FEATURES))
        Q = P * np.sqrt(prior)
        Q = Q / Q.sum(axis=1, keepdims=True)
        for (d, g, c), raw, p in zip(keys, P, Q):
            items = by_day[d]
            day = out["days"].setdefault(d, dict(zip(["aud", "startHour", "endHour", "shows"], day_features(items)),
                                                 gates={x: [] for x in GATES}))
            day["gates"][GATES[g]].append({"t": f"{c // 60:02d}:{c % 60:02d}",
                                           "p": [round(float(v), 3) for v in p],
                                           "cls": LABEL[int(np.argmax(raw))],
                                           "wait": round(float(np.dot(p, WAIT)), 1)})
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"예측 {len(out['days'])}일 → {a.out}")
    for d, v in list(out["days"].items())[:6]:
        top = max(((g, w) for g, ws in v["gates"].items() for w in ws), key=lambda gw: gw[1]["p"][2])
        print(f"  {d} 관람객 {v['aud']:,} 종료 {v['endHour']}시 · 혼잡 확률 최고 {top[0]} {top[1]['t']} {top[1]['p'][2]:.0%}")


if __name__ == "__main__":
    main()
