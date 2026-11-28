"""약물 재창출 — 제품 제안의 세 번째 보기 (v2). 지금 사이트(scouting.py)와 같은 코드.
매출 칸만 v2 방식(DuckDB 집계)으로 받는다."""
import re
import urllib.parse

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import scout_sources as ss
from core import data
from core.keys import kor2engkey as _kor2engkey

TODAY = pd.Timestamp.today().normalize()
HAS_REG = data.has("approval") and data.has("patent")
HAS_PAT = data.has("patent")

import repurpose

# ══════════════════════════ 약물 재창출 — 제품 제안 탭의 세 번째 보기 ══════════════════════════
# Open Targets 공개 자료로 '약 → 표적 단백질 → 질환' 지도를 그린다(계산은 repurpose.py).
# 버튼을 누를 때만 밖에 묻고, 받은 결과는 6시간 기억한다. 미리 받아 두는 파일은 없다.
_RP_OK, _RP_CAND, _RP_BAD, _RP_GREY, _RP_PURPLE = "#2C7A4B", "#2A5B8C", "#B0281F", "#6B7773", "#5B3596"
_RP_TRIAL_KO = {"RECRUITING": "모집 중", "NOT_YET_RECRUITING": "모집 예정", "ACTIVE_NOT_RECRUITING": "진행 중(모집 끝)",
                "ENROLLING_BY_INVITATION": "초청 모집"}


@st.cache_data(ttl=6 * 3600, max_entries=30, show_spinner=False)
def _rp_find(mode, q):
    return repurpose.find_drug(q) if mode == "drug" else repurpose.find_disease([x.strip() for x in q.split(",")])


@st.cache_data(ttl=6 * 3600, max_entries=8, show_spinner=False)
def _rp_fetch(mode, oid):
    return repurpose.fetch_drug(oid) if mode == "drug" else repurpose.fetch_disease(oid)


@st.cache_data(ttl=24 * 3600, max_entries=1, show_spinner=False)
def _rp_version():
    try:
        v = repurpose.gql("{meta{dataVersion{year month}}}")["meta"]["dataVersion"]
        return f"20{v['year']}년 {int(v['month'])}월"
    except Exception:
        return ""


def _ap_n():
    """허가 자료 판(파일 수정 시각) — 캐시 열쇠. 표를 올리지 않는다."""
    return int(data._mtime(data.FILES["approval"]))


@st.cache_data(max_entries=1, show_spinner=False)
def _rp_site(_n):
    """허가 자료 → (성분키별 정상 허가 품목 수, 성분키별 한글 성분명, 한글 코어→성분키)."""
    # v2: 허가 표 전체 대신 쓰는 칸만 잠깐 읽는다
    ap = ss.load_slim("approval", ["상태", "주성분", "주성분(영문)", "_key"])
    if "_key" not in ap.columns:
        ap["_key"] = ss.ing_key_series(ap["주성분(영문)"])
    act = ap[ap["상태"] == "정상"] if "상태" in ap.columns else ap
    cnt = act[act["_key"].astype(str).str.len() > 0].groupby("_key").size().to_dict()
    one = ss.is_single_series(ap["주성분"], ap["주성분(영문)"]) & (ap["_key"].astype(str).str.len() > 0)
    sub = pd.DataFrame({"k": ap.loc[one, "_key"].astype(str), "c": ss.ing_core_series(ap.loc[one, "주성분"])})
    sub = sub[sub["c"].str.len() >= 2]
    top = sub.groupby(["k", "c"]).size().reset_index(name="n").sort_values("n")
    kor = dict(zip(top.drop_duplicates("k", keep="last")["k"], top.drop_duplicates("k", keep="last")["c"]))
    c2k = dict(zip(top.drop_duplicates("c", keep="last")["c"], top.drop_duplicates("c", keep="last")["k"]))
    return cnt, kor, c2k


@st.cache_data(max_entries=1, show_spinner=False)
def _rp_patent(_n):
    """성분키 → 존속 중인 등재 특허의 가장 늦은 만료일 (오리지널 품목 특허를 우선)."""
    out = {}
    for kw in ({"single_only": True}, {"single_only": True, "original_only": True}):
        try:
            pb = ss.patent_by_key(**kw)
            out.update({k: v for k, v in zip(pb["_key"], pb["특허만료_전체"]) if k and pd.notna(v)})
        except Exception:
            pass
    return out


