import streamlit as st

TILES = [("종", "제품 종합분석", "search", "#5b6ef5"), ("제", "제품 제안", "sugg", "#e8842a"),
         ("매", "매출 분석", "sales", "#4e9e63"), ("특", "특허 분석", "patent", "#8b5cf6"),
         ("임", "임상 분석", "clinical", "#5fb0e8"), ("약", "약가 분석", "price", "#c9376b"),
         ("AI", "AI 분석", "ai", "#1c2030"), ("상", "상태", "status", "#6b7280")]

st.markdown("## 🔎 제품 검토")
st.caption("매출 IQVIA·UBIST · 허가/특허/임상 식약처 · 약가 심평원+NHIS")
css = ["<style>.hl{text-align:center;color:#5b6472;font-size:15px;font-weight:600;margin:10px 0 24px}"]
for ic, lb, k, col in TILES:
    css.append(f".st-key-h_{k} button{{background:{col}!important;color:#fff!important;width:88px!important;"
               f"height:88px!important;border:none!important;border-radius:22px!important;margin:0 auto!important;display:block}}"
               f".st-key-h_{k} button p{{font-size:30px!important;font-weight:800!important}}")
css.append("</style>")
st.markdown("".join(css), unsafe_allow_html=True)
pages = st.session_state.get("_pages", {})
for r in range(0, len(TILES), 4):
    cols = st.columns(4)
    for i, (ic, lb, k, col) in enumerate(TILES[r:r + 4]):
        with cols[i]:
            if st.button(ic, key=f"h_{k}") and k in pages:
                st.switch_page(pages[k])
            st.markdown(f"<div class='hl'>{lb}</div>", unsafe_allow_html=True)
