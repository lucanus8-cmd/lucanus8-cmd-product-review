"""특허 표에 '발명의 명칭' 칸을 더했을 때 사이트 메모리·속도가 얼마나 늘어나는지 실제 시트로 재 본다 (읽기만)."""
import io
import os
import sys
import time

import pandas as pd

sys.path.insert(0, ".")
sys.path.insert(0, ".github/scripts")
import gsheet                      # noqa: E402
import scout_sources as ss         # noqa: E402

SID = "1tj75vy8KcJSmz6-rAqmPpLThNt7ZymuQfi12Vbzz1CE"


def read():
    api = gsheet.svc()
    vals = gsheet._call(api.spreadsheets().values().get(spreadsheetId=SID, range="'특허정보'")).get("values", [])
    hdr = [str(c).split("\n")[0].strip() for c in vals[0]]
    return pd.DataFrame([(r + [""] * len(hdr))[:len(hdr)] for r in vals[1:]], columns=hdr)


def main():
    raw = read()
    print(f"시트 {len(raw):,}행")
    old_cols = [c for c in ss._PATENT_COLS if c != "DOMESTIC_INVN_NM"]
    res = {}
    for name, cols in (("지금(11칸)", old_cols), ("새로(12칸)", ss._PATENT_COLS)):
        df = raw[cols].copy()
        ss.enrich_patent(df)
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        size = len(buf.getvalue())
        t0 = time.time()
        d2 = pd.read_parquet(io.BytesIO(buf.getvalue()))
        if "DOMESTIC_INVN_NM" in d2.columns:
            d2["DOMESTIC_INVN_NM"] = d2["DOMESTIC_INVN_NM"].fillna("").astype(str).astype("category")
        ss.enrich_patent(d2)
        t_load = time.time() - t0
        mem = d2.memory_usage(deep=True).sum()
        t0 = time.time()
        for _ in range(5):
            d2.copy()                   # st.cache_data 가 부를 때마다 하는 복사
        t_copy = (time.time() - t0) / 5
        res[name] = (size, mem, t_load, t_copy)
        print(f"{name}: 파일 {size / 1e6:.1f}MB · 메모리 {mem / 1e6:.0f}MB · 읽기 {t_load:.2f}초 · 복사 {t_copy:.2f}초")
    a, b = res["지금(11칸)"], res["새로(12칸)"]
    print(f"늘어나는 양: 파일 +{(b[0] - a[0]) / 1e6:.1f}MB · 메모리 +{(b[1] - a[1]) / 1e6:.0f}MB "
          f"(+{(b[1] - a[1]) / a[1] * 100:.0f}%) · 복사 +{b[3] - a[3]:.2f}초")
    print("발명의 명칭 고유값:", raw["DOMESTIC_INVN_NM"].nunique())
    # 순서 정하기 시간 (린버크·많은 성분)
    for q in ("린버크", "아토르바스타틴", "메트포르민"):
        m = d2[d2["품목명"].astype(str).str.contains(q, regex=False) | d2["INGR_NAME"].astype(str).str.contains(q, regex=False)]
        t0 = time.time()
        o, t = ss.patent_order(m)
        dt = time.time() - t0
        print(f"[{q}] {len(m):,}행 · 순서 정하기 {dt:.3f}초 · 묶음 {t.value_counts().to_dict()}")
        x = m.loc[o].drop_duplicates("DOMESTIC_PATENT_NO").head(8)
        for _, r in x.iterrows():
            print(f"    {r['PAGE_GB_NM']} | {str(r['PATENTEE'])[:20]} | 허가 {str(r['업체명'])[:14]} | {r['DOMESTIC_PATENT_NO']} | "
                  f"{r['DOMESTIC_END_DATE']} | {r['PATENT_GB_CODE']} | {str(r['DOMESTIC_INVN_NM'])[:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
