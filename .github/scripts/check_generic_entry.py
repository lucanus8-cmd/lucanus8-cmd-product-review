"""제네릭 첫 급여 등재 찾기 시험 2차 (읽기만, 사이트에는 쓰지 않음). 공개 자료(심평원 약가·식약처 허가)만 쓴다.

바꾼 규칙
1) 성분: 허가 자료에서 '단일제'이고 성분 코어가 정확히 같은 품목의 영문키만 쓴다 (복합제 통한 다른 성분 끌려옴 방지).
   약가 쪽도 단일제만.
2) 오리지널: 그 성분 단일제 중 식약처 허가일이 가장 빠른 품목 (신약 표시와 상관없이).
   그 품목의 회사 제품(서방정·XR 등 개량)과 같은 상표 제품은 오리지널 쪽으로.
3) 약가 이력에 품목이 한꺼번에 처음 나온 날(자료 묶음)은 실제 등재일을 모르는 것으로 따로 센다.
4) 제품코드만 바뀐 경우를 새 등재로 세지 않도록, 제품명 기준으로 처음 나온 날을 쓴다.
"""
import base64
import json
import os
import re
import sys
import warnings
import logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
ROOT = os.getcwd()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

DEFAULT = ("다파글리플로진,올메사르탄,펠루비프로펜,펙소페나딘,로수바스타틴,아토르바스타틴,에제티미브,텔미사르탄,"
           "시타글립틴,리나글립틴,엠파글리플로진,리바록사반,아픽사반,에독사반,티카그렐러,"
           "에스오메프라졸,세레콕시브,프레가발린,솔리페나신,미라베그론,토파시티닙,두타스테리드,타다라필,레바미피드")


def setup_secret():
    info = gsheet._info()
    if not info:
        print("키 없음"); sys.exit(1)
    os.makedirs(".streamlit", exist_ok=True)
    b64 = base64.b64encode(json.dumps(info).encode()).decode()
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{b64}"\n')


def co(s):
    s = re.sub(r"\(주\)|주식회사|㈜|\s+", "", str(s))
    return s


def brand(name):
    """품목명 → 상표 줄기: '펠루비정(펠루비프로펜)' → '펠루비', '리피토정10밀리그램' → '리피토'"""
    s = re.sub(r"\s+", "", str(name))
    s = re.split(r"[0-9(_\[]", s)[0]
    s = re.sub(r"(서방|장용|구강붕해|필름코팅|연질|경질)?(정|캡슐|주|시럽|산|과립|액|현탁액|패취|겔|크림)$", "", s)
    return s


