"""AI(Gemini)가 사이트 자료를 직접 찾아보게 하는 '검색 도구' 모음 (C 방식 시험용).

Gemini 는 사이트 표를 직접 보지 못한다. 아래 함수 목록(TOOLS)을 알려주면 Gemini 가
'이 도구를 이 값으로 불러 달라'고 요청하고, 실제 찾기는 사이트가 해서 결과만 돌려준다.

원칙
  · 사이트 화면이 쓰는 표를 그대로 같이 쓴다(scout_sources 의 공유 표 · 한 벌만). 새로 표를 올리지 않는다.
  · 돌려주는 것은 작게(몇 줄 · 몇 백 자). 큰 표를 통째로 넘기지 않는다.
  · 매출은 요약 숫자만(합계·CAGR·제조사 수). 품목별 원자료는 넘기지 않는다.
  · 결과마다 출처와 기준을 붙인다.
"""
import re

import pandas as pd

import scout_sources as ss

MAX_ROWS = 15


def _core(s):
    return re.sub(r"\[[^\]]*\]|\([^)]*\)|\s+", "", str(s))


def _keys(q):
    """성분·제품 이름 → 성분키 집합 (사이트 종합분석과 같은 방법)."""
    q = str(q or "").strip()
    if not q:
        return set()
    keys = set(ss.resolve_keys(q))
    if not keys:
        ap = ss.load_approval()
        hit = ap[ap["품목명"].astype(str).str.replace(r"\s+", "", regex=True).str.contains(
            re.sub(r"\s+", "", q), na=False, regex=False)]
        keys = {k for k in hit["_key"].astype(str) if k}
    return keys


# ── 1. 성분·제품 알아보기 ─────────────────────────────────────────────
def find_ingredient(name):
    ap = ss.load_approval()
    keys = _keys(name)
    if not keys:
        return {"결과": "찾지 못함", "출처": "식약처 허가"}
    sub = ap[ap["_key"].isin(keys)]
    one = ss.is_single_series(sub.get("주성분", pd.Series("", index=sub.index)),
                              sub.get("주성분(영문)", pd.Series("", index=sub.index)))
    out = []
    for k, g in sub.groupby("_key"):
        s = one.loc[g.index]
        out.append({"성분키": k, "성분": _core(g["주성분"].mode().iat[0]) if len(g) else "",
                    "단일제 품목": int(s.sum()), "복합제 품목": int((~s).sum())})
    out.sort(key=lambda x: -(x["단일제 품목"] + x["복합제 품목"]))
    return {"결과": out[:MAX_ROWS], "출처": "식약처 허가"}


# ── 2. 허가 정보 ─────────────────────────────────────────────────────
def approval_info(ingredient):
    ap = ss.load_approval()
    keys = _keys(ingredient)
    sub = ap[ap["_key"].isin(keys)]
    if sub.empty:
        return {"결과": "허가 품목 없음", "출처": "식약처 허가"}
    one = ss.is_single_series(sub.get("주성분", pd.Series("", index=sub.index)),
                              sub.get("주성분(영문)", pd.Series("", index=sub.index)))
    d = ss._ap_date(sub)
    new = sub[sub.get("신약구분", pd.Series("", index=sub.index)).astype(str).str.strip() == "신약"]
    first = sub.loc[d.idxmin()] if d.notna().any() else None
    return {"결과": {
        "품목 수": int(len(sub)), "단일제": int(one.sum()), "복합제": int((~one).sum()),
        "업체 수": int(sub["업체명"].nunique()),
        "신약 품목": [f"{r['품목명']} ({r['업체명']})" for _, r in new.head(5).iterrows()],
        "최초 허가": (f"{first['품목명']} ({first['업체명']}) {d.min().date()}" if first is not None else ""),
        "최근 허가": str(d.max().date()) if d.notna().any() else ""},
        "출처": "식약처 허가 (허가일은 재등록 때 바뀔 수 있음)"}


