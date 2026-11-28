"""임상 분석의 계산기들 (생동 피험자 수 · 1상 비용 어림 · 간이 메타분석) — 지금 사이트 코드를 그대로 옮겼다."""
import math
import re
import time
import zlib
from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import be_calc
import cost_calc
import ctgov
import meta_calc

@st.cache_data(show_spinner=False, max_entries=1024)
def _be_n(design, cv, target, gmr, alpha, lo, hi):
    """목표 검정력을 넘는 가장 작은 인원. 순수 계산이라 캐시가 안전하다."""
    return be_calc.sample_size(design, cv, target, gmr, alpha, lo, hi)


@st.cache_data(show_spinner=False, max_entries=1024)
def _be_pw(design, n, cv, gmr, alpha, lo, hi):
    return be_calc.power(design, n, cv, gmr, alpha, lo, hi)


def _be_page():
    """생동 피험자 수 계산기.

    화면 구성은 biostatistics.ca 의 PowerTOST 계산기와 같게 맞췄다
    (왼쪽 입력 · 오른쪽 결과, 칸 일곱 개, 버튼 하나, 결과는 '전체' 인원).
    자료를 전혀 읽지 않는 순수 계산이라 화면이 가볍다.

    위젯 key 는 모두 be2_ 로 시작한다 — 예전 화면이 남긴 session_state 값과
    종류가 달라(라디오 '80%' → 숫자칸) 섞이면 오류가 나기 때문이다.
    """
    st.markdown("**🧮 생동 피험자 수** · 변동계수(CV%)를 넣으면 생물학적동등성시험에 "
                "몇 명이 필요한지 계산합니다 (TOST · 평균생동)")

    left, right = st.columns([5, 7], gap="large")

    with left:
        _labels, _keys = [], []
        for k in be_calc.DESIGN_ORDER:
            d = be_calc.DESIGNS[k]
            _labels.append(("⚠ 드묾 · " if d["rare"] else "")
                           + f"{d['label']}  ({d['seq']})")
            _keys.append(k)
        _pick = st.selectbox("설계", _labels, index=0, key="be2_design")
        dkey = _keys[_labels.index(_pick)]
        D = be_calc.DESIGNS[dkey]
        st.caption(D["note"])

        _kind = D["cv"]      # 개체내 / 전체
        _split = st.checkbox("AUC·Cmax 를 따로 넣기", value=False, key="be2_split")
        if _split:
            cv_auc = float(st.number_input(
                f"AUC CV% ({_kind})", min_value=0.0, max_value=200.0,
                value=25.0, step=1.0, format="%.4g", key="be2_cv_auc"))
            cv_cmax = float(st.number_input(
                f"Cmax CV% ({_kind})", min_value=0.0, max_value=200.0,
                value=35.0, step=1.0, format="%.4g", key="be2_cv_cmax"))
            _cvs = [("AUC", cv_auc), ("Cmax", cv_cmax)]
        else:
            cv_one = float(st.number_input(
                f"변동계수 CV% ({_kind})", min_value=0.0, max_value=200.0,
                value=30.0, step=1.0, format="%.4g", key="be2_cv"))
            _cvs = [("CV", cv_one)]

        gmr = float(st.number_input(
            "기대 기하평균비 GMR (시험약/대조약)", min_value=0.70, max_value=1.30,
            value=0.95, step=0.01, format="%.3f", key="be2_gmr"))
        lo = float(st.number_input(
            "생동한계 아래", min_value=0.50, max_value=0.99, value=0.80,
            step=0.01, format="%.2f", key="be2_lo"))
        hi = float(st.number_input(
            "생동한계 위", min_value=1.01, max_value=2.00, value=1.25,
            step=0.01, format="%.2f", key="be2_hi"))
        st.caption(f"{lo * 100:.2f}% ~ {hi * 100:.2f}%")
        target = float(st.number_input(
            "목표 검정력 (%)", min_value=50.0, max_value=99.0, value=80.0,
            step=1.0, format="%.4g", key="be2_pw"))
        alpha = float(st.number_input(
            "유의수준 α (단측, %)", min_value=0.5, max_value=20.0, value=5.0,
            step=0.5, format="%.4g", key="be2_alpha"))
        drop = float(st.number_input(
            "예상 탈락률 (%)", min_value=0.0, max_value=60.0, value=0.0,
            step=1.0, format="%.4g", key="be2_drop"))
        st.caption("탈락률을 넣으면 **등록해야 할 인원**도 같이 내줍니다. "
                   "0 이면 완료 기준만 봅니다.")
        _go = st.button("피험자 수 계산", type="primary",
                        use_container_width=True, key="be2_go")

    if _go:
        st.session_state["be2_done"] = True
    if not st.session_state.get("be2_done"):
        with right:
            st.info("왼쪽 값을 확인하고 **피험자 수 계산** 을 누르세요.")
        return

    parts = []
    for _nm, _cv in _cvs:
        if _cv and _cv > 0:
            n = _be_n(dkey, _cv / 100, target / 100, gmr, alpha / 100, lo, hi)
            parts.append({"name": _nm, "cv": _cv, "n": n,
                          "power": (_be_pw(dkey, n, _cv / 100, gmr,
                                           alpha / 100, lo, hi) if n else None)})
    done = [p for p in parts if p["n"]]

    with right:
        st.caption("값을 바꾸면 바로 다시 계산됩니다.")
        if not done:
            if not parts:
                st.info("CV% 를 넣어주세요.")
            else:
                st.warning("이 조건에서는 2,000명 안에서 목표 검정력에 닿지 못했습니다. "
                           "CV% 나 목표 검정력, 생동한계를 확인해 주세요.")
            return
        top = max(done, key=lambda q: q["n"])
        st.metric("필요 피험자 수 (전체 · 완료 기준)", f"{top['n']:,} 명")
        st.metric("실제 검정력", f"{top['power'] * 100:.2f} %")
        if drop > 0:
            _en = be_calc.enroll(top["n"], drop / 100, D["step"])
            st.metric("등록해야 할 인원", f"{_en:,} 명" if _en else "—",
                      delta=f"탈락 {drop:g}% 감안", delta_color="off")
        st.caption(f"순서당·군당이 아니라 **전체** 인원입니다. "
                   f"순서가 {D['step']}가지인 설계라 인원을 "
                   f"**{D['step']}의 배수**로 맞춥니다.")
        if len(done) > 1:
            _a, _b = done[0], done[1]
            st.info(f"**{top['name']}** 쪽이 더 많아 이쪽으로 정했습니다 — "
                    f"{_a['name']}(CV {_a['cv']:g}%) {_a['n']}명 · "
                    f"{_b['name']}(CV {_b['cv']:g}%) {_b['n']}명.")

    big = max(p["cv"] for p in done)

    if _kind == "전체":
        st.warning("병렬설계의 CV 는 **개체내가 아니라 전체(개체간 포함) 변동**입니다. "
                   "교차설계용 개체내 CV 를 그대로 넣으면 인원이 실제보다 적게 나옵니다.")
    if dkey == "paired":
        st.warning("**대응표본은 생동시험으로 인정되지 않습니다.** 순서를 무작위로 "
                   "배정하지도, 기간을 나누지도 않아 약의 효과와 기간 효과를 갈라낼 수 "
                   "없습니다. 같은 사람에게서 두 측정법을 비교할 때 쓰는 설계입니다.")
    if dkey == "2x4x2":
        st.warning("**Balaam 은 인원이 크게 늡니다.** 네 순서 중 RR·TT 군은 시험약과 "
                   "대조약을 둘 다 받지 않아 T/R 비교에 직접 쓰이지 않습니다 — 같은 "
                   "CV에서 2×2 교차의 약 4배가 듭니다.")
    if big > 30 and _kind != "전체":
        st.warning("CV 가 30% 를 넘습니다 — **고변동 의약품**입니다. 반복설계와 "
                   "기준조절(RSABE·ABEL)을 쓰면 인원을 크게 줄일 수 있습니다. "
                   "**이 계산기는 평균생동(ABE)만 다룹니다** — 기준조절 적용 가능 "
                   "여부는 의약품동등성시험기준에서 확인하세요.")

    if st.checkbox("CV 가 달라지면 — 표로 보기", key="be2_tbl_cv"):
        _rows = []
        for c in sorted({round(max(2.0, big + d)) for d in (-10, -5, 0, 5, 10)}):
            n = _be_n(dkey, c / 100, target / 100, gmr, alpha / 100, lo, hi)
            p = _be_pw(dkey, n, c / 100, gmr, alpha / 100, lo, hi) if n else None
            _rows.append({"CV%": f"{c:g}%",
                          "완료 기준": f"{n:,} 명" if n else "—",
                          "실제 검정력": f"{p * 100:.1f}%" if p else "—",
                          "": "← 지금 값" if abs(c - big) < 0.5 else ""})
        st.dataframe(pd.DataFrame(_rows), hide_index=True,
                     use_container_width=True, height=38 * (len(_rows) + 1) + 4)
        st.caption("지금 설계·GMR·검정력 그대로 두고 CV 만 바꿨을 때입니다. "
                   "가정 하나가 비용을 두 배로 만들 수 있습니다.")

    if st.checkbox("설계를 바꾸면 — 표로 보기", key="be2_tbl_design"):
        _d = []
        for k in be_calc.DESIGN_ORDER:
            d = be_calc.DESIGNS[k]
            n = _be_n(k, big / 100, target / 100, gmr, alpha / 100, lo, hi)
            _d.append({"설계": d["label"], "순서": d["seq"],
                       "CV 의미": d["cv"],
                       "완료 기준": f"{n:,} 명" if n else "—",
                       "비고": ("생동에서 거의 쓰지 않음" if d["rare"] else ""),
                       "": "← 지금 설계" if k == dkey else ""})
        st.dataframe(pd.DataFrame(_d), hide_index=True,
                     use_container_width=True, height=38 * (len(_d) + 1) + 4)
        st.caption(f"CV {big:g}% 로 모두 계산했습니다. 병렬의 CV 는 전체 변동이라 "
                   "교차설계 칸과 나란히 비교하면 안 됩니다 — 같은 약이면 전체 CV 가 "
                   "개체내보다 커서 실제로는 더 많이 필요합니다. 반복설계는 인원이 "
                   "적은 대신 기간·채혈이 늘어 1인당 비용이 올라갑니다.")

    with st.expander("계산 방법과 검산"):
        st.markdown(
            "**계산**\n\n"
            "- σ = √(ln(1 + CV²)) · SE² = bk × σ² ÷ N\n"
            f"- {D['label']}: bk = **{D['bk']:g}** · 자유도 = **{D['dfs']}** "
            f"· 인원은 **{D['step']}의 배수**\n"
            f"- TOST · 유의수준 {alpha:g}% 단측 · 생동한계 "
            f"{lo * 100:.2f}~{hi * 100:.2f}% · GMR {gmr:.3f}\n"
            "- 목표 검정력을 넘는 가장 작은 N 을 찾고, 등록 인원은 "
            "⌈N ÷ (1−탈락률)⌉ 을 배수로 올립니다\n\n"
            "**검산**\n\n"
            "- PowerTOST 문서 예제: 2×2 · CV 30% · GMR 0.95 · 검정력 80% → "
            "**40명 · 0.815845** (소수 여섯째 자리까지 일치)\n"
            "- 설계 12개의 bk·자유도·배수는 PowerTOST 원본 소스에서 확인한 값입니다\n"
            "- biostatistics.ca 의 PowerTOST 계산기에 **167가지 조건**을 직접 넣어 "
            "받은 값과 대조 — 인원·달성검정력 모두 전부 일치했습니다 "
            "(CV 5~60% · GMR 0.90~1.00 · 검정력 80~95% · α 5·10% · "
            "생동한계 0.8~1.25 와 0.9~1.11)\n"
            "- 저장소의 `tests/check_be_calc.py` 로 언제든 다시 돌려볼 수 있습니다\n\n"
            "**다루지 않는 것** — 고변동 의약품의 기준조절(RSABE·ABEL), "
            "비선형 약동학, 중간분석. 평균생동(ABE) 하나만 계산합니다.")


