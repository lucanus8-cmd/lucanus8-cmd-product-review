"""제품 제안 후보 찾기 (읽기만) — 사이트 '원료사 동향'과 같은 계산을 전체 시트로 돌려 본다.

① 원료 진입기 후보: 식약처 DMF 공고(최근 12개월 vs 그 전 12개월, 오리지널사 본인 등록 뺌)에
   성분명 생동·1상(24개월), 코드명 의심, 식약처 등재 물질특허 만료, 허가 품목·업체 수를 붙인다.
② FDA 표 '국내 성분 없음' 점검: 의심 성분이 국내 허가 시트에 어떻게 적혀 있는지 본다.

공개 자료(식약처·FDA)만 쓴다. 매출 자료는 읽지도 적지도 않는다. 키는 GitHub 비밀값에서만 읽는다.
사이트 코드·자료는 고치지 않는다 (scout_data/ 는 이 로봇 안에서만 잠깐 만든다).
"""
import os
import sys
import warnings
import logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
sys.path.insert(0, ".")
sys.path.insert(0, ".github/scripts")

import pandas as pd            # noqa: E402
import gsheet                  # noqa: E402

SHEETS = {"approval": "1x0lPMvsH0G-9cOdwi1kZy5yTAhAaz9N5BluK36iS8-Q",
          "patent": "1tj75vy8KcJSmz6-rAqmPpLThNt7ZymuQfi12Vbzz1CE",
          "clinical": "1iFPo8V-JLFQEkFgkfAH2l8-FtUEia0on6Q8OjtPr6sE"}
TODAY = pd.Timestamp.today().normalize()
PROBES = ["fluvoxamine", "citalopram", "miconazole", "betamethasone", "prednisone", "promethazine",
          "fluphenazine", "ranolazine", "lisdexamfetamine", "guanfacine", "droxidopa", "bumetanide"]
PROBES_KO = ["플루복사민", "시탈로프람", "미코나졸", "베타메타손", "프레드니손", "프로메타진",
             "플루페나진", "라놀라진", "리스덱스암페타민", "구안파신", "드록시도파", "부메타니드"]


