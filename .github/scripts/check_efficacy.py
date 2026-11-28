"""질병명 검색 시험: 식약처 허가 원본 시트에 '효능·효과' 칸이 있는지, 있으면 질병명으로 성분을 잘 찾는지.
허가 자료는 공개 자료다. 사이트에 쓰지 않는다. 매출은 '몇 개 성분 묶음이 걸리는지' 개수만 적고 금액은 적지 않는다."""
import base64, json, os, re, sys, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

TERMS = [s for s in (os.environ.get("WORDS") or "").split(",") if s.strip()] or [
    "고혈압", "당뇨", "이상지질혈증", "고지혈증", "골다공증", "위식도역류", "천식", "우울", "치매", "알츠하이머",
    "통풍", "전립선비대", "뇌졸중", "비염", "류마티스", "건선", "편두통", "과민성방광", "탈모", "변비"]


def main():
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import pandas as pd
    import bootstrap, scout_sources as ss
    from googleapiclient.discovery import build
    sheets = build("sheets", "v4", credentials=bootstrap._creds(), cache_discovery=False)
    t = time.time()
    raw = bootstrap._sheet_df(sheets, bootstrap.SHEETS["approval"])
    print(f"허가 원본 시트: {len(raw):,}행 · {raw.shape[1]}칸 · {time.time()-t:.0f}초 · 메모리 {raw.memory_usage(deep=True).sum()/1e6:.0f} MB")
    print("칸 이름:", list(raw.columns))
    cand = [c for c in raw.columns if re.search(r"효능|효과|적응|EE|용도", str(c), re.I)]
    print("\n효능·효과 후보 칸:", cand)
    if not cand:
        print("→ 효능·효과 칸이 없습니다. 원본(Apps Script 수집)에 칸을 더해야 합니다.")
        return 0
    for c in cand:
        s = raw[c].fillna("").astype(str).str.strip()
        ne = s[s.str.len() > 0]
        print(f"  [{c}] 채워진 행 {len(ne):,}/{len(s):,} ({len(ne)/max(len(s),1)*100:.0f}%) · 평균 {ne.str.len().mean():.0f}자 · "
              f"최대 {ne.str.len().max() if len(ne) else 0}자 · 칸 메모리 {s.memory_usage(deep=True)/1e6:.0f} MB")
        for ex in ne.sample(min(3, len(ne)), random_state=1).tolist():
            print("     예:", ex[:160].replace("\n", " "))
    ec = max(cand, key=lambda c: raw[c].fillna("").astype(str).str.len().sum())
    print(f"\n→ 쓸 칸: {ec}")

    ap = raw.copy()
    ap["_eff"] = ap[ec].fillna("").astype(str)
    ap["_eff"] = (ap["_eff"].str.replace(r"<[^>]+>", " ", regex=True)       # XML·HTML 꼬리표 제거
                  .str.replace(r"&[a-z]+;", " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip())
    ap["_key"] = ss.ing_key_series(ap.get("주성분(영문)", pd.Series("", index=ap.index)).fillna(""))
    ap["_core"] = ss.ing_core_series(ap.get("주성분", pd.Series("", index=ap.index)).fillna(""))
    single = ss.is_single_series(ap.get("주성분", pd.Series("", index=ap.index)), ap.get("주성분(영문)", pd.Series("", index=ap.index)))

    # 성분별 작은 색인: 성분키 → 효능 글 합본 (중복 제거)
    idx = (ap[ap["_key"].str.len() > 0].groupby("_key")["_eff"]
           .agg(lambda x: " | ".join(dict.fromkeys(v for v in x if v))[:4000]))
    print(f"성분별 효능 색인: {len(idx):,}개 성분 · 메모리 {idx.memory_usage(deep=True)/1e6:.1f} MB "
          f"(허가 원본 칸 통째로면 {ap['_eff'].memory_usage(deep=True)/1e6:.0f} MB)")

    # 매출(IQVIA·UBIST) 성분 묶음과 몇 개나 이어지는지 — 개수만
    try:
        print("\n매출 자료 받기:", bootstrap._do_build(include_sales=True, only={"iqvia", "ubist"}, force=True))
        print("허가·특허 자료 받기:", bootstrap._do_build(only={"approval", "patent"}, force=True))
        ss._ensure = lambda *a, **k: None
        c2k = ss.build_kor_core_key_map([(ss.load_approval().get("주성분"), ss.load_approval().get("주성분(영문)"))])
        sales = {}
        for src, path, col in (("IQVIA", bootstrap.SAVED / "iqvia.pkl", "성분명"), ("UBIST", bootstrap.SAVED / "ubist.pkl", "성분")):
            d = pd.read_pickle(path)
            col = col if col in d.columns else next((c for c in d.columns if "성분" in str(c)), None)
            names = pd.Series(d[col].dropna().astype(str).unique())
            k = ss.ing_key_series(names)
            k = k.where(k.str.len() > 0, ss.ing_core_series(names).map(c2k).fillna(""))
            sales[src] = set(k[k.str.len() > 0])
            print(f"  {src}: 성분 묶음 {len(names):,}개 중 영문 성분키로 이어지는 것 {int((k.str.len()>0).sum()):,}개")
    except Exception as e:
        print("매출 연결 시험 건너뜀:", type(e).__name__, str(e)[:120])
        sales = {}

    print("\n── 질병명 검색 ──")
    for term in TERMS:
        hit = ap["_eff"].str.contains(term, regex=False)
        keys = set(ap.loc[hit & (ap["_key"].str.len() > 0), "_key"])
        cores = ap.loc[hit & single, "_core"].value_counts()
        cores = [c for c in cores.index if len(c) >= 2][:12]
        extra = " · ".join(f"{s} 성분묶음 {len(keys & v)}개" for s, v in sales.items())
        print(f"[{term}] 품목 {int(hit.sum()):,}개 · 성분 {len(keys)}개 {('· ' + extra) if extra else ''}")
        print(f"    단일제 성분 예: {', '.join(cores)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