def _cost_prices():
    """1상 비용 단가. 코드의 기본값을 쓰고, Secrets 의 [phase1_cost] 에 값이
    있으면 그 값으로 덮어쓴다. Secrets 가 없어도 죽지 않는다.
    """
    try:
        raw = st.secrets.get("phase1_cost", None)
    except Exception:
        raw = None
    out = dict(cost_calc.DEFAULT_PRICES)
    if raw:
        for k in cost_calc.KEYS:
            try:
                out[k] = float(raw[k])
            except Exception:
                pass
    return out


def _cost_page():
    """1상 비용 어림 계산기. 자료를 읽지 않는 순수 계산이라 가볍다."""
    st.markdown("**💰 1상 비용 어림** · 숫자 다섯 개로 1상 시험의 대략적인 총 비용을 "
                "봅니다 (견적을 대신하지 않습니다)")

    base = cost_calc.clean(_cost_prices())
    have = cost_calc.ready(base)
    left, right = st.columns([5, 7], gap="large")

    with left:
        n = int(st.number_input("대상자 수 (명)", min_value=1, max_value=2000,
                                value=60, step=1, key="cost_n"))
        k = int(st.number_input("기 수", min_value=1, max_value=12,
                                value=2, step=1, key="cost_k"))
        pts = int(st.number_input("기당 채혈 포인트 (회)", min_value=1, max_value=100,
                                  value=20, step=1, key="cost_pts"))
        an = int(st.number_input("분석 성분 수 (개)", min_value=1, max_value=10,
                                 value=1, step=1, key="cost_an"))
        hrs = float(st.number_input("마지막 채혈 시점 (투약 후 시간)", min_value=1,
                                    max_value=2000, value=240, step=12,
                                    key="cost_hrs"))
        st.caption(f"총 검체 수 **{n * k * pts:,}개** = {n} × {k} × {pts}")

        # 단가가 없으면 바로 넣을 수 있게 칸을 펼쳐 둔다
        edit = st.checkbox("단가 고치기", value=not have, key="cost_edit")
        prices = dict(base)
        if edit:
            st.caption("단가는 **만 원** 단위로 넣습니다.")
            for key, label, hint, step in cost_calc.PRICE_KEYS:
                prices[key] = 1e4 * float(st.number_input(
                    f"{label} (만 원)", min_value=0.0, max_value=10_000_000.0,
                    value=float(base.get(key, 0)) / 1e4, step=step / 1e4,
                    format="%.6g", help=hint, key=f"cost_pm_{key}"))

    with right:
        if not cost_calc.ready(prices):
            st.info("단가가 모두 0입니다. 왼쪽 **단가 고치기**에 값을 넣어 주세요.")
            return
        r = cost_calc.estimate(n, k, pts, an, hrs, prices)
        tot = r["total"]
        st.metric("총 비용 (VAT 별도)", f"약 {tot / 1e8:.2f}억 원")
        st.metric("VAT 포함", f"약 {r['vat_total'] / 1e8:.2f}억 원")
        st.caption("오차 ±10% 안팎 · 할인 전 기준")
        _rows = []
        for _nm, _key in (("임상 CRO", "cro"), ("분석", "lab"),
                          ("병원 (실시기관 + 사례비)", "site")):
            _rows.append({"블록": _nm, "금액": f"{r[_key] / 1e8:.2f}억",
                          "비중": f"{(r[_key] / tot * 100) if tot else 0:.0f}%"})
        st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True,
                     height=38 * (len(_rows) + 1) + 4)
        st.caption(f"병원 단가는 1명·1기당 **{r['site_unit'] / 1e4:,.0f}만 원**으로 "
                   f"잡았습니다 (마지막 채혈 {hrs:g}시간 기준).")
        if edit:
            st.caption("고친 단가는 이 화면에서만 쓰이고 저장되지 않습니다.")

    st.caption("견적 네 건을 반올림한 어림값입니다. 업체와 성분에 따라 달라집니다. "
               "대조약·임상약·표준품 구입비, 식약처 수수료, 사내 인건비는 들어 있지 않고, "
               "실제 계약은 할인으로 3%에서 15%쯤 낮아질 수 있습니다. 건강한 성인 대상 1상 기준입니다.")

    with st.expander("계산 방식과 한계"):
        _p = cost_calc.clean(prices)

        def _m(key):
            return f"{_p[key] / 1e4:,.6g}만"

        st.markdown(
            "**계산**\n\n"
            f"- 임상 CRO = {_m('cro_base')} + {_m('cro_per_period')} × 기 "
            f"+ {_m('cro_per_subject')} × 대상자\n"
            f"- 분석 = ({_m('lab_base')} + {_m('lab_per_sample')} × 검체 수) "
            "× 성분 수\n"
            "- 병원 = 병원 단가 × 대상자 × 기 (실시기관 비용과 사례비를 합친 값)\n"
            f"- 병원 단가(1명·1기당): 24시간 이하 {_m('site_24h')} · "
            f"72시간 {_m('site_72h')} · 240시간 이상 {_m('site_240h')}. "
            "그 사이는 직선으로 이어 계산합니다\n"
            "- 검체 수 = 대상자 × 기 × 기당 채혈 포인트\n\n"
            "**맞춰 본 결과** — 견적 네 건(대상자 40명과 60명, 2기와 3기, 채혈 17회에서 "
            "22회)에 넣었을 때 "
            "총액 차이는 ±3% 안이었습니다. 단가를 그 네 건에서 뽑았으므로, "
            "새 견적에서는 더 벗어날 수 있습니다.\n\n"
            "**가정** — 성분이 여러 개면 분석을 성분마다 따로 한다고 보고 배수로 "
            "곱합니다(한 번에 분석할 수 있으면 실제는 더 적습니다). 병원 단가의 "
            "72시간·24시간 기준점은 추정이고, 240시간을 넘는 경우는 자료가 없어 "
            "240시간 값으로 둡니다.")


