"""매출 분석 v2 — 매출 표를 메모리에 두지 않고, 화면이 쓰는 줄·칸만 파일(parquet)에서 꺼낸다.

지금 사이트(app.py)는 IQVIA·UBIST 표 전체를 메모리에 올려 두고, 화면마다 거기서 조건에 맞는 줄을 고른다.
여기서는 같은 줄을 DuckDB 로 파일에서 바로 꺼내, 지금 사이트가 고른 표와 '똑같은' 표를 돌려준다.
  · 줄 순서: 파일 안 줄 번호(file_row_number) 순서 = 원래 표 순서. 줄 번호를 표의 index 로 둔다.
  · 칸 형식: 원래 표의 형식(dtype)으로 되돌린다. 빈 칸은 원래처럼 NaN.
  · 원래 표를 읽은 뒤 하던 손질(복합제 용량 다듬기, 단일제/복합제 구분)도 꺼낸 줄에 똑같이 한다.
  · 조건 검색(make_filter_mask)은 SQL 로 '넉넉히' 먼저 거르고, 마지막에 원래 코드로 한 번 더 정확히 거른다.
"""
import re
import threading

import numpy as np
import pandas as pd
import streamlit as st

from core import data
from dose_util import COMBO_LABELS, fix_iqvia_dose, iqvia_combo, price_ingredient_counts, ubist_combo

KINDS = ("iqvia", "ubist")
_LOCK = data._LOCK


def path(kind):
    return data.ensure(kind)


def mtime(kind):
    p = data.FILES[kind]
    return data._mtime(p) if p.exists() else 0.0


def _src(kind):
    p = path(kind)
    return f"read_parquet('{p.as_posix()}', file_row_number=true)" if p else None


