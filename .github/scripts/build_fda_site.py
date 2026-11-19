"""사이트 'FDA 비교' 화면이 읽을 작은 파일을 만든다 (data 가지 fda_site/ 에 저장).

읽는 것: out/fda/fda_products.csv · fda_meta.json (build_fda_tables) ·
        out/uskr/site_ingredients.csv · site_products.csv · 국내비교.csv (check_us_kr)
만드는 것: out/fda_site/ing.parquet · prod.parquet · ind.parquet · cmp.parquet · meta.json
공개 자료만 쓴다(FDA·식약처 공개 자료). 매출 자료는 읽지 않는다.
줄 수가 너무 적으면 만들지 않는다 (사이트는 어제 파일을 그대로 쓴다).
"""
import json
import os
import sys
import time

import pandas as pd

FDA = os.environ.get("OUT_DIR", "out/fda")
USKR = os.environ.get("USKR_OUT", "out/uskr")
OUT = os.environ.get("SITE_OUT", "out/fda_site")
MIN = {"ing": 2000, "prod": 15000, "ind": 2000, "cmp": 500}


def rd(p):
    return pd.read_csv(p, dtype=str, keep_default_na=False)


def main():
    meta = json.load(open(f"{FDA}/fda_meta.json"))
    ing = rd(f"{USKR}/site_ingredients.csv")
    prod = rd(f"{USKR}/site_products.csv")
    cmp_ = rd(f"{USKR}/국내비교.csv")
    lab = rd(f"{FDA}/fda_products.csv")
    ind = lab[(lab["구분"] != "ANDA") & (lab["미국 적응증(라벨)"] != "")]
    ind = (ind.drop_duplicates("신청번호")[["신청번호", "상표", "미국 적응증(라벨)", "라벨 기준일"]]
              .rename(columns={"미국 적응증(라벨)": "적응증"}))
    ind = ind[ind["신청번호"].isin(set(prod["신청번호"]))].reset_index(drop=True)
    for c in ("오리지널 허가 수", "제네릭 허가 수", "국내 품목 수", "국내 임상 승인 수", "국내 임상 진행 중"):
        ing[c] = pd.to_numeric(ing[c], errors="coerce").fillna(0).astype("int32")
    for c in ("오리지널 허가 수", "제네릭 허가 수"):
        cmp_[c] = pd.to_numeric(cmp_[c], errors="coerce").fillna(0).astype("int32")
    n = {"ing": len(ing), "prod": len(prod), "ind": len(ind), "cmp": len(cmp_)}
    print("줄 수:", n)
    low = [k for k in MIN if n[k] < MIN[k]]
    if low:
        print("너무 적음:", low, "— 사이트 파일을 만들지 않음 (어제 파일 유지)")
        return 3
    os.makedirs(OUT, exist_ok=True)
    tabs = {"ing": ing, "prod": prod, "ind": ind, "cmp": cmp_}
    for k, df in tabs.items():
        df.to_parquet(f"{OUT}/{k}.parquet", index=False)
    info = {"만든 시각(한국)": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600)),
            "Drugs@FDA 기준일": meta.get("Drugs@FDA 기준일", ""), "라벨 기준일": meta.get("라벨 기준일", ""),
            "성분 사전 기준일": meta.get("성분 사전 기준일", ""), "줄 수": n}
    json.dump(info, open(f"{OUT}/meta.json", "w"), ensure_ascii=False, indent=1)
    sz = {k: round(os.path.getsize(f"{OUT}/{k}.parquet") / 1e6, 2) for k in tabs}
    mem = {k: round(df.memory_usage(deep=True).sum() / 1e6, 1) for k, df in tabs.items()}
    print("파일 크기(MB):", sz, "· 메모리(MB):", mem, "· 합계", round(sum(mem.values()), 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
