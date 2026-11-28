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