def read_big(sid):
    """줄이 가장 많은 탭을 읽는다 ('_' 로 시작하는 도우미 탭은 뺌)."""
    api = gsheet.svc()
    meta = gsheet._call(api.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties(title,gridProperties)"))
    cand = [s for s in meta["sheets"] if not s["properties"]["title"].startswith("_")] or meta["sheets"]
    tabs = [(s["properties"]["title"], s["properties"].get("gridProperties", {}).get("rowCount", 0)) for s in cand]
    tab = max(tabs, key=lambda x: x[1])[0]
    vals = gsheet._call(api.spreadsheets().values().get(spreadsheetId=sid, range=f"'{tab}'",
                                                       valueRenderOption="FORMATTED_VALUE")).get("values", [])
    hdr = [str(c).split("\n")[0].strip() for c in vals[0]]
    df = pd.DataFrame([(r + [""] * len(hdr))[:len(hdr)] for r in vals[1:]], columns=hdr).fillna("")
    print(f"   {tab}: {len(df):,}행 (탭 {[t for t, _ in tabs]})")
    return df


def build_cache():
    os.makedirs("scout_data", exist_ok=True)
    out = {}
    for name, sid in SHEETS.items():
        # 특허는 줄이 가장 많은 탭('특허정보'), 허가·임상은 사이트 bootstrap 과 같이 칸이 가장 많은 탭
        df = read_big(sid) if name == "patent" else gsheet.read_sheet(sid)
        print(f"   {name}: {len(df):,}행")
        df.astype(str).to_parquet(f"scout_data/{name}.parquet", index=False)
        out[name] = df
    cl = out["clinical"]
    if "CLST_APRV_DT" in cl.columns:
        d = pd.to_datetime(cl["CLST_APRV_DT"].astype(str).str.replace(r"\D", "", regex=True).str[:8],
                           format="%Y%m%d", errors="coerce")
        print(f"   임상 승인일 범위: {d.min():%Y-%m-%d} ~ {d.max():%Y-%m-%d}")
    return out


def candidates():
    import scout_sources as ss
    import dmf_trend as dt
    ap = ss.load_approval()
    x = dt.prepare_dmf(ss.load_dmf(), ap)
    print(f"DMF {len(x):,}건 · 받은 시각 {(ss.dmf_meta() or {}).get('fetched_at')}")
    t_all, n_orig = dt.trend(x, TODAY, 12, True)
    print(f"최근 12개월 원료사 새 등록 {int(t_all['원료사_최근'].sum()):,}건 (그 전 {int(t_all['원료사_그전'].sum()):,}건)"
          f" · 오리지널사 본인 등록 {n_orig}건 뺌")
    t = t_all[(t_all["원료사_최근"] >= 3) & (t_all["원료사_증감"] > 0)].sort_values(
        ["원료사_증감", "새제조소", "원료사_최근"], ascending=False).copy()
    cx = dt.prepare_clinical(ss.load_clinical())
    named, sus, kw, sus_ex = [], [], [], []
    for r in t.itertuples():
        terms, _ = dt.ingredient_terms(cx, r.성분)
        since = (r.첫등록_최근 if pd.notna(r.첫등록_최근) else TODAY - pd.DateOffset(months=12)) - pd.DateOffset(months=6)
        named.append(dt.named_recent(cx, r.성분, TODAY))
        s = dt.suspects(cx, terms, since) if terms else pd.DataFrame()
        sus.append(len(s))
        sus_ex.append("; ".join(f"{a} {b}({c})" for a, b, c in zip(s.get("코드", []), s.get("개발사", []), s.get("의심 정도", [])))[:120]
                      if len(s) else "")
        kw.append(", ".join(terms))
    t["성분생동1상"], t["코드명의심"], t["질환"], t["코드명 예"] = named, sus, kw, sus_ex
    c2k = ss._kor_core_key_map()
    items = [(k, v) for k, v in c2k.items() if len(k) >= 3]

    def key(c):
        k = c2k.get(c) or c2k.get(ss.ing_core(c))
        if not k and len(c) >= 3:
            k = next((v for kk, v in items if kk.startswith(c) or c.startswith(kk)), "")
        return k or ""
    t["_key"] = [key(c) for c in t["성분"]]
    base = ss.patent_by_key(single_only=True)
    orig = ss.patent_by_key(single_only=True, original_only=True)
    _, _, basis = ss.original_items()
    both = pd.concat([orig[orig["_key"].isin(basis)], base[~base["_key"].isin(basis)]], ignore_index=True)
    both = both[both["_key"].astype(str).str.len() > 0].drop_duplicates("_key").set_index("_key")
    for c in ("특허만료_물질", "특허만료_물질형태", "특허만료_물질기타", "특허만료_용도"):
        t[c] = t["_key"].map(both[c]) if c in both.columns else pd.NA
    t["물질만료"] = pd.to_datetime(t["특허만료_물질"], errors="coerce").fillna(
        pd.to_datetime(t["특허만료_물질기타"], errors="coerce"))
    t = dt.judge(t, TODAY)
    comp = ss.competition_by_key().set_index("_key")
    for c in ("허가품목수", "허가업체수"):
        t[c] = t["_key"].map(comp[c]) if c in comp.columns else pd.NA
    f = lambda v: "" if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m")  # noqa: E731
    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_colwidth", 60)
    view = pd.DataFrame({
        "성분": t["성분"], "영문키": t["_key"],
        "원료사 12개월(그전)": [f"{a}({b})" for a, b in zip(t["원료사_최근"], t["원료사_그전"])],
        "새제조소": t["새제조소"], "추가신청": t["추가_최근"],
        "생동1상24m": t["성분생동1상"], "코드명": t["코드명의심"],
        "물질특허": t["물질만료"].map(f), "염형태특허": pd.to_datetime(t["특허만료_물질형태"], errors="coerce").map(f),
        "용도특허": pd.to_datetime(t.get("특허만료_용도"), errors="coerce").map(f),
        "허가품목": t["허가품목수"], "허가업체": t["허가업체수"], "단계": t["단계"], "질환": t["질환"]})
    print(f"\n=== ① 원료 진입기 후보 (최근 12개월 원료사 3건 이상 & 늘어남) · {len(view)}성분 ===")
    print(view.to_string(index=False))
    print("\n--- 코드명 의심 예 ---")
    for a, b in zip(t["성분"], t["코드명 예"]):
        if b:
            print(f"   {a}: {b}")


def probe_kr(ap):
    print("\n=== ② FDA 표 '국내 성분 없음' 점검: 국내 허가 시트 표기 ===")
    eng, kor = ap.get("주성분(영문)", pd.Series(dtype=str)).astype(str), ap.get("주성분", pd.Series(dtype=str)).astype(str)
    print(f"   상태 값: {ap['상태'].value_counts().head(6).to_dict() if '상태' in ap else '-'}")
    print(f"   전문/일반 값: {ap['전문/일반'].value_counts().head(6).to_dict() if '전문/일반' in ap else '-'}")
    print(f"   주성분(영문) 빈칸 비율: {(eng.str.strip() == '').mean():.1%}")
    for e, k in zip(PROBES, PROBES_KO):
        h = ap[eng.str.lower().str.contains(e, na=False) | kor.str.contains(k, na=False)]
        ok = h[h["상태"].eq("정상")] if "상태" in h else h
        print(f"\n   [{e} / {k}] {len(h)}품목 · 정상 {len(ok)}")
        for d in ok.head(4).to_dict("records"):
            print(f"      {str(d.get('품목명', ''))[:30]} | {d.get('전문/일반', '')} | 영문 '{str(d.get('주성분(영문)', ''))[:50]}' | 한글 '{str(d.get('주성분', ''))[:30]}'")


def probe_beta(ap):
    """베타메타손 디프로피오네이트 단일제가 국내 정상 품목에 있는지 (FDA 표는 '국내 성분 없음')."""
    eng = ap["주성분(영문)"].astype(str)
    one = ~eng.str.contains(r"/|\+|;", regex=True)
    h = ap[one & eng.str.lower().str.contains("betamethasone dipropionate") & ap["상태"].eq("정상")]
    print(f"\n   [betamethasone dipropionate 단일] 정상 {len(h)}품목")
    for d in h.head(6).to_dict("records"):
        print(f"      {str(d.get('품목명', ''))[:34]} | {d.get('전문/일반', '')} | 영문 '{str(d.get('주성분(영문)', ''))[:40]}'")


def main():
    if not gsheet.available():
        print("구글 키 없음")
        return 1
    print("시트 읽기 (전체)")
    data = build_cache()
    probe_kr(data["approval"])
    probe_beta(data["approval"])
    candidates()
    return 0


if __name__ == "__main__":
    sys.exit(main())
