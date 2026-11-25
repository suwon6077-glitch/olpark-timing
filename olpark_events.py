"""
올림픽공원 공식 홈페이지 '공연/행사 안내' → data/ksponco_events.json

목록: https://www.ksponco.or.kr/olympicpark/eventInfo/eventInfoList?mid=a20301010100
상세: https://www.ksponco.or.kr/olympicpark/eventInfo/eventInfoView.es?mid=a20301010100&evtSeq=번호

1) 목록 페이지에서 evtSeq·제목·장소·기간을 읽는다.
2) evtSeq는 등록 순서대로 늘어나므로, 지금까지 본 가장 큰 번호 다음부터 상세 페이지를 차례로 열어
   목록에 아직 안 뜬 다음 달 공연까지 찾는다(연속 15번 없으면 멈춤).
3) 상세 페이지에서 회차 시각, 러닝타임, 포스터 주소를 읽는다.
이미 본 공연은 data/ksponco_events.json 에 쌓아 두고, 상세는 7일에 한 번만 다시 읽는다.

사용법: python olpark_events.py [--debug debug/]   (--debug: 받은 HTML을 저장해 구조 확인용으로 남김)
"""
import argparse
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
BASE = "https://www.ksponco.or.kr/olympicpark/eventInfo/"
LIST = BASE + "eventInfoList?mid=a20301010100"
VIEW = BASE + "eventInfoView.es?mid=a20301010100&evtSeq={}"
SITE = "https://www.ksponco.or.kr"
STATE = "data/ksponco_events.json"
UA = {"User-Agent": "Mozilla/5.0 (olpark-timing; public-data contest; github.com/suwon6077-glitch/olpark-timing)"}
VENUES = ["KSPO DOME", "올림픽홀", "티켓링크 라이브 아레나", "핸드볼경기장", "우리금융아트홀", "88잔디마당",
          "잔디마당", "평화의광장", "올림픽공원 수영장", "SK올림픽핸드볼경기장", "벨로드롬", "테니스경기장"]
PERIOD = re.compile(r"(\d{4}[-.]\d{2}[-.]\d{2})\s*~\s*(\d{4}[-.]\d{2}[-.]\d{2})")
DOW = "월화수목금토일"
session = requests.Session()
session.headers.update(UA)


