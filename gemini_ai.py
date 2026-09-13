"""Gemini API 연동 (AI 분석용).
키: st.secrets["GEMINI_API_KEY"] · 모델: st.secrets["GEMINI_MODEL"](기본 gemini-2.0-flash)
로컬에서 키 없이 쓰려면 환경변수 GEMINI_API_KEY 도 인식.
"""
import os
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

def analyze(prompt, system=None):
    """Gemini 호출 → (성공여부, 결과문자열). 신규 SDK(google-genai) 사용."""
    key = api_key()
    if not key:
        return False, "GEMINI_API_KEY가 설정되지 않았습니다. (Secrets에 키를 넣어주세요)"
    try:
        from google import genai
        from google.genai import types
    except Exception:
        return False, "google-genai 패키지가 없습니다. requirements.txt로 설치되었는지 확인하세요."
    try:
        client = genai.Client(api_key=key)
        cfg = types.GenerateContentConfig(system_instruction=system) if system else None
        resp = client.models.generate_content(model=model_name(), contents=prompt, config=cfg)
        return True, (resp.text or "(빈 응답)")
    except Exception as e:
        return False, f"Gemini 호출 오류: {e}\n(GEMINI_MODEL 확인. 예: gemini-2.0-flash, gemini-2.5-flash, gemini-1.5-flash)"
