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
    return keys.map(lambda k: bool(pat.search(k)))