@st.cache_data(max_entries=1, show_spinner=False)
def _rp_kr_trials(_n):
    """성분키 → (식약처 임상 승인 건수, 가장 최근 승인일). 필요한 열만 잠깐 읽는다."""
    try:
        cl = ss.load_slim("clinical", ["성분명", "CLST_APRV_DT"])
        if cl is None or cl.empty:
            return {}
        _, _, c2k = _rp_site(_ap_n())
        k = ss.ing_core_series(cl["성분명"]).map(c2k).fillna("")
        d = pd.DataFrame({"k": k, "d": cl["CLST_APRV_DT"].astype(str).str[:8]})
        d = d[d["k"].str.len() > 0]
        g = d.groupby("k")["d"]
        return {a: (int(n), m) for a, n, m in zip(g.size().index, g.size().values, g.max().values)}
    except Exception:
        return {}


def has_sales(src):
    """매출 자료 파일이 준비돼 있는지만 확인한다(받아오지 않음)."""
    k = src.lower()
    return data.FILES[k].exists() or (data.SAVED / f"{k}.pkl").exists()


@st.cache_data(max_entries=2, show_spinner=False)
def _rp_sales(src, _mt=0):
    """성분키 → 최근 연도 매출(원). v2: 매출 표를 올리지 않고 DuckDB 로 성분별 최근 연도 합계만 꺼낸다."""
    try:
        S, cfg, yr = data.sales(src)
        if S is None or not yr:
            return {}, ""
        m = data.q(f'''SELECT "{cfg["ing"]}" AS n, sum(TRY_CAST("{yr[-1]}" AS DOUBLE)) AS v
                       FROM {S} WHERE "{cfg["ing"]}" IS NOT NULL GROUP BY 1''')
        nm = m["n"].astype(str)
        k = ss.ing_key_series(nm)
        blank = k.str.len() == 0
        c2k, _ = _kor2engkey()
        k.loc[blank] = ss.ing_core_series(nm[blank]).map(c2k).fillna("")
        s = pd.DataFrame({"k": k, "v": pd.to_numeric(m["v"], errors="coerce")})
        s = s[s["k"].str.len() > 0]
        return s.groupby("k")["v"].sum().to_dict(), str(yr[-1])
    except Exception:
        return {}, ""


def _rp_disease_terms(q):
    """한글 병명 → 영문 이름 (정해진 사전, AI 없음). 영문이면 그대로, 사전에 없으면 빈 값."""
    return repurpose.ko_disease(q)


def _rp_drug_term(q):
    """한글 약 이름 → 영문 성분명 (허가 자료). 영문이면 그대로."""
    q = q.strip()
    if not re.search(r"[가-힣]", q):
        return q
    try:
        ap = ss.load_slim("approval", ["주성분", "주성분(영문)"])
        hit = ap[ap["주성분"].astype(str).str.replace(r"\s+", "", regex=True).str.contains(q.replace(" ", ""), regex=False, na=False)]
        ks = ss.ing_key_series(hit["주성분(영문)"])
        ks = ks[ks.str.len() > 0]
        if len(ks):
            return ks.value_counts().index[0].lower()
    except Exception:
        pass
    return ""


def _rp_edge(fig, x0, y0, x1, y1, color, width, dash="solid"):
    ts = (0, .12, .3, .5, .7, .88, 1)
    fig.add_trace(go.Scatter(x=[x0 + (x1 - x0) * t for t in ts], y=[y0 + (y1 - y0) * (3 * t * t - 2 * t ** 3) for t in ts],
                             mode="lines", line=dict(color=color, width=width, dash=dash, shape="spline"),
                             hoverinfo="skip", showlegend=False))


_RP_RX = 1.72      # 오른쪽 칸은 이 위치에서 오른쪽으로 펼친다(긴 이름이 잘리지 않게)


