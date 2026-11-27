"""사이트 메모리 측정 (읽기만, 사이트에 쓰지 않음) — 매출 숫자는 적지 않고 메모리 크기(MB)만 적는다.
사이트와 같은 자료·같은 불러오기 방식으로 단계별 메모리(RSS)와 표 크기, cache_data 가 보관·복사하는 크기를 잰다."""
import base64, gc, json, os, pickle, sys, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402


def rss():
    with open("/proc/self/status") as f:
        for ln in f:
            if ln.startswith("VmRSS"):
                return int(ln.split()[1]) / 1024
    return 0.0


def mb(df):
    return df.memory_usage(deep=True).sum() / 1e6


LOG = []


def step(name, extra=""):
    gc.collect()
    r = rss()
    LOG.append((name, r))
    print(f"{name:<52} RSS {r:7.0f} MB  {extra}")


def main():
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import pandas as pd
    import bootstrap
    step("0. 시작 (pandas·bootstrap 불러옴)")
    t = time.time()
    print("   자료 만들기:", bootstrap._do_build(include_sales=True, force=True), f"{time.time()-t:.0f}초")
    gc.collect(); step("1. 자료 파일 만든 뒤 (만드는 데 쓴 메모리는 사이트 시작 때만)")
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    import streamlit  # noqa
    step("2. streamlit·scout_sources 불러옴")

    pr = ss.load_price(); step("3. 약가 load_price", f"표 {mb(pr):.0f} MB · {len(pr):,}행")
    ap = ss.load_approval(); step("4. 허가 load_approval", f"표 {mb(ap):.0f} MB · {len(ap):,}행")

    # ── 제네릭 등재 (작업 가지 코드) ──
    import generic_entry as ge
    t = time.time()
    p = ge.prep_price(pr); a = ge.prep_approval(ap, ss.ing_core, ss._ap_date); bulk = ge.bulk_dates(p); opts = ge.ingredient_options(a)
    step("5. 제네릭 등재 기준표 만듦 (_ge_base)", f"p {mb(p):.0f} MB · a {mb(a):.0f} MB · {time.time()-t:.1f}초")
    blob = pickle.dumps((p, a, bulk, opts), protocol=pickle.HIGHEST_PROTOCOL)
    step("6. cache_data 가 보관하는 직렬화 바이트", f"{len(blob)/1e6:.0f} MB (서버에 계속 남음)")
    t = time.time(); cp = pickle.loads(blob)
    step("7. cache_data 를 부를 때마다 만드는 복사본", f"{time.time()-t:.1f}초 (화면 다시 그릴 때마다)")
    del cp; gc.collect(); step("8. 복사본 버린 뒤")
    for term in ("에독사반", "로수바스타틴", "암로디핀 + 텔미사르탄"):
        r = ge.analyze(p, a, bulk, term, ss.ing_core)
    step("9. 성분 3개 계산")
    del blob, p, a; gc.collect(); step("10. 제네릭 표 모두 버린 뒤")

    # ── 매출 (사이트 app.py 와 같은 pkl) ──
    iq = pd.read_pickle(bootstrap.SAVED / "iqvia.pkl"); step("11. IQVIA 불러옴", f"표 {mb(iq):.0f} MB · {len(iq):,}행 · {iq.shape[1]}칸")
    ib = pickle.dumps(iq, protocol=pickle.HIGHEST_PROTOCOL); step("12. IQVIA cache_data 보관 바이트", f"{len(ib)/1e6:.0f} MB")
    ub = pd.read_pickle(bootstrap.SAVED / "ubist.pkl"); step("13. UBIST 불러옴", f"표 {mb(ub):.0f} MB · {len(ub):,}행 · {ub.shape[1]}칸")
    ubb = pickle.dumps(ub, protocol=pickle.HIGHEST_PROTOCOL); step("14. UBIST cache_data 보관 바이트", f"{len(ubb)/1e6:.0f} MB")
    try:
        from dose_util_feature import iqvia_combo, ubist_combo, price_ingredient_counts  # 로봇 전용 사본 (사이트 코드 아님)
        iq["_구성"] = iqvia_combo(iq)
        cn = price_ingredient_counts(ss.SD / "price.parquet")
        ub["_구성"] = ubist_combo(ub, cn)
        step("15. 단일제/복합제 칸 붙임 + 약가 성분 수 사전", f"사전 {len(cn):,}개")
        f1 = iq[iq["_구성"] == "단일제"]; f2 = ub[ub["_구성"] == "단일제"]
        step("16. '단일제만' 걸러낸 복사본", f"IQVIA {mb(f1):.0f} MB · UBIST {mb(f2):.0f} MB")
        del f1, f2
    except Exception as e:
        print("   단일제/복합제 측정 건너뜀:", type(e).__name__, str(e)[:80])
    gc.collect(); step("17. 마지막")
    peak = max(r for _, r in LOG)
    print(f"\n가장 높았던 RSS {peak:.0f} MB (무료 서버 한도 약 1,000 MB 내외)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