# ══════════════════════════ 간이 메타분석 ══════════════════════════
# 임상 분석 탭 → 📐 간이 메타분석. 계산은 meta_calc, 자료 받기는 ctgov (둘 다 별도 파일).
# [임상 찾기]를 누를 때만 ClinicalTrials.gov·PubMed 에 접속하고, 결과는 작은 표로 줄여
# 몇 시간 저장한다(최근 20개). 위젯 key 는 모두 ma_ 로 시작한다.
_MA_SIDES = [ctgov.SIDE_A, ctgov.SIDE_B, ctgov.SIDE_D]
_MA_OLD_SIDES = {"A vs 위약": ctgov.SIDE_A, "B vs 위약": ctgov.SIDE_B}   # 예전 CSV 호환
_MA_TEXT = ["비교", "임상번호", "제목", "질환", "평가변수", "묶음", "시점", "단위", "구분",
            "시험군", "대조군", "시험군 용량", "대조군 용량"]
_MA_TAIL = ["점검", "논문", "링크", "소그룹"]
_MA_NUM = {
    "연속형": [("시험군 인원", "n1"), ("시험군 평균", "m1"), ("시험군 SD", "sd1"),
             ("대조군 인원", "n2"), ("대조군 평균", "m2"), ("대조군 SD", "sd2"),
             ("차이", "d"), ("95% 하한", "lo"), ("95% 상한", "hi")],
    "이분형": [("시험군 발생", "e1"), ("시험군 인원", "n1"), ("대조군 발생", "e2"), ("대조군 인원", "n2")],
    "생존분석": [("HR", "hr"), ("95% 하한", "lo"), ("95% 상한", "hi")],
}


@st.cache_data(ttl=6 * 3600, max_entries=20, show_spinner=False)
def _ma_search(a, b, kind, out_kw, week, phases, cond):
    res = ctgov.search_all(a, b, kind, out_kw, week, phases, fetch=ctgov.fetch_studies, cond=cond)
    res["at"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600))
    return res            # res["v"] == 3: 평가변수·질환 묶음이 붙은 결과


@st.cache_data(ttl=24 * 3600, max_entries=40, show_spinner=False)
def _ma_pubs(ncts):
    return ctgov.pubmed_for_ncts(list(ncts))


@st.cache_data(ttl=6 * 3600, max_entries=30, show_spinner=False)
def _ma_pubsearch(q, top, rct):
    return ctgov.pubmed_search(q, top, rct)


def _ma_cols(kind):
    return ["합침"] + _MA_TEXT + [c for c, _ in _MA_NUM[kind]] + _MA_TAIL


def _ma_frame(kind, rows=()):
    """행 목록 → 입력표. 글자 칸은 object, 숫자 칸은 float 로 고정한다(편집기 dtype 오류 방지)."""
    cols = _ma_cols(kind)
    data = {c: [] for c in cols}
    for r in rows:
        for c in cols:
            data[c].append(r.get(c))
    df = pd.DataFrame(data, columns=cols)
    df["합침"] = pd.Series([bool(x) if x is not None and x == x else True for x in df["합침"]],
                         dtype=bool, index=df.index)
    for c in _MA_TEXT + _MA_TAIL:
        df[c] = pd.Series(["" if (x is None or x != x) else str(x) for x in df[c]], dtype=object, index=df.index)
    for c, _ in _MA_NUM[kind]:
        df[c] = pd.to_numeric(pd.Series(list(df[c]), dtype=object, index=df.index), errors="coerce").astype(float)
    return df


def _ma_rows_from_search(rows, kind, pubs):
    out = []
    keymap = dict(_MA_NUM[kind])
    for r in rows:
        row = {k: r.get(k, "") for k in ["합침"] + _MA_TEXT + ["점검", "소그룹"]}
        for col, k in keymap.items():
            row[col] = r.get(k)
        p = pubs.get(r["임상번호"])
        row["논문"] = f"{p[0]} {p[1]}".strip() if p else ""
        row["링크"] = ctgov.pubmed_link(r["임상번호"], p[2] if p else None)
        out.append(row)
    order = {s: i for i, s in enumerate(_MA_SIDES)}
    out.sort(key=lambda x: (order.get(x["비교"], 9), not x["합침"], x["임상번호"]))
    return out


def _ma_set_base(kind, df):
    st.session_state[f"ma_base_{kind}"] = df
    st.session_state[f"ma_ver_{kind}"] = st.session_state.get(f"ma_ver_{kind}", 0) + 1


def _ma_effect(kind, measure, r):
    g = lambda c: r.get(c)  # noqa: E731
    if kind == "연속형":
        return meta_calc.es_cont(measure, g("시험군 인원"), g("시험군 평균"), g("시험군 SD"),
                                 g("대조군 인원"), g("대조군 평균"), g("대조군 SD"),
                                 g("차이"), g("95% 하한"), g("95% 상한"))
    if kind == "이분형":
        return meta_calc.es_bin(measure, g("시험군 발생"), g("시험군 인원"), g("대조군 발생"), g("대조군 인원"))
    return meta_calc.es_surv(g("HR"), g("95% 하한"), g("95% 상한"))


def _ma_disp(measure, y):
    if measure in meta_calc.RATIO:
        return math.exp(y)
    return y * 100 if measure == "RD" else y


_MA_TF_RE = re.compile(r"(?:(\d+(?:\.\d+)?)\s*-?\s*(weeks?|wks?|months?|years?|days?)\b"
                       r"|\b(weeks?|wks?|months?|years?|days?)\s*(\d+(?:\.\d+)?))", re.I)
_MA_TF_WK = {"w": 1, "m": 52 / 12, "y": 52, "d": 1 / 7}


def _ma_weeks(tf):
    """시점 글자 → 주. '12 weeks'·'Week 12' 모두 읽고, 여러 개면 가장 마지막 값. 못 읽으면 None."""
    hits = _MA_TF_RE.findall(tf or "")
    if not hits:
        return None
    n1, u1, u2, n2 = hits[-1]
    n, u = (n1, u1) if n1 else (n2, u2)
    return round(float(n) * _MA_TF_WK[u[0].lower()], 1)


