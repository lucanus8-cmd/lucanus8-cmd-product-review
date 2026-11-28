"""매출 분석 (v2). 지금 사이트 매출 분석(app.py 4개 탭)을 그대로 띄운다.
매출 표는 scout_sources 의 공유 표(한 벌만)를 같이 쓴다. 다음 단계에서 DuckDB 로 가볍게 바꾼다."""
import streamlit as st

import bootstrap
from core import data

st.subheader("💰 매출 분석")
ready = (data.SAVED / "iqvia.pkl").exists() or (data.SAVED / "ubist.pkl").exists()
if st.session_state.get("_load_sales") and ready:
    import sales_analysis
    try:
        sales_analysis.render()
    except Exception as e:
        if type(e).__name__ in ("RerunException", "StopException"):
            raise
        st.error("매출 분석을 여는 중 문제가 생겼습니다. 다른 화면은 정상 사용할 수 있습니다.")
        with st.expander("자세한 오류 내용 (개발자용)"):
            st.code(f"{type(e).__name__}: {e}")
    if st.button("🧹 매출 데이터 닫기(메모리 절약)", key="btn_unload_sales"):
        st.session_state["_load_sales"] = False
        st.cache_data.clear()
        import scout_sources as ss
        ss.drop_sales_pickles()
        st.rerun()
else:
    st.info("매출 분석 데이터(IQVIA·UBIST)는 용량이 커서 필요할 때만 불러옵니다.\n\n아래 버튼을 누르면 매출 대시보드가 열립니다."
            + ("" if ready else "\n\n아직 자료가 준비되지 않았다면 버튼을 누를 때 함께 받아옵니다(처음엔 몇 분 걸릴 수 있습니다)."))
    if st.button("📊 매출 분석 불러오기", key="btn_load_sales", type="primary"):
        if not ready:
            with st.spinner("매출 자료(IQVIA·UBIST)를 받아오는 중… 처음엔 몇 분 걸릴 수 있습니다"):
                ok, err = bootstrap.fetch_sales()
            if not ok:
                st.error(f"매출 자료를 불러오지 못했습니다 — {err}")
                st.stop()
        st.session_state["_load_sales"] = True
        st.rerun()
