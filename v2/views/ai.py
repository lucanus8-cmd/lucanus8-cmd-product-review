"""AI 분석 (v2) — C 방식: AI 가 사이트 검색 도구를 골라 쓰며 대화로 답한다.

AI(Gemini)는 사이트 표를 보지 못한다. 질문을 받으면 '이 도구를 이 값으로 불러 달라'고 요청하고,
사이트가 DuckDB 로 필요한 줄만 찾아 작은 결과를 돌려준다. 매출은 요약 숫자만 보낸다.
"""
import json
import time

import streamlit as st

import gemini_ai
from core import ai_tools_v2 as at

st.subheader("🤖 AI 분석 — 대화형 (사이트 자료를 직접 찾아보며 답함)")

SYSTEM = (
    "너는 국내 제약회사의 제품 검토(제네릭·개량신약 개발) 보조다. 한국어로 답한다.\n"
    "- 사실(품목 수·특허 만료일·약가·매출·DMF·임상·효능)은 반드시 도구로 찾은 사이트 자료로만 말한다. "
    "기억이나 추측으로 숫자를 만들지 않는다. 자료에 없으면 '사이트 자료에 없음'이라고 한다.\n"
    "- 성분·제품 이름이 애매하면 먼저 find_ingredient 로 확인한다. 제품명이 복합제면 성분을 나눠서 본다.\n"
    "- 필요한 도구를 여러 번, 여러 개 불러도 된다. 비교 질문은 성분마다 불러서 비교한다.\n"
    "- 숫자 뒤에는 [출처]를 붙인다(예: [식약처 특허], [IQVIA 요약]).\n"
    "- 일반 지식(기전·임상적 의미 등)을 보탤 때는 '(일반 지식)'이라고 따로 표시한다.\n"
    "- 표로 정리하면 좋은 내용은 마크다운 표로 쓴다. 끝에 한두 줄로 결론(개발 관점 시사점)을 쓴다."
)
REVIEW = ("다음 성분/제품의 '제품 검토서'를 마크다운 표(항목 | 내용)로 만들어 줘: {t}\n"
          "항목 순서: 제품명(오리지널) | 허가 현황(품목·업체 수, 최초 허가) | 약가(급여 품목, 상한금액, 첫 등재) | "
          "주성분 | 효능·효과(사이트 효능 자료로 질병명 검색이 가능하면 활용) | 관련 특허(물질·염형태·용도 만료) | "
          "임상시험 현황(식약처 승인, 최근 24개월) | 매출(IQVIA 최근 5개년, 억원·CAGR) | DMF 등록 업체 | 종합 의견.\n"
          "도구로 찾은 자료만 쓰고, 없는 항목은 '사이트 자료에 없음'.")
EXAMPLES = ["과민성 방광 약 중에 특허가 곧 끝나고 시장이 큰 성분은?",
            "에독사반 제네릭은 언제 나왔고 원료(DMF) 등록사는 어디야?",
            "로수바스타틴이랑 아토르바스타틴 매출·약가 비교해줘"]

if not gemini_ai.available():
    st.info("Gemini API 키가 없습니다. Streamlit Secrets 에 `GEMINI_API_KEY` 를 넣으면 켜집니다.")
    st.stop()

ss_ = st.session_state
ss_.setdefault("ai_chat", [])          # [{"role": "user"|"model", "text": str, "tools": [...]}]

top = st.columns([3, 1])
top[0].caption(f"모델: {gemini_ai.model_name()} · 도구 {len(at.TOOLS)}개(허가·특허·약가·매출 요약·효능·후보 찾기·DMF·임상) · "
               "매출은 요약 숫자만 AI 에 보냅니다 · 무료 키는 분당·하루 질문 수가 묶여 있습니다")
if top[1].button("🧹 대화 지우기", use_container_width=True):
    ss_["ai_chat"] = []
    st.rerun()

with st.expander("📋 제품 검토서 만들기 (양식)", expanded=False):
    rc = st.columns([3, 1])
    rt = rc[0].text_input("제품/성분", key="ai_rv_t", placeholder="예: 미라베그론 / 릭시아나", label_visibility="collapsed")
    if rc[1].button("검토서 만들기", type="primary", use_container_width=True) and rt.strip():
        ss_["_ai_pending"] = REVIEW.format(t=rt.strip())

if not ss_["ai_chat"]:
    st.markdown("**이렇게 물어보세요**")
    ex = st.columns(len(EXAMPLES))
    for i, q in enumerate(EXAMPLES):
        if ex[i].button(q, key=f"ai_ex{i}", use_container_width=True):
            ss_["_ai_pending"] = q