def _rp_node(fig, x, y, text, border, nid, hover, bg="#FFFFFF", bw=2, size=14, badge=None, dash=False):
    rightside = x >= _RP_RX
    fig.add_annotation(x=x, y=y, text=text, showarrow=False, font=dict(size=size, color="#262730"),
                       bgcolor=bg, bordercolor=border, borderwidth=bw, borderpad=8, align="center",
                       xanchor="left" if rightside else "center")
    fig.add_trace(go.Scatter(x=[x + (0.12 if rightside else 0)], y=[y], mode="markers", customdata=[nid],
                             hovertext=[hover], hoverinfo="text",
                             marker=dict(size=34, color="rgba(0,0,0,0.01)"), showlegend=False))
    if badge:
        fig.add_annotation(x=x + (0.02 if rightside else 0.15), y=y + 0.38, text=badge, showarrow=False,
                           xanchor="left" if rightside else "center",
                           font=dict(size=11, color="#FFFFFF"), bgcolor=_RP_PURPLE, borderpad=3)


def _rp_short(s, n=34):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _rp_esc(s):
    return str(s or "").replace("<", "&lt;").replace(">", "&gt;")


def _rp_ongoing_box(reps):
    lines = []
    for r in reps[:5]:
        stt = _RP_TRIAL_KO.get((r.get("trialOverallStatus") or "").upper(), r.get("trialOverallStatus") or "")
        ph = repurpose.stage_ko(r.get("clinicalStage") or "")
        nid = (r.get("id") or "").upper()
        url = r.get("url") or (f"https://clinicaltrials.gov/study/{nid}" if nid.startswith("NCT") else "")
        lines.append(f"<a href='{url}' target='_blank'>{_rp_esc(nid)}</a> · {_rp_esc(ph)} · {_rp_esc(stt)}"
                     f" · 시작 {_rp_esc((r.get('trialStartDate') or '')[:7]) or '?'}<br>"
                     f"<span style='color:#555867'>{_rp_esc((r.get('title') or '')[:90])}</span>")
    st.markdown("<div style='border:1px solid #D9CCEC;background:#F6F2FB;border-radius:8px;padding:10px 12px;"
                "font-size:13px;line-height:1.55'><b style='color:#3F2370'>지금 진행 중</b><br>" + "<br>".join(lines)
                + ("<br>…" if len(reps) > 5 else "") + "</div>", unsafe_allow_html=True)


def _rp_bars(dts):
    for k, v in sorted(dts.items(), key=lambda x: -x[1]):
        st.progress(float(max(0.0, min(1.0, v))), text=f"{repurpose.DATATYPE_KO.get(k, k)} {v:.2f}")