def main():
    setup_secret()
    import pandas as pd
    import bootstrap
    print("자료 만들기:", bootstrap._do_build(only={"approval", "price"}, force=True))
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    pd.set_option("display.width", 260); pd.set_option("display.max_colwidth", 26)
    pr = ss.load_price()
    ap = ss.load_approval().copy()
    ap["_d"] = ss._ap_date(ap)
    ap_single = ap[ss.is_single_series(ap["주성분"], ap["주성분(영문)"])]

    gy = pr[pr["급여구분"] == "급여"].copy()
    psingle = ss.is_single_series(gy["주성분명"]) & gy["성분수"].astype(str).isin(["", "1", "1.0", "nan", "None"])
    gy = gy[psingle]
    # 자료 묶음 날짜: 제품명이 처음 나온 날이 한꺼번에 몰린 날
    allfirst = pr.groupby("제품명")["적용일자"].min()
    vc = allfirst.value_counts()
    bulk = set(vc[vc >= 3000].index)
    print(f"약가 이력 {pr['적용일자'].min().date()} ~ {pr['적용일자'].max().date()} · "
          f"자료 묶음 날짜(제품명 3천 개 이상이 처음 나온 날): {sorted(str(d.date()) for d in bulk)} "
          f"· 다음으로 많은 날 {[(str(d.date()), int(n)) for d, n in vc[vc < 3000].head(4).items()]}")
    last_bulk = max(bulk) if bulk else pr["적용일자"].min()

    words = [w.strip() for w in (os.environ.get("WORDS") or DEFAULT).split(",") if w.strip()]
    rows, detail = [], {}
    for term in words:
        core = ss.ing_core(term)
        a0 = ap_single[(ap_single["_core"] == core) | ap_single["_core"].astype(str).str.startswith(core)]
        keys = set(a0["_key"].astype(str)) - {""}
        # 오리지널은 같은 영문키의 단일제 전체에서 고른다 (한글 염 표기가 달라도: 포시가=다파글리플로진프로판디올)
        a = ap_single[ap_single["_key"].isin(keys)]
        r = {"성분": term, "키": ",".join(sorted(keys))[:16] or "(허가에 없음)"}
        if a.empty or not keys:
            rows.append(r); continue
        a = a[a["_d"].notna()].sort_values("_d")
        o_item = a.iloc[0]
        newflag = a[a["신약구분"].astype(str).str.strip() == "신약"]["품목명"].astype(str).tolist() if "신약구분" in a.columns else []
        o_co, o_br = co(o_item["업체명"]), brand(o_item["품목명"])
        r.update({"오리지널(최초허가)": f"{o_br}·{str(o_item['업체명'])[:8]}", "허가일": o_item["_d"].date(),
                  "신약표시": "같음" if any(brand(x) == o_br for x in newflag) else ("없음" if not newflag else "다름:" + brand(newflag[0]))})
        same = gy[gy["_key"].isin(keys)]
        is_o = same["업체명"].map(co).eq(o_co) | same["제품명"].map(brand).eq(o_br)
        o, g = same[is_o], same[~is_o]
        r["오리지널 약가품목"] = o["제품명"].nunique()
        if o.empty:   # 왜 못 찾았나: 약가 전체에서 상표로 찾아 본다
            hit = pr[pr["제품명"].astype(str).str.replace(r"\s+", "", regex=True).str.startswith(o_br)]
            print(f"[{term}] 약가에서 '{o_br}' 품목 {hit['제품명'].nunique()}개 · 업체 {hit['업체명'].astype(str).unique()[:3].tolist()}")
            if not hit.empty:
                h = hit.sort_values("적용일자").drop_duplicates("제품명", keep="last")
                print(h[["제품명", "업체명", "주성분명", "성분수", "급여구분", "적용일자", "_key"]].head(6).to_string(index=False))
        if not o.empty:
            r["오리지널 첫급여"] = o["적용일자"].min().date()
        first = g.groupby("제품명")["적용일자"].min()
        r["제네릭"] = len(first)
        unk = first[first.isin(bulk) | (first <= pr["적용일자"].min())]
        known = first[~first.index.isin(unk.index)]
        r["날짜모름(묶음)"] = len(unk)
        early = first[first <= pd.Timestamp("2010-02-01")]
        r["판정"] = (f"2010.02 전부터 제네릭 {len(early)}개" if len(early) else
                    ("제네릭 없음" if first.empty else "첫 등재 찾음"))
        if not known.empty:
            fd = known.min()
            who = known.idxmin()
            wco = g[g["제품명"] == who]["업체명"].iloc[0]
            r["첫 제네릭 등재"] = fd.date()
            r["첫 제네릭"] = f"{brand(who)}·{str(wco)[:6]}"
            r["1년안"] = int(((known >= fd) & (known < fd + pd.Timedelta(days=365))).sum())
            if not o.empty:
                cuts = []
                for _, og in o.groupby("제품명"):
                    og = og.sort_values("적용일자")
                    p, t = og["금액"].values, og["적용일자"].values
                    for i in range(1, len(og)):
                        if p[i] < p[i - 1] * 0.97 and pd.Timestamp(t[i]) >= fd - pd.Timedelta(days=31):
                            cuts.append((pd.Timestamp(t[i]), (p[i] - p[i - 1]) / p[i - 1] * 100)); break
                if cuts:
                    c = min(cuts); r["그뒤 오리지널 인하"] = f"{c[0].date()} {c[1]:+.0f}%"
        if "제제코드" in g.columns and not o.empty:
            oc = set(o["제제코드"].astype(str)) - {"", "nan"}
            f2 = g[g["제제코드"].astype(str).isin(oc)].groupby("제품명")["적용일자"].min()
            f2k = f2[~f2.isin(bulk)]
            r["동일제제 첫등재"] = f2k.min().date() if not f2k.empty else ("묶음 전" if len(f2) else "-")
        rows.append(r)
        detail[term] = (o.groupby("제품명")["적용일자"].min().sort_values().head(4),
                        first.sort_values().head(6))
    out = pd.DataFrame(rows)
    print()
    print(out.to_string(index=False, max_colwidth=26))
    for term in ["다파글리플로진", "올메사르탄", "세레콕시브", "펙소페나딘"]:
        if term in detail:
            o1, g1 = detail[term]
            print(f"\n[{term}] 오리지널 쪽 품목(첫 급여일): " + " / ".join(f"{k[:24]} {v.date()}" for k, v in o1.items()))
            print("   제네릭 앞쪽: " + " / ".join(f"{k[:24]} {v.date()}" for k, v in g1.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
