"""기존 매출 분석 대시보드(app.py)의 4개 탭(제품 대시보드·시장 분석·IQVIA vs UBIST 비교·성장 제품 탐색)을
'제품 검토' 앱의 매출 분석 탭 안에서 그대로 렌더링한다.
app.py 원본은 건드리지 않고, 소스에서 page_config/비밀번호/사이드바 업로드 부분만 제거해 실행한다.
"""
from pathlib import Path
import streamlit as st

_APP = Path(__file__).parent / "app.py"
_COMPILED = None

def _transform_source():
    lines = _APP.read_text(encoding="utf-8").split("\n")
    keep, state = [], "keep"
    for ln in lines:
        if state == "keep":
            if ln.startswith("st.set_page_config"):
                state = "skip_config"; continue
            if ln.startswith("# ── 사이드바"):
                state = "skip_sidebar"; continue
            keep.append(ln)
        elif state == "skip_config":            # page_config/비밀번호/타이틀 제거
            if ln.startswith("# 저장 폴더"):
                state = "keep"; keep.append(ln)
        elif state == "skip_sidebar":            # 사이드바 업로드 + st.stop 가드 제거
            if ln.startswith("# 데이터 로드"):
                state = "keep"; keep.append(ln)
    return "\n".join(keep)

def render():
    """매출 분석 4개 탭을 현재 위치에 렌더링."""
    global _COMPILED
    if not (_APP.exists()):
        st.info("app.py(매출 분석 원본)를 찾을 수 없습니다."); return
    if _COMPILED is None:
        _COMPILED = compile(_transform_source(), "app_sales_analysis", "exec")
    ns = {"__name__": "app_sales_analysis", "__file__": str(_APP)}
    exec(_COMPILED, ns)