# ── 3. 특허 만료 ─────────────────────────────────────────────────────
def patent_expiry(ingredient):
    keys = _keys(ingredient)
    pk = ss.patent_by_key(single_only=True)
    sub = pk[pk["_key"].isin(keys)]
    if sub.empty:
        return {"결과": "존속 중인 식약처 등재 특허 없음(또는 못 찾음)", "출처": "식약처 특허(등재 특허)"}
    out = []
    for _, r in sub.iterrows():
        row = {"성분키": r["_key"], "등록특허수": int(r.get("등록특허수", 0) or 0)}
        for lab, k in (("물질", "물질"), ("염·형태", "물질형태"), ("물질(기타)", "물질기타"), ("용도", "용도")):
            v = r.get(f"특허만료_{k}")
            row[f"{lab} 만료"] = str(pd.Timestamp(v).date()) if pd.notna(v) else "-"
        out.append(row)
    return {"결과": out[:MAX_ROWS], "출처": "식약처 특허(등재 특허, 단일제 기준)"}


# ── 4. 약가 ─────────────────────────────────────────────────────────
def price_summary(ingredient):
    pr = ss.load_price()
    keys = _keys(ingredient)
    sub = pr[pr["_key"].isin(keys)]
    if sub.empty:
        return {"결과": "약가 없음", "출처": "심평원 약가"}
    act = sub[sub["급여구분"] == "급여"]
    last = act.sort_values("적용일자").groupby("제품코드").tail(1)
    first_day = sub.groupby("제품코드")["적용일자"].min()
    yearly = (act.assign(y=act["적용일자"].dt.year).groupby("y")["제품코드"].nunique().tail(6))
    return {"결과": {
        "급여 품목 수(지금)": int(last["제품코드"].nunique()),
        "상한금액 최고·최저(지금)": [float(last["금액"].max()), float(last["금액"].min())] if len(last) else [],
        "가장 먼저 등재된 날": str(first_day.min().date()) if len(first_day) else "",
        "연도별 급여 품목 수": {int(k): int(v) for k, v in yearly.items()}},
        "출처": "심평원 약가 (2008-04 이후 이력)"}


# ── 5. 매출 요약 (요약 숫자만) ─────────────────────────────────────────
def _sales(src):
    import bootstrap
    path = bootstrap.SAVED / ("iqvia.pkl" if src == "IQVIA" else "ubist.pkl")
    if not path.exists():
        return None, None, None
    df = ss.load_sales_pickle(path)
    if src == "IQVIA":
        yr = sorted([c for c in df.columns if re.match(r"^\d{4}년$", str(c))])
        return df, yr, {"ing": "성분명", "maker": "회사명"}
    yr = sorted([c for c in df.columns if re.match(r"^처방조제액\(원\)_\d{4}년$", str(c))])
    return df, yr, {"ing": "성분", "maker": "제조사명"}


def _cagr(vals, yrs):
    pairs = [(int(re.search(r"(\d{4})", y).group(1)), v) for y, v in zip(yrs, vals) if v and v > 0]
    big = [(y, v) for y, v in pairs if v >= 1e8]
    if not big or len(pairs) < 2:
        return None
    (y0, v0), (y1, v1) = big[0], pairs[-1]
    return None if y1 <= y0 else round(((v1 / v0) ** (1 / (y1 - y0)) - 1) * 100, 1)


def sales_summary(ingredient, source="IQVIA"):
    df, yr, cfg = _sales(source)
    if df is None or not yr:
        return {"결과": "매출 자료 없음", "출처": source}
    keys = _keys(ingredient)
    k = df[cfg["ing"]].astype(str).map(ss.ing_key)
    sub = df[k.isin(keys)]
    if sub.empty:
        return {"결과": "매출 없음(이름이 안 맞을 수 있음)", "출처": source}
    tot = sub[yr].sum()
    return {"결과": {
        "최신연도": re.search(r"(\d{4})", yr[-1]).group(1),
        "최신연도 합계(억)": round(float(tot.iloc[-1]) / 1e8, 1),
        "CAGR(%)": _cagr(tot.tolist(), yr),
        "제조사 수": int(sub[cfg["maker"]].nunique()), "제품 수": int(sub["제품명"].nunique())},
        "출처": f"{source} (요약 숫자만)"}