@st.cache_resource(show_spinner=False, max_entries=4)
def _schema(kind, _mt):
    """(칸 이름 목록, 칸별 원래 형식) — 원래 형식은 파일에 적힌 pandas 형식 정보로 읽는다(앞 몇 줄만)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = path(kind)
    pf = pq.ParquetFile(p)
    b = next(pf.iter_batches(batch_size=64), None)
    t = pa.Table.from_batches([b], schema=pf.schema_arrow) if b is not None else pf.schema_arrow.empty_table()
    ref = t.to_pandas()
    return list(ref.columns), {c: ref[c].dtype for c in ref.columns}


def columns(kind):
    if not path(kind):
        return []
    return _schema(kind, mtime(kind))[0]


def empty(kind):
    """칸만 있고 줄은 없는 표 (화면 코드의 'is not None'·'.columns' 검사용)."""
    cols, dt = _schema(kind, mtime(kind))
    return pd.DataFrame({c: pd.Series(dtype=dt[c]) for c in cols})


def _q_arrow(sql, params=()):
    with _LOCK:
        cur = data._con().cursor()
    try:
        r = cur.execute(sql, list(params))
        for m in ("to_arrow_table", "fetch_arrow_table", "arrow"):     # DuckDB 판마다 이름이 다르다
            if hasattr(r, m):
                return getattr(r, m)()
        raise RuntimeError("DuckDB 에서 arrow 표를 받는 방법이 없음")
    finally:
        cur.close()


def _restore(df, kind):
    """DuckDB 가 돌려준 표를 원래 표와 같은 형식으로."""
    _, dt = _schema(kind, mtime(kind))
    for c in df.columns:
        want = dt.get(c)
        if want is None:
            continue
        try:
            if df[c].dtype != want:
                df[c] = df[c].astype(want)
        except Exception:
            pass
        if df[c].dtype == object:
            df[c] = df[c].where(df[c].notna(), np.nan)
    return df


# ── 단일제/복합제 ───────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False, max_entries=2)
def _code_n(_pmt):
    return price_ingredient_counts(data.SD / "price.parquet")


def _price_mt():
    p = data.SD / "price.parquet"
    return data._mtime(p) if p.exists() else 0.0


def _iq_combo_col(cols):
    return next((c for c in cols if str(c).replace(" ", "") in ("단일제/복합제", "단일/복합")), None)


@st.cache_resource(show_spinner=False, max_entries=4)
def _ubist_kind_map(_mt, _pmt):
    """UBIST 성분 글자(서로 다른 값) → 단일제/복합제. 원래 코드(ubist_combo)로 그대로 판정."""
    r = data.q(f'SELECT DISTINCT "성분" AS v FROM {_src("ubist")}')
    names = r["v"]
    k = ubist_combo(pd.DataFrame({"성분": names}), _code_n(_pmt))
    return {("\x00NULL" if (v is None or (isinstance(v, float) and v != v)) else str(v)): kk
            for v, kk in zip(names.tolist(), k.tolist())}


def _combo_sql(kind, sel):
    """구성(단일제·복합제) 고른 것만 — SQL 조건과 값."""
    if not sel or set(sel) == set(COMBO_LABELS):
        return "TRUE", []
    want = set(sel)
    cols = columns(kind)
    if kind == "iqvia":
        cc = _iq_combo_col(cols)
        if cc is None and "성분명" not in cols:
            return "TRUE", []
        v = f"upper(trim(coalesce(CAST(\"{cc}\" AS VARCHAR), 'nan')))" if cc else "'?'"
        plus = "contains(coalesce(CAST(\"성분명\" AS VARCHAR), 'nan'), '+')" if "성분명" in cols else "FALSE"
        expr = (f"(CASE WHEN {v} LIKE 'COMB%' THEN '복합제' WHEN {v} LIKE 'SING%' THEN '단일제' "
                f"WHEN {plus} THEN '복합제' ELSE '단일제' END)")
        return f"list_contains(?, {expr})", [sorted(want)]
    if "성분" not in cols:
        return "TRUE", []
    mp = _ubist_kind_map(mtime("ubist"), _price_mt())
    names = [n for n, k in mp.items() if k in want and n != "\x00NULL"]
    null_ok = mp.get("\x00NULL") in want
    cond = "list_contains(?, CAST(\"성분\" AS VARCHAR))" + (' OR "성분" IS NULL' if null_ok else "")
    return f"({cond})", [names]


# ── 줄 꺼내기 ───────────────────────────────────────────────────────────────
_HELP = {"iqvia": ["팩명", "용량", "성분명"], "ubist": ["성분"]}


def rows(kind, where="TRUE", params=(), cols=None, combo=None):
    """조건에 맞는 줄 (원래 표에서 같은 조건으로 고른 것과 같은 표). cols 를 주면 그 칸만(+손질에 필요한 칸)."""
    src = _src(kind)
    if src is None:
        return None
    allc = columns(kind)
    if cols is None:
        use = list(allc)
    else:
        want = set(cols) | set(_HELP[kind])
        if kind == "iqvia":
            cc = _iq_combo_col(allc)
            if cc:
                want.add(cc)
        use = [c for c in allc if c in want]
    csql, cprm = _combo_sql(kind, combo)
    sel = ", ".join(f'"{c}"' for c in use)
    sql = (f'SELECT file_row_number AS "__r", {sel} FROM {src} '
           f'WHERE ({where}) AND {csql} ORDER BY file_row_number')
    df = _q_arrow(sql, list(params) + cprm).to_pandas()
    df.index = pd.Index(df.pop("__r").to_numpy())
    if not df.index.is_monotonic_increasing:      # 여럿이 나눠 읽으면 순서가 섞여 올 수 있다 → 원래 줄 순서로
        df = df.sort_index(kind="stable")
    df = _restore(df, kind)
    # 원래 표를 읽은 뒤 하던 손질 (app.py 의 load_iqvia / load_ubist 와 같은 순서)
    if kind == "iqvia":
        try:
            df, _ = fix_iqvia_dose(df)
            # 원래는 표 전체에 손질하므로 (복합제가 늘 있어) '용량' 칸이 항상 object 형식이 된다 → 똑같이
            if "용량" in df.columns and df["용량"].dtype != object:
                df["용량"] = df["용량"].astype(object)
        except Exception:
            pass
        try:
            df["_구성"] = iqvia_combo(df)
        except Exception:
            pass
    else:
        try:
            df["_구성"] = ubist_combo(df, _code_n(_price_mt()))
        except Exception:
            pass
    if combo and set(combo) != set(COMBO_LABELS) and "_구성" in df.columns:
        df = df[df["_구성"].isin(list(combo))]
    if cols is not None:
        keep = [c for c in df.columns if c in set(cols) or c == "_구성"]
        df = df[keep]
    return df


_RX = re.compile(r"[\\^$.|?*+()\[\]{}]")


def prefilter(active, field_map, cols):
    """make_filter_mask 조건 → 넉넉한 SQL 조건(원래 조건에 맞는 줄은 모두 포함). 정확한 거르기는 꺼낸 뒤 원래 코드로."""
    conds, prm = [], []
    for f in active or []:
        col = field_map.get(f["field"])
        if not col or col not in cols:
            continue
        v = f["value"]
        if isinstance(v, list):
            conds.append(f'list_contains(?, CAST("{col}" AS VARCHAR))')
            prm.append([str(x) for x in v])
        elif f["field"] == "용량":
            continue                    # 용량은 다듬은 값(팩명 기준)으로 맞추므로 꺼낸 뒤에 거른다
        else:
            t = str(v).strip()
            if t and not _RX.search(t):     # 정규식 글자가 없을 때만 (있으면 꺼낸 뒤 원래 코드로만)
                conds.append(f"contains(lower(coalesce(CAST(\"{col}\" AS VARCHAR), 'nan')), lower(?))")
                prm.append(t)
    return (" AND ".join(conds) if conds else "TRUE"), prm


def any_applicable(active, field_map, cols):
    return any(field_map.get(f["field"]) and field_map.get(f["field"]) in cols for f in (active or []))


# ── 목록·요약 (원래 표 전체를 보던 것) ─────────────────────────────────────────
@st.cache_data(show_spinner=False, max_entries=16)
def distinct(kind, col, _mt, combo=None):
    """원래 코드: sorted(df[col].dropna().astype(str).unique()) — combo 를 주면 그 구성의 줄에서만."""
    if not col or col not in columns(kind):
        return []
    csql, cprm = _combo_sql(kind, combo)
    r = data.q(f'SELECT DISTINCT CAST("{col}" AS VARCHAR) AS v FROM {_src(kind)} WHERE "{col}" IS NOT NULL AND {csql}', cprm)
    return sorted(r["v"].astype(str).tolist())


@st.cache_data(show_spinner=False, max_entries=4)
def col_sums(kind, cols, _mt):
    """칸별 합계 (pd.to_numeric(errors='coerce').fillna(0).sum() 와 같음)."""
    cols = [c for c in cols if c in columns(kind)]
    if not cols:
        return {}
    r = data.q("SELECT " + ", ".join(f'coalesce(sum(TRY_CAST("{c}" AS DOUBLE)), 0) AS "{c}"' for c in cols)
               + f" FROM {_src(kind)}").iloc[0]
    return {c: float(r[c]) for c in cols}
