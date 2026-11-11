"""Gemini API 연동 (AI 분석용).
키: st.secrets["GEMINI_API_KEY"] · 모델: st.secrets["GEMINI_MODEL"](기본값은 model_name() 참고)
로컬에서 키 없이 쓰려면 환경변수 GEMINI_API_KEY 도 인식.
"""
import os
import time

import streamlit as st

def _get(name, default=None):
    try:
        v = st.secrets.get(name, None)
    except Exception:
        v = None
    return v or os.environ.get(name, default)

def api_key():
    return _get("GEMINI_API_KEY")

def model_name():
    return _get("GEMINI_MODEL", "gemini-3.6-flash")

def available():
    return bool(api_key())

# 잠깐 붐비거나(503) 한도에 걸린(429) 경우는 다시 부르면 대개 된다.
_TRANSIENT = ("503", "UNAVAILABLE", "500", "INTERNAL", "429", "RESOURCE_EXHAUSTED",
              "DEADLINE_EXCEEDED", "deadline")

# 설정한 모델이 계속 붐비면 자리가 넉넉한 쪽으로 넘어간다. 새로 나온 모델일수록
# 쓰려는 사람 대비 서버가 모자라 503 이 잦다.
FALLBACKS = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash")

LAST_MODEL = ""   # 실제로 답한 모델 (화면에서 알려주려고 남긴다)


def analyze(prompt, system=None, tries=3):
    """Gemini 호출 → (성공여부, 결과문자열). 신규 SDK(google-genai) 사용.

    붐비는 상태는 잠시 뒤 다시 부르면 풀리는 경우가 많아 몇 번 더 시도한다.
    """
    key = api_key()
    if not key:
        return False, "GEMINI_API_KEY가 설정되지 않았습니다. (Secrets에 키를 넣어주세요)"
    try:
        from google import genai
        from google.genai import types
    except Exception:
        return False, "google-genai 패키지가 없습니다. requirements.txt로 설치되었는지 확인하세요."

    global LAST_MODEL
    want = model_name()
    # 설정한 모델부터, 안 되면 다른 모델로. 같은 이름이 겹치면 건너뛴다.
    plan = [want] + [m for m in FALLBACKS if m != want]
    last, tried = "", []

    for mi, name in enumerate(plan):
        n = max(1, int(tries)) if mi == 0 else 1   # 대체 모델은 한 번씩만
        for i in range(n):
            try:
                client = genai.Client(api_key=key)
                cfg = types.GenerateContentConfig(system_instruction=system) if system else None
                resp = client.models.generate_content(model=name, contents=prompt, config=cfg)
                LAST_MODEL = name
                return True, (resp.text or "(빈 응답)")
            except Exception as e:
                last = str(e)
                if i < n - 1 and any(t in last for t in _TRANSIENT):
                    time.sleep(2 * (i + 1))      # 2초 → 4초
                    continue
                break
        tried.append(name)
        if not any(t in last for t in _TRANSIENT):
            break        # 붐빔이 아니라 설정 문제면 다른 모델로 바꿔도 소용없다

    LAST_MODEL = ""
    tried_s = " → ".join(tried)
    if "UNAVAILABLE" in last or "503" in last:
        return False, (f"Gemini 서버가 붐빕니다(503). 모델을 바꿔가며 시도했지만 모두 거절당했습니다 "
                       f"({tried_s}).\n모델 설정 문제가 아니라 구글 쪽 상태이니 잠시 뒤 다시 눌러주세요.")
    if "RESOURCE_EXHAUSTED" in last or "429" in last:
        return False, (f"Gemini 사용 한도에 걸렸습니다(429) — 시도한 모델: {tried_s}.\n"
                       "무료 키는 분당·하루 요청 수가 묶여 있습니다. 잠시 뒤 다시 시도하세요.")
    return False, (f"Gemini 호출 오류 (모델 {tried_s}): {last}\n"
                   "(GEMINI_MODEL 확인. 예: gemini-2.0-flash, gemini-2.5-flash, gemini-1.5-flash)")