def _show_tools(tools):
    if not tools:
        return
    with st.expander(f"🔎 찾아본 사이트 자료 {len(tools)}건", expanded=False):
        for t in tools:
            st.markdown(f"- `{t['name']}` {json.dumps(t['args'], ensure_ascii=False)} · {t['ms']}ms")


for m in ss_["ai_chat"]:
    with st.chat_message("user" if m["role"] == "user" else "assistant"):
        st.markdown(m["text"])
        if m["role"] == "model":
            _show_tools(m.get("tools"))

q = st.chat_input("질문을 입력하세요 (이어서 물어봐도 앞 대화를 기억합니다)") or ss_.pop("_ai_pending", None)

_TRANSIENT = ("UNAVAILABLE", "503", "overloaded", "DEADLINE", "INTERNAL", "500")


def _ask(question, history):
    """Gemini 에 도구를 쥐여 주고 답을 받는다 → (ok, 답, 도구 목록, 모델)."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=gemini_ai.api_key())
    tools = [types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in at.TOOLS])]
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM, tools=tools)
    contents = [types.Content(role=h["role"], parts=[types.Part(text=h["text"])]) for h in history[-8:]]
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
    used = []
    plan = [gemini_ai.model_name()] + [x for x in gemini_ai.FALLBACKS if x != gemini_ai.model_name()]
    status = st.status("자료를 찾아보는 중…", expanded=False)
    for model in plan:
        try:
            for _turn in range(8):
                r = None
                for i in range(3):
                    try:
                        r = client.models.generate_content(model=model, contents=contents, config=cfg)
                        break
                    except Exception as e:
                        if i < 2 and any(t in str(e) for t in _TRANSIENT):
                            time.sleep(2 * (i + 1)); continue
                        raise
                cand = r.candidates[0].content if r.candidates else None
                fcs = [p.function_call for p in ((cand.parts if cand else None) or []) if getattr(p, "function_call", None)]
                if not fcs:
                    status.update(label=f"찾아본 자료 {len(used)}건 · 답 완성", state="complete")
                    return True, (r.text or "(빈 응답)"), used, model
                contents.append(cand)
                parts = []
                for fc in fcs:
                    args = dict(fc.args or {})
                    status.update(label=f"찾는 중: {fc.name} {json.dumps(args, ensure_ascii=False)[:60]}")
                    t0 = time.time()
                    res = at.call(fc.name, args)
                    used.append({"name": fc.name, "args": args, "ms": int((time.time() - t0) * 1000)})
                    parts.append(types.Part.from_function_response(
                        name=fc.name, response={"result": json.loads(json.dumps(res, ensure_ascii=False, default=str))}))
                contents.append(types.Content(role="user", parts=parts))
            status.update(label="도구를 너무 여러 번 불러 멈췄습니다", state="error")
            return False, "질문이 너무 넓어 도구를 8번 넘게 불렀습니다. 성분이나 조건을 좁혀 다시 물어봐 주세요.", used, model
        except Exception as e:
            last = str(e)
            if any(t in last for t in _TRANSIENT):
                continue                          # 붐비면 다음 모델로
            status.update(label="오류", state="error")
            if "RESOURCE_EXHAUSTED" in last or "429" in last:
                return False, "Gemini 사용 한도에 걸렸습니다(429). 무료 키는 분당·하루 질문 수가 묶여 있어요. 잠시 뒤 다시 물어봐 주세요.", used, model
            return False, f"Gemini 호출 오류: {last[:300]}", used, model
    status.update(label="Gemini 서버가 붐빕니다", state="error")
    return False, "Gemini 서버가 붐빕니다(503). 잠시 뒤 다시 물어봐 주세요.", used, ""


if q:
    with st.chat_message("user"):
        st.markdown(q)
    with st.chat_message("assistant"):
        ok, ans, used, model = _ask(q, ss_["ai_chat"])
        if ok:
            st.markdown(ans)
            if model and model != gemini_ai.model_name():
                st.caption(f"설정한 모델이 붐벼서 {model} 로 답했습니다.")
        else:
            st.warning(ans)
        _show_tools(used)
    if ok:
        ss_["ai_chat"] += [{"role": "user", "text": q}, {"role": "model", "text": ans, "tools": used}]

if ss_["ai_chat"]:
    st.download_button("⬇️ 대화 저장(.md)", "\n\n".join(("**질문**: " if m["role"] == "user" else "") + m["text"]
                                                       for m in ss_["ai_chat"]),
                       file_name="AI분석_대화.md", mime="text/markdown", key="ai_dl")
