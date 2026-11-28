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

st.set_page_config(page_title="제품검토_v2", page_icon="🔎", layout="wide")

from core import buildsub  # noqa: E402
buildsub.install()          # 자료 만들기(엑셀 풀기 등)는 별도 작업에서 — 끝나면 메모리를 통째로 돌려받는다


def _gate():
    """비밀번호 잠금 (Secrets 에 APP_PASSWORD 가 있을 때만)."""
    try:
        pw = st.secrets.get("APP_PASSWORD", None)
    except Exception:
        pw = None
    if not pw or st.session_state.get("_authed"):
        return
    st.title("🔒 제품검토_v2")
    entered = st.text_input("비밀번호를 입력하세요", type="password")
    if entered == "":
        st.stop()
    if entered == pw:
        st.session_state["_authed"] = True
        st.rerun()
    st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


_gate()

# ── 자료 파일만 미리 만들기 (깨우기 로봇용: ?page=status&prep=1) — 지금 사이트와 같은 방식 ──────
# 화면은 그리지 않고 파일만 차례로 만든다(이미 최신이면 건너뜀). 매출은 v2 가 쓰는 parquet 로도 바꿔 둔다.
if st.query_params.get("prep") == "1":
    import time

    import bootstrap
    st.subheader("🔄 자료 미리 만들기")
    st.caption("화면은 열지 않고 자료 파일만 만들어 둡니다. 이미 최신인 자료는 건너뜁니다.")
    try:
        bootstrap.prepare_all()
    except Exception:
        pass

    def _prep_body():
        try:
            rows = bootstrap.prepare_status()
            _run, _done, _err = bootstrap.background_status()
        except Exception as _e:
            st.error(f"상태를 읽지 못했습니다 — {type(_e).__name__}")
            return
        st.markdown("  \n".join(f"{'✅' if s_ == '준비됨' else '⏳' if s_ == '만드는 중' else '·'} {lb} — {s_}"
                                 for _, lb, s_ in rows))
        left = [n for n, _, s_ in rows if s_ != "준비됨"]
        if not left:
            if not st.session_state.get("_prep_pq"):         # 매출 pkl → parquet (v2 매출 분석·제품 제안이 읽는 파일)
                from core import data as _d
                for _n in ("iqvia", "ubist"):
                    try:
                        _d.ensure(_n)
                    except Exception:
                        pass
                st.session_state["_prep_pq"] = True
            st.success("모두 준비됨")
            return
        if not _run:
            tries = st.session_state.get("_prep_tries", 0)
            if _err and tries >= 2:
                st.error("실패 — " + str(_err)[:200])
                return
            if time.time() - st.session_state.get("_prep_last", 0.0) > 15:
                st.session_state["_prep_tries"] = tries + 1
                st.session_state["_prep_last"] = time.time()
                try:
                    bootstrap.prepare_all()
                except Exception:
                    pass

    st.fragment(run_every="3s")(_prep_body)()
    st.stop()

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
st.markdown("<div style='font-size:13px;color:#8a8f99;margin:-6px 0 6px'>제품검토_v2 · 시험 중</div>",
            unsafe_allow_html=True)
nav.run()
