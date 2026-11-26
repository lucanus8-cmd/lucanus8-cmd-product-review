"""복합제의 오리지널·제네릭 첫 급여 등재 찾기 시험 (읽기만, 사이트에 쓰지 않음). 공개 자료(심평원 약가·식약처 허가)만.

1) 약가 자료에 복합제 성분명이 모두 적혀 있는지, 주성분코드가 함량 조합마다 다른지 본다.
2) 성분 조합(순서 무관)이 같은 허가 품목 중 최초 허가 = 오리지널.
3) 오리지널과 주성분코드가 같은 품목 = 동일제제 제네릭(◆), 조합은 같은데 코드가 다른 품목 = 염변경 등(◇).
"""
import base64, json, os, re, sys, warnings, logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
ROOT = os.getcwd()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

DEFAULT = ("텔미사르탄+암로디핀;시타글립틴+메트포르민;로수바스타틴+에제티미브;로사르탄+암로디핀;"
           "발사르탄+암로디핀;올메사르탄+암로디핀;아토르바스타틴+에제티미브;에제티미브+심바스타틴;"
           "다파글리플로진+메트포르민;리나글립틴+메트포르민;올메사르탄+암로디핀+히드로클로로티아지드;"
           "텔미사르탄+암로디핀+로수바스타틴")
SPLIT = r"[/,+]|및"


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


