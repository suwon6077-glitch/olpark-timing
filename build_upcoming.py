"""
다가오는 올림픽공원 공연 → docs/schedule.json 에 추가

연계 데이터(한국체육산업개발)는 지난 공연만 담고 있어서, 앞으로의 공연은 세 곳에서 받는다.
  1) 올림픽공원 공식 홈페이지 공연/행사 안내 (olpark_events.py → data/ksponco_events.json)
  2) KOPIS(공연예술통합전산망) Open API: 송파구 공연 중 공연장이 '올림픽공원'인 것 (KOPIS_KEY 있을 때)
  3) data/upcoming_manual.csv: 직접 적어 넣는 일정
같은 날·같은 공연장 규모·같은 시작 시각이면 1 → 2 → 3 순서로 하나만 남긴다.

사용법: python build_upcoming.py docs/schedule.json [--days 120]
환경변수 KOPIS_KEY 가 없으면 1)은 건너뛰고 2)만 넣는다.

관람객 수는 KOPIS에 없으므로 공연장 규모로 추정한다(est=true). 같은 날 공연이 여럿이면
연계 데이터와 같은 방식으로 그날 합계를 각 공연에 넣는다.
"""
import argparse
import csv
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
API = "http://www.kopis.or.kr/openApi/restful/pblprfr"
SONGPA = ("11", "1171")  # 서울특별시, 송파구
# 공연장별 추정 관람객(1회). 실제 판매량이 아니라 규모 기준 추정값이다.
VENUE_CAP = [
    ("KSPO", 15000), ("체조", 15000),
    ("핸드볼", 5000), ("티켓링크", 5000),
    ("올림픽홀", 3000),
    ("우리금융아트홀", 1200),
]
DEFAULT_CAP = 2000
DOW = "월화수목금토일"


def cap_of(venue):
    for k, v in VENUE_CAP:
        if k in venue:
            return v
    return DEFAULT_CAP


def venue_name(fcltynm):
    """'올림픽공원 (KSPO DOME(올림픽체조경기장))' → 'KSPO DOME(올림픽체조경기장)'"""
    m = re.match(r"\s*올림픽공원\s*\((.*)\)\s*$", fcltynm or "")
    return m.group(1).strip() if m else (fcltynm or "").strip()


def runtime_min(s, default=150):
    """'2시간 10분' → 130, '120분' → 120"""
    if not s:
        return default
    h = re.search(r"(\d+)\s*시간", s)
    m = re.search(r"(\d+)\s*분", s)
    t = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return t or default


def parse_guidance(s):
    """'화요일 ~ 금요일(20:00), 토요일(15:00,19:00), HOL(14:00)' → {요일번호: [시각…], 'HOL': [...]}"""
    out = defaultdict(list)
    for part in re.finditer(r"([^,(]+?)\s*\(([^)]*)\)", s or ""):
        days, times = part.group(1).strip(), re.findall(r"\d{1,2}:\d{2}", part.group(2))
        if not times:
            continue
        if days.upper().startswith("HOL"):
            out["HOL"] += times
            continue
        rng = re.match(r"(.)요일\s*~\s*(.)요일", days)
        if rng:
            a, b = DOW.index(rng.group(1)), DOW.index(rng.group(2))
            idx = list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
        else:
            idx = [DOW.index(c) for c in re.findall(r"(.)요일", days) if c in DOW]
        for i in idx:
            out[i] += times
    return out


def sessions(pf, holidays=frozenset()):
    """공연 상세 → 회차 목록 [(날짜, 시작 datetime)]"""
    g = parse_guidance(pf.get("dtguidance"))
    d0 = datetime.strptime(pf["prfpdfrom"], "%Y.%m.%d").date()
    d1 = datetime.strptime(pf["prfpdto"], "%Y.%m.%d").date()
    out, d = [], d0
    while d <= d1:
        times = g.get("HOL") if (d in holidays and g.get("HOL")) else g.get(d.weekday())
        for t in times or []:
            hh, mm = map(int, t.split(":"))
            out.append(datetime(d.year, d.month, d.day, hh, mm))
        d += timedelta(days=1)
    if not out and d0 == d1:  # 회차 안내가 없는 하루 공연
        out.append(datetime(d0.year, d0.month, d0.day, 18, 0))
    return out


