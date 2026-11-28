"""AI 분석(C 방식) 검색 도구 — v2 가벼운 판.

AI(Gemini/Claude)는 사이트 표를 직접 보지 못한다. 아래 도구 목록(TOOLS)을 알려 주면 AI 가
'이 도구를 이 값으로 불러 달라'고 요청하고, 실제 찾기는 사이트가 해서 작은 결과만 돌려준다.

원칙
  · 표를 통째로 올리지 않는다. DuckDB 로 파일에서 조건에 맞는 줄·칸만 꺼낸다.
  · 돌려주는 것은 작게(몇 줄 · 몇 백 자). 매출은 요약 숫자만(합계·CAGR·제조사 수).
  · 결과마다 출처와 기준을 붙인다.
"""
import re

import pandas as pd
import streamlit as st

import scout_sources as ss
from core import data
from core.names import nospace

MAX_ROWS = 15


def _core(s):
    return re.sub(r"\[[^\]]*\]|\([^)]*\)|\s+", "", str(s))


def _ap():
    """허가 표 (_k 성분키, _r 파일 안 줄 번호 — 지금 사이트 표와 같은 줄 순서를 지키려고)."""
    p = data.ensure("approval")
    if not p:
        raise RuntimeError("허가 자료가 아직 없습니다")
    return (f"(SELECT *, {data.key_expr('approval')} AS _k, file_row_number AS _r "
            f"FROM read_parquet('{p.as_posix()}', file_row_number=true))")


# ── 성분키 찾기 ─────────────────────────────────────────────────────
def _keys(q):
    """성분·제품 이름 → 성분키 집합 (주성분·영문 주성분, 없으면 품목명으로)."""
    q = str(q or "").strip()
    if not q:
        return set()
    A, k = _ap(), ss.ing_key(q)
    keys = {k} if (k and re.search(r"[A-Za-z]", q)) else set()
    qq = nospace(q).lower()
    r = data.q(f"""SELECT DISTINCT "주성분(영문)" AS e FROM {A}
                   WHERE contains({data.ns("주성분")}, ?) OR contains({data.ns("주성분(영문)")}, ?)""", [qq, qq])
    keys |= {x for x in ss.ing_key_series(r["e"].astype(str)) if x}
    if not keys:
        r = data.q(f"SELECT DISTINCT _k FROM {A} WHERE contains({data.ns('품목명')}, ?)", [qq])
        keys = {x for x in r["_k"].astype(str) if x and x != "None"}
    return set(list(keys)[:30])


def _ap_rows(keys, cols):
    A = _ap()
    sel = ", ".join(f'"{c}"' for c in cols)
    return data.q(f"SELECT _k, {sel} FROM {A} WHERE list_contains(?, _k) ORDER BY _r", [sorted(keys)])


def _single(df):
    return ss.is_single_series(df.get("주성분", pd.Series("", index=df.index)),
                               df.get("주성분(영문)", pd.Series("", index=df.index)))


# ── 1. 성분·제품 알아보기 ─────────────────────────────────────────────
def find_ingredient(name):
    keys = _keys(name)
    if not keys:
        return {"결과": "찾지 못함", "출처": "식약처 허가"}
    sub = _ap_rows(keys, ["주성분", "주성분(영문)", "품목명"])
    one = _single(sub)
    qq = nospace(name).lower()
    byname = sub[sub["품목명"].astype(str).map(lambda v: qq in nospace(v).lower())]
    out = []
    for k, g in sub.groupby("_k"):
        s = one.loc[g.index]
        out.append({"성분키": k, "성분": _core(g["주성분"].mode().iat[0]) if len(g) else "",
                    "단일제 품목": int(s.sum()), "복합제 품목": int((~s).sum())})
    out.sort(key=lambda x: -(x["단일제 품목"] + x["복합제 품목"]))
    res = {"결과": out[:MAX_ROWS], "출처": "식약처 허가"}
    if len(byname):   # 제품명으로 찾았으면 그 제품의 실제 주성분(복합제면 전부)을 같이 알려 준다
        res["이름이 맞은 제품"] = [{"품목명": r["품목명"], "주성분": r["주성분"]}
                              for _, r in byname.drop_duplicates("주성분").head(8).iterrows()]
    return res


