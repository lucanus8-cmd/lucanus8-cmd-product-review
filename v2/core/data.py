"""v2 데이터 층.

지금 사이트는 자료를 통째로 메모리(pandas)에 올려 두고 계산한다 → 자료·기능·사용자가 늘면 메모리 한도에 닿는다.
v2 는 자료를 파일(parquet)로만 두고, 화면이 필요한 '결과만' DuckDB 로 파일에서 바로 꺼낸다.
  · 표 전체를 메모리에 올리지 않는다.  · 결과는 몇 줄~몇 천 줄.  · DuckDB 메모리 상한을 둔다.

자료 파일은 지금 사이트와 같은 로봇·같은 코드(bootstrap)로 받는다(로봇 완성본이 있으면 그것을 받는다).
"""
import os
import sys
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]          # 저장소 맨 위 (bootstrap.py 가 있는 곳)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SD = ROOT / "scout_data"
SAVED = ROOT / "saved_data"
FILES = {"approval": SD / "approval.parquet", "patent": SD / "patent.parquet",
         "clinical": SD / "clinical.parquet", "price": SD / "price.parquet", "nego": SD / "nego.parquet",
         "iqvia": SAVED / "iqvia.parquet", "ubist": SAVED / "ubist.parquet"}
DUCK_MEM = os.environ.get("V2_DUCK_MEM", "256MB")
_LOCK = threading.Lock()


def _mtime(p):
    try:
        return Path(p).stat().st_mtime
    except Exception:
        return 0.0


def ensure(name):
    """자료 파일을 확보한다(없으면 받음, 오래되면 뒤에서 갱신). 파일 경로 또는 None."""
    import bootstrap
    if name in ("iqvia", "ubist"):
        pq = FILES[name]
        pkl = SAVED / f"{name}.pkl"
        try:
            bootstrap.ensure_one(name)
        except Exception:
            pass
        if pkl.exists() and (not pq.exists() or _mtime(pq) < _mtime(pkl)):
            _pkl_to_parquet(pkl, pq)
        return pq if pq.exists() else None
    try:
        bootstrap.ensure_one(name)
    except Exception:
        pass
    return FILES[name] if FILES[name].exists() else None


def _pkl_to_parquet(pkl, pq):
    """매출 pkl → parquet. 표를 한 번 펼쳐야 해서 따로 띄운 작업에서 한다(끝나면 메모리를 다 돌려줌)."""
    import subprocess
    code = ("import pandas as pd,sys;d=pd.read_pickle(sys.argv[1]);"
            "d.columns=[str(c) for c in d.columns];d.to_parquet(sys.argv[2]+'.tmp',index=False);"
            "import os;os.replace(sys.argv[2]+'.tmp',sys.argv[2])")
    try:
        subprocess.run([sys.executable, "-c", code, str(pkl), str(pq)], check=True, timeout=600,
                       capture_output=True)
    except Exception:
        pass


@st.cache_resource(show_spinner=False)
def _con():
    import duckdb
    c = duckdb.connect(database=":memory:")
    c.execute(f"SET memory_limit='{DUCK_MEM}'")
    c.execute("SET threads=2")
    c.execute("SET preserve_insertion_order=false")
    return c


def src(name):
    """SQL 안에서 쓸 자료 이름: read_parquet('경로'). 파일이 없으면 None."""
    p = ensure(name)
    return f"read_parquet('{p.as_posix()}')" if p else None


def q(sql, params=None):
    """SQL → 작은 DataFrame. 연결 하나를 여러 사람이 같이 쓰므로 커서를 따로 연다."""
    with _LOCK:
        cur = _con().cursor()
    try:
        return cur.execute(sql, params or []).df()
    finally:
        cur.close()


def has(name):
    return ensure(name) is not None


def file_date(name):
    p = FILES.get(name)
    if not p or not Path(p).exists():
        return ""
    return pd.Timestamp(_mtime(p), unit="s", tz="UTC").tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M")


def rss_mb():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * 4096 / 1e6
    except Exception:
        return 0.0


