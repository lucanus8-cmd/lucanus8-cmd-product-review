"""제품 검토 v2 — 새 주소용.

지금 사이트와 기능은 같게, 구조는 가볍게:
  · 화면마다 파일 하나(views/). 고른 화면만 실행된다(st.navigation).
  · 자료는 파일로만 두고 필요한 결과만 꺼내 쓴다(core/data.py, DuckDB).
  · 자료 파일은 지금 사이트와 같은 로봇·같은 코드(bootstrap)로 받는다.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="제품 검토", page_icon="🔎", layout="wide")


def _gate():
    """비밀번호 잠금 (Secrets 에 APP_PASSWORD 가 있을 때만)."""
    try:
        pw = st.secrets.get("APP_PASSWORD", None)
    except Exception:
        pw = None
    if not pw or st.session_state.get("_authed"):
        return
    st.title("🔒 제품 검토")
    entered = st.text_input("비밀번호를 입력하세요", type="password")
    if entered == "":
        st.stop()
    if entered == pw:
        st.session_state["_authed"] = True
        st.rerun()
    st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


_gate()

PAGES = [
    st.Page("views/home.py", title="홈", icon="🏠", url_path="home", default=True),
    st.Page("views/search.py", title="제품 종합분석", url_path="search"),
    st.Page("views/sugg.py", title="제품 제안", url_path="sugg"),
    st.Page("views/sales.py", title="매출 분석", url_path="sales"),
    st.Page("views/patent.py", title="특허 분석", url_path="patent"),
    st.Page("views/clinical.py", title="임상 분석", url_path="clinical"),
    st.Page("views/price.py", title="약가 분석", url_path="price"),
    st.Page("views/ai.py", title="AI 분석", url_path="ai"),
    st.Page("views/status.py", title="상태", url_path="status"),
]
st.session_state["_pages"] = {p.url_path: p for p in PAGES}
nav = st.navigation(PAGES, position="top")
st.markdown("<div style='font-size:13px;color:#8a8f99;margin:-6px 0 6px'>제품 검토 · 새 버전(v2, 시험 중)</div>",
            unsafe_allow_html=True)
nav.run()