# ── 2. 허가 정보 ─────────────────────────────────────────────────────
def approval_info(ingredient):
    keys = _keys(ingredient)
    if not keys:
        return {"결과": "허가 품목 없음", "출처": "식약처 허가"}
    sub = _ap_rows(keys, ["품목명", "업체명", "허가일자", "신약구분", "주성분", "주성분(영문)"])
    if sub.empty:
        return {"결과": "허가 품목 없음", "출처": "식약처 허가"}
    one = _single(sub)
    d = ss._ap_date(sub)
    new = sub[sub["신약구분"].astype(str).str.strip() == "신약"]
    first = sub.loc[d.idxmin()] if d.notna().any() else None
    return {"결과": {
        "품목 수": int(len(sub)), "단일제": int(one.sum()), "복합제": int((~one).sum()),
        "업체 수": int(sub["업체명"].nunique()),
        "신약 품목": [f"{r['품목명']} ({r['업체명']})" for _, r in new.head(5).iterrows()],
        "최초 허가": (f"{first['품목명']} ({first['업체명']}) {d.min().date()}" if first is not None else ""),
        "최근 허가": str(d.max().date()) if d.notna().any() else ""},
        "출처": "식약처 허가 (허가일은 재등록 때 바뀔 수 있음)"}


# ── 3. 특허 만료 (사이트 제품 제안과 같은 규칙 — 결과는 성분키별 작은 표) ───────────
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
    P = data.price()
    keys = _keys(ingredient)
    if P is None or not keys:
        return {"결과": "약가 없음", "출처": "심평원 약가"}
    sub = data.q(f'SELECT "제품코드", "금액", "적용일자", "급여구분" FROM {P} WHERE list_contains(?, _k)', [sorted(keys)])
    if sub.empty:
        return {"결과": "약가 없음", "출처": "심평원 약가"}
    sub["적용일자"] = pd.to_datetime(sub["적용일자"], errors="coerce")
    act = sub[sub["급여구분"] == "급여"]
    last = act.sort_values("적용일자").groupby("제품코드").tail(1)
    first_day = sub.groupby("제품코드")["적용일자"].min()
    yearly = act.assign(y=act["적용일자"].dt.year).groupby("y")["제품코드"].nunique().tail(6)
    return {"결과": {
        "급여 품목 수(지금)": int(last["제품코드"].nunique()),
        "상한금액 최고·최저(지금)": [float(last["금액"].max()), float(last["금액"].min())] if len(last) else [],
        "가장 먼저 등재된 날": str(first_day.min().date()) if len(first_day) else "",
        "연도별 급여 품목 수": {int(k): int(v) for k, v in yearly.items()}},
        "출처": "심평원 약가 (2008-04 이후 이력)"}


# ── 5. 매출 요약 (요약 숫자만) ─────────────────────────────────────────
@st.cache_resource(show_spinner=False, max_entries=4)
def _name_keys(src, _mt):
    """매출 자료의 성분 이름 → 성분키 (영문이면 그대로, 한글이면 허가 자료의 한글→영문 대응)."""
    S, cfg, yr = data.sales(src)
    names = data.q(f'SELECT DISTINCT CAST("{cfg["ing"]}" AS VARCHAR) AS n FROM {S} WHERE "{cfg["ing"]}" IS NOT NULL')["n"]
    k = ss.ing_key_series(names)
    blank = k.str.len() == 0
    if blank.any():
        A = _ap()
        ap = data.q(f'SELECT "주성분", "주성분(영문)" FROM {A}')
        one = _single(ap)
        c2k = dict(zip(ss.ing_core_series(ap.loc[one, "주성분"]), ss.ing_key_series(ap.loc[one, "주성분(영문)"])))
        k.loc[blank] = ss.ing_core_series(names[blank]).map(c2k).fillna("")
    return dict(zip(names, k))