def _ma_wk_txt(w):
    return f"{w:g}주"


def _ma_tally(vals, top=4):
    c = Counter(v for v in vals if v)
    out = [f"{v} ({n})" for v, n in c.most_common(top)]
    if len(c) > top:
        out.append(f"외 {len(c) - top}가지")
    return " · ".join(out) or "—"


def _ma_ep_summary(df):
    """합침에 체크된 줄의 질환·평가변수·시점 요약(괄호는 줄 수)."""
    on = df[df["합침"] == True] if "합침" in df else df   # noqa: E712
    if on.empty or not any(str(x).strip() for c in ("질환", "평가변수", "시점", "구분") for x in on[c]):
        return ""
    wk = [_ma_weeks(x) for x in on["시점"]]
    return ("**합치는 임상의 질환** — " + _ma_tally(on["질환"]) + "  \n"
            "**평가변수** — " + _ma_tally(on["평가변수"], 3) + "  \n"
            "**시점** — " + _ma_tally([_ma_wk_txt(w) if w is not None else (t or "")[:30]
                                     for w, t in zip(wk, on["시점"])]) +
            " · **1차/2차** — " + _ma_tally(on["구분"]))


def _ma_pick_group(kind, res, comp, cur):
    """질환 묶음·평가변수 묶음을 고르게 하고, 고른 묶음의 줄만 돌려준다. 고른 것이 바뀌면 입력표를 새로 만든다.
    질환은 '전체'가 없다(섞이지 않게). 평가변수 묶음은 1차 평가변수 줄이 많은 것을 먼저 고른다."""
    rows = (cur or {}).get("rows") or []
    pubs = st.session_state.get(f"ma_pubs_{kind}") or {}
    if not rows:
        if st.session_state.get(f"ma_cur_{kind}") != (comp, None, None):
            st.session_state[f"ma_cur_{kind}"] = (comp, None, None)
            _ma_set_base(kind, _ma_frame(kind))
        return [], "", 0
    st.markdown("##### 질환 · 평가변수 묶음 <span style='font-weight:400;color:#808495;font-size:13.5px'>"
                "— 같은 질환, 같은 척도·방식·시점끼리만 합칩니다</span>", unsafe_allow_html=True)
    dopts = ctgov.disease_options(rows)
    dlist = [d for d, *_ in dopts]
    dcnt = {d: (na, nb, nd) for d, na, nb, nd in dopts}
    dk = f"ma_dis_{kind}"
    if st.session_state.get(dk) not in dlist:
        st.session_state[dk] = dlist[0]
    dis = st.radio(
        "질환 묶음 (ClinicalTrials.gov 표준 병명 · 괄호는 A·B 임상 수)", dlist, horizontal=True, key=dk,
        format_func=lambda d: f"{d} (A {dcnt[d][0]} · B {dcnt[d][1]}" + (f" · 직접 {dcnt[d][2]}" if dcnt[d][2] else "") + ")")
    gopts = ctgov.group_options(rows, dis)
    usable = [g for g in gopts if g["ok"]]
    shown = usable or gopts
    if not shown:
        return [], "", 0
    if not usable:
        st.warning("이 질환에는 A·B 양쪽에 같이 있는 평가변수 묶음이 없습니다. 한쪽만 있는 묶음을 고를 수는 있지만 "
                   "간접 비교는 나오지 않습니다.")
    keys = [g["key"] for g in shown]
    gkey = f"ma_grp_{kind}"
    tkey = "ma_grptab_%s_%08x" % (kind, zlib.crc32(repr((comp, dis, res.get("at"), st.session_state.get(f"ma_nsearch_{kind}", 0))).encode()))
    sel = ((st.session_state.get(tkey) or {}).get("selection") or {}).get("rows") or []
    if sel and sel[0] < len(keys):
        st.session_state[gkey] = keys[sel[0]]
    if st.session_state.get(gkey) not in keys:
        st.session_state[gkey] = keys[0]
    gk = st.session_state[gkey]
    df = pd.DataFrame([{
        "고름": "●" if g["key"] == gk else "", "평가변수 묶음": g["label"], "A 임상": g["a"], "B 임상": g["b"],
        "직접 비교": g["d"], "1차 평가변수 줄": g["prim"],
        "원문 표현 예": (g["ex"][0][:90] if g["ex"] else "") + (f" 외 {len(g['ex']) - 1}가지" if len(g["ex"]) > 1 else "")}
        for g in shown])
    st.dataframe(df, key=tkey, on_select="rerun", selection_mode="single-row", hide_index=True,
                 use_container_width=True, height=min(35 * len(df) + 38, 330),
                 column_config={"고름": st.column_config.TextColumn("고름", width="small"),
                                "평가변수 묶음": st.column_config.TextColumn("평가변수 묶음", width="medium"),
                                "원문 표현 예": st.column_config.TextColumn("원문 표현 예", width="large")})
    one = [f"{g['label']} (A {g['a']} · B {g['b']})" for g in gopts if not g["ok"]] if usable else []
    st.caption("줄 왼쪽 칸을 눌러 고르면 그 묶음으로 바뀝니다. 처음에는 1차 평가변수 줄이 가장 많은 묶음을 고릅니다."
               + (" · 한쪽에만 있어 고를 수 없는 묶음: " + " · ".join(one[:6]) + (" …" if len(one) > 6 else "")
                  if one else ""))
    picked = ctgov.pick_rows(rows, dis, gk)
    now = (comp, dis, gk)
    if st.session_state.get(f"ma_cur_{kind}") != now:
        st.session_state[f"ma_cur_{kind}"] = now
        _ma_set_base(kind, _ma_frame(kind, _ma_rows_from_search(picked, kind, pubs)))
    return picked, next(g["label"] for g in shown if g["key"] == gk), len(gopts)


def _ma_forest(items, measure):
    """items: dict(label, y, se, w, kind='study'|'pool'|'head', color)."""
    n = len(items)
    fig = go.Figure()
    ys, labels = [], []
    sx, sy, sp, sm, ssz = [], [], [], [], []
    for i, it in enumerate(items):
        yy = n - i
        ys.append(yy)
        labels.append(it["label"])
        if it["kind"] == "head":
            continue
        m = _ma_disp(measure, it["y"])
        lo = _ma_disp(measure, it["y"] - meta_calc.Z * it["se"])
        hi = _ma_disp(measure, it["y"] + meta_calc.Z * it["se"])
        txt = (f"{meta_calc.fmt(measure, meta_calc.show(measure, it['y']))} "
               f"[{meta_calc.fmt(measure, meta_calc.show(measure, it['y'] - meta_calc.Z * it['se']))}, "
               f"{meta_calc.fmt(measure, meta_calc.show(measure, it['y'] + meta_calc.Z * it['se']))}]")
        fig.add_annotation(x=1.01, xref="paper", y=yy, yref="y", text=("<b>" + txt + "</b>") if it["kind"] == "pool" else txt,
                           showarrow=False, xanchor="left", font=dict(size=12, color="#31333f"))
        if it["kind"] == "study":
            sx.append(m)
            sy.append(yy)
            sp.append(hi - m)
            sm.append(m - lo)
            ssz.append(7 + (it.get("w") or 0) * 0.3)
        else:
            fig.add_trace(go.Scatter(x=[lo, m, hi, m, lo], y=[yy, yy + .32, yy, yy - .32, yy], fill="toself",
                                     mode="lines", line=dict(color=it["color"], width=1), fillcolor=it["color"],
                                     hoverinfo="skip", showlegend=False))
    if sx:
        fig.add_trace(go.Scatter(x=sx, y=sy, mode="markers", showlegend=False, hoverinfo="skip",
                                 marker=dict(symbol="square", size=ssz, color="#2f6f8f"),
                                 error_x=dict(type="data", symmetric=False, array=sp, arrayminus=sm,
                                              color="#2f6f8f", thickness=1.5, width=0)))
    nul = 1.0 if measure in meta_calc.RATIO else 0.0
    fig.add_vline(x=nul, line=dict(color="#b0b5c0", dash="dash", width=1))
    fig.update_yaxes(tickvals=ys, ticktext=labels, range=[0.4, n + 0.6], showgrid=False, zeroline=False)
    fig.update_xaxes(type="log" if measure in meta_calc.RATIO else "linear", showgrid=True, gridcolor="#f0f1f4",
                     zeroline=False, title_text=f"{meta_calc.MEASURE_LABEL[measure]}"
                     + (" (%p)" if measure == "RD" else "") + "   ← 더 낮음 · 더 높음 →")
    fig.update_layout(height=26 * n + 90, margin=dict(l=10, r=170, t=10, b=50), plot_bgcolor="#fff")
    return fig


