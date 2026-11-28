import streamlit as st

from core import data

st.subheader("데이터 연결 상태 (v2)")
rows = []
for n, lab in [("approval", "허가"), ("patent", "특허"), ("clinical", "임상"), ("price", "약가"),
               ("nego", "공단협상"), ("iqvia", "매출 IQVIA"), ("ubist", "매출 UBIST")]:
    p = data.FILES[n]
    rows.append({"자료": lab, "파일": "있음" if p.exists() else "없음", "받은 시각": data.file_date(n),
                 "크기(MB)": round(p.stat().st_size / 1e6, 1) if p.exists() else None})
st.dataframe(rows, hide_index=True, use_container_width=True)
st.caption(f"서버 메모리 지금 {data.rss_mb():.0f} MB · DuckDB 상한 {data.DUCK_MEM}")
