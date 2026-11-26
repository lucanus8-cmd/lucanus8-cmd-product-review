"""제네릭 첫 급여 등재 찾기 시험 (읽기만, 사이트에는 쓰지 않음). 공개 자료(심평원 약가·식약처 허가)만 쓴다.

사이트 '약가 조회' 그래프와 같은 방법으로 오리지널 품목을 고르고, 같은 성분의 나머지 급여 품목을
제네릭으로 보아 품목마다 첫 급여 적용일자를 구한다. 동일제제(제제코드 같음) 기준도 함께 본다.
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

DEFAULT = ("펠루비프로펜,펙소페나딘,로수바스타틴,아토르바스타틴,에제티미브,텔미사르탄,올메사르탄,"
           "시타글립틴,리나글립틴,다파글리플로진,엠파글리플로진,리바록사반,아픽사반,에독사반,티카그렐러,"
           "에스오메프라졸,셀레콕시브,프레가발린,솔리페나신,미라베그론,토파시티닙,두타스테리드,타다라필,레바미피드")


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
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 30)
    pr = ss.load_price()
    gy = pr[pr["급여구분"] == "급여"]
    d0, d1 = pr["적용일자"].min(), pr["적용일자"].max()
    print(f"약가 이력 {len(pr):,}행 · 제품 {pr['제품코드'].nunique():,}개 · 기간 {d0.date()} ~ {d1.date()}")
    print("급여구분 값:", pr["급여구분"].value_counts().head(5).to_dict())
    print("칸:", list(pr.columns))
    fc = gy.groupby("제품코드")["적용일자"].min()
    print("품목별 첫 급여일 중 자료 시작일과 같은 것:", int((fc == d0).sum()), "/", len(fc),
          "· 첫 급여일 분포(연):", fc.dt.year.value_counts().sort_index().to_dict())
    print("첫 급여일이 몰린 날 (상위 10):", {str(k.date()): int(v) for k, v in fc.value_counts().head(10).items()})
    # 몰린 날에 처음 나온 품목이 그 전에 다른 코드로 있었는지: 같은 제품명이 더 이른 날짜에 있는지
    nm_first = gy.groupby("제품명")["적용일자"].min()
    code_nm = gy.drop_duplicates("제품코드").set_index("제품코드")["제품명"]
    for day in list(fc.value_counts().head(4).index):
        codes = fc[fc == day].index
        nms = code_nm.reindex(codes)
        earlier = int((nm_first.reindex(nms.values).values < day).sum())
        print(f"   {day.date()}: 처음 나온 품목 {len(codes):,}개 중 같은 제품명이 더 일찍 있던 것 {earlier:,}개")
    if "성분수" in gy.columns:
        print("성분수 값:", gy["성분수"].astype(str).value_counts().head(5).to_dict())

    words = [w.strip() for w in (os.environ.get("WORDS") or DEFAULT).split(",") if w.strip()]
    rows = []
    for term in words:
        core = ss.ing_core(term)
        keys = ss.resolve_keys(term) | ss.resolve_keys(core)
        origs = ss.original_products(keys) if keys else []
        pref = {re.split(r"[0-9]", re.sub(r"\s", "", o))[0] for o in origs}
        pref = {p for p in pref if len(p) >= 2}
        single = gy["성분수"].astype(str).isin(["1", "1.0"]) if "성분수" in gy.columns else True
        same = gy[(gy["_key"].isin(keys) | (gy["_core"] == core)) & single]
        nms = same["제품명"].astype(str).tolist()
        is_o = pd.Series([any(re.sub(r"\s", "", n).startswith(p) for p in pref) for n in nms],
                         index=same.index, dtype=bool)
        o, g = same[is_o], same[~is_o]
        r = {"성분": term, "키": ",".join(sorted(keys))[:20], "오리지널 이름": ",".join(sorted(pref))[:24] or "(못 찾음)",
             "오리지널 품목": o["제품코드"].nunique(), "제네릭 품목": g["제품코드"].nunique()}
        if not o.empty:
            r["오리지널 첫 급여"] = o["적용일자"].min().date()
        if not g.empty:
            first = g.groupby("제품코드")["적용일자"].min()
            pre = int((first <= d0).sum())
            real = first[first > d0]
            r["자료시작 전"] = pre
            if not real.empty:
                fd = real.min()
                who = g[(g["제품코드"] == real.idxmin())]["제품명"].iloc[0]
                r["첫 제네릭 등재"] = fd.date()
                r["첫 제네릭 품목"] = str(who)[:22]
                r["1년 안 등재 수"] = int(((real >= fd) & (real < fd + pd.Timedelta(days=365))).sum())
                # 첫 등재 뒤 오리지널 첫 인하
                if not o.empty:
                    oc = o.sort_values("적용일자")
                    after = []
                    for _, og in oc.groupby("제품코드"):
                        og = og.sort_values("적용일자")
                        p = og["금액"].values; t = og["적용일자"].values
                        for i in range(1, len(og)):
                            if p[i] < p[i - 1] and pd.Timestamp(t[i]) >= fd - pd.Timedelta(days=31):
                                after.append((pd.Timestamp(t[i]), (p[i] - p[i - 1]) / p[i - 1] * 100)); break
                    if after:
                        a = min(after)
                        r["그 뒤 오리지널 첫 인하"] = f"{a[0].date()} {a[1]:+.0f}%"
            # 동일제제 기준 (오리지널과 제제코드가 같은 것만)
            if "제제코드" in g.columns and not o.empty:
                oc_codes = set(o["제제코드"].astype(str)) - {"", "nan"}
                gs = g[g["제제코드"].astype(str).isin(oc_codes)]
                f2 = gs.groupby("제품코드")["적용일자"].min()
                f2 = f2[f2 > d0]
                r["동일제제 제네릭"] = gs["제품코드"].nunique()
                r["동일제제 첫 등재"] = f2.min().date() if not f2.empty else "-"
        rows.append(r)
    out = pd.DataFrame(rows)
    print()
    print(out.to_string(index=False))
    # 이상해 보이는 것: 오리지널을 못 찾음 / 제네릭인데 오리지널보다 먼저 등재
    print()
    for r in rows:
        if r.get("오리지널 품목", 0) == 0:
            print(f"⚠ {r['성분']}: 오리지널을 못 찾음 → 같은 성분 전부가 제네릭으로 잡힘")
        if r.get("첫 제네릭 등재") and r.get("오리지널 첫 급여") and r["첫 제네릭 등재"] < r["오리지널 첫 급여"]:
            print(f"⚠ {r['성분']}: 첫 제네릭({r['첫 제네릭 등재']})이 오리지널 첫 급여({r['오리지널 첫 급여']})보다 먼저")
    # 한 성분 자세히: 첫 등재 앞뒤 품목
    for term in words[:3]:
        core = ss.ing_core(term)
        keys = ss.resolve_keys(term) | ss.resolve_keys(core)
        single = gy["성분수"].astype(str).isin(["1", "1.0"]) if "성분수" in gy.columns else True
        same = gy[(gy["_key"].isin(keys) | (gy["_core"] == core)) & single]
        first = same.groupby(["제품코드", "제품명"])["적용일자"].min().sort_values().reset_index()
        print(f"\n[{term}] 품목별 첫 급여일 (앞 12개)")
        print(first.head(12).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
