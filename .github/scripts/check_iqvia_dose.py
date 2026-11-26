"""IQVIA 복합제 용량 나누기 점검 (읽기만) — 매출 숫자·제품명·팩명은 적지 않는다.

적는 것: 전체 행 수, 팩명에 '/'가 있는 행 수, 용량이 바뀐 행 수, 바뀐 뒤 용량 개수가 늘어난 제품 수,
바뀐 용량의 모양(숫자를 9로 가린 것) 상위 몇 개, 바뀌지 않은 '/' 팩명의 모양(가린 것) 상위 몇 개.
"""
import re
import sys
import warnings
import logging
from collections import Counter

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
sys.path.insert(0, ".")
sys.path.insert(0, ".github/scripts")

import gsheet                  # noqa: E402
from dose_util import fix_iqvia_dose, dose_match   # noqa: E402


def mask(s):
    return re.sub(r"\d+(?:\.\d+)?", "9", str(s))


def main():
    if not gsheet.available():
        print("구글 키 없음")
        return 1
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        gsheet._info(), scopes=["https://www.googleapis.com/auth/drive.readonly"])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    import bootstrap
    df = bootstrap._parse_iqvia(bootstrap._download(drive, bootstrap.IQVIA_ID))
    print(f"행 {len(df):,} · 칸 있음: 팩명={'팩명' in df.columns} 용량={'용량' in df.columns} 제품명={'제품명' in df.columns}")
    if "팩명" not in df.columns or "용량" not in df.columns:
        return 1
    before = df["용량"].astype(str).tolist()
    slash = df["팩명"].astype(str).str.contains("/", regex=False)
    df, n = fix_iqvia_dose(df)
    after = df["용량"].astype(str).tolist()
    print(f"팩명에 '/' 있는 행 {int(slash.sum()):,} · 용량 바뀐 행 {n:,}")
    ch = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    print("바뀐 모양(원래 → 바뀐 값, 숫자는 9):",
          Counter(f"{mask(before[i])} → {mask(after[i])}" for i in ch).most_common(8))
    left = [mask(p) for p, s, a, b in zip(df["팩명"], slash, before, after) if s and a == b]
    print("'/' 있는데 안 바뀐 팩명 모양:", Counter(left).most_common(10))
    print("이때 원래 용량 모양:", Counter(mask(b) for p, s, b, a in zip(df["팩명"], slash, before, after) if s and a == b).most_common(6))
    if "제품명" in df.columns:
        g0 = df.assign(_o=before).groupby("제품명")["_o"].nunique()
        g1 = df.groupby("제품명")["용량"].nunique()
        print(f"용량 종류가 늘어난 제품 수: {int((g1 > g0).sum()):,}")
        # 예시 하나: 트윈콤비 (공개된 제품명·허가 용량만, 매출 없음)
        t = df[df["제품명"].astype(str).str.contains("트윈콤비", regex=False)]
        print("트윈콤비 용량 종류:", sorted(t["용량"].astype(str).unique().tolist()))
        for q in ["40", "40/10", "40mg/5mg"]:
            print(f"  용량 검색 '{q}' → {int(dose_match(t['용량'], q).sum())}팩")
    check_ubist(drive, bootstrap)
    return 0


def check_ubist(drive, bootstrap):
    """UBIST: 용량은 '성분' 글자에서 뽑는다. 복합제가 나뉘는지, 같은 숫자가 겹쳐 하나로 줄어드는지 본다."""
    print("\n===== UBIST =====")
    u = bootstrap._parse_ubist(bootstrap._download(drive, bootstrap.UBIST_ID))
    other = [c for c in u.columns if not re.search(r"_\d{4}", str(c))][:15]
    print(f"행 {len(u):,} · 칸: {other}")
    if "성분" not in u.columns or "용량" not in u.columns:
        return
    pat = re.compile(r"(\d+\.?\d*\s?(?:mg|mcg|µg|ug|g|ml|mL|L|IU|단위|%|㎎|㎍|㎕|㎖|㏖|㎏|㎗))", re.IGNORECASE)
    allm = u["성분"].astype(str).map(lambda s: [m.replace(" ", "") for m in pat.findall(s)])
    multi = allm.map(len) >= 2
    shrunk = allm.map(lambda l: len(l) != len(dict.fromkeys(l)))
    print(f"성분에 용량 2개 이상(복합제) {int(multi.sum()):,}행 · 그중 같은 숫자가 겹쳐 하나로 줄어든 행 {int(shrunk.sum()):,}")
    print("성분 글자 모양(숫자 9) 상위:", Counter(mask(x) for x in u.loc[multi, "성분"].astype(str)).most_common(5))
    print("용량 비어 있는 행:", int((u["용량"].astype(str) == "").sum()))
    nm = u["제품명"].astype(str)
    ing = u["성분"].astype(str).str.lower()
    print("제품명에 '트윈' 들어간 UBIST 제품:", sorted(nm[nm.str.contains("트윈", regex=False)].unique().tolist())[:20])
    ta = u[ing.str.contains("telmisartan") & ing.str.contains("amlodipine")]
    print(f"성분에 telmisartan+amlodipine 함께 있는 행 {len(ta)} · 제조사 {sorted(ta['제조사명'].astype(str).unique().tolist())[:15]}")
    for _, r in ta[["제품명", "성분", "용량"]].drop_duplicates().head(12).iterrows():
        print("  ", r["제품명"], "|", r["성분"], "|", r["용량"])
    tl = u[ing.str.contains("telmisartan") & ~ing.str.contains("amlodipine")]
    print("telmisartan만 있는데 제품명이 복합제 같은 것(예시):",
          sorted(tl.loc[tl["제품명"].astype(str).str.contains("/|콤비|플러스|듀오", regex=True), "제품명"].astype(str).unique().tolist())[:10])
    tw = u[nm.str.contains("트윈", regex=False)]
    for _, r in tw[["제품명", "성분", "용량"]].drop_duplicates().head(12).iterrows():
        print("  트윈:", r["제품명"], "|", r["성분"], "|", r["용량"])
    for q in ["40", "40/10", "40mg/5mg"]:
        print(f"  telmisartan+amlodipine 용량 검색 '{q}' → {int(dose_match(ta['용량'], q).sum())}행")
    cm = u.loc[multi, "성분"].astype(str)
    plus = cm[cm.str.contains(r"\+|,|/", regex=True)]
    print(f"복합제 같은 성분 글자(+ , /) {len(plus):,}행 · 모양:", Counter(mask(x) for x in plus).most_common(5))
    if shrunk.any():
        ex = u.loc[shrunk, ["제품명", "성분", "용량"]].drop_duplicates().head(5)
        print("겹쳐 줄어든 예 (제품명 · 성분 · 용량):")
        for _, r in ex.iterrows():
            print("  ", r["제품명"], "|", r["성분"], "|", r["용량"])


if __name__ == "__main__":
    sys.exit(main())