def _sales_cfg(src):
    src = "UBIST" if str(src or "").upper() == "UBIST" else "IQVIA"
    S, cfg, yr = data.sales(src)
    return src, S, cfg, yr


def _cagr(vals, yrs):
    pairs = [(int(re.search(r"(\d{4})", y).group(1)), v) for y, v in zip(yrs, vals) if v and v > 0]
    big = [(y, v) for y, v in pairs if v >= 1e8]
    if not big or len(pairs) < 2:
        return None
    (y0, v0), (y1, v1) = big[0], pairs[-1]
    return None if y1 <= y0 else round(((v1 / v0) ** (1 / (y1 - y0)) - 1) * 100, 1)


def sales_summary(ingredient, source="IQVIA"):
    src, S, cfg, yr = _sales_cfg(source)
    if S is None or not yr:
        return {"결과": "매출 자료 없음", "출처": src}
    keys = _keys(ingredient)
    nk = _name_keys(src, data._mtime(data.FILES[src.lower()]))
    names = sorted(n for n, k in nk.items() if k in keys)
    if not names:
        return {"결과": "매출 없음(이름이 안 맞을 수 있음)", "출처": src}
    sums = ", ".join(f'sum(TRY_CAST("{y}" AS DOUBLE)) AS "{y}"' for y in yr)
    r = data.q(f'''SELECT {sums}, count(DISTINCT "{cfg["maker"]}") AS mk, count(DISTINCT "{cfg["prod"]}") AS pd_
                   FROM {S} WHERE list_contains(?, CAST("{cfg["ing"]}" AS VARCHAR))''', [names]).iloc[0]
    tot = [float(r[y] or 0) for y in yr]
    return {"결과": {
        "최신연도": re.search(r"(\d{4})", yr[-1]).group(1),
        "최신연도 합계(억)": round(tot[-1] / 1e8, 1),
        "연도별 합계(억)": {re.search(r"(\d{4})", y).group(1): round(v / 1e8, 1) for y, v in zip(yr[-5:], tot[-5:])},
        "CAGR(%)": _cagr(tot, yr),
        "제조사 수": int(r["mk"]), "제품 수": int(r["pd_"])},
        "출처": f"{src} (요약 숫자만, 단일제·복합제 합계)"}


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
    top = h.head(MAX_ROWS)
    nm = data.q(f'SELECT _k, first("주성분" ORDER BY _r) AS n FROM {_ap()} WHERE list_contains(?, _k) GROUP BY 1',
                [list(top.index.astype(str))]).set_index("_k")["n"].map(_core)
    return {"결과": {"성분 수": int(len(h)), "첫머리": int((h["위치"] == "첫머리").sum()),
                   "목록": [{"성분키": k, "성분": nm.get(k, k), "위치": r_["위치"], "문구": r_["문구"][:120]}
                          for k, r_ in top.iterrows()]},
            "출처": "식약처 허가 효능·효과 원문"}


