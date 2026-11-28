import streamlit as st

from core import data

BUILD = "v2-2026-11-28-l"
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
# 지금 사이트와 같은 '지금 새로고침': 구글 시트·드라이브에서 모든 자료(매출 포함)를 다시 받는다. 화면은 멈추지 않고 뒤에서.
import bootstrap  # noqa: E402
_run, _done, _err = bootstrap.background_status()
c1, c2 = st.columns([1, 3])
if c1.button("🔄 지금 새로고침", disabled=_run, type="primary",
             help="구글 시트·드라이브에서 자료를 다시 받아옵니다(매출 포함). 화면은 멈추지 않고 뒤에서 진행되며, "
                  "끝나면 새로고침했을 때 반영됩니다."):
    bootstrap.refresh_now(include_sales=True)
    st.rerun()
if _run:
    c2.info("자료를 새로 받는 중입니다… 끝나면 화면을 새로고침(F5)하면 반영됩니다.")
elif _err:
    c2.warning(f"마지막 새로고침이 실패했습니다 — {_err}")
elif _done:
    c2.success("새로고침이 끝났습니다. 화면을 새로고침(F5)하면 새 자료가 보입니다.")
else:
    c2.caption(f"자동 갱신: 매일 한국시간 새벽 {bootstrap.REFRESH_HOUR_KST}시 기준 · 매출은 주 1회")

if st.button("자료 다시 받기 (없는 것만)"):
    for n in ("approval", "patent", "clinical", "price", "nego"):
        data._TRIED.pop(n, None)
        data.ensure(n)
    st.rerun()
