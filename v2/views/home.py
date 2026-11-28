"""홈 — 지금 사이트 홈과 같은 원형 아이콘 화면."""
import streamlit as st

TILES = [("종", "제품 종합분석", "search", "#5b6ef5"), ("제", "제품 제안", "sugg", "#e8842a"),
         ("매", "매출 분석", "sales", "#4e9e63"), ("특", "특허 분석", "patent", "#8b5cf6"),
         ("임", "임상 분석", "clinical", "#5fb0e8"), ("약", "약가 분석", "price", "#c9376b"),
         ("AI", "AI 분석", "ai", "#1c2030"), ("상", "상태", "status", "#6b7280")]

st.markdown(
    "<div style='color:#1f2a44;font-size:26px;font-weight:800;letter-spacing:-.01em'>🔎 제품검토_v2</div>"
    "<div style='color:#9aa3b2;font-size:12px;margin-bottom:8px'>매출 IQVIA·UBIST · 허가/특허/임상 식약처 · 약가 심평원+NHIS</div>",
    unsafe_allow_html=True)

css = ["<style>", ".hlabel{text-align:center;color:#5b6472;font-size:15px;font-weight:600;margin:12px 0 26px;}"]
for ic, lb, k, col in TILES:
    kc = f"st-key-home_{k}"
    css.append(
        f".{kc}{{width:132px;height:132px;border-radius:50%;background:#fff;border:1px solid #edeff3;"
        f"box-shadow:0 2px 10px rgba(20,30,55,.06);display:flex;align-items:center;justify-content:center;"
        f"margin:0 auto;transition:box-shadow .15s,border-color .15s,transform .15s;}}"
        f".{kc}:hover{{border-color:#c7d2fe;box-shadow:0 10px 24px rgba(59,130,246,.22);transform:translateY(-2px);}}"
        f".{kc} div[data-testid='stButton']{{width:100%;display:flex;justify-content:center;}}"
        f".{kc} button{{background:{col} !important;color:#fff !important;width:88px !important;height:88px !important;"
        f"min-height:88px !important;border:none !important;border-radius:22px !important;box-shadow:none !important;"
        f"padding:0 !important;margin:0 auto !important;}}"
        f".{kc} button:hover{{background:{col} !important;filter:brightness(1.06);}}"
        f".{kc} button p{{font-size:34px !important;font-weight:800 !important;line-height:1;margin:0;letter-spacing:-.02em;}}")
css.append("</style>")
st.markdown("".join(css), unsafe_allow_html=True)
st.markdown("<div style='height:4vh'></div>", unsafe_allow_html=True)

pages = st.session_state.get("_pages", {})
for r in range(0, len(TILES), 4):
    cols = st.columns(4)
    for i, (ic, lb, k, col) in enumerate(TILES[r:r + 4]):
        with cols[i]:
            if st.button(ic, key=f"home_{k}") and k in pages:
                st.switch_page(pages[k])
            st.markdown(f"<div class='hlabel'>{lb}</div>", unsafe_allow_html=True)
