"""제네릭 첫 급여 등재를 '같은 약가 주성분코드'로 찾기 시험 (읽기만, 사이트에 쓰지 않음). 공개 자료만.

오리지널 품목 → 그 품목의 주성분코드(예: 429030ASY: 앞 4자리 성분, 다음 2자리 함량, 끝 투여·제형)
→ 같은 코드로 약가에 등재된 다른 품목 = 제네릭. 이름·성분명 맞추기 없이 코드로만 묶는다.
A) 오리지널을 지금 사이트 방식(허가 '신약' 품목명 앞글자, 성분 '포함' 검색)으로 고른 경우
B) 오리지널을 '같은 성분 단일제 중 최초 허가' 품목으로 고른 경우
"""
import base64, json, os, re, sys, warnings, logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
ROOT = os.getcwd()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

DEFAULT = ("펠루비프로펜,펙소페나딘,로수바스타틴,아토르바스타틴,에제티미브,텔미사르탄,올메사르탄메독소밀,"
           "시타글립틴,리나글립틴,다파글리플로진,엠파글리플로진,리바록사반,아픽사반,에독사반,티카그렐러,"
           "에스오메프라졸,세레콕시브,프레가발린,솔리페나신,미라베그론,토파시티닙,두타스테리드,레바미피드")


def setup_secret():
    info = gsheet._info()
    if not info:
        print("키 없음"); sys.exit(1)
    os.makedirs(".streamlit", exist_ok=True)
    b64 = base64.b64encode(json.dumps(info).encode()).decode()
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{b64}"\n')


def brand(name):
    s = re.sub(r"\s+", "", str(name))
    s = re.split(r"[0-9(_\[]", s)[0]
    return re.sub(r"(서방|장용|구강붕해|필름코팅|연질|경질)?(정|캡슐|주|시럽|산|과립|액|현탁액|패취|겔|크림)$", "", s)


def co(s):
    return re.sub(r"\(주\)|주식회사|㈜|\(유\)|\s+", "", str(s))


def main():
    setup_secret()
    import pandas as pd
    import bootstrap
    print("자료 만들기:", bootstrap._do_build(only={"approval", "price"}, force=True))
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    pd.set_option("display.width", 260)
    pr = ss.load_price()
    ap = ss.load_approval().copy()
    ap["_d"] = ss._ap_date(ap)
    ap1 = ap[ss.is_single_series(ap["주성분"], ap["주성분(영문)"])]
    pr["_cd"] = pr["주성분코드"].astype(str).str.strip().str.upper().replace({"NAN": "", "NONE": ""})
    pr["_cd"] = pr["_cd"].where(pr["_cd"].str.len() > 0, pr["제제코드"].astype(str).str.strip().str.upper())
    pr["_ing4"] = pr["_cd"].str[:4]
    gy = pr[pr["급여구분"] == "급여"]
    print(f"주성분코드 있는 행 {int((pr['_cd'].str.len() >= 9).sum()):,} / {len(pr):,} · 모양 예 {pr['_cd'].dropna().unique()[:5].tolist()}")
    print(f"주성분코드와 제제코드가 둘 다 있을 때 같은 비율: "
          f"{float((pr['주성분코드'].astype(str).str.upper() == pr['제제코드'].astype(str).str.upper())[pr['제제코드'].astype(str).str.len() > 0].mean()):.3f}")
    nm_first = pr.groupby("제품명")["적용일자"].min()
    vc = nm_first.value_counts()
    bulk = set(vc[vc >= 3000].index)

    def site_origs(term):            # 지금 사이트(_price_trend_codes 성분)와 같은 방식
        core = ss.ing_core(term)
        keys = ss.resolve_keys(term) | ss.resolve_keys(core)
        origs = ss.original_products(keys) if keys else []
        pref = {re.split(r"[0-9]", re.sub(r"\s", "", o))[0] for o in origs}
        pref = {p for p in pref if len(p) >= 2}
        return pr[pr["제품명"].astype(str).apply(lambda n: any(re.sub(r"\s", "", n).startswith(p) for p in pref))]

    def first_origs(term):           # 같은 성분 단일제 중 최초 허가
        core = ss.ing_core(term)
        a0 = ap1[(ap1["_core"] == core) | ap1["_core"].astype(str).str.startswith(core)]
        keys = set(a0["_key"].astype(str)) - {""}
        a = ap1[ap1["_key"].isin(keys) & ap1["_d"].notna()].sort_values("_d")
        if a.empty:
            return pr.iloc[0:0], ""
        o = a.iloc[0]
        b, c = brand(o["품목명"]), co(o["업체명"])
        nm = pr["제품명"].astype(str).map(brand)
        return pr[(nm == b) | ((pr["업체명"].map(co) == c) & pr["_key"].isin(keys))], f"{b}({o['_d'].date()})"

    def by_code(orig, level):
        codes = set(orig["_cd"]) - {""}
        if not codes:
            return None
        sel = gy["_ing4"].isin({c[:4] for c in codes}) if level == 4 else gy["_cd"].isin(codes)
        oth = gy[sel & ~gy["제품명"].isin(set(orig["제품명"]))]
        # 같은 회사 개량품(서방 등)은 오리지널 쪽: 오리지널 회사 품목 제외
        oco = set(orig["업체명"].map(co))
        oth = oth[~oth["업체명"].map(co).isin(oco)]
        f = oth.groupby("제품명")["적용일자"].min()
        unk = f[f.isin(bulk)]
        k = f[~f.isin(bulk)]
        early = int((f <= pd.Timestamp("2010-02-01")).sum())
        res = {"n": len(f), "모름": len(unk), "2010전": early}
        if not k.empty:
            res["첫"] = k.min().date(); res["누구"] = brand(k.idxmin())
        return res

    rows = []
    for term in [w.strip() for w in (os.environ.get("WORDS") or DEFAULT).split(",") if w.strip()]:
        A = site_origs(term)
        B, bdesc = first_origs(term)
        r = {"성분": term,
             "A 오리지널(사이트)": ",".join(sorted({brand(x) for x in A["제품명"]}))[:22] or "-",
             "B 오리지널(최초허가)": bdesc or "-",
             "B 코드": ",".join(sorted(set(B["_cd"]) - {""}))[:30] or "(코드 없음)"}
        for tag, o in (("A", A), ("B", B)):
            for lv, lab in ((4, "성분"), (9, "동일제제")):
                x = by_code(o, lv) if not o.empty else None
                if x:
                    first = f"{x.get('첫', '-')} {x.get('누구', '')}".strip()
                    if x["2010전"]:
                        first = f"2010전 {x['2010전']}개 · 이후 {first}"
                    r[f"{tag}-{lab}"] = f"{x['n']}개 · {first}"
                else:
                    r[f"{tag}-{lab}"] = "-"
        rows.append(r)
    out = pd.DataFrame(rows)
    for c in out.columns:
        print()
    print(out[["성분", "A 오리지널(사이트)", "B 오리지널(최초허가)", "B 코드"]].to_string(index=False))
    print()
    print(out[["성분", "A-성분", "B-성분"]].to_string(index=False))
    print()
    print(out[["성분", "A-동일제제", "B-동일제제"]].to_string(index=False))
    # 포시가처럼 성분명이 빈 오리지널
    f = pr[pr["제품명"].astype(str).str.startswith("포시가")]
    print("\n포시가 행:", f[["제품명", "주성분코드", "제제코드", "주성분명", "급여구분", "적용일자"]].drop_duplicates("제품명", keep="last").to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
