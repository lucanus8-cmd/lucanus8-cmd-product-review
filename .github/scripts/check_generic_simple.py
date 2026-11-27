"""단순 규칙 시험 (읽기만, 사이트에 쓰지 않음). 공개 자료(식약처 허가·심평원 약가)만.

1) 성분 → 허가 자료에서 그 성분(단일제, 복합제는 조합)으로 가장 먼저 허가받은 품목 = 오리지널
2) 오리지널 품목의 보험코드(EDI) → 약가 자료의 제품코드 → 그 행의 약가코드(주성분코드)
3) 같은 약가코드를 가진 다른 제품이 약가에 처음 등재된 시기
"""
import base64, json, os, re, sys, warnings, logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
ROOT = os.getcwd()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

DEFAULT = ("펠루비프로펜;클로피도그렐;암로디핀;로수바스타틴;아토르바스타틴;텔미사르탄;시타글립틴;리나글립틴;다파글리플로진;"
           "엠파글리플로진;리바록사반;아픽사반;에독사반;티카그렐러;에스오메프라졸;세레콕시브;프레가발린;"
           "솔리페나신;미라베그론;토파시티닙;몬테루카스트;페북소스타트;실로스타졸;"
           "텔미사르탄+암로디핀;로수바스타틴+에제티미브;시타글립틴+메트포르민;다파글리플로진+메트포르민;"
           "아토르바스타틴+에제티미브;아토르바스타틴+암로디핀;로사르탄+히드로클로로티아지드;"
           "몬테루카스트+레보세티리진;올메사르탄+암로디핀+히드로클로로티아지드")


def setup_secret():
    info = gsheet._info()
    if not info:
        print("키 없음"); sys.exit(1)
    os.makedirs(".streamlit", exist_ok=True)
    b64 = base64.b64encode(json.dumps(info).encode()).decode()
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{b64}"\n')


def main():
    setup_secret()
    import pandas as pd
    import bootstrap
    print("자료 만들기:", bootstrap._do_build(only={"approval", "price"}, force=True))
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    pr = ss.load_price()
    ap = ss.load_approval().copy()
    ap["_d"] = ss._ap_date(ap)
    print("보험코드(EDI) 예:", ap["보험코드(EDI)"].astype(str).head(8).tolist())

    def cores(s):
        parts = [p for p in re.split(r"[|/,+]|및", str(s or "")) if p.strip()]
        return frozenset(c for c in (ss.ing_core(p) for p in parts) if c)
    ap["_cores"] = ap["주성분"].map(cores)

    def edis(s):
        return {x for x in re.findall(r"\d{9}", str(s or ""))}
    ap["_edi"] = ap["보험코드(EDI)"].map(edis)
    print(f"허가 {len(ap):,}건 중 보험코드 있는 품목 {int((ap['_edi'].map(len) > 0).sum()):,}")

    def _c(x):
        x = x.strip().upper() if isinstance(x, str) else ""
        return "" if x in ("NAN", "NONE", "NULL") else x
    pr["_cd"] = pr["주성분코드"].map(_c)
    alt = pr["제제코드"].map(_c)
    pr["_cd"] = pr["_cd"].where(pr["_cd"].str.len() > 0, alt)
    pr["_pc"] = pr["제품코드"].astype(str).str.strip()
    gy = pr[pr["급여구분"] == "급여"]
    vc = pr.groupby("제품명")["적용일자"].min().value_counts()
    bulk = set(vc[vc >= 3000].index)
    print("자료 묶음 날짜(그날 처음 나온 품목은 실제 등재일 모름):", sorted(str(d.date()) for d in bulk))

    rows = []
    for item in [c.strip() for c in (os.environ.get("WORDS") or DEFAULT).split(";") if c.strip()]:
        want = frozenset(ss.ing_core(x) for x in item.split("+"))

        def ok(cs):          # 성분이 정확히 같은 조합 (염 표기는 ing_core 가 떼어냄)
            return len(cs) == len(want) and all(any(c == w or c.startswith(w) for c in cs) for w in want)
        a = ap[ap["_cores"].map(ok) & ap["_d"].notna()].sort_values("_d")
        r = {"성분": item, "허가 품목수": len(a)}
        if a.empty:
            r["결과"] = "허가에서 못 찾음"; rows.append(r); continue
        o = a.iloc[0]
        r["오리지널(최초허가)"] = f"{str(o['품목명'])[:16]} · {str(o['업체명'])[:8]} · {o['_d'].date()}"
        e = o["_edi"]
        r["EDI"] = ",".join(sorted(e))[:20] or "(없음)"
        orow = pr[pr["_pc"].isin(e)]
        if orow.empty:
            r["결과"] = "약가에서 오리지널 EDI 못 찾음"; rows.append(r); continue
        codes = sorted({c for c in orow["_cd"] if c})
        r["약가코드"] = ",".join(codes)
        r["오리지널 첫급여"] = str(orow[orow["급여구분"] == "급여"]["적용일자"].min().date()) if (orow["급여구분"] == "급여").any() else "-"
        oth = gy[gy["_cd"].isin(codes) & ~gy["_pc"].isin(e)]
        f = oth.groupby("제품명").agg(d=("적용일자", "min"), co=("업체명", "first"))
        r["같은 약가코드 제품"] = len(f)
        known = f[~f["d"].isin(bulk)]
        pre = int((f["d"] <= pd.Timestamp("2010-02-01")).sum())
        r["2010.02 이전부터"] = pre
        if not known.empty:
            k = known.sort_values("d").iloc[0]
            r["첫 등재(날짜 아는 것 중)"] = f"{k['d'].date()} {str(known.sort_values('d').index[0])[:18]} · {str(k['co'])[:8]}"
        # 참고: 같은 회사가 같은 성분으로 받은 다른 함량 품목까지 넣으면
        same_co = a[a["업체명"].astype(str) == str(o["업체명"])]
        e2 = set().union(*same_co["_edi"].tolist()) if not same_co.empty else set()
        codes2 = sorted({c for c in pr[pr["_pc"].isin(e2)]["_cd"] if c})
        if set(codes2) != set(codes):
            oth2 = gy[gy["_cd"].isin(codes2) & ~gy["_pc"].isin(e2)]
            f2 = oth2.groupby("제품명")["적용일자"].min()
            k2 = f2[~f2.isin(bulk)]
            r["(참고) 같은 회사 다른 함량 포함"] = (f"약가코드 {len(codes2)}개 · 제품 {len(f2)}개 · 첫 "
                                         f"{k2.min().date() if not k2.empty else '-'}")
        r["결과"] = "찾음"
        rows.append(r)
    pd.set_option("display.width", 320); pd.set_option("display.max_colwidth", 60)
    for r in rows:
        print("—" * 70)
        for k, v in r.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