# ── 6. 질병명으로 성분 찾기 ───────────────────────────────────────────
def disease_search(disease, exclude=""):
    import efficacy as ee
    idx, _ = ee.index()
    r = ee.search(pd.Series(idx.index, index=idx.index), disease, exclude)
    h = r[r["걸림"]].copy()
    if h.empty:
        return {"결과": "효능·효과에 그 말이 든 성분 없음", "출처": "식약처 허가 효능·효과"}
    h["_b"] = (h["위치"] != "첫머리").astype(int)
    h = h.sort_values(["_b", "_ee_rank"])
    ap = ss.load_approval()
    nm = ap.drop_duplicates("_key").set_index("_key")["주성분"].map(_core)
    return {"결과": {"성분 수": int(len(h)), "첫머리": int((h["위치"] == "첫머리").sum()),
                   "목록": [{"성분키": k, "성분": nm.get(k, k), "위치": r_["위치"], "문구": r_["문구"][:120]}
                          for k, r_ in h.head(MAX_ROWS).iterrows()]},
            "출처": "식약처 허가 효능·효과 원문"}


# ── 7. 조건으로 후보 찾기 (제품 제안과 같은 조건, 간단판) ────────────────────
def candidate_filter(min_size_eok=50, min_cagr=0.0, patent_by_year=None, disease="", source="IQVIA"):
    df, yr, cfg = _sales(source)
    if df is None or not yr:
        return {"결과": "매출 자료 없음", "출처": source}
    k = df[cfg["ing"]].astype(str).map(ss.ing_key)
    g = df.assign(_key=k)[k.str.len() > 0].groupby("_key")
    agg = g[yr].sum()
    out = pd.DataFrame({"시장규모(억)": (agg[yr[-1]] / 1e8).round(1)})
    out["CAGR(%)"] = [_cagr(list(r), yr) for r in agg[yr].to_numpy()]
    out = out[(out["시장규모(억)"] >= float(min_size_eok)) & (out["CAGR(%)"].fillna(-1e9) >= float(min_cagr))]
    if patent_by_year:
        pk = ss.patent_by_key(single_only=True).set_index("_key")
        exp = pk.reindex(out.index)["특허만료_물질"]
        out = out[exp.isna() | (pd.to_datetime(exp).dt.year <= int(patent_by_year))]
        out["물질특허 만료"] = pd.to_datetime(exp.reindex(out.index)).dt.date.astype("string").fillna("-")
    if disease:
        import efficacy as ee
        r = ee.search(pd.Series(out.index, index=out.index), disease)
        out = out[r["걸림"]]
    out = out.sort_values("시장규모(억)", ascending=False)
    rows = [{"성분키": i, **{c: (None if pd.isna(v) else v) for c, v in r.items()}} for i, r in out.head(MAX_ROWS).iterrows()]
    return {"결과": {"후보 수": int(len(out)), "상위": rows}, "출처": f"{source} 매출 요약 · 식약처 특허·효능"}


