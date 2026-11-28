"""약가 조회 · 제네릭 등재 시점 (v2 가벼운 판).

계산 규칙은 예전에 72개 성분으로 시험한 generic_entry.py 를 그대로 쓴다(ge.analyze).
다른 점은 '약가 이력 전체'를 메모리에 올리지 않는 것 — 고른 성분에 필요한 제품의 약가 이력만 파일에서 꺼낸다.
  ① 허가 자료로 그 성분 품목(과 이 성분이 든 복합제)의 정리 이름을 구하고
  ② 약가에서 그 이름의 제품 → 그 제품들의 약가코드(주성분코드·제제코드)가 같은 제품까지 찾은 뒤
  ③ 찾은 제품코드의 이력 전체를 꺼낸다(원래 표에서 고른 것과 같은 줄, 같은 순서).
ge.analyze 가 쓰는 줄은 모두 ③ 안에 있으므로 결과가 같다. '자료 묶음 날짜'는 전체에서 SQL 로 한 번 센다.
"""
import pandas as pd
import streamlit as st

import generic_entry as ge
import scout_sources as ss
from core import data


def _mt():
    return data._mtime(data.FILES["price"]), data._mtime(data.FILES["approval"])


def _psrc():
    p = data.ensure("price")
    return f"read_parquet('{p.as_posix()}', file_row_number=true)" if p else None


@st.cache_resource(show_spinner="제네릭 등재 계산 준비 중…", max_entries=1)
def _base(_pmt, _amt):
    """허가 자료(필요한 칸만) 정리 · 성분 목록 · 자료 묶음 날짜 · 약가 제품명 정리표. 파일이 바뀔 때만 다시."""
    ap = ss.load_slim("approval", ["품목기준코드", "품목명", "업체명", "허가일자", "주성분", "신약구분", "상태"])
    a = ge.prep_approval(ap, ss.ing_core, ss._ap_date)
    opts = ge.ingredient_options(a)
    S = _psrc()
    b = data.q(f"""SELECT d, count(*) AS n FROM (
                     SELECT "제품명", min(TRY_CAST("적용일자" AS TIMESTAMP)) AS d FROM {S}
                     WHERE "제품명" IS NOT NULL GROUP BY 1) WHERE d IS NOT NULL GROUP BY d HAVING count(*) >= ?""",
               [ge.BULK_MIN])
    bulk = set(pd.to_datetime(b["d"]))
    names = data.q(f'SELECT DISTINCT CAST("제품명" AS VARCHAR) AS n FROM {S} WHERE "제품명" IS NOT NULL')["n"]
    by_nn = {}
    for n in names:
        by_nn.setdefault(ge.nn(n), []).append(n)
    return a, opts, bulk, by_nn


def options():
    try:
        return _base(*_mt())[1]
    except Exception:
        return []


def _rows(where, prm):
    S = _psrc()
    cols = '"제품코드", "제품명", "업체명", "적용일자", "급여구분", "금액", "주성분코드", "제제코드"'
    df = data.q(f"SELECT file_row_number AS __r, {cols} FROM {S} WHERE {where}", prm)
    return df.sort_values("__r", kind="stable").drop(columns="__r").reset_index(drop=True)


@st.cache_data(show_spinner=False, max_entries=12)
def run(term, pick, _mt_):
    """ge.analyze(전체 약가, …) 와 같은 결과를, 필요한 제품의 약가 이력만으로."""
    a, _, bulk, by_nn = _base(*_mt_)
    want = ge.want_of(term, ss.ing_core)
    if not want:
        return ge.analyze(pd.DataFrame(columns=["_nn"]), a, bulk, term, ss.ing_core, pick)
    hit = a[a["_cores"].map(lambda cs: ge._match(cs, want)) & a["_d"].notna()]
    nns = set(hit["_nn"])
    if len(want) == 1:      # 단일제면 이 성분이 든 복합제 이름도 (빼낸 복합제 수를 세려고)
        combo = a[a["_cores"].map(lambda cs: len(cs) > 1 and any(any(c == w or c.startswith(w) for c in cs)
                                                                   for w in want))]
        nns |= set(combo["_nn"])
    pnames = sorted({n for k in nns for n in by_nn.get(k, [])})
    if not pnames:
        empty = ge.prep_price(_rows("FALSE", []))
        return ge.analyze(empty, a, bulk, term, ss.ing_core, pick)
    r1 = ge.prep_price(_rows('list_contains(?, CAST("제품명" AS VARCHAR))', [pnames]))
    cds = sorted({c for c in r1["_cd"] if c})
    pcs = set(r1["_pc"])
    if cds:
        r2 = _rows("""list_contains(?, upper(trim(CAST("주성분코드" AS VARCHAR))))
                      OR list_contains(?, upper(trim(CAST("제제코드" AS VARCHAR))))""", [cds, cds])
        pcs |= set(r2["제품코드"].astype(str).str.strip())
    p = ge.prep_price(_rows('list_contains(?, trim(CAST("제품코드" AS VARCHAR)))', [sorted(pcs)]))
    r = ge.analyze(p, a, bulk, term, ss.ing_core, pick)
    for k in ("same", "salt"):       # 표시 형식만 맞춤: 전체 표로 계산하면 '지금상한가'가 소수형(312.0)이 된다
        if isinstance(r.get(k), pd.DataFrame) and "지금상한가" in r[k].columns:
            r[k]["지금상한가"] = r[k]["지금상한가"].astype("float64")
    return r