def _repurpose_view(src):
    ss_state = st.session_state
    st.markdown("#### ④ 약물 재창출 — 새 적응증 찾기")
    _ver = _rp_version()
    st.caption("Open Targets 공개 자료" + (f"({_ver} 판)" if _ver else "") + "와 사이트의 특허·허가·식약처 임상 자료를 묶어 그립니다. "
               "가설 탐색용이며 효능 근거가 아닙니다.")
    # 지도에서 '이 질환/약으로 다시 그리기'를 누르면 여기서 입력칸을 바꿔 둔다(위젯을 만들기 전이라 안전)
    _pv = ss_state.pop("rp_pivot", None)
    if _pv:
        ss_state["rp_mode"] = "🩺 질환으로" if _pv["mode"] == "disease" else "💊 약으로"
        ss_state["rp_q_" + _pv["mode"]] = _pv["name"]
        ss_state["rp_query"] = (_pv["mode"], _pv["name"], _pv["id"])
    mode_l = st.radio("무엇으로 찾을까", ["💊 약으로", "🩺 질환으로"], horizontal=True, key="rp_mode")
    mode = "drug" if mode_l.startswith("💊") else "disease"
    a, b, c, d = st.columns([1.4, 1, 1, 1])
    if mode == "drug":
        q = a.text_input("약 이름 (한글·영문)", key="rp_q_drug", placeholder="예: 실데나필, gabapentin")
        n_t = b.slider("표적 수 (최대)", 1, 10, 5, key="rp_nt1")
        n_p = c.slider("표적당 질환 수", 3, 20, 8, key="rp_np1")
        min_s = d.slider("최소 연결 점수", 0.0, 1.0, 0.2, 0.05, key="rp_ms1",
                         help="Open Targets 의 단백질–질환 연결 점수(0~1). 낮으면 약한 근거까지, 높으면 잘 알려진 연결만 보입니다.")
        e1, e2, e4 = st.columns([1, 1.2, 0.8])
        hide_ok = e1.checkbox("허가된 적응증 숨기기", key="rp_c1")
        only_on = e2.checkbox("진행 중 임상이 있는 질환만", key="rp_c3")
    else:
        q = a.text_input("질환 이름 (한글·영문)", key="rp_q_disease", placeholder="예: 기침, Raynaud disease")
        n_t = b.slider("표적 수 (최대)", 1, 12, 6, key="rp_nt2")
        n_p = c.slider("표적당 약 수", 1, 8, 3, key="rp_np2")
        dom_only = d.checkbox("국내 허가된 약만", True, key="rp_c4")
        pat_first = d.checkbox("특허 끝난 약을 위로", True, key="rp_c5")
        e1, e2, e4 = st.columns([1, 1.2, 0.8])
        with_new = e1.checkbox("임상 중인 신약도 보기", key="rp_c6")
        only_on = e2.checkbox("진행 중 임상이 있는 약만", key="rp_c7")
    if e4.button("🔎 지도 그리기", type="primary", use_container_width=True, key="rp_go"):
        ss_state["rp_query"] = (mode, (q or "").strip(), "")

    query = ss_state.get("rp_query")
    if not query or query[0] != mode or not query[1]:
        st.info("이름을 넣고 **🔎 지도 그리기**를 누르세요. 약은 한글로 넣어도 허가 자료에서 영문 성분명을 찾습니다. "
                "질환은 영문으로 넣거나, 자주 쓰는 한글 병명(예: 기침·고혈압·천식)은 정해진 사전으로 바꿉니다.")
        return
    _, qtext, force_id = query

    # ① 이름 → Open Targets 번호
    if mode == "drug":
        term = _rp_drug_term(qtext)
        if not term:
            st.warning(f"'{qtext}'을(를) 허가 자료에서 찾지 못했습니다. 영문 성분명으로 넣어 보세요.")
            return
    else:
        term = _rp_disease_terms(qtext)
        if not term:
            st.warning(f"'{qtext}'은(는) 한글 병명 사전에 없습니다. 영문 질환명으로 넣어 보세요. 예: cough, Raynaud disease")
            return
    try:
        cands = _rp_find(mode, term)
    except Exception as e:
        st.error(f"Open Targets 에 접속하지 못했습니다 — 잠시 뒤 다시 눌러 주세요. ({type(e).__name__})")
        return
    if force_id and force_id not in [x[0] for x in cands]:
        cands = [(force_id, qtext)] + list(cands)
    if not cands:
        st.warning(f"Open Targets 에서 '{term}'을(를) 찾지 못했습니다. 다른 이름으로 넣어 보세요.")
        return
    ids = [x[0] for x in cands][:8]
    nm = dict(cands)
    pick_key = f"rp_pick::{mode}::{qtext}"
    if force_id and pick_key not in ss_state:
        ss_state[pick_key] = force_id
    if len(ids) > 1:
        oid = st.radio("찾은 이름 (다른 뜻이면 고르세요)" + (f" — 검색어: {term}" if term != qtext else ""), ids,
                       format_func=lambda i: nm.get(i, i), horizontal=True, key=pick_key)
    else:
        oid = ids[0]
        if term != qtext:
            st.caption(f"검색어: {term} → {nm.get(oid, oid)}")

    # ② 받기 (6시간 기억)
    try:
        with st.spinner("Open Targets 에서 받는 중…"):
            raw = _rp_fetch(mode, oid)
    except Exception as e:
        st.error(f"Open Targets 에 접속하지 못했습니다 — 잠시 뒤 다시 눌러 주세요. ({type(e).__name__})")
        return
    if not raw:
        st.warning("자료를 받지 못했습니다. 다른 이름으로 넣어 보세요.")
        return

    # ③ 사이트 자료 (특허·허가·식약처 임상)
    try:
        _n = _ap_n()
        cnt, kor, _c2k = _rp_site(_n)
    except Exception:
        _n, cnt, kor = 0, {}, {}
    pat = _rp_patent(_n) if HAS_PAT else {}
    today = pd.Timestamp.today().normalize()

    def _pat_state(k):
        v = pat.get(k)
        if v is None or pd.isna(v):
            return "등재 특허 없음", False
        v = pd.Timestamp(v)
        return (f"특허 유효 (~{v:%Y-%m})", True) if v >= today else (f"특허 만료 ({v:%Y-%m})", False)

    def _drug_ko(name):
        k = repurpose.drug_key(name)
        return kor.get(k) or str(name or "").title()

    if mode == "drug":
        if not raw["targets"]:
            st.warning(f"Open Targets 에 {raw['name']}의 작용 표적(단백질) 정보가 없어 지도를 그릴 수 없습니다.")
            return
        g = repurpose.drug_graph(raw, n_t, n_p, min_s, hide_approved=hide_ok, only_ongoing=only_on)
    else:
        if not raw["targets"]:
            st.warning(f"Open Targets 에 {raw['name']}와(과) 연결된 표적 정보가 없습니다.")
            return
        keep = (lambda k: k in cnt) if (dom_only and cnt) else None
        rank = (lambda k: 1 if _pat_state(k)[1] else 0) if pat_first else None
        g = repurpose.disease_graph(raw, n_t, n_p, approved_only=not with_new, only_ongoing=only_on,
                                    keep_drug=keep, rank_drug=rank)
    if not g["right"]:
        st.info("조건에 맞는 칸이 없습니다. 최소 점수를 낮추거나 체크를 풀어 보세요.")
        return

    # 한글 병명은 정해진 사전에 있을 때만 덧붙인다(AI 없음). 단백질은 Open Targets 영문 이름·분류를 그대로 쓴다.
    ko = {n_: repurpose.ko_name(n_) for n_ in ([d_["name"] for d_ in g["right"]] if mode == "drug" else [raw["name"]])}

    # ④ 범례
    if mode == "drug":
        leg = [("solid", _RP_OK, "허가됨"), ("solid", _RP_CAND, "임상 있음 (최고 단계)"), ("solid", _RP_BAD, "중단 (효과 부족·안전성)"),
               ("dashed", _RP_GREY, "임상 없음 · 근거만 (재창출 후보)")]
    else:
        leg = [("solid", "#22705F", "국내 특허 만료·등재 특허 없음"), ("dashed", "#9AA3A0", "특허 유효"),
               ("solid", "#C27C0E", "이미 이 질환에 허가")]
    parts = [f"<span style='margin-right:16px;white-space:nowrap'><span style='display:inline-block;width:14px;height:14px;"
             f"border:2px {k} {c_};border-radius:3px;vertical-align:-2px'></span> {t}</span>" for k, c_, t in leg]
    parts.append(f"<span style='white-space:nowrap'><span style='font-size:11px;padding:1px 7px;background:{_RP_PURPLE};"
                 f"color:#fff;border-radius:8px'>진행 중 N</span> 지금 진행 중인 임상</span>")
    st.markdown("<div style='font-size:13px;color:#45485A;line-height:2'>" + "".join(parts) + "</div>", unsafe_allow_html=True)

    # ⑤ 지도 — 지난번에 누른 칸(차트 선택)을 먼저 읽어 그 칸을 강조한다
    sig = f"{mode}:{oid}:{n_t}:{n_p}"
    rkey = "id" if mode == "drug" else "key"
    opts = [d_[rkey] for d_ in g["right"]] + ["T:" + m["id"] for m in g["mid"]]
    try:
        _ev = ss_state.get("rp_chart::" + sig) or {}
        _pts = (_ev.get("selection") or {}).get("points") or []
        _cd = _pts[0].get("customdata") if _pts else None
        _cd = _cd[0] if isinstance(_cd, (list, tuple)) else _cd
        if _cd and _cd in opts and _cd != ss_state.get("rp_last_click"):
            ss_state["rp_last_click"] = _cd
            ss_state["rp_sel"] = _cd
    except Exception:
        pass
    if ss_state.get("rp_sel") not in opts:
        ss_state["rp_sel"] = opts[0]
    fig = go.Figure()
    L = g["left"]
    for m in g["mid"]:
        _rp_edge(fig, 0, L["y"], 1, m["y"], "#262730", 2 + 3 * float(m.get("score") or 1.0) if mode == "disease" else 4)
    ymap = {m["id"]: m["y"] for m in g["mid"]}
    for tid, rid, s in g["edges"]:
        d_ = next(x for x in g["right"] if x[rkey] == rid)
        if mode == "drug":
            col = {"ok": _RP_OK, "cand": _RP_CAND, "bad": _RP_BAD}.get(d_["kind"], _RP_GREY)
            dash = "solid" if d_["kind"] in ("ok", "cand") else "dash"
        else:
            col, dash = "#9AA3A0", ("dash" if _pat_state(d_["key"])[1] else "solid")
        _rp_edge(fig, 1, ymap[tid], _RP_RX, d_["y"], col, 1.5 + 4 * float(s), dash)
    if mode == "drug":
        _rp_node(fig, 0, L["y"], f"<b>{_rp_esc(_drug_ko(L['name']))}</b><br><span style='font-size:11px'>{_rp_esc(L['name'].lower())}</span>",
                 "#A8501C", "L", L["name"], bg="#F8E7DC", size=15)
    else:
        _ko_l = ko.get(raw["name"]) or ""
        _rp_node(fig, 0, L["y"], f"<b>{_rp_esc(_ko_l or raw['name'])}</b>" + (f"<br><span style='font-size:11px'>{_rp_esc(raw['name'])}</span>" if _ko_l else ""),
                 "#262730", "L", raw["name"], bg="#F1F2F5", size=15)
    for m in g["mid"]:
        sub = repurpose.class_ko(m.get("cls")) or "단백질"
        sub += f" · {repurpose.action_ko(m.get('action'))}" if mode == "drug" and m.get("action") else (
            f" · {m['score']:.2f}" if mode == "disease" else "")
        _rp_node(fig, 1, m["y"], f"<b><i>{_rp_esc(m['symbol'])}</i></b><br><span style='font-size:10.5px'>"
                 f"{_rp_esc(_rp_short(m['name'], 30))}</span><br><span style='font-size:11px'>{_rp_esc(sub)}</span>",
                 "#22705F", "T:" + m["id"], f"{m['symbol']} · {m['name']}", bg="#DFF0EA", size=14)
    sel = ss_state.get("rp_sel")
    for d_ in g["right"]:
        rid = d_[rkey]
        badge = f"진행 중 {len(d_['ongoing'])}" if d_["ongoing"] else None
        if mode == "drug":
            col = {"ok": _RP_OK, "cand": _RP_CAND, "bad": _RP_BAD}.get(d_["kind"], _RP_GREY)
            nm_ = ko.get(d_["name"]) or d_["name"]
            txt = f"{_rp_esc(_rp_short(nm_))}  <span style='font-size:11px;color:{col}'>{_rp_esc(d_['label'])}</span>"
            _rp_node(fig, _RP_RX, d_["y"], ("<b>" + txt + "</b>") if sel == rid else txt, col, rid,
                     f"{d_['name']} · 연결 점수 {d_['score']:.2f}", bg="#EAF1FA" if sel == rid else "#FFFFFF",
                     bw=3 if sel == rid else 2, badge=badge)
        else:
            active = _pat_state(d_["key"])[1]
            if d_["dz_stage"] == "APPROVAL":
                tag, tc = "이미 이 질환 허가", "#8A4B00"
            elif d_["dz_stage"]:
                tag, tc = "이 질환 " + repurpose.stage_ko(d_["dz_stage"]), "#1E4F7D"
            elif d_["key"] not in cnt:
                tag, tc = "국내 허가 없음", "#45485A"
            elif active:
                tag, tc = "특허 유효", "#45485A"
            else:
                tag, tc = f"국내 {cnt.get(d_['key'], 0)}품목", "#45485A"
            txt = f"{_rp_esc(_rp_short(_drug_ko(d_['name']), 26))}  <span style='font-size:11px;color:{tc}'>{_rp_esc(tag)}</span>"
            border = "#C27C0E" if d_["dz_stage"] == "APPROVAL" else ("#9AA3A0" if active else "#22705F")
            _rp_node(fig, _RP_RX, d_["y"], ("<b>" + txt + "</b>") if sel == rid else txt, "#1E4F7D" if sel == rid else border, rid,
                     f"{d_['name']} · {repurpose.stage_ko(d_['stage'])}", bg="#FFFFFF" if active else "#DFF0EA",
                     bw=3 if sel == rid else 2, badge=badge)
    n_right = len(g["right"])
    heads = [(0, "약" if mode == "drug" else "질환"), (1, "표적 단백질 (유전자 · 단백질)"),
             (_RP_RX + 0.45, "연결된 질환 (점수 순)" if mode == "drug" else "그 표적에 작용하는 약")]
    for x, t in heads:
        fig.add_annotation(x=x, y=1.0, yref="paper", yanchor="bottom", text=t, showarrow=False,
                           font=dict(size=12, color="#555867"))
    fig.update_layout(height=max(380, 64 * n_right + 90), margin=dict(l=10, r=10, t=34, b=10), plot_bgcolor="#FFFFFF",
                      xaxis=dict(visible=False, range=[-0.35, 3.05], fixedrange=True),
                      yaxis=dict(visible=False, range=[-0.7, max(n_right - 1, 1) + 0.7], fixedrange=True),
                      hoverlabel=dict(bgcolor="#FFFFFF"), dragmode=False, clickmode="event+select")

    left, right = st.columns([2.3, 1])
    with left:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                        on_select="rerun", selection_mode="points", key="rp_chart::" + sig)
        st.caption("선 굵기 = 연결 점수 · 칸에 마우스를 올리면 이름이, 누르면 오른쪽에 근거가 나옵니다.")

    def _lab(o):
        if o.startswith("T:"):
            m = next(x for x in g["mid"] if "T:" + x["id"] == o)
            return f"표적 · {m['symbol']}"
        d_ = next(x for x in g["right"] if x[rkey] == o)
        return (ko.get(d_["name"]) or d_["name"]) if mode == "drug" else _drug_ko(d_["name"])

    with right:
        with st.container(border=True):
            o = st.selectbox("자세히 볼 칸", opts, format_func=_lab, key="rp_sel")
            if o.startswith("T:"):
                m = next(x for x in g["mid"] if "T:" + x["id"] == o)
                st.markdown(f"### *{m['symbol']}*")
                st.markdown(f"**단백질**: {_rp_esc(m['name'])}", unsafe_allow_html=True)
                st.markdown(f"**분류**: {_rp_esc(repurpose.class_ko(m.get('cls')) or '-')}"
                            + (f"  \n**이 약의 작용**: {_rp_esc(repurpose.action_ko(m.get('action')))} ({_rp_esc(m.get('moa'))})"
                               if mode == "drug" and m.get("action") else "")
                            + (f"  \n**이 질환과 연결 점수**: {m['score']:.2f}" if mode == "disease" else ""), unsafe_allow_html=True)
                if mode == "disease" and m.get("dts"):
                    _rp_bars(m["dts"])
                st.markdown(f"[Open Targets 에서 보기](https://platform.opentargets.org/target/{m['id']})")
            elif mode == "drug":
                d_ = next(x for x in g["right"] if x["id"] == o)
                st.markdown(f"### {_rp_esc(ko.get(d_['name']) or d_['name'])}")
                if ko.get(d_["name"]):
                    st.caption(d_["name"])
                col = {"ok": "green", "cand": "blue", "bad": "red"}.get(d_["kind"], "gray")
                st.markdown(f":{col}-badge[{d_['label']}]" + (f" :violet-badge[진행 중 {len(d_['ongoing'])}]" if d_["ongoing"] else ""))
                tsyms = ", ".join(next((m["symbol"] for m in g["mid"] if m["id"] == t), t) for t, _ in d_["targets"])
                st.markdown(f"**연결 점수 {d_['score']:.2f}** · 표적 *{_rp_esc(tsyms)}*")
                _rp_bars(d_["dts"])
                st.markdown(f"**이 약으로 이 질환 임상 (Open Targets)**  \n{d_['n_trials']}건"
                            + (f" · 진행 중 {len(d_['ongoing'])}" if d_["ongoing"] else ""))
                if d_["ongoing"]:
                    _rp_ongoing_box(d_["ongoing"])
                st.markdown(f"[Open Targets 질환 보기](https://platform.opentargets.org/disease/{d_['id']}) · "
                            f"[ClinicalTrials.gov 검색](https://clinicaltrials.gov/search?cond={urllib.parse.quote(d_['name'])}"
                            f"&intr={urllib.parse.quote(raw['name'].lower())})")
                if st.button("이 질환으로 지도 다시 그리기 →", use_container_width=True, key="rp_pv_d"):
                    ss_state["rp_pivot"] = {"mode": "disease", "name": d_["name"], "id": d_["id"]}
                    ss_state.pop("rp_sel", None)
                    st.rerun()
            else:
                d_ = next(x for x in g["right"] if x["key"] == o)
                st.markdown(f"### {_rp_esc(_drug_ko(d_['name']))}")
                st.caption(d_["name"].lower())
                ptxt, active = _pat_state(d_["key"])
                b1 = f":{'gray' if active else 'green'}-badge[국내 {ptxt}]"
                if d_["dz_stage"] == "APPROVAL":
                    b2 = " :orange-badge[이미 이 질환에 허가]"
                elif d_["dz_stage"]:
                    b2 = f" :blue-badge[이 질환 {repurpose.stage_ko(d_['dz_stage'])}]"
                else:
                    b2 = " :gray-badge[이 질환 임상 없음]"
                st.markdown(b1 + b2 + (f" :violet-badge[진행 중 {len(d_['ongoing'])}]" if d_["ongoing"] else ""))
                for t, s in d_["targets"]:
                    m = next((x for x in g["mid"] if x["id"] == t), None)
                    if m:
                        st.markdown(f"**표적** *{_rp_esc(m['symbol'])}* — {_rp_esc(repurpose.class_ko(m.get('cls')) or '단백질')}"
                                    f" · 질환 연결 {s:.2f}  \n<span style='font-size:12.5px;color:#555867'>{_rp_esc(m['name'])}</span>",
                                    unsafe_allow_html=True)
                st.markdown(f"**전체 최고 단계**: {repurpose.stage_ko(d_['stage'])}  \n**이 질환 임상 (Open Targets)**: {d_['n_trials']}건"
                            + (f" · 중단 이유: {', '.join(sorted(set(d_['fails'])))}" if d_["fails"] else ""))
                if d_["ongoing"]:
                    _rp_ongoing_box(d_["ongoing"])
                kr = _rp_kr_trials(_n).get(d_["key"])
                sales_txt = ""
                if has_sales(src):
                    sv, sy = _rp_sales(src, data._mtime(data.FILES[src.lower()]))
                    v = sv.get(d_["key"])
                    sales_txt = f"  \n성분 매출 ({src} {sy}): {v / 1e8:,.1f}억" if v else f"  \n성분 매출 ({src}): 자료에 없음"
                st.markdown(f"**국내 (사이트 자료)**  \n허가 품목 {cnt.get(d_['key'], 0)}개"
                            + (f" · 식약처 임상 승인 {kr[0]}건 (최근 {kr[1][:4]}-{kr[1][4:6]})" if kr else " · 식약처 임상 승인 없음")
                            + sales_txt)
                st.markdown(f"[Open Targets 약 보기](https://platform.opentargets.org/drug/{d_['id']})")
                if st.button("이 약으로 지도 다시 그리기 →", use_container_width=True, key="rp_pv_r"):
                    ss_state["rp_pivot"] = {"mode": "drug", "name": d_["name"].lower(), "id": d_["id"]}
                    ss_state.pop("rp_sel", None)
                    st.rerun()

    # ⑥ 같은 내용 표
    st.markdown("##### 같은 내용 표")
    if mode == "drug":
        tsym = {m["id"]: m["symbol"] for m in g["mid"]}
        t = pd.DataFrame([{"질환": ko.get(d_["name"]) or d_["name"], "질환 (영문)": d_["name"],
                           "표적": ", ".join(tsym.get(x, x) for x, _ in d_["targets"]), "연결 점수": round(d_["score"], 2),
                           "상태": d_["label"], "임상 수": d_["n_trials"], "진행 중": len(d_["ongoing"])} for d_ in g["right"]])
    else:
        tsym = {m["id"]: m["symbol"] for m in g["mid"]}
        t = pd.DataFrame([{"약": _drug_ko(d_["name"]), "약 (영문)": d_["name"].lower(),
                           "표적": ", ".join(tsym.get(x, x) for x, _ in d_["targets"]),
                           "표적 연결 점수": round(max(s for _, s in d_["targets"]), 2),
                           "전체 최고 단계": repurpose.stage_ko(d_["stage"]),
                           "이 질환 단계": repurpose.stage_ko(d_["dz_stage"]) or "-", "진행 중": len(d_["ongoing"]),
                           "국내 특허": _pat_state(d_["key"])[0], "국내 허가 품목": cnt.get(d_["key"], 0)} for d_ in g["right"]])
    st.dataframe(t, hide_index=True, use_container_width=True)
    st.download_button("CSV 받기", t.to_csv(index=False).encode("utf-8-sig"), f"repurpose_{mode}.csv", key="rp_csv")