def main():
    setup_secret()
    import pandas as pd
    import bootstrap
    print("자료 만들기:", bootstrap._do_build(only={"approval", "price"}, force=True))
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    pd.set_option("display.width", 260); pd.set_option("display.max_colwidth", 60)
    pr = ss.load_price()
    ap = ss.load_approval().copy()
    ap["_d"] = ss._ap_date(ap)

    def strip(s):
        return re.sub(r"\[[^\]]*\]|\([^)]*\)", "", str(s or ""))

    def eng_set(s):
        parts = [p for p in re.split(SPLIT, strip(s)) if p.strip()]
        return frozenset(k for k in (ss.ing_key(p) for p in parts) if k)

    def kor_set(s):
        parts = [p for p in re.split(SPLIT, strip(s)) if p.strip()]
        return [ss.ing_core(p) for p in parts if ss.ing_core(p)]

    # ① 약가 자료의 복합제 표기 모양
    multi = pr[pr["성분수"].astype(str).isin(["2", "3", "4"])]
    print(f"성분수 2~4 행 {len(multi):,} · 제품 {multi['제품명'].nunique():,}")
    smp = multi.drop_duplicates("제품명").sample(min(8, multi['제품명'].nunique()), random_state=1)
    print(smp[["제품명", "주성분명", "주성분코드", "성분수"]].to_string(index=False))
    has_sep = multi["주성분명"].astype(str).str.contains(SPLIT, regex=True)
    print(f"→ 복합제 행 중 성분명에 구분자(/ , +)가 있는 비율 {float(has_sep.mean()):.2f} · "
          f"성분명 빈 행 {int((multi['주성분명'].astype(str).str.strip() == '').sum()):,}")
    blank_cnt = pr["성분수"].astype(str).str.strip().isin(["", "nan", "None"])
    print(f"성분수 비어 있는 행 {int(blank_cnt.sum()):,} · 그중 성분명에 구분자 있는 행 "
          f"{int((blank_cnt & pr['주성분명'].astype(str).str.contains(SPLIT, regex=True)).sum()):,}")

    pr["_set"] = pr["주성분명"].map(eng_set)
    # 성분명이 비어 있는 행: 같은 주성분코드의 다른 행에서 조합을 빌려 온다
    code_set = (pr[pr["_set"].map(len) > 0].groupby(pr["주성분코드"].astype(str))["_set"]
                .agg(lambda x: x.value_counts().index[0]))
    empty = pr["_set"].map(len) == 0
    pr.loc[empty, "_set"] = pr.loc[empty, "주성분코드"].astype(str).map(code_set)
    pr["_set"] = pr["_set"].map(lambda v: v if isinstance(v, frozenset) else frozenset())
    print(f"성분 조합을 못 정한 행 {int((pr['_set'].map(len) == 0).sum()):,} / {len(pr):,}")
    gy = pr[pr["급여구분"] == "급여"]
    nm_first = pr.groupby("제품명")["적용일자"].min()
    vc = nm_first.value_counts()
    bulk = set(vc[vc >= 3000].index)

    # 허가 자료의 복합제 표기 모양
    tw = ap[ap["품목명"].astype(str).str.contains("트윈스타|자누메트|로수젯|아모잘탄", regex=True)]
    print("\n허가 복합제 표기 예:")
    print(tw[["품목명", "주성분", "주성분(영문)"]].head(6).to_string(index=False))
    SPLIT_AP = r"[/,+|;\n]|및"

    def eng_set_ap(s):
        parts = [p for p in re.split(SPLIT_AP, strip(s)) if p.strip()]
        return frozenset(k for k in (ss.ing_key(p) for p in parts) if k)
    ap["_eset"] = ap["주성분(영문)"].map(eng_set_ap)
    ap1 = ap[ss.is_single_series(ap["주성분"], ap["주성분(영문)"])]
    print("허가 영문 조합 성분 수 분포:", ap["_eset"].map(len).value_counts().sort_index().to_dict())

    def kor2key(w):
        c = ss.ing_core(w)
        a1 = ap1[(ap1["_core"] == c) | ap1["_core"].astype(str).str.startswith(c)]
        v = a1["_key"].astype(str)
        v = v[v.str.len() > 0]
        return v.value_counts().index[0] if len(v) else ""

    rows = []
    for combo in [c.strip() for c in (os.environ.get("WORDS") or DEFAULT).split(";") if c.strip()]:
        eset = frozenset(kor2key(x) for x in combo.split("+"))
        n = len(combo.split("+"))
        a = ap[ap["_eset"] == eset]
        r = {"조합": combo, "허가 품목": len(a), "영문 조합": "+".join(sorted(eset))}
        a = a[a["_d"].notna()].sort_values("_d")
        if a.empty or len(eset) != n:
            r["판정"] = "허가에서 조합 못 찾음"; rows.append(r); continue
        o = a.iloc[0]
        ob = brand(o["품목명"])
        r["오리지널(최초허가)"] = f"{ob} · {str(o['업체명'])[:10]} · {o['_d'].date()}"
        same = gy[gy["_set"] == eset]
        orig = same[same["제품명"].map(brand) == ob]
        r["약가 같은조합 품목"] = same["제품명"].nunique()
        r["오리지널 약가품목"] = orig["제품명"].nunique()
        if orig.empty:
            r["판정"] = "약가에서 오리지널 못 찾음"; rows.append(r); continue
        codes = sorted(set(orig["주성분코드"].astype(str)))
        r["오리지널 코드(함량별)"] = ", ".join(codes)[:60]
        r["코드수/함량수"] = f"{len(codes)}/{orig['제품명'].nunique()}"
        oth = same[~same["제품명"].isin(set(orig["제품명"]))]
        oth = oth[oth["업체명"].astype(str) != str(orig["업체명"].iloc[0])]
        f = oth.groupby(["제품명"]).agg(d=("적용일자", "min"), c=("주성분코드", "first"))
        dz = f[f["c"].astype(str).isin(codes)]
        sz = f[~f["c"].astype(str).isin(codes)]

        def first(df):
            k = df[~df["d"].isin(bulk)]
            pre = int((df["d"] <= pd.Timestamp("2010-02-01")).sum())
            if k.empty:
                return f"{len(df)}개 · " + ("2010전" if pre else "-")
            nm = k["d"].idxmin()
            s = f"{len(df)}개 · 첫 {k['d'].min().date()} {brand(nm)}"
            return (f"(2010전 {pre}) " if pre else "") + s
        r["◆ 동일제제(같은 코드)"] = first(dz)
        r["◇ 염변경 등(다른 코드)"] = first(sz)
        # 함량별 첫 제네릭
        per = []
        for c in codes:
            sub = dz[dz["c"].astype(str) == c]
            sub = sub[~sub["d"].isin(bulk)]
            per.append(f"{c}:{sub['d'].min().date() if not sub.empty else '-'}")
        r["함량별 첫 제네릭"] = " ".join(per)[:90]
        r["판정"] = "찾음"
        rows.append(r)
        if combo.startswith("텔미사르탄+암로디핀") and n == 2:
            print("\n[텔미사르탄+암로디핀] 오리지널 약가 품목:")
            print(orig.drop_duplicates("제품명")[["제품명", "주성분코드", "주성분명"]].to_string(index=False))
            print("  ◇ 염변경 등 앞쪽:", sz.sort_values("d").head(5).to_dict("index"))
    out = pd.DataFrame(rows)
    print()
    for _, r in out.iterrows():
        print("—" * 60)
        for k, v in r.items():
            if pd.notna(v) and v != "":
                print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
