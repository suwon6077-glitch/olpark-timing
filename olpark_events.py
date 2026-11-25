"""
올림픽공원 공식 홈페이지 '공연/행사 안내' → data/ksponco_events.json

목록 화면은 달마다 아래 주소에서 JSON을 받아 그린다(페이지 스크립트에 적힌 방식 그대로).
  POST https://www.ksponco.or.kr/olympicpark/eventInfo/eventInfoListAjax.es
       {"nowYM": "202612", "plcCd": ""}  → [[이번 달 공연...], [지난 공연...]]
  항목: evtSeq, title, plc(공연장), beginDt, endDt, tm(공연 시간 안내), fileNm(포스터)
이번 달부터 앞으로 6개월을 받고, 실패하면 목록 HTML(이번 달)로 대신한다.
상세 페이지(eventInfoView.es?evtSeq=번호)에서는 소요시간과 더 자세한 공연 시간을 읽는다(7일에 한 번).

사용법: python olpark_events.py [--months 6] [--debug debug/]
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
SITE = "https://www.ksponco.or.kr"
CTX = SITE + "/olympicpark"
LIST = CTX + "/eventInfo/eventInfoList?mid=a20301010100"
AJAX = CTX + "/eventInfo/eventInfoListAjax.es"
VIEW = CTX + "/eventInfo/eventInfoView.es?mid=a20301010100&evtSeq={}"
STATE = "data/ksponco_events.json"
DOW = "월화수목금토일"
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (olpark-timing; public-data contest)", "Referer": LIST})


def get(url, tries=3):
    for i in range(tries):
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


def ymd(x):
    x = re.sub(r"\D", "", str(x or ""))[:8]
    return f"{x[:4]}-{x[4:6]}-{x[6:8]}" if len(x) == 8 else ""


def month_list(ym):
    """한 달 치 JSON → [{seq,title,venue,from,to,tm,poster}]"""
    for i in range(3):
        try:
            r = s.post(AJAX, json={"nowYM": ym, "plcCd": ""}, timeout=30,
                       headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
            if r.status_code == 200:
                data = r.json()
                rows = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else data
                return [{"seq": int(it["evtSeq"]), "title": (it.get("title") or "").strip(),
                         "venue": (it.get("plc") or "").strip(), "from": ymd(it.get("beginDt")),
                         "to": ymd(it.get("endDt")), "tm": (it.get("tm") or "").strip(),
                         "poster": f"{CTX}/attachFiles/eventInfo/{it['fileNm']}" if it.get("fileNm") else ""}
                        for it in rows if it.get("evtSeq")]
        except (requests.RequestException, ValueError, KeyError, TypeError):
            pass
        time.sleep(2 * (i + 1))
    return None


def html_list(html):
    """목록 HTML(a.item) → 같은 형식"""
    out = []
    for a in BeautifulSoup(html, "html.parser").select("a.item[href*=evtSeq]"):
        m = re.search(r"evtSeq=(\d+)", a["href"])
        dt = a.select_one(".date")
        p = re.findall(r"\d{4}-\d{2}-\d{2}", dt.get_text() if dt else "")
        img = a.select_one("img")
        tx = lambda sel: (a.select_one(sel).get_text(" ", strip=True) if a.select_one(sel) else "")
        out.append({"seq": int(m.group(1)), "title": tx(".title"), "venue": tx(".place"),
                    "from": p[0] if p else "", "to": p[-1] if p else "", "tm": tx(".time"),
                    "poster": SITE + img["src"] if img and img.get("src", "").startswith("/") else ""})
    return out


def view(seq):
    """상세 페이지 → {title, period, 시간, 소요시간, poster}"""
    h = get(VIEW.format(seq))
    if not h:
        return None
    soup = BeautifulSoup(h, "html.parser")
    box = soup.select_one(".concert_view") or soup
    info = {}
    for li in box.select(".detail_info li"):
        k, v = li.find("em"), li.find("span")
        if k and v:
            info[k.get_text(strip=True)] = v.get_text(" ", strip=True)
    t = box.select_one(".title_info .title")
    img = box.select_one(".poster img")
    place = soup.select_one(".basic_tab li.place .sub_tabs a")
    if not info.get("기간"):
        return None
    p = re.findall(r"\d{4}-\d{2}-\d{2}", info["기간"])
    return {"title": t.get_text(" ", strip=True) if t else "", "from": p[0], "to": p[-1],
            "tmDetail": info.get("시간", ""), "runtime": runtime(info.get("소요시간", "")),
            "venueDetail": place.get_text(strip=True) if place else "",
            "poster": SITE + img["src"] if img and img.get("src", "").startswith("/") else ""}


def runtime(x, default=150):
    h = re.search(r"(\d+)\s*시간", x or "")
    m = re.search(r"(\d+)\s*분", x or "")
    t = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return t if 30 <= t <= 600 else default


# ---- 공연 시간 안내 문구 → 회차 ----
TIME = re.compile(r"(오전|오후|AM|PM|am|pm)?\s*(\d{1,2})\s*(?::\s*(\d{2})(?::\d{2})?\s*(AM|PM|am|pm)?|시(?:\s*(\d{1,2})\s*분|\s*반)?|\s*(AM|PM|am|pm)|(?<=[오후전]\s\d)$|(?<=[오후전]\d)$|(?<=[오후전]\s\d\d)$)")
DATE = re.compile(r"(?:(\d{4})\s*년\s*)?(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*(?:일(?!\s*(?:요일|자))|(?=\())|(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)")


def times_in(seg, with_pos=False):
    out, ap = [], None
    for m in TIME.finditer(seg):
        if not m.group(1) and m.group(0).strip().isdigit():
            continue
        a = (m.group(4) or m.group(6) or m.group(1) or "").upper()
        if a in ("오전", "AM"):
            ap = "AM"
        elif a in ("오후", "PM"):
            ap = "PM"
        h = int(m.group(2))
        mi = int(m.group(3) or m.group(5) or (30 if "반" in m.group(0) else 0))
        if ap == "PM" and h < 12:
            h += 12
        elif ap == "AM" and h == 12:
            h = 0
        elif not ap and 1 <= h <= 7:  # '일 2시, 6시'처럼 오전/오후를 생략한 낮·저녁 시각
            h += 12
        if 8 <= h <= 23 and mi < 60:
            out.append((m.start(), f"{h:02d}:{mi:02d}"))
    return out if with_pos else [t for _, t in out]


def dates_in(seg, d0, d1):
    """'2026년 11월 27일(금)', '6월 27일(토) - 28일(일)', '2026.11.21' → [(위치, date 또는 None(기간 밖), 범위표시)]"""
    out, mo, yr = [], None, None
    for m in DATE.finditer(seg):
        if m.group(4):
            mo, dd = int(m.group(4)), int(m.group(5))
            if not (1 <= mo <= 12):
                continue
        else:
            yr = int(m.group(1)) if m.group(1) else yr
            mo = int(m.group(2)) if m.group(2) else mo
            dd = int(m.group(3))
        if not mo:
            continue
        y = yr or (d0.year if (mo, dd) >= (d0.month, d0.day) else d1.year)
        try:
            dt = date(y, mo, dd)
        except ValueError:
            continue
        rng = bool(out) and bool(re.search(r"[-~∼]\s*$", seg[max(0, m.start() - 4):m.start()]))
        out.append((m.start(), dt if d0 <= dt <= d1 else None, rng, m.end()))
    return out


def days_in(seg):
    seg = re.sub(r"\([^)]*\)", "", seg)
    if "매일" in seg or "전일" in seg:
        return set(range(7))
    out = set()
    if "평일" in seg:
        out |= set(range(5))
    if "주말" in seg or "토·일" in seg:
        out |= {5, 6}
    m = re.search(r"([월화수목금토일])(?:요일)?\s*[~\-∼]\s*([월화수목금토일])(?:요일)?", seg)
    if m:
        a, b = DOW.index(m.group(1)), DOW.index(m.group(2))
        out |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
    head = re.split(r"\d|오전|오후", seg, 1)[0]  # 시각 앞의 요일 글자만 ('수목금 19시', '토 15시')
    for c in re.findall(r"[월화수목금토일]", re.sub(r"[월화수목금토일]요일", lambda x: x.group(0)[0], head)):
        out.add(DOW.index(c))
    return out


def sessions(text, d0, d1):
    weekly, explicit = set(), {}
    for seg in re.split(r"\s*(?:/|\||\n|;)\s*", text or ""):
        tp = times_in(seg, with_pos=True)
        if not tp:
            continue
        ts = [t for _, t in tp]
        ds = dates_in(seg, d0, d1)
        valid = [x for _, x, _, _ in ds if x]
        rest = seg[ds[-1][3]:] if ds else ""
        if valid and any(r for _, _, r, _ in ds) and days_in(rest):
            seg = rest  # '2/20(토) ~ 2/21(일) 토 18시' → 기간 표시 뒤의 요일별 시각으로 처리
            ds, valid = [], []
        if valid:
            if any(r for _, _, r, _ in ds):  # 'A일 - B일' 범위: 그 사이 모든 날 × 시각
                a, b = min(valid), max(valid)
                days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
                pairs = {(x, t) for x in days for t in ts}
            elif tp[0][0] < ds[0][0]:  # 시각이 날짜보다 먼저 나오면 모든 날 × 시각
                pairs = {(x, t) for x in valid for t in ts}
            else:  # 각 시각을 바로 앞에 적힌 날짜와 짝짓기
                pairs = set()
                for pos, t in tp:
                    prev = [x for p_, x, _, _ in ds if p_ < pos]
                    if prev and prev[-1]:
                        pairs.add((prev[-1], t))
            for x, t in pairs:
                explicit.setdefault(x, set()).add(t)
            continue
        if ds:  # 날짜가 있지만 모두 기간 밖(오타 등)
            continue
        wk = days_in(seg)
        if not wk and (d1 - d0).days <= 3:  # 요일·날짜 없이 시각만: 짧은 공연이면 매일 그 시각
            wk = set(range(7))
        x = d0
        while x <= d1 and wk:
            if x.weekday() in wk:
                weekly |= {(x, t) for t in ts}
            x += timedelta(days=1)
    # 날짜를 콕 집어 적은 회차가 있으면 그날은 요일 규칙보다 우선
    out = {f"{x} {t}" for x, t in weekly if x not in explicit}
    out |= {f"{x} {t}" for x, ts_ in explicit.items() for t in ts_}
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--debug", default="")
    a = ap.parse_args()
    today = datetime.now(KST).date()
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {"events": {}}
    ev = {int(k): v for k, v in state.get("events", {}).items() if v.get("title") and v.get("title") != "사이트맵"}

    found, ok_months = {}, 0
    list_html = get(LIST)  # 쿠키를 받고, JSON이 막히면 이 HTML을 쓴다
    if a.debug and list_html:
        os.makedirs(a.debug, exist_ok=True)
        open(os.path.join(a.debug, "list.html"), "w", encoding="utf-8").write(list_html)
    y, m = today.year, today.month
    for _ in range(a.months):
        ym = f"{y}{m:02d}"
        rows = month_list(ym)
        if rows is not None:
            ok_months += 1
            for r in rows:
                found[r["seq"]] = r
            print(f"{ym}: {len(rows)}건")
        else:
            print(f"{ym}: JSON 실패")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        time.sleep(0.5)
    if not ok_months and list_html:  # JSON이 막히면 목록 HTML(이번 달)
        for r in html_list(list_html):
            found[r["seq"]] = r
        print(f"목록 HTML에서 {len(found)}건")
    if a.debug:
        os.makedirs(a.debug, exist_ok=True)
        json.dump(list(found.values()), open(os.path.join(a.debug, "months.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    for seq, r in found.items():
        e = ev.setdefault(seq, {"seq": seq})
        e.update({k: v for k, v in r.items() if v})
        e["url"] = VIEW.format(seq)
    # 상세: 아직 안 끝난 공연만, 7일에 한 번
    for seq, e in sorted(ev.items()):
        if e.get("to", "") < today.isoformat() or e.get("checked", "") > (today - timedelta(days=7)).isoformat():
            continue
        v = view(seq)
        if v:
            for k in ("tmDetail", "runtime", "venueDetail"):
                e[k] = v[k]
            for k in ("title", "from", "to", "poster"):
                if v[k] and not e.get(k):
                    e[k] = v[k]
        e["checked"] = today.isoformat()
        time.sleep(0.7)
    for e in ev.values():
        d0, d1 = date.fromisoformat(e["from"]), date.fromisoformat(e["to"])
        e["sessions"] = sessions(e.get("tmDetail", ""), d0, d1) or sessions(e.get("tm", ""), d0, d1)
        e.setdefault("venue", e.get("venueDetail", ""))
        if not e.get("venue"):
            e["venue"] = e.get("venueDetail", "")

    keep = {k: v for k, v in ev.items() if v.get("to", "9999") >= (today - timedelta(days=365)).isoformat()}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"updated": datetime.now(KST).isoformat(timespec="minutes"), "source": LIST,
               "events": {str(k): v for k, v in sorted(keep.items())}},
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    up = [v for v in keep.values() if v.get("to", "") >= today.isoformat()]
    print(f"저장 {len(keep)}건 · 다가오는 공연 {len(up)}건 · 회차 시각을 못 읽은 공연 {sum(1 for v in up if not v['sessions'])}건")
    for v in sorted(up, key=lambda v: v["from"])[:40]:
        print(f"  {v['from']}~{v['to']} | {v.get('venue','')} | {v['title'][:30]} | {v['sessions'][:3]}{'…' if len(v['sessions'])>3 else ''}")


if __name__ == "__main__":
    main()
