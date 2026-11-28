"""특허 분석 (v2). 지금 사이트와 같은 조건·같은 표.
특허 표를 메모리에 통째로 올리지 않고, 조건에 맞는 줄만 DuckDB 로 파일에서 꺼낸다."""
import pandas as pd
import plotly.express as px
import streamlit as st

import scout_sources as ss
from core import data
from core.names import nospace
from core.ui import show_df

TODAY = pd.Timestamp.today().normalize()
st.subheader("⚖️ 특허 분석 — 조건별")
PT = data.table("patent")
if PT is None or not data.has("approval"):
    st.error(data.missing_note("patent" if PT is None else "approval"))
    st.stop()

pc = data.cols("patent")
PG = ss.PATENT_PAGE_COL
exp = ('TRY_CAST("_exp" AS TIMESTAMP)' if "_exp" in pc else
       "TRY_STRPTIME(regexp_replace(CAST(\"DOMESTIC_END_DATE\" AS VARCHAR), '[^0-9]', '', 'g'), '%Y%m%d')")


def _opts(col, where="true", prm=None):
    if col not in pc:
        return []
    r = data.q(f'SELECT DISTINCT trim(CAST("{col}" AS VARCHAR)) AS v FROM {PT} WHERE {where}', prm or [])["v"]
    return sorted({x for x in r if x})


where, prm = ["true"], []
if PG in pc:
    pg_opts = _opts(PG)
    pg_sel = st.multiselect("구분", pg_opts, default=[o for o in pg_opts if "제품" in o], key="p_page",
                            help="제품특허 = 원개발사가 식약처 의약품 특허목록에 등재한 특허. "
                                 "기타특허 = 같은 성분에 관한 다른 특허(다른 회사의 염·수화물·조성 특허 등).")
    if pg_sel:
        where.append(f'list_contains(?, trim(CAST("{PG}" AS VARCHAR)))'); prm.append(pg_sel)
f = st.columns(4)
w0, p0 = " AND ".join(where), list(prm)
t_sel = f[0].multiselect("특허유형", _opts("PATENT_GB_CODE", w0, p0), key="p_type")
s_opts = _opts("DOMESTIC_PATENT_STATUS", w0, p0)
s_sel = f[1].multiselect("상태", s_opts, default=[x for x in ["등록"] if x in s_opts], key="p_stat")
patee = f[2].text_input("특허권자 검색", key="p_ee")
kw = f[3].text_input("품목/성분 검색", key="p_kw")
if t_sel:
    where.append('list_contains(?, trim(CAST("PATENT_GB_CODE" AS VARCHAR)))'); prm.append(t_sel)
if s_sel:
    where.append('list_contains(?, trim(CAST("DOMESTIC_PATENT_STATUS" AS VARCHAR)))'); prm.append(s_sel)
if patee:
    where.append('contains(lower(CAST("PATENTEE" AS VARCHAR)), lower(?))'); prm.append(patee)
if kw:
    k = nospace(kw).lower()
    ors = [f"contains({data.ns(c)}, ?)" for c in ("품목명", "INGR_ENG_NAME", "INGR_NAME") if c in pc]
    if ors:
        where.append("(" + " OR ".join(ors) + ")"); prm += [k] * len(ors)
where.append(f"{exp} IS NOT NULL")
W = " AND ".join(where)

yr = data.q(f"SELECT min(year({exp})) AS lo, max(year({exp})) AS hi, count(*) AS n FROM {PT} WHERE {W}", prm).iloc[0]
if int(yr["n"]) == 0:
    st.warning("조건에 맞는 특허 없음")
    st.stop()
lo, hi = int(yr["lo"]), int(yr["hi"])
if lo >= hi:
    st.caption(f"만료 연도: {lo}년 (해당 연도 특허만 존재)")
    rng = (lo, hi)
else:
    rng = st.slider("만료 연도 범위", lo, hi, (lo, hi), key=f"p_rng_{lo}_{hi}")
    if (rng[0], rng[1]) != (lo, hi):
        st.caption(f"전체 {lo}~{hi}년 중 {rng[0]}~{rng[1]}년만 보는 중")
W2 = W + f" AND year({exp}) BETWEEN ? AND ?"
prm2 = prm + [int(rng[0]), int(rng[1])]

m = data.q(f"""SELECT count(*) AS n, count(DISTINCT "PATENTEE") AS ee,
                      count(*) FILTER (WHERE datediff('day', ?::TIMESTAMP, {exp}) / 365.25 >= 0
                                         AND datediff('day', ?::TIMESTAMP, {exp}) / 365.25 < 3) AS soon
               FROM {PT} WHERE {W2}""", [TODAY, TODAY] + prm2).iloc[0]
k3 = st.columns(3)
k3[0].metric("특허 건수", f"{int(m['n']):,}")
k3[1].metric("특허권자 수", f"{int(m['ee']):,}")
k3[2].metric("향후 3년 내 만료", f"{int(m['soon']):,}")
cnt = data.q(f'SELECT year({exp}) AS 만료연도, count(*) AS 건수 FROM {PT} WHERE {W2} GROUP BY 1 ORDER BY 1', prm2)
st.plotly_chart(px.bar(cnt, x="만료연도", y="건수", title="만료 연도별 특허 건수"), width="stretch")

cmap = {PG: "구분", "품목명": "품목명", "INGR_NAME": "성분", "PATENT_GB_CODE": "유형", "PATENTEE": "특허권자",
        "DOMESTIC_PATENT_STATUS": "상태", "DOMESTIC_END_DATE": "만료일", "DOMESTIC_INVN_NM": "발명의 명칭"}
sel = ", ".join(f'"{a}" AS "{b}"' for a, b in cmap.items() if a in pc)
rows = data.q(f"""SELECT {sel}, round(datediff('day', ?::TIMESTAMP, {exp}) / 365.25, 1) AS "만료D(년)"
                  FROM {PT} WHERE {W2}""", [TODAY] + prm2)
order = [v for a, v in cmap.items() if a in pc and v != "발명의 명칭"] + ["만료D(년)"] + (["발명의 명칭"] if "DOMESTIC_INVN_NM" in pc else [])
rows = rows[[c for c in order if c in rows.columns]]
show_df(rows.sort_values("만료일") if "만료일" in rows.columns else rows, height=360, file_name="특허분석.csv")
