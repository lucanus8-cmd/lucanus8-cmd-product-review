"""로봇이 만든 FDA 표·국내 비교 결과를 구글 시트 '제품검토 · FDA 비교'에 덮어쓴다. 사이트와 상관없다.

읽는 파일: out/fda/fda_products.csv · fda_substance_names.csv · fda_usan_inn_pairs.csv · fda_meta.json · out/uskr/국내비교.csv
공개 자료만 쓴다(매출 자료 없음). 키는 GitHub 비밀값에서만 읽고 화면에 적지 않는다.
"""
import json
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import gsheet  # noqa: E402

SHEET_ID = os.environ.get("FDA_SHEET_ID", "1CpIrt1kbGw_yiGFNpQB2gRiOiUWlBovMtsOs-X1gmZs")
FDA = os.environ.get("OUT_DIR", "out/fda")
USKR = os.environ.get("USKR_OUT", "out/uskr")


def rd(p):
    return pd.read_csv(p, dtype=str, keep_default_na=False)


def main():
    if not gsheet.available():
        print("GitHub 비밀값 GCP_SERVICE_ACCOUNT 를 읽지 못함 (없거나 모양이 다름)")
        gsheet.diagnose()
        return 1
    print("키 확인됨 · 서비스 계정:", gsheet.account_email())
    meta = json.load(open(f"{FDA}/fda_meta.json"))
    prod = rd(f"{FDA}/fda_products.csv")
    names = rd(f"{FDA}/fda_substance_names.csv")
    pairs = rd(f"{FDA}/fda_usan_inn_pairs.csv")
    comp = rd(f"{USKR}/국내비교.csv")

    ind = prod[(prod["구분"] != "ANDA") & (prod["미국 적응증(라벨)"] != "")]
    ind = (ind.groupby("신청번호", sort=False)
              .agg(구분=("구분", "first"), 상표=("상표", lambda x: ", ".join(dict.fromkeys(x))[:120]),
                   **{"주성분(대표 이름)": ("주성분(대표 이름)", "first"), "판매 상태": ("판매 상태", lambda x: ", ".join(sorted(set(x)))),
                      "미국 적응증(라벨)": ("미국 적응증(라벨)", "first"), "라벨 기준일": ("라벨 기준일", "first")})
              .reset_index())
    plist = prod.drop(columns=["미국 적응증(라벨)", "라벨 기준일"])
    now = time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600))
    guide = pd.DataFrame({"항목": [
        "갱신 시각 (한국)", "FDA 허가 자료 기준일", "FDA 라벨 기준일", "FDA 성분 사전 기준일",
        "국내 비교", "FDA 허가목록", "FDA 적응증", "성분 대조표", "미국명→국제명", "만드는 방법"],
        "내용": [now, meta.get("Drugs@FDA 기준일", ""), meta.get("라벨 기준일", ""), meta.get("성분 사전 기준일", ""),
               f"{len(comp):,}줄 — 미국 판매 중인데 국내 정상 품목에 성분이 없거나(국내 성분 없음) 그 제형이 없는 것. "
               "질환군·미국 적응증·식약처 임상시험 승인 포함",
               f"{len(plist):,}줄 — Drugs@FDA 전 제품 (판매 중·중단 포함)",
               f"{len(ind):,}줄 — 신청(NDA·BLA)별 FDA 라벨 적응증",
               f"{len(names):,}줄 — FDA 성분 사전의 공식 이름(INN·USAN·JAN 등), 염은 모체로 묶음",
               f"{len(pairs):,}줄 — FDA 가 한 성분에 함께 적은 미국 이름과 국제 이름",
               "로봇(GitHub)이 FDA 공개 자료와 식약처 허가·임상 시트를 맞춰 덮어씀. 손으로 고친 내용은 다음 갱신 때 지워짐"]})
    tabs = {"안내": guide, "국내 비교": comp, "FDA 허가목록": plist, "FDA 적응증": ind,
            "성분 대조표": names, "미국명→국제명": pairs}
    gsheet.write_tabs(SHEET_ID, tabs, order=list(tabs))
    print("시트 쓰기 끝:", f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
