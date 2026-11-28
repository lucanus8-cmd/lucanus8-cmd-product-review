"""v2: 자료 만들기(구글 시트·드라이브에서 받아 표로 풀기)를 사이트 본체가 아니라 잠깐 띄운 별도 작업에서 한다.

지금 사이트는 '매출 분석 불러오기'·자동 갱신 때 엑셀을 사이트 안에서 직접 표로 푼다. 이때 수백 MB 를 쓰고,
파이썬은 다 쓴 메모리를 운영체제에 잘 돌려주지 않아 그만큼 계속 남는다.
여기서는 같은 함수(bootstrap._do_build)를 별도 작업에서 돌리고, 끝나면 그 작업을 닫아 메모리를 통째로 돌려받는다.
결과 파일은 같다(같은 코드가 같은 곳에 쓴다).
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]

_CHILD = r'''
import base64, json, os, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, sys.argv[2])
import streamlit as _st
_sec = json.loads(base64.b64decode(os.environ.pop("V2_BUILD_SECRETS", "e30=")))
import bootstrap

class _P:                                   # bootstrap 이 읽는 st.secrets 만 본체에서 받은 값으로
    secrets = _sec
    def __getattr__(self, n):
        return getattr(_st, n)

bootstrap.st = _P()
r = bootstrap._do_build_here(**json.loads(sys.argv[1]))
print("__RESULT__" + json.dumps(r, default=str))
'''


def _secrets_blob():
    out = {}
    for k in ("gcp_service_account_b64", "gcp_service_account", "prebuilt"):
        try:
            v = st.secrets.get(k, None)
        except Exception:
            v = None
        if v is None:
            continue
        out[k] = v if isinstance(v, str) else json.loads(json.dumps(v, default=lambda o: dict(o)))
    return base64.b64encode(json.dumps(out).encode()).decode()


def do_build(include_sales=False, force=False, only=None):
    """bootstrap._do_build 와 같은 일을 별도 작업에서. 결과값도 같다."""
    env = dict(os.environ)
    env["V2_BUILD_SECRETS"] = _secrets_blob()          # 키는 화면·파일에 남기지 않고 작업에만 건넨다
    args = json.dumps({"include_sales": bool(include_sales), "force": bool(force),
                       "only": sorted(only) if only else None})
    p = subprocess.run([sys.executable, "-c", _CHILD, args, str(ROOT)], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=1800)
    for ln in reversed((p.stdout or "").splitlines()):
        if ln.startswith("__RESULT__"):
            return json.loads(ln[len("__RESULT__"):])
    tail = [ln for ln in (p.stderr or "").splitlines() if ln.strip()][-3:]
    raise RuntimeError("자료 만들기 작업 실패 — " + (" | ".join(tail)[-300:] or f"종료 코드 {p.returncode}"))


def install():
    """bootstrap 안의 모든 '자료 만들기'(화면에서 처음 받기·불러오기·자동 갱신·깨우기 로봇)를 별도 작업으로."""
    import bootstrap
    if getattr(bootstrap, "_V2_SUB", False):
        return
    bootstrap._do_build = do_build
    bootstrap._V2_SUB = True
