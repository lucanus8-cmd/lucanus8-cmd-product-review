import streamlit as st

from core import data

BUILD = "v2-2026-11-28-d"
st.subheader("데이터 연결 상태 (v2)")
st.caption(f"코드 버전: {BUILD}")
rows = []
for n, lab in [("approval", "허가"), ("patent", "특허"), ("clinical", "임상"), ("price", "약가"),
               ("nego", "공단협상"), ("iqvia", "매출 IQVIA"), ("ubist", "매출 UBIST")]:
    p = data.FILES[n]
    rows.append({"자료": lab, "파일": "있음" if p.exists() else "없음", "받은 시각": data.file_date(n),
                 "크기(MB)": round(p.stat().st_size / 1e6, 1) if p.exists() else None,
                 "못 받은 이유": data.ERR.get(n, "")})
st.dataframe(rows, hide_index=True, width="stretch")
st.caption(f"서버 메모리 지금 {data.rss_mb():.0f} MB · DuckDB 상한 {data.DUCK_MEM}")

try:
    has_key = bool(st.secrets.get("gcp_service_account_b64", None)) or ("gcp_service_account" in st.secrets)
except Exception:
    has_key = False
st.caption("구글 서비스계정 키(Secrets): " + ("있음" if has_key else "**없음** — 앱 설정 → Secrets 에 지금 사이트의 Secrets 를 넣어 주세요"))
if st.button("🔄 자료 다시 받기 (없는 것만)"):
    for n in ("approval", "patent", "clinical", "price", "nego"):
        data._TRIED.pop(n, None)
        data.ensure(n)
    st.rerun()