def _meta_page():
    st.markdown("**📐 간이 메타분석** · 두 성분을 같은 비교약(위약 등)과 비교한 임상을 합쳐 효과를 비교합니다 "
                "(제품 검토 참고용 · 허가·논문 제출용이 아닙니다)")

    c = st.columns([1, 1, 1.3, 1.3])
    a_txt = c[0].text_input("성분 A", key="ma_a", placeholder="예: bempedoic acid",
                            help="영문 성분명. 용량을 정하려면 뒤에 숫자 (예: empagliflozin 10)")
    b_txt = c[1].text_input("성분 B", key="ma_b", placeholder="예: ezetimibe")
    kind = c[2].radio("지표 종류", list(_MA_NUM), horizontal=True, key="ma_kind")
    lower_better = c[3].radio("방향", ["낮을수록 좋음", "높을수록 좋음"], horizontal=True,
                              key="ma_dir") == "낮을수록 좋음"
    c = st.columns([1, .55, 1, 1.1, 1.1, .8])
    out_kw = c[0].text_input("지표 이름 (영문 일부)", key="ma_out", placeholder="예: LDL, HbA1c",
                             help="쉼표로 여러 개 (하나라도 들어가면 찾음)")
    week = int(c[1].number_input("시점 (주)", min_value=0, max_value=520, value=12, step=1, key="ma_week",
                                 help="0 이면 시점 상관없이"))
    cond = c[2].text_input("질환 (영문, 선택)", key="ma_cond", placeholder="예: type 2 diabetes",
                           help="비우면 질환 상관없이 찾습니다")
    phases = c[3].multiselect("단계", ["1상", "2상", "3상", "4상"], default=["3상", "4상"], key="ma_ph")
    with c[4]:
        st.write("")
        use_ct = st.checkbox("ClinicalTrials.gov에서 찾기", value=True, key="ma_ct")
    with c[5]:
        st.write("")
        go_btn = st.button("🔎 임상 찾기", type="primary", key="ma_go", use_container_width=True)

    a_name = (a_txt or "").strip() or "A"
    b_name = (b_txt or "").strip() or "B"
    what = (out_kw or "").strip() or "지표"
    bkey = f"ma_base_{kind}"
    if bkey not in st.session_state:
        _ma_set_base(kind, _ma_frame(kind))

    if go_btn:
        if not use_ct:
            st.info("ClinicalTrials.gov 찾기를 껐습니다. 아래 표에 직접 넣거나 CSV를 올리세요.")
        elif not (a_txt or "").strip() or not (b_txt or "").strip():
            st.warning("성분 A와 성분 B를 모두 넣어 주세요.")
        elif not phases:
            st.warning("단계를 하나 이상 골라 주세요.")
        else:
            try:
                with st.spinner("ClinicalTrials.gov에서 임상을 찾는 중…"):
                    res = _ma_search(a_txt.strip(), b_txt.strip(), kind, (out_kw or "").strip(), week,
                                     tuple(sorted(phases)), (cond or "").strip())
            except Exception as e:
                res = None
                st.error(f"ClinicalTrials.gov에 접속하지 못했습니다 ({type(e).__name__}). "
                         "잠시 뒤 다시 누르거나, 아래 표에 직접 넣어 주세요.")
            if res is not None:
                pubs = {}
                try:
                    with st.spinner("PubMed에서 논문을 잇는 중…"):
                        pubs = _ma_pubs(tuple(sorted({r["임상번호"] for v in res["by"].values() for r in v["rows"]})))
                except Exception:
                    st.caption("PubMed 논문 연결은 이번에 하지 못했습니다 (링크는 그대로 씁니다).")
                st.session_state[f"ma_res_{kind}"] = res
                st.session_state[f"ma_pubs_{kind}"] = pubs
                st.session_state[f"ma_comp_{kind}"] = res["default"]
                st.session_state.pop(f"ma_cur_{kind}", None)       # 아래에서 질환·묶음을 고른 뒤 표를 만든다
                st.session_state.pop(f"ma_dis_{kind}", None)
                st.session_state.pop(f"ma_grp_{kind}", None)
                st.session_state[f"ma_nsearch_{kind}"] = st.session_state.get(f"ma_nsearch_{kind}", 0) + 1

    res = st.session_state.get(f"ma_res_{kind}")
    if res and res.get("v") != 3:        # 예전 모양으로 저장된 결과는 버린다
        res = None
        st.session_state.pop(f"ma_res_{kind}", None)
    comp = ctgov.PLACEBO
    cur = None
    grp_label, n_grp = "", 0
    if res:
        usable = [c for c, na, nb, ok in res["comparators"] if ok and c in res["by"]]
        cnt = {c: (na, nb) for c, na, nb, ok in res["comparators"]}
        if usable:
            if st.session_state.get(f"ma_comp_{kind}") not in usable:
                st.session_state[f"ma_comp_{kind}"] = res["default"] if res["default"] in usable else usable[0]
            comp = st.radio(
                "공통 비교약 (두 성분 임상에 모두 나오는 비교약 · 괄호는 A·B 임상 수)", usable, horizontal=True,
                key=f"ma_comp_{kind}",
                format_func=lambda c: f"{c} ({res['a']} {cnt[c][0]} · {res['b']} {cnt[c][1]})")
        else:
            st.warning("두 성분 임상에 공통으로 쓰인 비교약이 없습니다. 같은 임상 안 직접 비교가 있으면 그것만 계산되고, "
                       "없으면 PubMed 등에서 찾아 표에 직접 넣어 주세요.")
        others = [f"{c} ({res['a']} {na} · {res['b']} {nb})" for c, na, nb, ok in res["comparators"]
                  if not ok and (na or nb)]
        if others:
            st.caption("한쪽에만 있는 비교약 (고를 수 없음): " + " · ".join(others[:8]))
        cur = res["by"].get(comp) or res["by"].get(ctgov.PLACEBO)
        sel_rows, grp_label, n_grp = _ma_pick_group(kind, res, comp, cur)
        nside = Counter(r["비교"] for r in sel_rows)
        st.caption(f"ClinicalTrials.gov {res['at']} 받음 ({res['sec']:.1f}초) · {res['a']} 결과 있는 임상 "
                   f"{res['found']['A']}개 중 {nside.get(ctgov.SIDE_A, 0)}줄, {res['b']} {res['found']['B']}개 중 "
                   f"{nside.get(ctgov.SIDE_B, 0)}줄 · 같은 임상 안 직접 비교 {nside.get(ctgov.SIDE_D, 0)}줄"
                   + (" · 임상이 많아 성분당 500개까지만 봤습니다" if res.get("capped") else "")
                   + " · 체크를 바꾸면 결과가 다시 계산되고, 비교약·질환·평가변수 묶음을 바꾸면 표가 새로 만들어집니다"
                   "(고친 내용은 사라짐)")
    comp_disp = comp

    # ── ② 입력표 ──────────────────────────────────────────────
    st.markdown(f"##### ② 합칠 임상 확인 <span style='font-weight:400;color:#808495;font-size:13.5px'>"
                f"— A = {a_name} · B = {b_name} · 비교군 = {comp_disp} · 표 안에서 체크·숫자를 바로 고칠 수 있습니다</span>",
                unsafe_allow_html=True)
    base = st.session_state[bkey]
    cfg = {
        "합침": st.column_config.CheckboxColumn("합침", default=True, width="small"),
        "비교": st.column_config.SelectboxColumn("비교", options=_MA_SIDES, default=ctgov.SIDE_A, required=True),
        "시험군 용량": st.column_config.TextColumn("시험군 용량", help="군 이름에서 읽은 용량 (고칠 수 있음)"),
        "대조군 용량": st.column_config.TextColumn("대조군 용량", help="군 이름에서 읽은 용량 (고칠 수 있음)"),
        "임상번호": st.column_config.TextColumn("임상번호"),
        "제목": st.column_config.TextColumn("제목", width="medium"),
        "질환": st.column_config.TextColumn("질환", width="medium", help="ClinicalTrials.gov 에 등록된 대상 질환"),
        "평가변수": st.column_config.TextColumn("평가변수", width="large", help="숫자를 읽어 온 평가변수 이름 (등록 원문)"),
        "시점": st.column_config.TextColumn("시점", help="평가 시점 (등록 원문)"),
        "단위": st.column_config.TextColumn("단위"),
        "구분": st.column_config.SelectboxColumn("구분", options=["1차", "2차", ""], help="1차 평가변수인지 2차인지"),
        "점검": st.column_config.TextColumn("점검", disabled=True),
        "논문": st.column_config.TextColumn("논문"),
        "링크": st.column_config.LinkColumn("링크", display_text="PubMed ↗"),
        "소그룹": st.column_config.TextColumn("소그룹", help="소그룹 비교에 씁니다 (예: 지역, 바탕 치료)"),
    }
    for col, _ in _MA_NUM[kind]:
        cfg[col] = st.column_config.NumberColumn(col, format="%.4g")
    edited = st.data_editor(base, key=f"ma_ed_{kind}_{st.session_state.get(f'ma_ver_{kind}', 0)}",
                            column_config=cfg, num_rows="dynamic", hide_index=True, use_container_width=True)
    _sum = _ma_ep_summary(edited)
    if _sum:
        st.caption(_sum)

    b1, b2, b3 = st.columns([1, 1, 1])
    b1.download_button("📥 입력표 CSV 내려받기",
                       data=edited.assign(_종류=kind, _A=a_name, _B=b_name).to_csv(index=False).encode("utf-8-sig"),
                       file_name="meta_input.csv", mime="text/csv", key="ma_dl_in", use_container_width=True)
    up = b2.file_uploader("📤 CSV 올리기", type=["csv"], key=f"ma_up_{kind}", label_visibility="collapsed")
    if up is not None and st.session_state.get(f"ma_upid_{kind}") != (up.name, up.size):
        st.session_state[f"ma_upid_{kind}"] = (up.name, up.size)
        try:
            u = pd.read_csv(up, encoding="utf-8-sig")
            need = [c for c, _ in _MA_NUM[kind]] + ["비교"]
            miss = [x for x in need if x not in u.columns]
            if miss:
                st.error(f"이 CSV에는 '{kind}' 입력표 칸이 없습니다: {', '.join(miss)}")
            else:
                rows = u.where(pd.notna(u), None).to_dict("records")
                for r_ in rows:
                    r_["비교"] = _MA_OLD_SIDES.get(r_.get("비교"), r_.get("비교"))
                _ma_set_base(kind, _ma_frame(kind, rows))
                st.rerun()
        except Exception as e:
            st.error(f"CSV를 읽지 못했습니다 ({type(e).__name__}).")
    if b3.button("🧩 용량 짝으로 소그룹 나누기", key="ma_dosegrp", use_container_width=True,
                 help="소그룹 칸을 '시험군 용량 vs 대조군 용량'으로 채웁니다. 결과 아래 '소그룹 비교'에서 따로 합친 값을 봅니다."):
        df = edited.copy()
        df["소그룹"] = [f"{(a_ or '?')} vs {(b_ or '?')}" for a_, b_ in zip(df["시험군 용량"], df["대조군 용량"])]
        _ma_set_base(kind, _ma_frame(kind, df.to_dict("records")))
        st.rerun()

    if cur and any((cur.get("skipped") or {}).values()):
        with st.expander("빠진 임상 보기 (사유별)"):
            for k, nm in (("A", res["a"]), ("B", res["b"])):
                sk = cur["skipped"].get(k) or {}
                if sk:
                    st.markdown(f"**{nm}** — " + " · ".join(f"{w} {n}개" for w, n in
                                                           sorted(sk.items(), key=lambda x: -x[1])))
            st.caption("1차·2차 평가변수 중 지표 이름·시점·종류가 맞는 것이 없거나, 그 성분 군 또는 고른 비교약 군이 없는 임상입니다.")

    infos = {}
    if cur and st.session_state.get(f"ma_cur_{kind}"):
        _gk = st.session_state[f"ma_cur_{kind}"][2]
        for _k, _v in (cur.get("infos") or {}).items():
            _n, _sd, _g = _k.split("|", 2)
            if _g == _gk:
                infos[f"{_n}|{_sd}"] = _v
    if infos:
        with st.expander("군 바꾸기 (ClinicalTrials.gov에서 불러온 임상)"):
            keys = [f"{r['임상번호']}|{r['비교']}" for r in edited.to_dict("records")
                    if f"{r['임상번호']}|{r['비교']}" in infos]
            if keys:
                pk = st.selectbox("임상", keys, key="ma_rg_pick",
                                  format_func=lambda k: f"{k.split('|')[0]} · {k.split('|')[1]} · {infos[k]['title'][:60]}")
                inf = infos[pk]
                st.caption(f"평가변수: {inf['outcome'][:120]} · {inf['timeframe'][:60]} · 단위 {inf['unit']}")
                gids = [g for g, _ in inf["groups"]]
                gname = dict(inf["groups"])
                q1, q2, q3 = st.columns([2, 2, 1])
                g1 = q1.selectbox("시험군", gids, format_func=lambda g: gname.get(g, g), key="ma_rg_g1")
                g2 = q2.selectbox("대조군", gids, index=min(1, len(gids) - 1),
                                  format_func=lambda g: gname.get(g, g), key="ma_rg_g2")
                with q3:
                    st.write("")
                    if st.button("적용", key="ma_rg_go", use_container_width=True):
                        new = ctgov.regroup(kind, inf, g1, g2)
                        df = edited.copy()
                        nct, side = pk.split("|")
                        hit = (df["임상번호"] == nct) & (df["비교"] == side)
                        for col, k in _MA_NUM[kind]:
                            df.loc[hit, col] = new.get(k)
                        df.loc[hit, "시험군"] = gname.get(g1, g1)
                        df.loc[hit, "대조군"] = gname.get(g2, g2)
                        df.loc[hit, "시험군 용량"] = ctgov.dose_of(gname.get(g1, ""))
                        df.loc[hit, "대조군 용량"] = ctgov.dose_of(gname.get(g2, ""))
                        df.loc[hit, "합침"] = True
                        _ma_set_base(kind, _ma_frame(kind, df.to_dict("records")))
                        st.rerun()

    with st.expander("④ PubMed에서 논문 찾아 넣기"):
        p1, p2, p3, p4 = st.columns([2.2, 1.4, .8, .7])
        pq = p1.text_input("찾을 말 (영문)", key="ma_pq",
                           placeholder=f"예: {(a_txt or 'ezetimibe').strip()} placebo")
        top = p2.radio("학술지", ["NEJM·Lancet·JAMA·BMJ", "전체"], horizontal=True, key="ma_ptop") != "전체"
        with p3:
            st.write("")
            rct = st.checkbox("RCT만", value=True, key="ma_prct")
        with p4:
            st.write("")
            pgo = st.button("찾기", key="ma_pgo", use_container_width=True)
        if pgo and (pq or "").strip():
            try:
                st.session_state["ma_pm"] = _ma_pubsearch(pq.strip(), top, rct)
            except Exception as e:
                st.error(f"PubMed에 접속하지 못했습니다 ({type(e).__name__}).")
        pm = st.session_state.get("ma_pm")
        if pm:
            total, items = pm
            st.caption(f"{total:,}건 중 {len(items)}건 · [+ 줄 추가]를 누르면 표에 빈 줄이 생기고, 숫자는 논문을 보고 넣습니다")
            for it in items:
                r1, r2 = st.columns([8, 1])
                free = " · 무료 전문" if it.get("pmc") else ""
                r1.markdown(f"{it['year']} · *{it['journal']}* · [{it['title']}]"
                            f"({ctgov.pubmed_link(pmid=it['pmid'])}){free}")
                if r2.button("+ 줄 추가", key=f"ma_padd_{it['pmid']}", use_container_width=True):
                    tl = it["title"].lower()
                    side = ctgov.SIDE_B if (b_txt and b_txt.split()[0].lower() in tl
                                            and not (a_txt and a_txt.split()[0].lower() in tl)) else ctgov.SIDE_A
                    rows = edited.to_dict("records") + [{
                        "합침": True, "비교": side, "임상번호": "", "제목": it["title"][:120],
                        "점검": "직접 입력", "논문": f"{it['journal']} {it['year']}".strip(),
                        "링크": ctgov.pubmed_link(pmid=it["pmid"]), "소그룹": ""}]
                    _ma_set_base(kind, _ma_frame(kind, rows))
                    st.rerun()

    # ── ⑤ 결과 ────────────────────────────────────────────────
    st.markdown("##### ⑤ 결과")
    if res and grp_label:
        st.caption(f"지금 결과는 **{grp_label}** 묶음만 합친 값입니다"
                   + (f" (이번 찾기에서 평가변수 묶음 {n_grp}개 · 다른 묶음은 위 '평가변수 묶음' 표에서 고르세요)" if n_grp > 1 else "")
                   + ". 표에서 줄을 고치거나 더하면 그 줄도 함께 합칩니다.")
    top_box = st.container()
    left, side = st.columns([1.6, 1], gap="large")
    with side:
        method = st.radio("합치는 방법", ["고정효과", "랜덤효과"], index=1, horizontal=True, key="ma_method")
        measure = st.radio("효과크기", meta_calc.MEASURES[kind], horizontal=True, key=f"ma_meas_{kind}",
                           format_func=lambda m: meta_calc.MEASURE_LABEL[m])

    per, notes, used = {s: [] for s in _MA_SIDES}, [], {s: set() for s in _MA_SIDES}
    finals = 0
    for r in edited.to_dict("records"):
        if not r.get("합침") or r.get("비교") not in per:
            continue
        y, v, how = _ma_effect(kind, measure, r)
        nm = r.get("임상번호") or (r.get("제목") or "")[:20] or "(이름 없음)"
        if y is None:
            notes.append(f"{nm} ({r['비교']}): {how}")
            continue
        per[r["비교"]].append(dict(label=nm, y=y, v=v, sub=(r.get("소그룹") or "").strip() or "(없음)",
                                   title=r.get("제목") or "", cd=(r.get("대조군 용량") or "").strip(),
                                   wk=_ma_weeks(r.get("시점")), pri=(r.get("구분") or "").strip()))
        used[r["비교"]].add(r.get("임상번호") or "")
        if "최종값" in (r.get("점검") or ""):
            finals += 1

    pooled = {}
    for s_, lst in per.items():
        if lst:
            p = meta_calc.pool([x["y"] for x in lst], [x["v"] for x in lst])
            m, se, w = meta_calc.pick(p, method)
            pooled[s_] = dict(p=p, m=m, se=se, w=w)

    if not any(per.values()):
        top_box.info("임상을 찾거나 표에 숫자를 넣으면 결과가 나옵니다.")
        if notes:
            st.caption("계산에서 빠진 줄: " + " / ".join(notes[:10]))
        return

    ind = None
    if ctgov.SIDE_A in pooled and ctgov.SIDE_B in pooled:
        pa, pb = pooled[ctgov.SIDE_A], pooled[ctgov.SIDE_B]
        ind = meta_calc.bucher(pa["m"], pa["se"], pb["m"], pb["se"])
    dct = pooled.get(ctgov.SIDE_D)

    with top_box:
        k = st.columns(4)

        def _met(col, title, pdict):
            if not pdict:
                col.metric(title, "—")
                return
            mm, lo, hi = meta_calc.ci(measure, pdict["m"], pdict["se"])
            col.metric(title, meta_calc.fmt(measure, mm))
            col.caption(f"{meta_calc.fmt(measure, lo)} ~ {meta_calc.fmt(measure, hi)} · 임상 {pdict['p']['k']} · "
                        f"I² {pdict['p']['i2']:.0f}%")
        _met(k[0], f"{a_name} vs {comp_disp}", pooled.get(ctgov.SIDE_A))
        _met(k[1], f"{b_name} vs {comp_disp}", pooled.get(ctgov.SIDE_B))
        if ind:
            mm, lo, hi = meta_calc.ci(measure, ind[0], ind[1])
            k[2].metric("간접 비교 (A − B)" if measure not in meta_calc.RATIO else "간접 비교 (A / B)",
                        meta_calc.fmt(measure, mm))
            k[2].caption(f"{meta_calc.fmt(measure, lo)} ~ {meta_calc.fmt(measure, hi)} · p {ind[2]:.2f}")
        else:
            k[2].metric("간접 비교", "—")
        _met(k[3], "직접 비교 (같은 임상)", dct)
        if measure == "MD":
            st.caption("평균차는 넣은 숫자의 단위를 따릅니다 (예: 변화율이면 %p).")
        elif measure == "RD":
            st.caption("위험차는 %p 로 보여 줍니다.")

    items = []
    for s_, head, nm in ((ctgov.SIDE_A, f"<b>{a_name} vs {comp_disp}</b>", "A"),
                         (ctgov.SIDE_B, f"<b>{b_name} vs {comp_disp}</b>", "B"),
                         (ctgov.SIDE_D, f"<b>같은 임상 안 직접 비교 ({a_name} − {b_name})</b>", "D")):
        if not per[s_]:
            continue
        items.append(dict(kind="head", label=head))
        w = pooled[s_]["w"]
        for x, wi in zip(per[s_], w):
            items.append(dict(kind="study", label=f"{x['label']} · {x['title'][:22]}", y=x["y"],
                              se=math.sqrt(x["v"]), w=wi))
        items.append(dict(kind="pool", label=f"<b>합친 값 ({method})</b>", y=pooled[s_]["m"],
                          se=pooled[s_]["se"], color="#7a5aa6" if nm == "D" else "#2f6f8f"))
    if ind:
        items.append(dict(kind="head", label=f"<b>간접 비교 ({a_name} − {b_name}, {comp_disp} 공통)</b>"))
        items.append(dict(kind="pool", label="<b>간접 비교</b>", y=ind[0], se=ind[1], color="#b4572e"))
    with left:
        st.plotly_chart(_ma_forest(items, measure), use_container_width=True, key="ma_forest")

    with side:
        msgs = []
        if ind:
            msgs.append(meta_calc.verdict(measure, ind[0], ind[1], lower_better, a_name, b_name, what))
        if dct:
            msgs.append("같은 임상 안 직접 비교 — "
                        + meta_calc.verdict(measure, dct["m"], dct["se"], lower_better, a_name, b_name, what))
        if not msgs:
            msgs.append(f"두 성분을 비교하려면 A vs {comp_disp}, B vs {comp_disp} 임상이 모두 있거나, "
                        "같은 임상 안 직접 비교가 있어야 합니다.")
        st.info("**결론**\n\n" + "\n\n".join(msgs))
        warns = []
        for s_, nm in ((ctgov.SIDE_A, a_name), (ctgov.SIDE_B, b_name), (ctgov.SIDE_D, "직접 비교")):
            if s_ in pooled:
                kk, i2 = pooled[s_]["p"]["k"], pooled[s_]["p"]["i2"]
                if i2 >= 50:
                    warns.append(f"{nm}: 임상 사이 차이가 큰 편입니다 (I² {i2:.0f}%). 개별 결과도 함께 보세요.")
                if kk == 1 and s_ != ctgov.SIDE_D:
                    warns.append(f"{nm}: 임상이 1개뿐이라 이 결과는 그 임상 하나에 좌우됩니다.")
                elif kk == 2 and method == "랜덤효과" and s_ != ctgov.SIDE_D:
                    warns.append(f"{nm}: 임상이 {kk}개뿐이라 랜덤효과 추정이 불안정합니다.")
        if comp_disp != ctgov.PLACEBO:
            for s_, nm in ((ctgov.SIDE_A, a_name), (ctgov.SIDE_B, b_name)):
                ds = sorted({x["cd"] for x in per[s_] if x["cd"]})
                if len(ds) > 1:
                    warns.append(f"{nm}: 비교약({comp_disp}) 용량이 임상마다 다릅니다 ({', '.join(ds)}). "
                                 "[용량 짝으로 소그룹 나누기]로 나눠 보세요.")
        shared = (used[ctgov.SIDE_A] & used[ctgov.SIDE_B]) - {""}
        if shared and ind:
            warns.append(f"{', '.join(sorted(shared))} 은(는) 두 성분에 같이 쓰였습니다 (같은 비교군). "
                         "간접 비교가 실제보다 약간 확실해 보일 수 있습니다.")
        for s_, nm in ((ctgov.SIDE_A, a_name), (ctgov.SIDE_B, b_name), (ctgov.SIDE_D, "직접 비교")):
            wks = sorted({x["wk"] for x in per[s_] if x["wk"] is not None})
            if len(wks) > 1 and wks[-1] >= wks[0] * 1.5:
                warns.append(f"{nm}: 평가 시점이 임상마다 다릅니다 ({', '.join(_ma_wk_txt(w) for w in wks[:5])}). "
                             "시점이 크게 다르면 따로 보세요.")
            if {"1차", "2차"} <= {x["pri"] for x in per[s_]}:
                warns.append(f"{nm}: 1차 평가변수와 2차 평가변수가 섞여 있습니다.")
        wa = sorted({x["wk"] for x in per[ctgov.SIDE_A] if x["wk"] is not None})
        wb = sorted({x["wk"] for x in per[ctgov.SIDE_B] if x["wk"] is not None})
        if ind and wa and wb and len(wa) == 1 and len(wb) == 1 and max(wa[0], wb[0]) >= min(wa[0], wb[0]) * 1.5:
            warns.append(f"{a_name}({_ma_wk_txt(wa[0])})과 {b_name}({_ma_wk_txt(wb[0])})의 평가 시점이 달라 "
                         "간접 비교가 기울 수 있습니다.")
        if measure == "SMD" and finals:
            warns.append("SMD 에 '최종값' 임상이 섞여 있습니다. 변화량과 최종값은 섞지 않는 것이 원칙입니다.")
        if ind and dct:
            ilo, ihi = meta_calc.ci(measure, ind[0], ind[1])[1:]
            dlo, dhi = meta_calc.ci(measure, dct["m"], dct["se"])[1:]
            nul = meta_calc.null_value(measure)
            if (ilo <= nul <= ihi) != (dlo <= nul <= dhi):
                warns.append("직접 비교와 간접 비교의 결론이 다릅니다. 임상 대상·바탕 치료를 확인하세요.")
        for w_ in warns:
            st.warning(w_, icon="⚠️")
        if notes:
            st.caption("계산에서 빠진 줄: " + " / ".join(notes[:8]) + (" …" if len(notes) > 8 else ""))

    with st.expander("하나씩 빼 보기 — 임상 하나씩 빼고 다시 계산"):
        out = []
        for s_ in _MA_SIDES:
            lst = per[s_]
            if len(lst) < 2:
                continue
            loo = meta_calc.leave_one_out([x["y"] for x in lst], [x["v"] for x in lst], method)
            for x, r_ in zip(lst, loo):
                out.append({"비교": s_, "뺀 임상": x["label"], "합친 값 [95% CI]": meta_calc.fmt_ci(measure, r_[0], r_[1]),
                            "I²(%)": round(r_[2])})
        if out:
            st.dataframe(pd.DataFrame(out), hide_index=True, use_container_width=True)
        else:
            st.caption("임상이 2개 이상인 쪽이 없습니다.")

    with st.expander("깔때기 그림 — 작은 임상이 한쪽으로 쏠렸는지"):
        fig = go.Figure()
        for s_, color in ((ctgov.SIDE_A, "#2f6f8f"), (ctgov.SIDE_B, "#b4572e")):
            lst = per[s_]
            if not lst:
                continue
            fig.add_trace(go.Scatter(x=[_ma_disp(measure, x["y"]) for x in lst], y=[math.sqrt(x["v"]) for x in lst],
                                     mode="markers", name=a_name if s_.startswith("A") else b_name,
                                     text=[x["label"] for x in lst], marker=dict(size=9, color=color)))
            fe = pooled[s_]["p"]["fe"]
            mx = max(math.sqrt(x["v"]) for x in lst) * 1.1
            fig.add_trace(go.Scatter(x=[_ma_disp(measure, fe - meta_calc.Z * mx), _ma_disp(measure, fe),
                                        _ma_disp(measure, fe + meta_calc.Z * mx)], y=[mx, 0, mx], mode="lines",
                                     line=dict(color=color, dash="dot", width=1), showlegend=False, hoverinfo="skip"))
        fig.update_yaxes(autorange="reversed", title_text="표준오차 (아래로 갈수록 작은 임상)")
        fig.update_xaxes(type="log" if measure in meta_calc.RATIO else "linear",
                         title_text=meta_calc.MEASURE_LABEL[measure])
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=40), plot_bgcolor="#fff")
        st.plotly_chart(fig, use_container_width=True, key="ma_funnel")
        st.caption("임상이 10개 미만이면 쏠림을 판단하기 어렵습니다. 점이 삼각형 안에 고르게 퍼져 있으면 무난합니다.")

    with st.expander("소그룹 비교 — 표의 '소그룹' 칸 기준"):
        out = []
        for s_ in _MA_SIDES:
            groups = {}
            for x in per[s_]:
                groups.setdefault(x["sub"], []).append(x)
            if len(groups) < 2:
                continue
            for g_, lst in groups.items():
                p = meta_calc.pool([x["y"] for x in lst], [x["v"] for x in lst])
                m, se, _ = meta_calc.pick(p, method)
                out.append({"비교": s_, "소그룹": g_, "임상 수": p["k"],
                            "합친 값 [95% CI]": meta_calc.fmt_ci(measure, m, se), "I²(%)": round(p["i2"])})
        if out:
            st.dataframe(pd.DataFrame(out), hide_index=True, use_container_width=True)
        else:
            st.caption("소그룹이 2개 이상인 쪽이 없습니다. 표의 '소그룹' 칸에 지역·바탕 치료 등을 적으면 나뉩니다.")

    res_rows = []
    for s_ in _MA_SIDES:
        for x, wi in zip(per[s_], pooled.get(s_, {}).get("w", [])):
            res_rows.append({"비교": s_, "임상": x["label"], "효과 [95% CI]": meta_calc.fmt_ci(measure, x["y"], math.sqrt(x["v"])),
                             "가중치(%)": round(wi, 1)})
        if s_ in pooled:
            res_rows.append({"비교": s_, "임상": f"합친 값 ({method})",
                             "효과 [95% CI]": meta_calc.fmt_ci(measure, pooled[s_]["m"], pooled[s_]["se"]),
                             "가중치(%)": 100.0})
    if ind:
        res_rows.append({"비교": "간접 비교 (A−B)", "임상": f"{a_name} vs {b_name}",
                         "효과 [95% CI]": meta_calc.fmt_ci(measure, ind[0], ind[1]), "가중치(%)": None})
    st.download_button("📥 결과 CSV 내려받기",
                       data=pd.DataFrame(res_rows).to_csv(index=False).encode("utf-8-sig"),
                       file_name="meta_result.csv", mime="text/csv", key="ma_dl_out")


