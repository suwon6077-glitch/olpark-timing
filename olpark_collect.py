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
def collect_kcisa(url, key, rows=1000, out="inout_raw.csv", sleep=0.3):
    page, written, total, fields = 1, 0, None, None
    f = open(out, "w", newline="", encoding="utf-8-sig")
    writer = None
    try:
        while True:
            params = {"serviceKey": key, "numOfRows": rows, "pageNo": page}
            for attempt in range(3):
                try:
                    r = requests.get(url, params=params, timeout=60)
                    r.raise_for_status()
                    break
                except requests.RequestException as e:
                    print(f"  page {page} 재시도 {attempt + 1}/3: {e}")
                    time.sleep(3)
            else:
                print(f"page {page} 실패 → 중단. 다음에 --start {page} 로 이어받기")
                break

            if page == 1:
                print("첫 응답 앞부분:\n", r.text[:500], "\n")

            items, tc = parse_items(r.text)
            if total is None:
                total = tc
                print(f"totalCount = {total}")
            if not items:
                break

            if writer is None:
                fields = list(items[0].keys())
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
            writer.writerows(items)
            written += len(items)
            print(f"page {page}: 누적 {written}/{total}")

            if total and written >= total:
                break
            page += 1
            time.sleep(sleep)
    finally:
        f.close()
    print(f"완료: {out} ({written}행)")


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
        collect_kcisa(KCISA_URLS[args.cmd], args.key, args.rows, args.out)
    elif args.cmd == "inout2024":
        collect_odcloud(args.key, args.rows, args.out)
    else:
        try:
            collect_realtime(args.interval, args.out, args.once)
        except KeyboardInterrupt:
            print("\n종료")
            sys.exit(0)
