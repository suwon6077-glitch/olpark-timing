"""
올림픽공원 데이터 수집 스크립트

사용법
  1) KCISA API 전체 받기 (페이지 반복 호출 → CSV)
       python olpark_collect.py inout  --key 발급받은키   # 입출차정보 → inout_raw.csv
       python olpark_collect.py linked --key 발급받은키   # 연계 데이터 → linked_raw.csv
       python olpark_collect.py inout2024 --key 포털일반인증키  # 공공데이터포털 입출차(20240731) → inout2024_raw.csv
  2) 실시간 주차대수 주기 수집 (기관 홈페이지 API, 공개키)
       python olpark_collect.py realtime --interval 300
     → 5분마다 호출해 realtime_parking.csv에 한 줄씩 추가. Ctrl+C로 종료.

필요 패키지: pip install requests
"""
import argparse
import csv
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

KCISA_URLS = {
    "inout": "https://api.kcisa.kr/openapi/API_CPM_008/request",   # 입출차정보
    "linked": "https://api.kcisa.kr/openapi/API_CPM_009/request",  # 입출차·공연·날씨 연계
}
REALTIME_URL = "https://www.ksponco.or.kr/online/main/parkingorsApi.do"
REALTIME_KEY = "PARKING21803504"  # 기관 홈페이지에 공개된 키


# ---------- 공통: 응답 파싱 ----------
def parse_items(text):
    """JSON 또는 XML 응답에서 item 목록과 totalCount를 꺼낸다."""
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        body = data.get("response", data).get("body", data)
        items = body.get("items", {})
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        total = int(body.get("totalCount", 0) or 0)
        return items, total

    root = ET.fromstring(text)
    items = []
    for it in root.iter("item"):
        items.append({child.tag: (child.text or "").strip() for child in it})
    tc = root.find(".//totalCount")
    total = int(tc.text) if tc is not None and tc.text else 0
    return items, total


# ---------- 1) 입출차 API 전체 수집 ----------
KCISA_FIELDS = ["TITLE", "URL", "LOCAL_ID", "ISSUED_DATE", "DESCRIPTION",
                "SUB_DESCRIPTION", "COLLECTED_DATE", "REG_DT"]
BACKOFF = [15, 30, 60, 120, 300, 600]  # 429(요청 과다) 등 실패 시 대기 초


def collect_kcisa(url, key, rows=1000, out="inout_raw.csv", sleep=2.0,
                  start=1, max_minutes=None):
    """페이지를 반복 호출해 CSV로 저장.
    - start>1 이면 기존 파일 뒤에 이어 붙임
    - 429 등 실패 시 BACKOFF 간격으로 기다렸다 재시도
    - max_minutes 가 지나면 멈추고 다음 시작 페이지를 next_page.txt 에 기록
    종료코드: 0 = 전부 받음 또는 시간 제한으로 정상 중단, 1 = 재시도 모두 실패
    """
    t0 = time.time()
    page, written, total = start, 0, None
    new_file = start == 1 or not os.path.exists(out)
    f = open(out, "w" if new_file else "a", newline="", encoding="utf-8-sig")
    writer = csv.DictWriter(f, fieldnames=KCISA_FIELDS, extrasaction="ignore")
    if new_file:
        writer.writeheader()
    status = 0
    try:
        while True:
            if max_minutes and (time.time() - t0) / 60 > max_minutes:
                print(f"시간 제한 {max_minutes}분 도달 → page {page} 부터 다음 실행에서 이어받기")
                break
            params = {"serviceKey": key, "numOfRows": rows, "pageNo": page}
            r = None
            for attempt, wait in enumerate([0] + BACKOFF):
                if wait:
                    print(f"  page {page}: {wait}초 대기 후 재시도 ({attempt}/{len(BACKOFF)})")
                    time.sleep(wait)
                try:
                    r = requests.get(url, params=params, timeout=120)
                    if r.status_code == 200 and "<resultCode>0000" in r.text[:300]:
                        break
                    print(f"  page {page}: HTTP {r.status_code} {r.text[:150]!r}")
                except requests.RequestException as e:
                    print(f"  page {page}: {e}")
                r = None
            if r is None:
                print(f"page {page} 실패 → 중단")
                status = 1
                break

            if page == start:
                print("첫 응답 앞부분:\n", r.text[:300], "\n")
            items, tc = parse_items(r.text)
            if total is None:
                total = tc
                print(f"totalCount = {total}, 예상 마지막 페이지 = {-(-total // rows)}")
            if not items:
                print("빈 페이지 → 끝")
                page = None
                break
            writer.writerows(items)
            f.flush()
            written += len(items)
            done = (page - 1) * rows + len(items)
            el = (time.time() - t0) / 60
            print(f"page {page}: 이번 실행 {written}행, 전체 진행 {done}/{total} ({el:.1f}분)")
            if total and done >= total:
                page = None
                break
            page += 1
            time.sleep(sleep)
    finally:
        f.close()
        with open("next_page.txt", "w") as pf:
            pf.write("DONE" if page is None else str(page))
    print(f"이번 실행 저장: {out} (+{written}행), 다음 시작 페이지: "
          f"{'없음(완료)' if page is None else page}")
    if status:
        sys.exit(1)


