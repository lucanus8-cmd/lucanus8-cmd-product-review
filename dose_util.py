"""용량 다듬기 (매출 분석용).

IQVIA 원자료의 '용량' 칸은 복합제도 첫 성분 용량만 적혀 있다 (예: 트윈콤비 40/10, 40/5 → 둘 다 '40MG').
팩명에는 '40MG/10'처럼 두 용량이 다 있으므로, 팩명에서 읽은 값의 첫 용량이 '용량' 칸과 같을 때만
그 값으로 바꾼다. 같지 않으면 원래 값을 그대로 둔다.
"""
import re

import pandas as pd

_UNIT = r"(?:MCG|MG|UG|µG|G|ML|IU|%)"
# 숫자+단위 뒤에 '/숫자(+단위)'가 하나 이상 이어지는 덩어리
# 예: 40MG/10 · 1G/200MG · 10MG/5MG/12.5MG · 50MG/500. (뒤 점) · 10MG/.5G (앞 점)
_NUM = r"(?:\d+(?:\.\d*)?|\.\d+)"
_PACK_DOSE = re.compile(
    rf"(?<![\d.])(\d+(?:\.\d+)?)\s*({_UNIT})((?:\s*/\s*{_NUM}\s*{_UNIT}?)+)(?![\d])",
    re.IGNORECASE)
_PART = re.compile(rf"({_NUM})\s*({_UNIT})?", re.IGNORECASE)


def _fmt(n):
    """'10.' → '10', '.5' → '0.5', '12.50' → '12.5'"""
    return ("%.4f" % float(n)).rstrip("0").rstrip(".")


def _norm(s):
    return re.sub(r"\s+", "", str(s)).upper()


def pack_dose(pack, dose):
    """팩명에서 복합 용량을 읽어 '40MG/10' 꼴로 돌려준다. 못 읽거나 '용량' 칸과 안 맞으면 None."""
    if not isinstance(pack, str) or "/" not in pack:
        return None
    m = _PACK_DOSE.search(pack.upper())
    if not m:
        return None
    first_num, first_unit = m.group(1), m.group(2).upper()
    first = f"{first_num}{first_unit}"
    d = _norm(dose) if dose is not None and not (isinstance(dose, float) and pd.isna(dose)) else ""
    if d in ("", "NAN", "NONE"):
        d = ""
    if d and d != first:
        return None
    parts = [first]
    for num, unit in _PART.findall(m.group(3)):
        unit = unit.upper()
        if unit == "ML" or first_unit == "ML":   # 5MG/5ML 같은 농도 표기는 복합제가 아니므로 건드리지 않는다
            return None
        parts.append(f"{_fmt(num)}{unit}")      # 팩명에 단위가 없으면 지어내지 않는다 (40MG/10)
    return "/".join(parts)


def fix_iqvia_dose(df):
    """IQVIA 표의 '용량'을 팩명 기준 복합 용량으로 바꾼다 (복합제만, 표를 그 자리에서 고친다).
    바뀐 행 수를 함께 돌려준다."""
    if df is None or "팩명" not in df.columns or "용량" not in df.columns:
        return df, 0
    packs = df["팩명"].tolist()
    doses = df["용량"].tolist()
    out = list(doses)
    n = 0
    for i, (p, d) in enumerate(zip(packs, doses)):   # 위치로 바꾼다 (행 번호가 겹쳐도 안전)
        v = pack_dose(p, d)
        if v is not None:
            out[i] = v
            n += 1
    if n:
        df["용량"] = pd.Series(out, index=df.index, dtype=object)
    return df, n


_NUMS = re.compile(r"\d+(?:\.\d+)?|\.\d+")


def dose_key(s):
    """'40MG/10MG', '40mg/10mg', '40/10' → '40/10'. 숫자가 없으면 ''."""
    nums = _NUMS.findall(str(s))
    return "/".join(str(float(n)).rstrip("0").rstrip(".") for n in nums)


def dose_match(series, query):
    """용량 검색: 단위·대소문자·띄어쓰기와 상관없이 숫자 묶음으로 맞춘다.
    '40' → 40, 40/10, 40/5 (140은 아님) · '40/10' → 40/10만 · '10mg' → 10 (100은 아님)."""
    q = dose_key(query)
    if not q:
        return series.astype(str).str.contains(str(query).strip(), case=False, na=False, regex=False)
    pat = re.compile(rf"(?:^|/){re.escape(q)}(?:/|$)")
    keys = series.astype(str).map(dose_key)
    return keys.map(lambda k: bool(pat.search(k))).astype(bool)


# ── 단일제 / 복합제 구분 (매출 분석) ─────────────────────────────────────────
COMBO_LABELS = ["단일제", "복합제"]


def iqvia_combo(df):
    """IQVIA: '단일제/복합제' 칸(SINGLE/COMBINE)을 그대로 쓴다. 칸이 없으면 성분명의 '+' 로 판단."""
    col = next((c for c in df.columns if str(c).replace(" ", "") in ("단일제/복합제", "단일/복합")), None)
    if col is not None:
        v = df[col].astype(str).str.upper().str.strip()
        out = pd.Series("단일제", index=df.index, dtype=object)
        out[v.str.startswith("COMB")] = "복합제"
        unknown = ~v.str.startswith("COMB") & ~v.str.startswith("SING")
        if unknown.any() and "성분명" in df.columns:
            out[unknown & df["성분명"].astype(str).str.contains("+", regex=False)] = "복합제"
        return out
    if "성분명" in df.columns:
        return df["성분명"].astype(str).str.contains("+", regex=False).map({True: "복합제", False: "단일제"})
    return pd.Series("단일제", index=df.index, dtype=object)


_ING_NAME = re.compile(r"[a-z][a-z\- ]*[a-z]")


def ubist_ingredients(text):
    """UBIST '성분' 글자 → 성분 이름 묶음.
    예: 'anagliptin 100㎎, metformin hydrochloride 1000㎎ [648400ATB]' → {anagliptin, metformin}
        'rosuvastatin calcium 10.4㎎ (10㎎ as rosuvastatin) [454001ATB]' → {rosuvastatin}
    쉼표로 나눈 조각마다 첫 영문 단어(성분명)를 모은다. 같은 성분이 두 번 나와도 하나로 센다."""
    t = re.sub(r"\[[^\]]*\]", "", str(text or ""))
    t = re.sub(r"\([^)]*\)", "", t).lower().replace("·", ",")   # streptodornase·streptokinase 같은 표기도 두 성분
    names = set()
    for part in t.split(","):
        m = _ING_NAME.search(part)
        if m:
            names.add(m.group(0).split()[0])
    return names


def ubist_combo(df):
    """UBIST: 성분 칸에 서로 다른 성분이 2개 이상이면 복합제."""
    col = "성분" if "성분" in df.columns else ("성분명" if "성분명" in df.columns else None)
    if col is None:
        return pd.Series("단일제", index=df.index, dtype=object)
    s = df[col].astype(str)
    uniq = pd.Series(s.unique())
    kind = dict(zip(uniq, uniq.map(lambda x: "복합제" if len(ubist_ingredients(x)) >= 2 else "단일제")))
    return s.map(kind)
