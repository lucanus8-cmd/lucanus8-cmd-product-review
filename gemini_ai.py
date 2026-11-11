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

    last = ""
    for i in range(max(1, int(tries))):
        try:
            client = genai.Client(api_key=key)
            cfg = types.GenerateContentConfig(system_instruction=system) if system else None
            resp = client.models.generate_content(model=model_name(), contents=prompt, config=cfg)
            return True, (resp.text or "(빈 응답)")
        except Exception as e:
            last = str(e)
            if i < tries - 1 and any(t in last for t in _TRANSIENT):
                time.sleep(2 * (i + 1))      # 2초 → 4초
                continue
            break

    if "UNAVAILABLE" in last or "503" in last:
        return False, (f"Gemini 서버가 지금 붐빕니다(503). {tries}번 다시 시도했지만 안 됐습니다.\n"
                       "모델 설정 문제가 아니라 구글 쪽 일시적인 상태이니, 잠시 뒤 다시 눌러주세요.")
    if "RESOURCE_EXHAUSTED" in last or "429" in last:
        return False, ("Gemini 사용 한도에 걸렸습니다(429). 잠시 뒤 다시 시도하거나, "
                       "요금제·한도를 확인하세요.")
    return False, (f"Gemini 호출 오류: {last}\n"
                   "(GEMINI_MODEL 확인. 예: gemini-2.0-flash, gemini-2.5-flash, gemini-1.5-flash)")
