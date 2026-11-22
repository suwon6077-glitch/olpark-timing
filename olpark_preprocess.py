"""
입출차 원자료(inout_raw.csv) → 분석용 테이블(inout_clean.csv)

사용법: python olpark_preprocess.py inout_raw.csv inout_clean.csv

DESCRIPTION 예시
  입구:동2문입구#1, 입차시간:2025-08-24 13:48:38, 출구:동2문출구#1, 출차시간:2025-08-24 17:21:57
→ 입구문, 입구차로, 입차시각, 출구문, 출구차로, 출차시각, 주차시간_분, 출차누락
"""
import sys

import pandas as pd

DESC_PAT = (
    r"입구:\s*(?P<입구>[^,]*?)\s*,\s*입차시간:\s*(?P<입차시각>[^,]*?)\s*"
    r"(?:,\s*출구:\s*(?P<출구>[^,]*?)\s*,\s*출차시간:\s*(?P<출차시각>.*?))?\s*$"
)
GATE_PAT = r"^(?P<문>.*?문)(?:입구|출구)?\s*#?(?P<차로>\d*)$"


def preprocess(src, dst):
    raw = pd.read_csv(src, dtype=str, encoding="utf-8-sig")
    d = raw["DESCRIPTION"].str.extract(DESC_PAT)

    gin = d["입구"].str.extract(GATE_PAT)
    gout = d["출구"].str.extract(GATE_PAT)

    df = pd.DataFrame({
        "ID": raw["LOCAL_ID"],
        "입구": d["입구"],
        "입구문": gin["문"],
        "입구차로": gin["차로"],
        "입차시각": pd.to_datetime(d["입차시각"], errors="coerce"),
        "출구": d["출구"],
        "출구문": gout["문"],
        "출구차로": gout["차로"],
        "출차시각": pd.to_datetime(d["출차시각"].replace("", pd.NA), errors="coerce"),
    })
    df["주차시간_분"] = (df["출차시각"] - df["입차시각"]).dt.total_seconds() / 60
    df["출차누락"] = df["출차시각"].isna()

    # 분석용 파생 변수
    df["입차일"] = df["입차시각"].dt.date
    df["입차시"] = df["입차시각"].dt.hour
    df["출차시"] = df["출차시각"].dt.hour
    df["요일"] = df["입차시각"].dt.day_of_week  # 0=월

    df = df.drop_duplicates("ID").sort_values("입차시각")
    df.to_csv(dst, index=False, encoding="utf-8-sig")

    # 품질 점검 요약
    print(f"원자료 {len(raw):,}행 → 정리 {len(df):,}행")
    print(f"DESCRIPTION 파싱 실패: {d['입차시각'].isna().sum():,}행")
    print(f"출차 누락(장기주차 등): {df['출차누락'].sum():,}행")
    print(f"음수 주차시간: {(df['주차시간_분'] < 0).sum():,}행")
    print("\n입구문 분포\n", df["입구문"].value_counts(dropna=False).head(10))
    print("\n출구문 분포\n", df["출구문"].value_counts(dropna=False).head(10))
    bad = df.loc[df["출구문"].isna() & ~df["출차누락"], "출구"].value_counts().head(10)
    if len(bad):
        print("\n문 이름 형식이 다른 출구 값\n", bad)
    print("\n입차 기간:", df["입차시각"].min(), "~", df["입차시각"].max())


if __name__ == "__main__":
    preprocess(sys.argv[1], sys.argv[2])