# ── 자료별 '표준 모양' (칸 이름·형식을 맞춘 SQL 조각) ─────────────────────────
@st.cache_resource(show_spinner=False, max_entries=16)
def _cols(path_str, mt):
    with _LOCK:
        cur = _con().cursor()
    try:
        return [r[0] for r in cur.execute(f"DESCRIBE SELECT * FROM read_parquet('{path_str}')").fetchall()]
    finally:
        cur.close()


def cols(name):
    p = ensure(name)
    return _cols(p.as_posix(), _mtime(p)) if p else []


def price():
    """약가 표 (금액 숫자, 적용일자 날짜, _k 성분키)."""
    p = ensure("price")
    if not p:
        return None
    have = set(cols("price"))
    def c(x, cast=None):
        if x not in have:
            return f"NULL AS \"{x}\""
        return f"TRY_CAST(\"{x}\" AS {cast}) AS \"{x}\"" if cast else f"\"{x}\""
    k = ('"_key"' if "_key" in have else
         "upper(regexp_extract(CAST(\"주성분명\" AS VARCHAR), '([A-Za-z]{3,})', 1))")
    sel = ", ".join([c("제품코드"), c("제품명"), c("업체명"), c("금액", "DOUBLE"), c("적용일자", "TIMESTAMP"),
                     c("주성분명"), c("제제코드"), c("투여"), c("급여구분"), c("분류"), f"{k} AS _k"])
    return f"(SELECT {sel} FROM read_parquet('{p.as_posix()}'))"


def approval():
    """허가 표 (_k 성분키)."""
    p = ensure("approval")
    if not p:
        return None
    have = set(cols("approval"))
    k = ('"_key"' if "_key" in have else
         "upper(regexp_extract(CAST(\"주성분(영문)\" AS VARCHAR), '([A-Za-z]{3,})', 1))")
    keep = [x for x in ("품목기준코드", "품목명", "업체명", "허가일자", "전문/일반", "주성분", "주성분(영문)",
                        "신약구분", "상태", "보험코드(EDI)") if x in have]
    sel = ", ".join([f"\"{x}\"" for x in keep] + [f"{k} AS _k"])
    return f"(SELECT {sel} FROM read_parquet('{p.as_posix()}'))"


def ns(col):
    """띄어쓰기 없앤 소문자 칸 (SQL 조각) — 지금 사이트의 '띄어쓰기 무시 포함 검색'과 같게."""
    return f"lower(regexp_replace(CAST(\"{col}\" AS VARCHAR), '\\s', '', 'g'))"


def table(name):
    """자료 파일 그대로(모든 칸). 조건을 붙여 필요한 줄만 꺼낼 때 쓴다."""
    p = ensure(name)
    return f"read_parquet('{p.as_posix()}')" if p else None


SALES = {"IQVIA": {"prod": "제품명", "maker": "회사명", "ing": "성분명", "yr": r"^\d{4}년$"},
         "UBIST": {"prod": "제품명", "maker": "제조사명", "ing": "성분", "yr": r"^처방조제액\(원\)_\d{4}년$"}}


def sales(src):
    """(SQL 원본, 설정, 연도 칸 목록). 없으면 (None, cfg, [])."""
    import re
    cfg = SALES[src]
    name = src.lower()
    p = ensure(name)
    if not p:
        return None, cfg, []
    yr = sorted([c for c in cols(name) if re.match(cfg["yr"], str(c))])
    return f"read_parquet('{p.as_posix()}', file_row_number=true)", cfg, yr


_KEY_SRC = {"approval": "주성분(영문)", "patent": "INGR_ENG_NAME", "price": "주성분명", "clinical": "성분명"}


def key_expr(name):
    """그 자료의 성분키 칸 (저장된 _key 가 있으면 그것, 없으면 영문 성분명에서 바로 만든다)."""
    if "_key" in cols(name):
        return '"_key"'
    c = _KEY_SRC.get(name)
    if c and c in cols(name):
        return f"upper(regexp_extract(CAST(\"{c}\" AS VARCHAR), '([A-Za-z]{{3,}})', 1))"
    return "''"
