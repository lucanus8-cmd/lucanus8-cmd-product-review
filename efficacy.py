"""식약처 허가 '효능·효과' 원문으로 질병명(키워드) 찾기.

로봇(build_efficacy.py)이 품목마다 효능·효과 PDF 에서 글을 뽑아, 원문은 고치지 않고 그대로 두되
같은 글은 한 번만 저장한다(효능번호). 사이트는 그 작은 파일 2개만 받는다.
  · ee_text.parquet : 효능번호, 효능효과(원문)
  · ee_item.parquet : 품목기준코드, 효능번호, 변경일자
  · meta.json       : 기준일·품목 수 등
찾기는 '그 말이 들어 있으면 전부'(띄어쓰기 무시). 걸린 문구를 보여주고, 번호 항목 첫머리에 나오는 것을 위로 올린다.
"""
import io
import json
import os
import re
import urllib.request

import pandas as pd
import streamlit as st

BASE = os.environ.get("EE_SITE_BASE",
                      "https://raw.githubusercontent.com/lucanus8-cmd/lucanus8-cmd-product-review/data/efficacy_site/")


def _get(name, timeout=30):
    if BASE.startswith("http"):
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "product-review-app"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    with open(os.path.join(BASE, name), "rb") as f:
        return f.read()


@st.cache_resource(ttl=6 * 3600, max_entries=1, show_spinner="효능·효과 자료 불러오는 중…")
def _load_raw():
    tx = pd.read_parquet(io.BytesIO(_get("ee_text.parquet")))
    it = pd.read_parquet(io.BytesIO(_get("ee_item.parquet")))
    try:
        meta = json.loads(_get("meta.json", timeout=10).decode("utf-8"))
    except Exception:
        meta = {}
    return tx, it, meta


def available():
    try:
        _load_raw()
        return True
    except Exception:
        return False


def meta():
    try:
        return _load_raw()[2]
    except Exception:
        return {}


@st.cache_resource(ttl=6 * 3600, max_entries=2, show_spinner=False)
def _index(ap_mtime):
    """성분키(_key) → 그 성분 단일제 품목들의 서로 다른 효능 글을 ' | ' 로 이은 것."""
    import scout_sources as ss
    tx, it, _ = _load_raw()
    ap = ss.load_approval()
    cols = [c for c in ("품목기준코드", "_key", "주성분", "주성분(영문)") if c in ap.columns]
    ap = ap[cols].copy()
    ap["품목기준코드"] = ap["품목기준코드"].astype(str).str.strip()
    one = ss.is_single_series(ap.get("주성분", pd.Series("", index=ap.index)),
                              ap.get("주성분(영문)", pd.Series("", index=ap.index)))
    ap = ap[one & (ap["_key"].astype(str).str.len() > 0)]
    it = it.copy()
    it["품목기준코드"] = it["품목기준코드"].astype(str).str.strip()
    j = ap.merge(it[["품목기준코드", "효능번호"]], on="품목기준코드", how="inner")
    j = j.drop_duplicates(["_key", "효능번호"])
    t = tx.set_index("효능번호")["효능효과"]
    j["t"] = j["효능번호"].map(t).fillna("")
    j = j[j["t"].str.len() > 0]
    idx = j.groupby("_key")["t"].agg(lambda x: " | ".join(x))
    return idx, idx.str.replace(" ", "", regex=False)


def index():
    import scout_sources as ss
    return _index(ss._mtime(ss.SD / "approval.parquet"))


def _words(s):
    return [w.strip() for w in re.split(r"[,，]", str(s or "")) if w.strip()]


def _score(compact, k):
    """작을수록 위: 키워드가 번호 항목(1. 2. …) 시작에서 얼마나 가까운지. 12자 안이면 '첫머리'(1000 미만)."""
    best = 9e9
    for m in re.finditer(re.escape(k), compact):
        st_ = [x.end() for x in re.finditer(r"효능효과|(?<![0-9])[0-9]{1,2}\.(?![0-9])|\||-(?=[가-힣])|(?<=[.;])",
                                            compact[:m.start()])]
        d = m.start() - (st_[-1] if st_ else 0)
        best = min(best, d if d <= 12 else 1000 + d)
    return best


def _snippet(text, k, w=40):
    pat = r"\s*".join(map(re.escape, k))
    m = re.search(pat, text)
    if not m:
        return ""
    a, b = max(0, m.start() - w), min(len(text), m.end() + w)
    return ("…" if a else "") + text[a:m.start()] + "【" + text[m.start():m.end()] + "】" + text[m.end():b] + ("…" if b < len(text) else "")


def search(keys, words, exclude=""):
    """keys(성분키 시리즈) 중 효능·효과에 words 중 하나라도 든 것.
    → DataFrame(index=keys.index, 칸: 걸림(bool), 문구, 위치(첫머리/본문), _ee_rank)"""
    ws = [w.replace(" ", "") for w in _words(words)]
    ex = [w.replace(" ", "") for w in _words(exclude)]
    idx, cmp_ = index()
    out = pd.DataFrame({"걸림": False, "문구": "", "위치": "", "_ee_rank": 9e9}, index=keys.index)
    if not ws:
        return out
    for i, k in keys.items():
        c = cmp_.get(k)
        if not isinstance(c, str) or not any(w in c for w in ws):
            continue
        if ex:
            parts = [p for p in re.split(r"[|.;]", c) if any(w in p for w in ws)]
            if parts and all(any(e in p for e in ex) for p in parts):
                continue
        sc, w0 = min((_score(c, w), w) for w in ws if w in c)
        out.at[i, "걸림"] = True
        out.at[i, "문구"] = _snippet(idx[k], w0)
        out.at[i, "위치"] = "첫머리" if sc < 1000 else "본문"
        out.at[i, "_ee_rank"] = sc
    return out
