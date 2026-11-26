"""제네릭 첫 급여 등재 찾기 — 단일제·복합제 섞어 한 규칙으로 시험 (읽기만, 사이트에 쓰지 않음). 공개 자료만.

규칙
- 성분 조합: 약가 '주성분명'을 성분마다 영문키로 (단일제=1개, 복합제=여러 개, 순서 무관). 빈 성분명은 같은 주성분코드에서 빌림.
- 오리지널: 허가에서 성분 조합이 정확히 같은 품목 중 최초 허가 품목. 약가에서는 그 상표로 시작하는 품목(서방·XR·듀오 아님)과
  그 회사 품목을 오리지널 쪽으로.
- ◆ 동일제제 = 오리지널과 주성분코드가 같은 품목 / ◇ 염변경 등 = 조합은 같은데 코드가 다른 품목.
- 약가 이력에 수천 품목이 한꺼번에 처음 나온 날(자료 묶음)은 등재일 모름.
"""
import base64, json, os, re, sys, warnings, logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
ROOT = os.getcwd()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

DEFAULT = ("클로피도그렐;암로디핀;로수바스타틴;아토르바스타틴;텔미사르탄;시타글립틴;리나글립틴;다파글리플로진;"
           "엠파글리플로진;리바록사반;아픽사반;에독사반;티카그렐러;에스오메프라졸;세레콕시브;프레가발린;"
           "솔리페나신;미라베그론;토파시티닙;몬테루카스트;페북소스타트;실로스타졸;"
           "텔미사르탄+암로디핀;로수바스타틴+에제티미브;시타글립틴+메트포르민;다파글리플로진+메트포르민;"
           "아토르바스타틴+에제티미브;아토르바스타틴+암로디핀;로사르탄+히드로클로로티아지드;"
           "몬테루카스트+레보세티리진;엠파글리플로진+리나글립틴;올메사르탄+암로디핀+히드로클로로티아지드")


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
    pr = ss.load_price()
    ap = ss.load_approval().copy()
    ap["_d"] = ss._ap_date(ap)

    def strip(s):
        return re.sub(r"\[[^\]]*\]|\([^)]*\)", "", str(s or ""))

    def kset(s, sep):
        parts = [p for p in re.split(sep, strip(s)) if p.strip()]
        return frozenset(k for k in (ss.ing_key(p) for p in parts) if k)

    pr["_set"] = pr["주성분명"].map(lambda s: kset(s, r"[,/+]"))
    def _c(x):
        x = x.strip().upper() if isinstance(x, str) else ""
        return "" if x in ("NAN", "NONE", "NULL") else x
    code = pr["주성분코드"].map(_c)
    alt = pr["제제코드"].map(_c)
    pr["_cd"] = code.where(code.str.len() > 0, alt)          # 최신 고시 행은 제제코드만 있을 수 있다
    print(f"코드 빈 행 {int((pr['_cd'].str.len() == 0).sum()):,} · 성분명 빈 행 {int((pr['주성분명'].astype(str).str.strip().isin(['', 'nan'])).sum()):,}")
    has = pr["_set"].map(len) > 0
    cmap = pr[has].groupby("_cd")["_set"].agg(lambda x: x.value_counts().index[0])
    pr.loc[~has, "_set"] = pr.loc[~has, "_cd"].map(cmap)
    pr["_set"] = pr["_set"].map(lambda v: v if isinstance(v, frozenset) else frozenset())
    c4 = pr[has & (pr["_set"].map(len) == 1)].groupby(pr["_cd"].str[:4])["_set"].agg(lambda x: x.value_counts().index[0])
    miss = pr["_set"].map(len) == 0
    pr.loc[miss, "_set"] = pr.loc[miss, "_cd"].str[:4].map(c4)
    pr["_set"] = pr["_set"].map(lambda v: v if isinstance(v, frozenset) else frozenset())
    print(f"성분 조합 못 정한 행(코드로 채운 뒤) {int((pr['_set'].map(len) == 0).sum()):,}")
    gy = pr[pr["급여구분"] == "급여"]
    vc = pr.groupby("제품명")["적용일자"].min().value_counts()
    bulk = set(vc[vc >= 3000].index)
    ap["_eset"] = ap["주성분(영문)"].map(lambda s: kset(s, r"[/,+|;\n]|및"))
    ap1 = ap[ap["_eset"].map(len) == 1]
    # 영문 성분명이 빈 허가(오래된 품목)는 한글 성분을 영문키로 바꿔 채운다
    k2 = {}
    for c, k in zip(ap1["_core"].astype(str), ap1["_key"].astype(str)):
        if c and k:
            k2.setdefault(c, {}).setdefault(k, 0); k2[c][k] += 1
    k2 = {c: max(v, key=v.get) for c, v in k2.items()}

    def kor_eset(s):
        parts = [p for p in re.split(r"[|/,+]|및", str(s or "")) if p.strip()]
        ks = {k2.get(ss.ing_core(p), "") for p in parts}
        return frozenset(k for k in ks if k) if all(ks) else frozenset()
    miss = pr["_set"].map(len) == 0

    def from_name(n):
        m = re.search(r"\(([^()_]+)\)", str(n))
        if not m:
            return frozenset()
        return kor_eset(m.group(1))
    pr.loc[miss, "_set"] = pr.loc[miss, "제품명"].map(from_name)
    pr["_set"] = pr["_set"].map(lambda v: v if isinstance(v, frozenset) else frozenset())
    print(f"성분 조합 못 정한 행(제품명 괄호로 채운 뒤) {int((pr['_set'].map(len) == 0).sum()):,}")
    gy = pr[pr["급여구분"] == "급여"]
    blank = ap["_eset"].map(len) == 0
    ap.loc[blank, "_eset"] = ap.loc[blank, "주성분"].map(kor_eset)
    print(f"영문 성분 빈 허가 {int(blank.sum()):,}건 → 한글로 채움 {int((ap.loc[blank, '_eset'].map(len) > 0).sum()):,}건")

    def kor2key(w):
        c = ss.ing_core(w)
        a1 = ap1[(ap1["_core"] == c) | ap1["_core"].astype(str).str.startswith(c)]
        v = a1["_key"].astype(str)
        v = v[v.str.len() > 0]
        return v.value_counts().index[0] if len(v) else ""

    out = []
    for item in [c.strip() for c in (os.environ.get("WORDS") or DEFAULT).split(";") if c.strip()]:
        eset = frozenset(kor2key(x) for x in item.split("+"))
        r = {"품목": item, "구분": "복합" if "+" in item else "단일"}
        a = ap[(ap["_eset"] == eset) & ap["_d"].notna()].sort_values("_d")
        if a.empty or "" in eset:
            r["결과"] = "허가에서 못 찾음"; out.append(r); continue
        new = a[a["신약구분"].astype(str).str.strip() == "신약"] if "신약구분" in a.columns else a.iloc[0:0]
        o = new.iloc[0] if not new.empty else a.iloc[0]
        r["근거"] = "신약표시" if not new.empty else "최초허가"
        ob = brand(o["품목명"])
        if item in ("클로피도그렐", "암로디핀", "로사르탄+히드로클로로티아지드"):
            print(f"[{item}] 허가 앞쪽:", [(str(x)[:14], str(d.date()), st) for x, d, st in
                                       zip(a["품목명"].head(4), a["_d"].head(4), a["상태"].head(4))])
            pv = ap[ap["품목명"].astype(str).str.contains("플라빅스|노바스크|코자플러스", regex=True)]
            print("   찾는 오리지널 허가:", [(str(x)[:14], str(e)[:30], str(k)[:30], str(d)[:10]) for x, e, k, d in
                                        zip(pv["품목명"].head(5), pv["주성분(영문)"].head(5), pv["주성분"].head(5), pv["_d"].head(5))])
        r["오리지널"] = f"{ob}({o['_d'].year})"
        same = gy[gy["_set"] == eset]
        # 최초허가로 고른 경우: 처음 자료에 나올 때 같은 조합에서 더 비싼 품목이 있으면 그것을 오리지널로
        #   (양도양수로 허가일이 새로 찍힌 오래된 오리지널: 노바스크·코자플러스 등)
        if r.get("근거") == "최초허가" and not same.empty:
            d0 = same["적용일자"].min()
            snap = same[same["적용일자"] <= d0 + pd.Timedelta(days=62)].copy()
            snap["_b"] = snap["제품명"].map(brand)
            bp = snap.groupby("_b")["금액"].max()
            if ob in bp.index and len(bp) and bp.max() > bp[ob] * 1.01:
                newb = bp.idxmax()
                r["근거"] = f"최초허가({ob})→최고가"
                ob = newb
                r["오리지널"] = f"{ob}(최고가)"
        nm = same["제품명"].astype(str).str.replace(r"\s+", "", regex=True)
        is_br = nm.str.startswith(ob)
        orig = same[nm.map(brand) == ob]
        if orig.empty:
            r["결과"] = "약가에서 오리지널 못 찾음"; out.append(r); continue
        ocos = set(same.loc[is_br, "업체명"].map(co))
        codes = {c for c in orig["_cd"] if c}
        oth = same[~is_br & ~same["업체명"].map(co).isin(ocos)]
        f = oth.groupby("제품명").agg(d=("적용일자", "min"), c=("_cd", "first"))
        r["오리지널 첫급여"] = str(orig["적용일자"].min().date())[:7]
        d0 = orig["적용일자"].min()
        snap = same[(same["적용일자"] <= d0 + pd.Timedelta(days=62)) & same["_cd"].isin(codes)]
        if not snap.empty:
            top = snap.sort_values("금액", ascending=False).iloc[0]
            r["그때 최고가"] = "오리지널" if brand(top["제품명"]) == ob else brand(top["제품명"])[:8]
        # 단일제는 코드 앞 4자리(염까지 같은 성분)가 같으면 함량이 달라도 ◆. 복합제는 함량 조합마다 앞 4자리가 달라 코드 전체로.
        same_ing = f["c"].isin(codes) | (f["c"].str[:4].isin({c[:4] for c in codes}) if len(eset) == 1 else False)
        r["오리지널 코드"] = ",".join(sorted({c[:4] for c in codes}))[:20]
        for tag, sub in (("◆같은성분코드", f[same_ing]), ("◇염변경등", f[~same_ing])):
            k = sub[~sub["d"].isin(bulk)]
            pre = int((sub["d"] <= pd.Timestamp("2010-02-01")).sum())
            if sub.empty:
                r[tag] = "없음"
            elif pre:
                r[tag] = f"{len(sub)}개 · 2010전부터({pre})"
            elif k.empty:
                r[tag] = f"{len(sub)}개 · 날짜모름"
            else:
                r[tag] = f"{len(sub)}개 · {str(k['d'].min().date())[:7]} {brand(k['d'].idxmin())[:8]}"
        out.append(r)
    df = pd.DataFrame(out)
    pd.set_option("display.width", 260); pd.set_option("display.max_colwidth", 34)
    print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
