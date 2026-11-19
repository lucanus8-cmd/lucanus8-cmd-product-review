"""특허 자료를 11칸으로 줄여도 사이트 결과가 똑같은지 확인한다 (사이트에는 아무것도 쓰지 않음).

1) main 가지의 예전 bootstrap.py 로 허가·특허 자료 파일을 만든다 (특허 18칸)
2) 지금 가지의 bootstrap.py 로 다시 만든다 (특허 11칸)
3) 두 파일로 특허를 쓰는 화면 계산(성분별 만료·단일제·오리지널·등재 여부·검색용 표)을 각각 돌려 비교한다.
키는 GitHub 비밀값에서만 읽어 임시 secrets 파일에 두고, 화면에 적지 않는다.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.getcwd()
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402


def setup_secret():
    info = gsheet._info()
    if not info:
        print("키 없음")
        sys.exit(1)
    os.makedirs(".streamlit", exist_ok=True)
    b64 = base64.b64encode(json.dumps(info).encode()).decode()
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{b64}"\n')


def build(mod_name):
    import importlib
    shutil.rmtree("scout_data", ignore_errors=True)
    m = importlib.import_module(mod_name)
    t0 = time.time()
    r = m._do_build(only={"approval", "patent"}, force=True)
    print(f"   {mod_name}: {r} · {time.time() - t0:.0f}초")
    os.makedirs("/tmp/pt", exist_ok=True)
    shutil.copy("scout_data/patent.parquet", f"/tmp/pt/{mod_name}.parquet")
    shutil.copy("scout_data/approval.parquet", f"/tmp/pt/{mod_name}_ap.parquet")
    if os.path.exists("scout_data/keymap.parquet"):
        shutil.copy("scout_data/keymap.parquet", f"/tmp/pt/{mod_name}_km.parquet")


def results(variant):
    """그 변형 파일을 사이트 자료 자리에 두고 특허 화면 계산을 돌린다."""
    import pandas as pd
    import streamlit as st
    import scout_sources as ss
    os.makedirs("scout_data", exist_ok=True)
    shutil.copy(f"/tmp/pt/{variant}.parquet", "scout_data/patent.parquet")
    shutil.copy(f"/tmp/pt/{variant}_ap.parquet", "scout_data/approval.parquet")
    if os.path.exists(f"/tmp/pt/{variant}_km.parquet"):
        shutil.copy(f"/tmp/pt/{variant}_km.parquet", "scout_data/keymap.parquet")
    ss._ensure = lambda *a, **k: None          # 받기 시도 없이 지금 파일만 쓴다
    st.cache_data.clear()
    out = {}
    pt = ss.load_patent()
    out["load_patent(쓰는 칸)"] = pt[[c for c in ss._PATENT_COLS + ["_key", "_exp", "_single", "_core"]]].reset_index(drop=True)
    out["mem_MB"] = pt.memory_usage(deep=True).sum() / 1e6
    out["ncol"] = pt.shape[1]
    for nm, kw in (("성분별 만료(기본)", {}), ("단일제", {"single_only": True}), ("오리지널", {"original_only": True}),
                   ("등재 무관", {"listed_only": False}), ("단일+오리지널", {"single_only": True, "original_only": True})):
        out[nm] = ss.patent_by_key(**kw).sort_values("_key").reset_index(drop=True)
    out["등재 표시"] = ss.listed_patent_mask(pt).reset_index(drop=True)
    out["존속 표시"] = ss.active_patent_mask(pt["DOMESTIC_PATENT_STATUS"]).reset_index(drop=True)
    out["오리지널 표시"] = ss.original_patent_mask(pt).reset_index(drop=True)
    slim = ss.load_slim("patent", ["품목명", "INGR_ENG_NAME", "INGR_NAME", "PATENT_GB_CODE", "DOMESTIC_PATENT_STATUS",
                                   "DOMESTIC_END_DATE", "PATENTEE", ss.PATENT_PAGE_COL])
    out["AI 탭 읽기"] = ss.enrich_patent(slim).reset_index(drop=True)
    for q in ("아토르바스타틴", "rivaroxaban", "세레브렉스", "넥시움", "아브락산", "dapagliflozin", "에제티미브"):
        out[f"검색 {q}"] = pt[pt["품목명"].astype(str).str.contains(q, case=False, regex=False)
                              | pt["INGR_ENG_NAME"].astype(str).str.contains(q, case=False, regex=False)
                              | pt["INGR_NAME"].astype(str).str.contains(q, case=False, regex=False)
                              ][ss._PATENT_COLS + ["_key", "_exp"]].reset_index(drop=True)
    return out


def main():
    setup_secret()
    try:
        old = subprocess.run(["git", "show", "origin/main:bootstrap.py"], capture_output=True, text=True, check=True).stdout
        open("bootstrap_old.py", "w").write(old)
        print("1) 예전 코드(main)로 자료 만들기")
        build("bootstrap_old")
        print("2) 새 코드로 자료 만들기")
        build("bootstrap")
        import pandas as pd
        a = results("bootstrap_old")
        b = results("bootstrap")
        print(f"\n특허 표: 예전 {a['ncol']}칸 · {a['mem_MB']:.0f}MB  →  새 {b['ncol']}칸 · {b['mem_MB']:.0f}MB")
        print(f"   파일 크기: 예전 {os.path.getsize('/tmp/pt/bootstrap_old.parquet') / 1e6:.1f}MB → "
              f"새 {os.path.getsize('/tmp/pt/bootstrap.parquet') / 1e6:.1f}MB")
        bad = 0
        for k in a:
            if k in ("mem_MB", "ncol"):
                continue
            x, y = a[k], b[k]
            try:
                if isinstance(x, pd.DataFrame):
                    cols = [c for c in x.columns if c in y.columns]
                    pd.testing.assert_frame_equal(x[cols], y[cols], check_dtype=False)
                    extra = set(x.columns) ^ set(y.columns)
                    if extra and k != "AI 탭 읽기":
                        raise AssertionError(f"칸이 다름 {extra}")
                else:
                    pd.testing.assert_series_equal(x, y, check_dtype=False, check_names=False)
                print(f"   같음 ✅ {k} ({len(x):,}줄)")
            except AssertionError as e:
                bad += 1
                print(f"   다름 ❌ {k}: {str(e)[:300]}")
        print("\n결과:", "모두 같음" if not bad else f"{bad}가지 다름")
        return 1 if bad else 0
    finally:
        for p in (".streamlit/secrets.toml",):
            try:
                os.remove(p)
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
