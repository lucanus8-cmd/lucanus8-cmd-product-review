"""성분 이름 다듬기 (지금 사이트 scout_sources 의 같은 함수들을 그대로 옮김 — 결과가 같아야 한다)."""
import re

import pandas as pd


def ing_key(s):
    """영문 성분명 → 조인 키 (첫 알파벳 단어, 3자 이상 대문자)."""
    for tok in re.split(r"[^A-Za-z]+", str(s or "")):
        if len(tok) >= 3:
            return tok.upper()
    return ""


_SALT_SUFFIX = sorted([
    "브롬화수소산염", "메탄술폰산염", "타르타르산염", "말레산염", "푸마르산염", "숙신산염",
    "베실산염", "메실산염", "토실산염", "글루콘산염", "구연산염", "시트르산염", "주석산염",
    "아세트산염", "초산염", "젖산염", "염산염", "황산염", "인산염", "질산염", "탄산염", "중탄산염",
    "이나트륨", "일나트륨", "칼슘", "칼륨", "나트륨", "마그네슘", "아연",
    "삼수화물", "이수화물", "일수화물", "반수화물", "사수화물", "수화물", "무수물", "무수", "염",
], key=len, reverse=True)


def ing_core(s):
    """한글 성분명 → 코어. '[코드]'·괄호·공백·염/수화물 접미어 제거, 조합제는 첫 성분."""
    s = re.sub(r"\[[^\]]*\]", "", str(s or ""))
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+", "", s)
    s = re.split(r"[/,]|및|\+", s)[0]
    changed = True
    while changed:
        changed = False
        for suf in _SALT_SUFFIX:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                s = s[: -len(suf)]
                changed = True
                break
    return s.strip()


def nospace(s):
    return re.sub(r"\s+", "", str(s or ""))


def clean_name(s):
    """'[M264293]리바록사반(미분화)' → '리바록사반(미분화)' 처럼 앞의 코드만 뗀다."""
    return re.sub(r"\[[^\]]*\]", "", str(s or "")).strip()


def is_single_series(*srs):
    m = None
    for sr in srs:
        t = sr.astype(str).str.replace(r"\[[^\]]*\]|\([^)]*\)", "", regex=True)
        one = ~t.str.contains(r"[/,]|및|\+", regex=True, na=False)
        m = one if m is None else (m & one)
    return m if m is not None else pd.Series(dtype=bool)


# 제형어(정/캡슐/액 등) — 브랜드 핵심어만 남길 때 뗀다. 긴 것이 앞에 와야 한다.
FORM_RE = (r"(서방정|속붕정|장용정|설하정|츄어블정|구강붕해정|정제|정|연질캡슐|경질캡슐|캡슐|"
           r"주사액|주사|프리필드시린지|건조시럽|시럽|과립|산제|산|점안액|점비액|점이액|"
           r"현탁액|흡입액|외용액|액|크림|연고|겔|패치|좌제)$")


def edi_codes(v):
    """허가시트 보험코드(EDI)에서 9자리 코드들 (지금 사이트 _edi_codes 와 같음)."""
    raw = str(v).strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
        d = re.sub(r"\D", "", raw)
        if len(d) % 9 == 8:
            d = "0" + d
        return {d[:9]} if len(d) >= 9 else set()
    out = set()
    for p in parts:
        d = re.sub(r"\D", "", p)
        if len(d) == 9:
            out.add(d)
        elif len(d) == 8:
            out.add("0" + d)
    return out


def calc_cagr(values, labels, min_base=1e8):
    def yr(c):
        m = re.search(r"(\d{4})", str(c))
        return int(m.group(1)) if m else 0
    pairs = [(l, v) for l, v in zip(labels, values) if v and v > 0]
    starts = [(l, v) for l, v in pairs if v >= min_base]
    if not starts or len(pairs) < 2:
        return None
    sl, sv = starts[0]
    el, ev = pairs[-1]
    n = yr(el) - yr(sl)
    return None if n <= 0 or ev <= 0 else ((ev / sv) ** (1 / n) - 1) * 100


def year_of(c):
    m = re.search(r"(\d{4})", str(c))
    return m.group(1) if m else str(c)
