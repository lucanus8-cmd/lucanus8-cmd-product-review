"""제품 하나가 성분 검색(제네릭 등재)에서 왜 빠지는지 본다 (읽기만, 공개 자료). WORDS='제품 앞글자;성분'."""
import base64, json, os, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402


def main():
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import pandas as pd
    import bootstrap
    print("자료 만들기:", bootstrap._do_build(only={"approval", "price"}, force=True))
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    import generic_entry as ge
    pd.set_option("display.width", 300); pd.set_option("display.max_colwidth", 60); pd.set_option("display.max_columns", 20)
    prod, term = (os.environ.get("WORDS") or "에바원;에독사반").split(";")
    pr = ss.load_price()
    m = pr[pr["제품명"].astype(str).str.replace(" ", "").str.startswith(prod)]
    print(f"\n[약가] '{prod}' {len(m)}행")
    print(m[["제품코드", "제품명", "업체명", "주성분명", "주성분코드", "제제코드", "성분수", "급여구분", "적용일자", "금액"]].to_string(index=False))
    ap = ss.load_approval()
    a = ap[ap["품목명"].astype(str).str.replace(" ", "").str.startswith(prod)]
    print(f"\n[허가] '{prod}' {len(a)}행")
    print(a[[c for c in ["품목명", "업체명", "허가일자", "주성분", "주성분(영문)", "상태", "신약구분"] if c in a.columns]].to_string(index=False))
    p = ge.prep_price(pr); bulk = ge.bulk_dates(p)
    aa = ge.prep_approval(ap, ss.ing_core, ss._ap_date)
    print("\n허가 성분 코어:", [ge.cores_of(s, ss.ing_core) for s in a["주성분"].head(3)], "· 찾는 성분:", ge.want_of(term, ss.ing_core))
    r = ge.analyze(p, aa, bulk, term, ss.ing_core)
    print("오리지널:", r["chosen"], r["reason"], "· 약가코드:", r["codes"], "· 오리지널 제품코드 수:", len(r["orig_codes"]), "· note:", r["note"])
    for k in ("same", "salt"):
        d = r[k]
        print(f"{k}: {len(d)}개", [] if d.empty else d.head(5)[["제품명", "등재일", "업체"]].values.tolist())
        if not d.empty:
            print("   여기에 이 제품 있음?", bool(d["제품명"].astype(str).str.replace(" ", "").str.startswith(prod).any()))
    pp = p[p["제품명"].astype(str).str.replace(" ", "").str.startswith(prod)]
    print("\n이 제품의 정리 이름·약가코드:", pp[["_nn", "_cd", "급여구분"]].drop_duplicates().values.tolist()[:6])
    print("허가 정리 이름:", [ge.nn(x) for x in a["품목명"].head(4)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