def xml_rows(text):
    root = ET.fromstring(text)
    return [{c.tag: (c.text or "").strip() for c in db} for db in root.iter("db")]


def get(url, params, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200 and r.text.strip().startswith("<"):
                return r.text
        except requests.RequestException:
            pass
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"KOPIS 응답 실패: {url}")


def from_kopis(key, start, days):
    """송파구 공연 목록(31일씩, 100건씩) → 공연장이 올림픽공원인 공연의 회차들"""
    found = {}
    s = start
    end = start + timedelta(days=days)
    while s < end:
        e = min(s + timedelta(days=30), end)
        page = 1
        while True:
            rows = xml_rows(get(API, {
                "service": key, "stdate": s.strftime("%Y%m%d"), "eddate": e.strftime("%Y%m%d"),
                "cpage": page, "rows": 100, "signgucode": SONGPA[0], "signgucodesub": SONGPA[1],
            }))
            for r in rows:
                if "올림픽공원" in r.get("fcltynm", ""):
                    found[r["mt20id"]] = r
            if len(rows) < 100:
                break
            page += 1
            time.sleep(0.5)
        s = e + timedelta(days=1)
    items = []
    for pid in found:
        det = xml_rows(get(f"{API}/{pid}", {"service": key}))
        if not det:
            continue
        pf = det[0]
        venue = venue_name(pf.get("fcltynm"))
        run = runtime_min(pf.get("prfruntime"))
        for st in sessions(pf):
            if not (start <= st.date() <= end):
                continue
            items.append({"date": st.strftime("%Y-%m-%d"), "title": pf.get("prfnm", ""), "venue": venue,
                          "start": st.strftime("%H:%M"), "end": (st + timedelta(minutes=run)).strftime("%H:%M"),
                          "endHour": math.ceil((st.hour * 60 + st.minute + run) / 60), "seats": cap_of(venue),
                          "src": "KOPIS", "id": pid})
        time.sleep(0.3)
    return items


def from_manual(path, start):
    """data/upcoming_manual.csv: date,title,venue,start,end,audience (audience는 비워도 됨)"""
    if not os.path.exists(path):
        return []
    items = []
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        if not r.get("date") or r["date"].startswith("#"):
            continue
        d = datetime.strptime(r["date"].strip(), "%Y-%m-%d").date()
        if d < start:
            continue
        st = (r.get("start") or "18:00").strip()
        en = (r.get("end") or "").strip()
        if not en:
            h, m = map(int, st.split(":"))
            en = f"{(h * 60 + m + 150) // 60:02d}:{(m + 150) % 60:02d}"
        eh, em = map(int, en.split(":"))
        venue = (r.get("venue") or "").strip()
        aud = (r.get("audience") or "").strip()
        items.append({"date": d.isoformat(), "title": r["title"].strip(), "venue": venue, "start": st, "end": en,
                      "endHour": eh + (1 if em else 0), "seats": int(aud) if aud else cap_of(venue), "src": "직접 입력"})
    return items