# ── 8. DMF 원료사 ────────────────────────────────────────────────────
def dmf_info(ingredient):
    dmf = ss.load_dmf()
    if dmf is None or dmf.empty:
        return {"결과": "DMF 자료 없음", "출처": "식약처 DMF"}
    ic = ss._dmf_ing_col(dmf)
    ap = ss.load_approval()
    cores = {c for c in ap.loc[ap["_key"].isin(_keys(ingredient)), "_core"].astype(str) if len(c) >= 2}
    sub = dmf[ss.ing_core_series(dmf[ic]).isin(cores)] if ic and cores else dmf.iloc[0:0]
    if sub.empty:
        return {"결과": "등록된 DMF 없음(또는 못 찾음)", "출처": "식약처 DMF"}
    d = pd.to_datetime(sub.get("DMF_PERMIT_DATE", pd.Series("", index=sub.index)), errors="coerce")
    now = pd.Timestamp.now()
    return {"결과": {"DMF 건수": int(len(sub)), "신청인 수": int(sub["ENTP_NAME"].nunique()),
                   "최근 12개월 등록": int((d >= now - pd.Timedelta(days=365)).sum()),
                   "그 전 12개월 등록": int(((d < now - pd.Timedelta(days=365)) & (d >= now - pd.Timedelta(days=730))).sum()),
                   "신청인 예": list(sub["ENTP_NAME"].dropna().astype(str).unique()[:10])},
            "출처": "식약처 DMF"}


FUNCS = {f.__name__: f for f in (find_ingredient, approval_info, patent_expiry, price_summary, sales_summary,
                                  disease_search, candidate_filter, dmf_info)}

# Gemini 에 알려줄 도구 설명 (function declarations)
TOOLS = [
    {"name": "find_ingredient", "description": "성분·제품 이름으로 정확한 성분(성분키)과 단일제/복합제 품목 수를 찾는다.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "approval_info", "description": "성분의 국내 허가 정보: 품목 수, 업체 수, 신약 품목, 최초·최근 허가.",
     "parameters": {"type": "object", "properties": {"ingredient": {"type": "string"}}, "required": ["ingredient"]}},
    {"name": "patent_expiry", "description": "성분의 식약처 등재 특허 만료일(물질·염형태·물질기타·용도).",
     "parameters": {"type": "object", "properties": {"ingredient": {"type": "string"}}, "required": ["ingredient"]}},
    {"name": "price_summary", "description": "성분의 심평원 약가: 급여 품목 수, 상한금액 최고·최저, 첫 등재일, 연도별 품목 수.",
     "parameters": {"type": "object", "properties": {"ingredient": {"type": "string"}}, "required": ["ingredient"]}},
    {"name": "sales_summary", "description": "성분의 국내 매출 요약(최신연도 합계 억원, CAGR, 제조사 수). source=IQVIA 또는 UBIST.",
     "parameters": {"type": "object", "properties": {"ingredient": {"type": "string"}, "source": {"type": "string"}},
                    "required": ["ingredient"]}},
    {"name": "disease_search", "description": "질병명(쉼표로 여러 개)이 식약처 효능·효과 원문에 든 성분 목록과 걸린 문구.",
     "parameters": {"type": "object", "properties": {"disease": {"type": "string"}, "exclude": {"type": "string"}},
                    "required": ["disease"]}},
    {"name": "candidate_filter", "description": "조건에 맞는 개발 후보 성분: 최소 시장규모(억), 최소 CAGR(%), 이 연도까지 물질특허 만료, 질병명.",
     "parameters": {"type": "object", "properties": {"min_size_eok": {"type": "number"}, "min_cagr": {"type": "number"},
                                                    "patent_by_year": {"type": "integer"}, "disease": {"type": "string"},
                                                    "source": {"type": "string"}}}},
    {"name": "dmf_info", "description": "성분의 식약처 DMF(원료) 등록 건수, 신청인, 최근 12개월 증감.",
     "parameters": {"type": "object", "properties": {"ingredient": {"type": "string"}}, "required": ["ingredient"]}},
]


def call(name, args):
    """Gemini 가 요청한 도구를 실행. 실패해도 예외 대신 짧은 오류 문구를 돌려준다."""
    f = FUNCS.get(name)
    if not f:
        return {"오류": f"없는 도구: {name}"}
    try:
        return f(**(args or {}))
    except Exception as e:
        return {"오류": f"{type(e).__name__}: {str(e)[:150]}"}
