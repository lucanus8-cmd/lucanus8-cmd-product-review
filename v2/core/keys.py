"""한글 성분·브랜드 → 영문 성분키 대응표 (지금 사이트 _kor2engkey 와 같은 규칙).
허가·특허 표는 지금 사이트와 같은 공유 로더(scout_sources, 한 벌만)로 읽는다."""
import pandas as pd
import streamlit as st

import scout_sources as ss
from core import data


@st.cache_resource(show_spinner="성분 키 매핑 중…", max_entries=2)
def _maps(_amt, _pmt):
    c2k, b2k = {}, {}

    def feed(kor_ing, kor_name, eng_key, single):
        ok = single & (eng_key.astype(str).str.len() > 0)
        if not ok.any():
            return
        core = ss.ing_core_series(kor_ing[ok])
        brand = (kor_name[ok].astype(str).str.replace(r"\s+", "", regex=True)
                 .str.split(r"[0-9(\[]", regex=True).str[0].fillna(""))
        k = eng_key[ok].astype(str)
        cc = pd.DataFrame({"c": core, "k": k})
        cc = cc[cc["c"].str.len() >= 2].drop_duplicates("c")
        for a, b in zip(cc["c"], cc["k"]):
            c2k.setdefault(a, b)
        bb = pd.DataFrame({"b": brand, "k": k})
        bb = bb[bb["b"].str.len() >= 2].drop_duplicates("b")
        for a, b in zip(bb["b"], bb["k"]):
            b2k.setdefault(a, b)

    try:
        ap = ss.load_approval()
        ing, eng = ap.get("주성분", pd.Series(dtype=str)), ap.get("주성분(영문)", pd.Series(dtype=str))
        feed(ing, ap.get("품목명", pd.Series(dtype=str)), ss.ing_key_series(eng), ss.is_single_series(ing, eng))
    except Exception:
        pass
    try:
        pt = ss.load_patent()
        if "INGR_NAME" in pt.columns:
            feed(pt["INGR_NAME"], pt.get("품목명", pd.Series(dtype=str)), pt["_key"],
                 ss.is_single_series(pt["INGR_NAME"], pt.get("INGR_ENG_NAME", pd.Series("", index=pt.index))))
    except Exception:
        pass
    return c2k, b2k


def kor2engkey():
    return _maps(data._mtime(data.FILES["approval"]), data._mtime(data.FILES["patent"]))