def fetch(url, tries=3):
    for i in range(tries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


def d(s):
    return datetime.strptime(s.replace(".", "-"), "%Y-%m-%d").date()


def find_venue(text):
    for v in VENUES:
        if v in text:
            return v
    return ""


def parse_list(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.find_all("a", href=re.compile(r"evtSeq=(\d+)")):
        seq = int(re.search(r"evtSeq=(\d+)", a["href"]).group(1))
        text = a.get_text(" ", strip=True)
        p = PERIOD.search(text)
        strong = a.find(["strong", "b", "h3", "h4", "dt"])
        title = strong.get_text(" ", strip=True) if strong else text[: p.start()].strip() if p else text
        em = a.find(["em", "i"])
        venue = em.get_text(" ", strip=True) if em else find_venue(text)
        img = a.find("img")
        e = out.setdefault(seq, {"seq": seq})
        if title and len(title) > len(e.get("title", "")):
            e["title"] = title
        if venue:
            e["venue"] = venue
        if p:
            e["from"], e["to"] = d(p.group(1)).isoformat(), d(p.group(2)).isoformat()
        if img and img.get("src") and "attachFiles" in img["src"]:
            e["poster"] = SITE + img["src"] if img["src"].startswith("/") else img["src"]
        e["listText"] = text[:300]
    return out


def to24(ampm, h):
    h = int(h)
    if ampm == "오후" and h < 12:
        h += 12
    if ampm == "오전" and h == 12:
        h = 0
    if not ampm and 1 <= h <= 7:  # '일 2시, 6시'처럼 오전/오후 없이 쓴 저녁·오후 시각
        h += 12
    return h


def parse_times(text, d0, d1):
    """회차 시각 → ['YYYY-MM-DD HH:MM', ...]"""
    out = set()
    # 1) 날짜가 적힌 회차: '2026년 11월 27일(금) 오후 7시', '11월 28일 18:00', '11.27(금) 19:00'
    pat = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(?:\([^)]{1,3}\))?\s*"
                     r"(오전|오후)?\s*(\d{1,2})\s*(?:시(?:\s*(\d{1,2})\s*분)?|:(\d{2}))")
    pat2 = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})\s*\([^)]{1,3}\)\s*(오전|오후)?\s*(\d{1,2}):(\d{2})")
    for m in pat.finditer(text):
        y = int(m.group(1)) if m.group(1) else (d0.year if int(m.group(2)) >= d0.month else d1.year)
        h = to24(m.group(4), m.group(5)); mi = int(m.group(6) or m.group(7) or 0)
        try:
            day = date(y, int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if d0 <= day <= d1 and 8 <= h <= 23:
            out.add(f"{day} {h:02d}:{mi:02d}")
    for m in pat2.finditer(text):
        mo, dd = int(m.group(1)), int(m.group(2))
        y = d0.year if mo >= d0.month else d1.year
        try:
            day = date(y, mo, dd)
        except ValueError:
            continue
        h = to24(m.group(3), m.group(4))
        if d0 <= day <= d1 and 8 <= h <= 23:
            out.add(f"{day} {h:02d}:{m.group(5)}")
    if out:
        return sorted(out)
    # 2) 요일로 적힌 회차: '금 19:00 / 토 18:00', '화~금 오후 8시', '평일 20시, 주말 15시'
    wk = {}
    grp = {"평일": range(0, 5), "주말": range(5, 7), "매일": range(0, 7)}
    pat3 = re.compile(r"(평일|주말|매일|[월화수목금토일](?:요일)?(?:\s*[~\-]\s*[월화수목금토일](?:요일)?)?)\s*[:)]?\s*"
                      r"((?:(?:오전|오후)?\s*\d{1,2}\s*(?::\d{2}|시(?:\s*\d{1,2}\s*분)?)\s*[,/·]?\s*)+)")
    for m in pat3.finditer(text):
        key = m.group(1).replace("요일", "").replace(" ", "")
        if key in grp:
            idx = list(grp[key])
        elif re.match(r"[월화수목금토일][~\-][월화수목금토일]", key):
            a, b = DOW.index(key[0]), DOW.index(key[-1])
            idx = list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
        else:
            idx = [DOW.index(key[0])]
        ap = None
        for t in re.finditer(r"(오전|오후)?\s*(\d{1,2})\s*(?::(\d{2})|시(?:\s*(\d{1,2})\s*분)?)", m.group(2)):
            ap = t.group(1) or ap
            h = to24(ap, t.group(2))
            if 8 <= h <= 23:
                for i in idx:
                    wk.setdefault(i, set()).add(f"{h:02d}:{int(t.group(3) or t.group(4) or 0):02d}")
    day = d0
    while day <= d1 and wk:
        for t in sorted(wk.get(day.weekday(), [])):
            out.add(f"{day} {t}")
        day += timedelta(days=1)
    return sorted(out)


def runtime(text, default=150):
    m = re.search(r"(?:관람\s*시간|러닝\s*타임|공연\s*시간)[\s\S]{0,20}?(?:총\s*)?(?:(\d)\s*시간\s*)?(\d{1,3})\s*분", text)
    if m:
        return (int(m.group(1) or 0) * 60 + int(m.group(2))) or default
    m = re.search(r"(?:관람\s*시간|러닝\s*타임)[\s\S]{0,20}?(\d)\s*시간", text)
    return int(m.group(1)) * 60 if m else default


def parse_view(html, seq):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    p = PERIOD.search(text)
    if not p:
        return None
    d0, d1 = d(p.group(1)), d(p.group(2))
    title = ""
    og = soup.find("meta", property="og:title")
    for el in soup.find_all(["h2", "h3", "h4", "strong", "p", "div", "dt"], class_=re.compile(r"tit|subject|name", re.I)):
        t = el.get_text(" ", strip=True)
        if 2 < len(t) < 120 and t not in ("공연/행사", "공연안내", "공연/행사 안내") and not PERIOD.search(t):
            title = t
            break
    if not title:  # '기간' 바로 앞 줄
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            if PERIOD.search(ln) or ln.startswith("기간"):
                title = lines[i - 1] if i else ""
                break
    if not title and og:
        title = og.get("content", "")
    poster = ""
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if "attachFiles/eventInfo" in src:
            poster = SITE + src if src.startswith("/") else src
            break
    # 본문 중 공연 정보 부분만(공연장 소개 문구에서 시각을 잘못 읽지 않도록 기간 이후 2,000자)
    body = text[p.start(): p.start() + 2000]
    return {"seq": seq, "title": title, "from": d0.isoformat(), "to": d1.isoformat(), "poster": poster,
            "sessions": parse_times(body, d0, d1), "runtime": runtime(body), "venueGuess": find_venue(body[:600]),
            "url": VIEW.format(seq)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", default="")
    ap.add_argument("--scan", type=int, default=15, help="연속으로 이만큼 없으면 번호 탐색을 멈춤")
    ap.add_argument("--back", type=int, default=0, help="처음 실행 때 가장 큰 번호에서 이만큼 거슬러 올라가 탐색")
    a = ap.parse_args()
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {"events": {}}
    ev = {int(k): v for k, v in state["events"].items()}
    today = datetime.now(KST).date()

    html = fetch(LIST)
    if not html:
        raise SystemExit("목록 페이지를 받지 못했습니다.")
    if a.debug:
        os.makedirs(a.debug, exist_ok=True)
        open(os.path.join(a.debug, "list.html"), "w", encoding="utf-8").write(html)
    listed = parse_list(html)
    print(f"목록에서 {len(listed)}건: {sorted(listed)}")
    for seq, e in listed.items():
        ev.setdefault(seq, {}).update(e)

    # 상세를 읽을 번호: 목록에 뜬 것 + 기존에 알던 것 중 아직 안 끝난 것(7일마다) + 새 번호 탐색
    todo = set(listed)
    for seq, e in ev.items():
        if e.get("to", "9999") >= today.isoformat() and e.get("checked", "") <= (today - timedelta(days=7)).isoformat():
            todo.add(seq)
    top = max(ev) if ev else 0
    start = top - a.back if a.back else top
    misses, seq, first = 0, start, True
    while misses < a.scan:
        seq += 1
        if seq in ev and seq not in todo:
            continue
        h = fetch(VIEW.format(seq))
        info = parse_view(h, seq) if h else None
        if a.debug and first and info:
            open(os.path.join(a.debug, f"view_{seq}.html"), "w", encoding="utf-8").write(h)
            first = False
        if info:
            misses = 0
            ev.setdefault(seq, {}).update({k: v for k, v in info.items() if v not in ("", None)})
            ev[seq]["checked"] = today.isoformat()
            todo.discard(seq)
        elif seq > top:  # 이미 아는 범위 안의 빈 번호(삭제된 글)는 멈춤 조건에 넣지 않는다
            misses += 1
        time.sleep(0.7)
    for seq in sorted(todo):
        h = fetch(VIEW.format(seq))
        info = parse_view(h, seq) if h else None
        if info:
            if a.debug and first:
                open(os.path.join(a.debug, f"view_{seq}.html"), "w", encoding="utf-8").write(h)
                first = False
            ev[seq].update({k: v for k, v in info.items() if v not in ("", None) and not (k == "title" and ev[seq].get("title"))})
            ev[seq]["checked"] = today.isoformat()
        time.sleep(0.7)

    for e in ev.values():  # 장소: 목록의 장소 > 상세 본문에서 찾은 장소
        e.setdefault("venue", e.get("venueGuess", ""))
    # 끝난 지 1년 넘은 공연은 정리
    keep = {k: v for k, v in ev.items() if v.get("to", "9999") >= (today - timedelta(days=365)).isoformat()}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"updated": datetime.now(KST).isoformat(timespec="minutes"), "source": LIST,
               "events": {str(k): v for k, v in sorted(keep.items())}},
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    up = [v for v in keep.values() if v.get("to", "") >= today.isoformat()]
    print(f"저장 {len(keep)}건 (다가오는 공연 {len(up)}건, 회차 시각을 못 읽은 공연 {sum(1 for v in up if not v.get('sessions'))}건)")


if __name__ == "__main__":
    main()