# ---------- 1-2) 공공데이터포털(odcloud) 입출차 2024 ----------
ODCLOUD_URL = ("https://api.odcloud.kr/api/15138735/v1/"
               "uddi:d0e4bf79-59b5-4dc4-b5b8-ecf05e94e98c")


def collect_odcloud(key, per_page=1000, out="inout2024_raw.csv", sleep=0.2):
    page, written, total = 1, 0, None
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        writer = None
        while True:
            params = {"serviceKey": key, "page": page, "perPage": per_page,
                      "returnType": "JSON"}
            for attempt in range(3):
                try:
                    r = requests.get(ODCLOUD_URL, params=params, timeout=60)
                    r.raise_for_status()
                    break
                except requests.RequestException as e:
                    print(f"  page {page} 재시도 {attempt + 1}/3: {e}")
                    time.sleep(3)
            else:
                print(f"page {page} 실패 → 중단")
                break
            js = r.json()
            if total is None:
                total = js.get("totalCount", 0)
                print(f"totalCount = {total}")
            rows = js.get("data", [])
            if not rows:
                break
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()),
                                        extrasaction="ignore")
                writer.writeheader()
            writer.writerows(rows)
            written += len(rows)
            print(f"page {page}: 누적 {written}/{total}")
            if total and written >= total:
                break
            page += 1
            time.sleep(sleep)
    print(f"완료: {out} ({written}행)")


# ---------- 2) 실시간 주차대수 주기 수집 ----------
def collect_realtime(interval=300, out="realtime_parking.csv", once=False):
    new_file = not os.path.exists(out)
    if os.path.dirname(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    if not once:
        print(f"{interval}초 간격 수집 시작 → {out}  (Ctrl+C로 종료)")
    while True:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            r = requests.get(REALTIME_URL, params={"apiKey": REALTIME_KEY}, timeout=30)
            raw = r.text.strip().replace("\n", " ")
            value = ""
            try:
                value = json.loads(raw).get("parking_sum", "")
            except Exception:
                try:
                    node = ET.fromstring(raw).find(".//parking_sum")
                    value = node.text if node is not None else ""
                except Exception:
                    pass
            row = [ts, value, raw[:300]]
        except requests.RequestException as e:
            row = [ts, "", f"ERROR {e}"]

        with open(out, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["수집시각", "parking_sum", "원본응답"])
                new_file = False
            w.writerow(row)
        print(row[:2])
        if once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, default_out in [("inout", "inout_raw.csv"), ("linked", "linked_raw.csv")]:
        a = sub.add_parser(name)
        a.add_argument("--key", required=True)
        a.add_argument("--rows", type=int, default=1000)
        a.add_argument("--out", default=default_out)
        a.add_argument("--start", type=int, default=1, help="시작 페이지 (이어받기)")
        a.add_argument("--sleep", type=float, default=2.0, help="호출 간격(초)")
        a.add_argument("--max-minutes", type=float, default=None)
    c = sub.add_parser("inout2024")
    c.add_argument("--key", required=True)
    c.add_argument("--rows", type=int, default=1000)
    c.add_argument("--out", default="inout2024_raw.csv")
    b = sub.add_parser("realtime")
    b.add_argument("--interval", type=int, default=300)
    b.add_argument("--out", default="realtime_parking.csv")
    b.add_argument("--once", action="store_true", help="한 번만 호출하고 종료 (GitHub Actions용)")
    args = ap.parse_args()

    if args.cmd in KCISA_URLS:
        collect_kcisa(KCISA_URLS[args.cmd], args.key, args.rows, args.out,
                      args.sleep, args.start, args.max_minutes)
    elif args.cmd == "inout2024":
        collect_odcloud(args.key, args.rows, args.out)
    else:
        try:
            collect_realtime(args.interval, args.out, args.once)
        except KeyboardInterrupt:
            print("\n종료")
            sys.exit(0)