def from_ksponco(path, start, days):
    if not os.path.exists(path):
        return []
    end = start + timedelta(days=days)
    items = []
    for e in json.load(open(path, encoding="utf-8"))["events"].values():
        if not e.get("from") or e.get("to", "") < start.isoformat():
            continue
        run = int(e.get("runtime") or 150)
        ss = e.get("sessions") or []
        known = bool(ss)
        if not ss:  # 회차 시각을 못 읽었으면 기간 중 매일 18시로 두고 표시
            d0, d1 = datetime.strptime(e["from"], "%Y-%m-%d").date(), datetime.strptime(e["to"], "%Y-%m-%d").date()
            ss = [f"{d0 + timedelta(days=i)} 18:00" for i in range((d1 - d0).days + 1)]
        for s in ss:
            st = datetime.strptime(s, "%Y-%m-%d %H:%M")
            if not (start <= st.date() <= end):
                continue
            venue = e.get("venue", "")
            items.append({"date": st.strftime("%Y-%m-%d"), "title": e.get("title", ""), "venue": venue,
                          "start": st.strftime("%H:%M"), "end": (st + timedelta(minutes=run)).strftime("%H:%M"),
                          "endHour": math.ceil((st.hour * 60 + st.minute + run) / 60), "seats": cap_of(venue),
                          "src": "올림픽공원 홈페이지", "poster": e.get("poster", ""), "url": e.get("url", ""),
                          "timeKnown": known})
    return items


def merge(path, new):
    S = json.load(open(path, encoding="utf-8"))
    old = [x for x in S.get("items", []) if not x.get("est")]  # 지난번에 넣은 예정 공연은 지우고 새로 넣는다
    last_real = max((x["date"] for x in old), default="")
    seen, ups = set(), []
    # KOPIS를 먼저 넣고, 같은 날·같은 공연장 규모·같은 시작 시각인 직접 입력 일정은 중복으로 본다
    rank = {"올림픽공원 홈페이지": 0, "KOPIS": 1}
    for x in sorted(new, key=lambda x: (rank.get(x["src"], 2), x["date"], x["start"])):
        k = (x["date"], x["start"], cap_of(x["venue"]))
        if k in seen or x["date"] <= last_real:
            continue
        seen.add(k)
        ups.append(x)
    # 그날 합계(공연별 하루 최대 회차 기준)
    per_day = defaultdict(dict)
    for x in ups:
        per_day[x["date"]][x["title"]] = x["seats"]
    by_title_day = {}
    for x in ups:  # 하루에 회차가 여럿이면 마지막 회차로 한 줄
        by_title_day[(x["date"], x["title"])] = x
    for (d, t), x in sorted(by_title_day.items()):
        dd = datetime.strptime(d, "%Y-%m-%d")
        old.append({
            "date": d, "dow": DOW[dd.weekday()], "title": t, "venue": x["venue"],
            "from": int(x["start"][:2]), "to": x["endHour"], "start": x["start"], "end": x["end"],
            "audience": sum(per_day[d].values()), "peakCars": None, "peakHour": None,
            "est": True, "src": x["src"],
            **{k: x[k] for k in ("poster", "url") if x.get(k)},
            **({"timeKnown": False} if x.get("timeKnown") is False else {}),
        })
    old.sort(key=lambda x: (x["date"], x["from"], x["title"]))
    S["items"] = old
    S["range"] = [old[0]["date"], old[-1]["date"]] if old else None
    S["updated"] = datetime.now(KST).isoformat(timespec="minutes")
    S["upcomingSource"] = "올림픽공원 홈페이지 공연/행사 안내 + KOPIS + 직접 입력 (관람객은 공연장 규모 기준 추정)"
    json.dump(S, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    n = len(by_title_day)
    from collections import Counter
    print(f"예정 공연 {n}건 추가 {dict(Counter(x['src'] for x in by_title_day.values()))}, 기간 {S['range']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("schedule")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--manual", default="data/upcoming_manual.csv")
    a = ap.parse_args()
    today = datetime.now(KST).date()
    items = from_ksponco("data/ksponco_events.json", today, a.days) + from_manual(a.manual, today)
    key = os.environ.get("KOPIS_KEY", "").strip()
    if key:
        items += from_kopis(key, today, a.days)
    else:
        print("KOPIS_KEY 없음: 직접 입력 일정만 넣습니다.")
    merge(a.schedule, items)