# ── 7. 조건으로 후보 찾기 (제품 제안과 같은 조건, 간단판) ────────────────────
def candidate_filter(min_size_eok=50, min_cagr=0.0, patent_by_year=None, disease="", source="IQVIA"):
    src, S, cfg, yr = _sales_cfg(source)
    if S is None or not yr:
        return {"결과": "매출 자료 없음", "출처": src}
    sums = ", ".join(f'sum(TRY_CAST("{y}" AS DOUBLE)) AS "{y}"' for y in yr)
    m = data.q(f'SELECT CAST("{cfg["ing"]}" AS VARCHAR) AS n, {sums} FROM {S} WHERE "{cfg["ing"]}" IS NOT NULL GROUP BY 1')
    nk = _name_keys(src, data._mtime(data.FILES[src.lower()]))
    m["_key"] = m["n"].map(nk).fillna("")
    agg = m[m["_key"].str.len() > 0].groupby("_key")[yr].sum()
    out = pd.DataFrame({"시장규모(억)": (agg[yr[-1]] / 1e8).round(1)})
    out["CAGR(%)"] = [_cagr(list(r), yr) for r in agg[yr].to_numpy()]
    out = out[(out["시장규모(억)"] >= float(min_size_eok or 0)) & (out["CAGR(%)"].fillna(-1e9) >= float(min_cagr or 0))]
    if patent_by_year:
        pk = ss.patent_by_key(single_only=True).drop_duplicates("_key").set_index("_key")
        exp = pd.to_datetime(pk.reindex(out.index)["특허만료_물질"], errors="coerce")
        out = out[exp.isna() | (exp.dt.year <= int(patent_by_year))]
        out["물질특허 만료"] = exp.reindex(out.index).dt.date.astype("string").fillna("-")
    if disease:
        import efficacy as ee
        r = ee.search(pd.Series(out.index, index=out.index), disease)
        out = out[r["걸림"]]
    out = out.sort_values("시장규모(억)", ascending=False)
    rows = [{"성분키": i, **{c: (None if pd.isna(v) else v) for c, v in r.items()}} for i, r in out.head(MAX_ROWS).iterrows()]
    return {"결과": {"후보 수": int(len(out)), "상위": rows}, "출처": f"{src} 매출 요약 · 식약처 특허·효능"}


# ── 8. DMF 원료사 ────────────────────────────────────────────────────
def dmf_info(ingredient):
    dmf = ss.load_dmf()
    if dmf is None or dmf.empty:
        return {"결과": "DMF 자료 없음", "출처": "식약처 DMF"}
    ic = ss._dmf_ing_col(dmf)
    keys = _keys(ingredient)
    ap = _ap_rows(keys, ["주성분"]) if keys else pd.DataFrame({"주성분": []})
    cores = {c for c in ss.ing_core_series(ap["주성분"].astype(str)) if len(c) >= 2}
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


# ── 9. 식약처 임상 ───────────────────────────────────────────────────
def clinical_summary(ingredient):
    C = data.table("clinical")
    keys = _keys(ingredient)
    if C is None or not keys:
        return {"결과": "임상 자료 없음", "출처": "식약처 임상시험 승인"}
    ap = _ap_rows(keys, ["주성분"])
    cores = sorted({c for c in ss.ing_core_series(ap["주성분"].astype(str)) if len(c) >= 2})
    if not cores:
        return {"결과": "성분 이름을 못 찾음", "출처": "식약처 임상시험 승인"}
    have = set(data.cols("clinical"))
    pick = [c for c in ("성분명", "제품명", "CLINIC_STEP_NM", "CLST_APRV_DT", "TRGT_DISS_NM", "원개발사") if c in have]
    ors = " OR ".join([f"contains({data.ns('성분명')}, ?)"] * len(cores))
    cl = data.q(f'SELECT {", ".join(chr(34) + c + chr(34) for c in pick)} FROM {C} WHERE {ors}',
                [nospace(c).lower() for c in cores])
    if cl.empty:
        return {"결과": "승인된 임상 없음", "출처": "식약처 임상시험 승인"}
    raw = cl["CLST_APRV_DT"].astype(str)
    d = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
    d = d.fillna(pd.to_datetime(raw, errors="coerce", format="mixed"))
    cl = cl.assign(_d=d).sort_values("_d", ascending=False)
    return {"결과": {"승인 건수": int(len(cl)),
                   "단계별": cl["CLINIC_STEP_NM"].astype(str).value_counts().head(8).to_dict(),
                   "최근 24개월": int((d >= pd.Timestamp.now() - pd.DateOffset(months=24)).sum()),
                   "최근 승인": [{"날짜": str(r["_d"].date()) if pd.notna(r["_d"]) else "", "단계": r["CLINIC_STEP_NM"],
                              "제품": r.get("제품명", ""), "대상질환": str(r.get("TRGT_DISS_NM", ""))[:60]}
                             for _, r in cl.head(8).iterrows()]},
            "출처": "식약처 임상시험 승인"}


