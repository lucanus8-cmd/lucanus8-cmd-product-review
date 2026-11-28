"""화면 공용 작은 도구 (지금 사이트 scouting.py 의 같은 함수들)."""
import re

import pandas as pd
import streamlit as st

CAP = 500


def _nospace(sr):
    return sr.astype(str).str.replace(r"\s+", "", regex=True)


def match_mask(df, cols, q):
    qn = re.sub(r"\s+", "", str(q))
    m = pd.Series(False, index=df.index)
    for c in cols:
        if c in df.columns:
            m |= _nospace(df[c]).str.contains(qn, case=False, na=False, regex=False)
    return m


def contains(df, cols, q):
    return df[match_mask(df, cols, q)]


def isin(df, col, sel):
    return df if not sel else df[df[col].astype(str).isin(sel)]


def msel(container, label, series, key, default=None):
    sr = series.dropna().astype(str).str.strip()
    opts = sorted(sr[sr.str.len() > 0].unique().tolist())
    return container.multiselect(label, opts, default=[d for d in (default or []) if d in opts], key=key)


def show_df(df, height=360, cap=CAP, key=None, file_name="결과.csv", container=None):
    c = container or st
    n = len(df)
    c.dataframe(df.head(cap), width="stretch", height=height, hide_index=True)
    if n > cap:
        c.caption(f"표에는 상위 {cap:,}건만 표시했습니다 (전체 {n:,}건). 전체는 아래 CSV로 받으세요.")
        c.download_button(f"⬇️ 전체 {n:,}건 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                          file_name=file_name, key=key or f"dl_{file_name}_{n}")
