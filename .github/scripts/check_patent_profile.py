"""식약처_특허정보 시트가 하루 사이 12.6만 → 19.6만 줄로 늘어난 이유를 본다. 읽기만 한다.

비교 기준: 데이터관리파일에 남아 있는 옛 임시 탭 '_stg_특허정보' (9월 수집분, 12만 줄) — 열 수 있으면.
"""
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import gsheet  # noqa: E402

NEW_ID = "1tj75vy8KcJSmz6-rAqmPpLThNt7ZymuQfi12Vbzz1CE"
CTRL_ID = "1bUOMuziqrD77N-zQ7jQWga7gfICVWklV4S3a-osAmag"


def norm(df):
    """머리글 '품목기준코드\n(ITEM_SEQ)' → 'ITEM_SEQ' (괄호 속 영문 코드)."""
    df = df.copy()
    cols = []
    for c in df.columns:
        m = re.search(r"\(([A-Z0-9_]+)\)\s*$", str(c))
        cols.append(m.group(1) if m else str(c).strip())
    df.columns = cols
    ren = {"품목기준코드": "ITEM_SEQ", "품목명": "ITEM_NAME", "업체명": "ENTP_NAME"}
    return df.rename(columns=ren)


def read_tab(sid, tab):
    api = gsheet.svc().spreadsheets()
    v = api.values().get(spreadsheetId=sid, range=f"'{tab}'").execute().get("values", [])
    hdr = [str(c).strip() for c in v[0]]
    rows = [r + [""] * (len(hdr) - len(r)) for r in v[1:]]
    return pd.DataFrame([r[:len(hdr)] for r in rows], columns=hdr)


def profile(df, name):
    for need in ("ITEM_SEQ", "ITEM_NAME", "PAGE_GB_NM", "DOMESTIC_PATENT_NO"):
        if need not in df.columns:
            df[need] = ""
            print(f"   ({name}: '{need}' 칸 없음)")
    print(f"\n==== {name}: {len(df):,}줄 · 열 {df.shape[1]}개")
    print("   열:", list(df.columns))
    print(f"   품목 수 {df['ITEM_SEQ'].nunique():,} · 특허번호 수 {df['DOMESTIC_PATENT_NO'].replace('', pd.NA).nunique():,}")
    print(f"   완전히 같은 줄(중복) {df.duplicated().sum():,}")
    for c in ("PAGE_GB_NM", "DOMESTIC_PATENT_STATUS", "PATENT_GB_CODE"):
        if c in df.columns:
            print(f"   {c}:", df[c].value_counts().head(8).to_dict())
    lst = df[df["PAGE_GB_NM"].str.contains("제품", na=False)]
    print(f"   제품특허(등재) 줄 {len(lst):,} · 품목 {lst['ITEM_SEQ'].nunique():,}")
    per = df.groupby("ITEM_SEQ").size()
    print(f"   품목당 줄 수: 중앙값 {per.median():.0f} · 최대 {per.max():,}")
    top = df.groupby(["ITEM_SEQ", "ITEM_NAME"]).size().sort_values(ascending=False).head(5)
    print("   줄이 가장 많은 품목:", [(n, k) for (_, n), k in top.items()])


def main():
    if not gsheet.available():
        print("키 없음")
        return 1
    tabs = [x["properties"]["title"] for x in gsheet.svc().spreadsheets().get(
        spreadsheetId=NEW_ID, fields="sheets.properties(title)").execute()["sheets"]]
    print("특허 파일 탭:", tabs)
    new = norm(read_tab(NEW_ID, "특허정보"))
    print("머리글:", list(new.columns))
    profile(new, "지금 특허 시트")
    try:
        old = norm(read_tab(CTRL_ID, "_stg_특허정보"))
        profile(old, "옛 수집분 (_stg_특허정보, 9월)")
    except Exception as e:
        print("\n옛 수집분 못 엶 (서비스 계정에 공유 안 됨일 수 있음):", type(e).__name__, str(e)[:120])
        return 0
    ko, kn = set(old["ITEM_SEQ"]), set(new["ITEM_SEQ"])
    print(f"\n==== 비교: 품목 — 옛날만 {len(ko - kn):,} · 둘 다 {len(ko & kn):,} · 새로 생김 {len(kn - ko):,}")
    add = new[~new["ITEM_SEQ"].isin(ko)]
    print(f"   새 품목의 줄 {len(add):,} · 그중 제품특허 {add['PAGE_GB_NM'].str.contains('제품', na=False).sum():,}")
    print("   새 품목 PAGE_GB_NM:", add["PAGE_GB_NM"].value_counts().to_dict())
    print("   새 품목 예:", add.drop_duplicates("ITEM_SEQ")["ITEM_NAME"].head(12).tolist())
    both = new[new["ITEM_SEQ"].isin(ko & kn)].groupby("ITEM_SEQ").size()
    bo = old[old["ITEM_SEQ"].isin(ko & kn)].groupby("ITEM_SEQ").size()
    d = (both - bo).dropna()
    print(f"   같은 품목의 줄 수 변화: 늘어난 품목 {(d > 0).sum():,} · 줄어든 {(d < 0).sum():,} · 합계 {d.sum():+,.0f}")
    lo = old[old["PAGE_GB_NM"].str.contains("제품", na=False)]
    ln = new[new["PAGE_GB_NM"].str.contains("제품", na=False)]
    print(f"   제품특허 줄: 옛 {len(lo):,} → 지금 {len(ln):,} · 제품특허 품목 옛 {lo['ITEM_SEQ'].nunique():,} → 지금 {ln['ITEM_SEQ'].nunique():,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
