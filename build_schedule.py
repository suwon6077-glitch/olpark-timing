"""
연계 데이터(linked_raw.csv) → 공연 일정 JSON (docs/schedule.json)

사용법: python build_schedule.py linked_raw.csv docs/schedule.json

연계 데이터는 1시간 단위 행이고, 공연이 있는 시간대에 공연명과 관람객 수가 들어 있다.
같은 날 같은 공연이 표시된 시간대를 묶어 하루 한 줄의 일정으로 만든다.
그날의 최대 주차대수와 그 시각도 함께 넣는다.
"""
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

KEYS = ["공연명", "공연관람객수", "주차대수", "방문객", "기온", "풍향", "강수량", "습도", "휴일"]
OPEN = "([{［〈《「『‘“<（【"
CLOSE = ")]}］〉》」』’”>）】"


def kv(s):
    """'공연명:A, B,공연관람객수:10000' → dict. 값 안의 쉼표는 다음 '키:' 앞에서만 끊는다."""
    out = {}
    for part in re.split(r",(?=\s*(?:" + "|".join(KEYS) + r"):)", s or ""):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def split_titles(s):
    """여러 공연이 ', '로 이어진 경우 나눈다. 괄호·따옴표 안의 쉼표는 무시."""
    titles, buf, depth = [], "", 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth = max(0, depth - 1)
        if depth == 0 and s.startswith(", ", i):
            titles.append(buf.strip())
            buf = ""
            i += 2
            continue
        buf += ch
        i += 1
    if buf.strip():
        titles.append(buf.strip())
    merged = []  # '안녕, 겨울'처럼 제목 안의 쉼표로 잘린 짧은 조각은 앞 제목에 다시 붙인다
    for t in titles:
        if merged and len(t) < 6:
            merged[-1] += ", " + t
        else:
            merged.append(t)
    return merged


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def build(src, dst):
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    shows = defaultdict(lambda: {"hours": [], "aud": 0})
    peak = {}
    nhours = defaultdict(int)
    seen = set()
    for r in rows:
        t = r.get("ISSUED_DATE", "")
        if not t or t in seen:
            continue
        seen.add(t)
        ts = datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S")
        day = ts.strftime("%Y-%m-%d")
        a, b = kv(r.get("TITLE")), kv(r.get("DESCRIPTION"))
        nhours[day] += 1
        cars = num(b.get("주차대수"))
        if cars is not None and (day not in peak or cars > peak[day][0]):
            peak[day] = (cars, ts.hour)
        name = a.get("공연명", "")
        aud = num(a.get("공연관람객수")) or 0
        if not name:
            continue
        for title in split_titles(name):
            k = (day, title)
            shows[k]["hours"].append(ts.hour)
            shows[k]["aud"] = max(shows[k]["aud"], aud)

    items = []
    for (day, title), v in shows.items():
        hs = sorted(v["hours"])
        d = datetime.strptime(day, "%Y-%m-%d")
        p = peak.get(day) if nhours[day] >= 20 else None  # 하루 기록이 덜 찬 날은 최대 주차를 비운다
        items.append({
            "date": day,
            "dow": "월화수목금토일"[d.weekday()],
            "title": title,
            "from": hs[0],
            "to": hs[-1] + 1,          # 공연 표시가 끝나는 시각(정시)
            "audience": int(v["aud"]),  # 같은 시간대 공연이 여럿이면 합계일 수 있음
            "peakCars": int(p[0]) if p else None,
            "peakHour": p[1] if p else None,
        })
    items.sort(key=lambda x: (x["date"], x["from"], x["title"]))
    kst = timezone(timedelta(hours=9))
    out = {
        "updated": datetime.now(kst).isoformat(timespec="minutes"),
        "source": "한국체육산업개발(주) 올림픽공원 차량입출차·공연·날씨 연계 데이터",
        "range": [items[0]["date"], items[-1]["date"]] if items else None,
        "items": items,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"공연 {len(items)}건, 기간 {out['range']}, 저장: {dst}")


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
