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
        raw = str(s or "").strip()
        if raw in ("", "nan", "None"):
            return set()
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):   # 숫자로 뭉개진 값 → 첫 코드만 믿을 수 있다
            d = re.sub(r"\D", "", raw)
            return {d[:9]} if len(d) >= 9 else set()
        return {p for p in (re.sub(r"\D", "", x) for x in parts) if len(p) == 9}
    ap["_edi"] = ap["보험코드(EDI)"].map(edis)
    n_ok = int(ap["보험코드(EDI)"].astype(str).str.fullmatch(r"\d{9}(,\d{9})*").sum())
    n_any = int((ap["_edi"].map(len) > 0).sum())
    print(f"허가 {len(ap):,}건 중 보험코드(첫 코드라도) 있는 품목 {n_any:,} · 정상(텍스트) {n_ok:,}")

    def _c(x):
        x = x.strip().upper() if isinstance(x, str) else ""
        return "" if x in ("NAN", "NONE", "NULL") else x
    pr["_cd"] = pr["주성분코드"].map(_c)
    alt = pr["제제코드"].map(_c)
    pr["_cd"] = pr["_cd"].where(pr["_cd"].str.len() > 0, alt)
    pr["_pc"] = pr["제품코드"].astype(str).str.strip()
    # 약가 제품명 = 허가 품목명 + '_(규격)' → 규격을 떼고 띄어쓰기를 없애 허가 품목명과 정확히 맞춘다
    pr["_nm"] = pr["제품명"].astype(str).str.split("_(", regex=False).str[0].str.replace(r"\s+", "", regex=True)

    def nn(x):
        """제품명 정리: 규격(_(…)) 떼기 · 괄호 속 성분명 떼기 · 띄어쓰기 없애기 · 단위 표기 통일"""
        t = str(x).split("_(")[0]
        t = re.sub(r"\([^)]*\)", "", t)
        t = re.sub(r"\s+", "", t).lower()
        for a_, b_ in (("마이크로그램", "mcg"), ("마이크로그람", "mcg"), ("밀리그램", "mg"), ("밀리그람", "mg"),
                       ("㎎", "mg"), ("㎍", "mcg"), ("μg", "mcg"), ("밀리리터", "ml"), ("㎖", "ml"),
                       ("그램", "g"), ("그람", "g"), ("·", ""), (".0mg", "mg")):
            t = t.replace(a_, b_)
        return t
    pr["_nn"] = pr["제품명"].map(nn)
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
        r["오리지널"] = f"{str(o['품목명'])[:16]} · {str(o['업체명'])[:8]} · {o['_d'].date()} (최초허가)"
        # 예외: 더 늦게 허가됐어도 '신약' 표시가 있는 품목이 있으면 그 품목으로
        new = a[a["신약구분"].astype(str).str.strip() == "신약"] if "신약구분" in a.columns else a.iloc[0:0]
        if not new.empty and str(new.iloc[0]["품목기준코드"]) != str(o["품목기준코드"]):
            was = f"{str(o['품목명'])[:12]}({o['_d'].date()})"
            o = new.iloc[0]
            r["오리지널"] = (f"{str(o['품목명'])[:16]} · {str(o['업체명'])[:8]} · {o['_d'].date()} "
                          f"(신약 표시로 바꿈 · 최초허가는 {was})")
        def brand(n):
            t = re.sub(r"\s+", "", str(n))
            t = re.split(r"[0-9(_\[]", t)[0]
            return re.sub(r"(서방|장용|구강붕해|필름코팅|연질|경질)?(정|캡슐|주|시럽|산|과립|액|현탁액|패취|겔|크림)$", "", t)
        ob = brand(o["품목명"])
        fam = a[(a["업체명"].astype(str) == str(o["업체명"])) & (a["품목명"].map(brand) == ob)]
        r["오리지널 품목(함량)"] = len(fam)
        names = {nn(x) for x in fam["품목명"]}
        orow = pr[pr["_nn"].isin(names)]
        exact = pr[pr["_nm"].isin({re.sub(r"\s+", "", str(x)) for x in fam["품목명"]})]
        r["약가 연결"] = ("제품명(그대로 일치)" if not exact.empty and set(exact["_pc"]) == set(orow["_pc"])
                       else "제품명(정리해서 일치)") if not orow.empty else "-"
        if orow.empty:
            cand = pr[pr["_nn"].str.startswith(ob.lower())]["제품명"].drop_duplicates().head(4).tolist()
            r["허가 품목명"] = " / ".join(str(x)[:30] for x in fam["품목명"].head(3))
            r["약가에 있는 비슷한 이름"] = " / ".join(str(x)[:34] for x in cand) or "(상표로도 없음 → 비급여일 수 있음)"
            r["결과"] = "약가에서 오리지널 못 찾음"; rows.append(r); continue
        e = set(orow["_pc"])
        codes = sorted({c for c in orow["_cd"] if c})
        r["약가코드"] = ",".join(codes)
        r["오리지널 첫급여"] = str(orow[orow["급여구분"] == "급여"]["적용일자"].min().date()) if (orow["급여구분"] == "급여").any() else "-"
        oth = gy[gy["_cd"].isin(codes) & ~gy["_pc"].isin(e)]
        oth = oth[oth["_nm"].map(brand) != ob]          # 오리지널 자기 제품(예전 코드·회사명)은 뺀다
        f = oth.groupby("제품명").agg(d=("적용일자", "min"), co=("업체명", "first"))
        r["같은 약가코드 제품"] = len(f)
        known = f[~f["d"].isin(bulk)]
        pre = int((f["d"] <= pd.Timestamp("2010-02-01")).sum())
        r["2010.02 이전부터"] = pre
        if not known.empty:
            k = known.sort_values("d").iloc[0]
            r["첫 등재(날짜 아는 것 중)"] = f"{k['d'].date()} {str(known.sort_values('d').index[0])[:18]} · {str(k['co'])[:8]}"
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