FUNCS = {f.__name__: f for f in (find_ingredient, approval_info, patent_expiry, price_summary, sales_summary,
                                  disease_search, candidate_filter, dmf_info, clinical_summary)}

_S = {"type": "string"}
TOOLS = [
    {"name": "find_ingredient", "description": "성분·제품 이름으로 정확한 성분(성분키)과 단일제/복합제 품목 수를 찾는다. 제품명이면 그 제품의 실제 주성분도 알려 준다. 처음에 이름을 확인할 때 쓴다.",
     "parameters": {"type": "object", "properties": {"name": _S}, "required": ["name"]}},
    {"name": "approval_info", "description": "성분의 국내 허가 정보: 품목 수, 업체 수, 신약 품목, 최초·최근 허가.",
     "parameters": {"type": "object", "properties": {"ingredient": _S}, "required": ["ingredient"]}},
    {"name": "patent_expiry", "description": "성분의 식약처 등재 특허 만료일(물질·염형태·물질기타·용도), 단일제 기준.",
     "parameters": {"type": "object", "properties": {"ingredient": _S}, "required": ["ingredient"]}},
    {"name": "price_summary", "description": "성분의 심평원 약가: 급여 품목 수, 상한금액 최고·최저, 첫 등재일, 연도별 급여 품목 수.",
     "parameters": {"type": "object", "properties": {"ingredient": _S}, "required": ["ingredient"]}},
    {"name": "sales_summary", "description": "성분의 국내 매출 요약(최신연도 합계 억원, 최근 5년 연도별 합계, CAGR, 제조사 수). source 는 IQVIA 또는 UBIST.",
     "parameters": {"type": "object", "properties": {"ingredient": _S, "source": _S}, "required": ["ingredient"]}},
    {"name": "disease_search", "description": "질병명(쉼표로 여러 개)이 식약처 허가 효능·효과 원문에 든 성분 목록과 걸린 문구. exclude 에 빼는 말.",
     "parameters": {"type": "object", "properties": {"disease": _S, "exclude": _S}, "required": ["disease"]}},
    {"name": "candidate_filter", "description": "조건에 맞는 개발 후보 성분: 최소 시장규모(억), 최소 CAGR(%), 이 연도까지 물질특허 만료(또는 없음), 질병명.",
     "parameters": {"type": "object", "properties": {"min_size_eok": {"type": "number"}, "min_cagr": {"type": "number"},
                                                    "patent_by_year": {"type": "integer"}, "disease": _S, "source": _S}}},
    {"name": "dmf_info", "description": "성분의 식약처 DMF(원료) 등록 건수, 신청인, 최근 12개월과 그 전 12개월 등록 수.",
     "parameters": {"type": "object", "properties": {"ingredient": _S}, "required": ["ingredient"]}},
    {"name": "clinical_summary", "description": "성분의 식약처 임상시험 승인: 건수, 단계별(생동·1상·3상 등), 최근 24개월, 최근 승인 목록.",
     "parameters": {"type": "object", "properties": {"ingredient": _S}, "required": ["ingredient"]}},
]


def call(name, args):
    """AI 가 요청한 도구를 실행. 실패해도 예외 대신 짧은 오류 문구를 돌려준다."""
    f = FUNCS.get(name)
    if not f:
        return {"오류": f"없는 도구: {name}"}
    try:
        return f(**(args or {}))
    except Exception as e:
        return {"오류": f"{type(e).__name__}: {str(e)[:150]}"}
