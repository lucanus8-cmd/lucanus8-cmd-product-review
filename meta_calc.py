"""간이 메타분석 계산 (자료를 읽지 않는 순수 계산).

- 효과크기: 평균차 MD, 표준화 평균차 SMD(Hedges g), 위험비 RR, 오즈비 OR, 위험차 RD, 위험비 HR
- 합치기: 고정효과(역분산), 랜덤효과(DerSimonian-Laird)
- 이질성: Q, I², τ²
- 간접 비교: Bucher (공통 비교군 하나)
- 하나씩 빼 보기

비율(RR·OR·HR)은 로그 척도에서 합치고, 보여 줄 때만 되돌린다.
숫자는 R metafor 패키지와 같은 식을 쓴다 (tests/check_meta_calc.py 로 대조).
"""
import math

Z = 1.959963984540054          # 95% 양쪽
RATIO = {"RR", "OR", "HR"}     # 로그 척도에서 계산하는 효과크기

MEASURES = {
    "연속형": ["MD", "SMD"],
    "이분형": ["RR", "OR", "RD"],
    "생존분석": ["HR"],
}
MEASURE_LABEL = {
    "MD": "평균차 MD", "SMD": "표준화 평균차 SMD",
    "RR": "위험비 RR", "OR": "오즈비 OR", "RD": "위험차 RD", "HR": "위험비 HR",
}


def num(x):
    """칸 값 → 숫자. 비었거나 숫자가 아니면 None."""
    if x is None:
        return None
    try:
        if isinstance(x, str):
            x = x.strip().replace(",", "").replace("−", "-")
            if not x:
                return None
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def pnorm2(z):
    """양쪽 p 값."""
    return math.erfc(abs(z) / math.sqrt(2))


# ── 효과크기 ─────────────────────────────────────────────────────────
def es_cont(measure, n1=None, m1=None, sd1=None, n2=None, m2=None, sd2=None,
            d=None, lo=None, hi=None):
    """연속형 한 임상 → (y, v, 쓴 방법) 또는 (None, None, 이유).

    MD: 차이와 신뢰구간이 있으면 그것(임상이 낸 조정된 값)을, 없으면 군별 평균·SD 로.
    SMD: 군별 평균·SD·인원이 있어야 한다 (Hedges g, metafor 와 같은 분산식).
    """
    n1, m1, sd1, n2, m2, sd2 = map(num, (n1, m1, sd1, n2, m2, sd2))
    d, lo, hi = map(num, (d, lo, hi))
    arms = None not in (n1, m1, sd1, n2, m2, sd2) and n1 > 0 and n2 > 0
    if measure == "MD":
        if None not in (d, lo, hi) and hi > lo:
            return d, ((hi - lo) / (2 * Z)) ** 2, "차이·신뢰구간"
        if arms and sd1 >= 0 and sd2 >= 0 and (sd1 > 0 or sd2 > 0):
            return m1 - m2, sd1 ** 2 / n1 + sd2 ** 2 / n2, "군별 평균·SD"
        return None, None, "차이·신뢰구간 또는 군별 평균·SD가 없음"
    if measure == "SMD":
        if not arms or n1 + n2 <= 2:
            return None, None, "SMD는 군별 인원·평균·SD가 모두 필요"
        sp2 = ((n1 - 1) * sd1 ** 2 + (n2 - 1) * sd2 ** 2) / (n1 + n2 - 2)
        if sp2 <= 0:
            return None, None, "SD가 0"
        dd = (m1 - m2) / math.sqrt(sp2)
        j = 1 - 3 / (4 * (n1 + n2 - 2) - 1)
        g = j * dd
        v = 1 / n1 + 1 / n2 + g ** 2 / (2 * (n1 + n2))
        return g, v, "Hedges g"
    raise ValueError(measure)


def es_bin(measure, e1, n1, e2, n2):
    """이분형 한 임상 → (y, v, 메모) 또는 (None, None, 이유).

    칸에 0 이 있으면 네 칸 모두에 0.5 를 더한다 (metafor 기본값과 같음, RD 는 더하지 않음).
    두 군 모두 0건이면 RR·OR 은 계산하지 않는다.
    """
    e1, n1, e2, n2 = map(num, (e1, n1, e2, n2))
    if None in (e1, n1, e2, n2) or n1 <= 0 or n2 <= 0:
        return None, None, "발생 건수·인원이 없음"
    if e1 < 0 or e2 < 0 or e1 > n1 or e2 > n2:
        return None, None, "발생 건수가 인원보다 많거나 음수"
    if measure == "RD":
        p1, p2 = e1 / n1, e2 / n2
        v = p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2
        if v <= 0:
            return None, None, "두 군 모두 0% 또는 100%"
        return p1 - p2, v, ""
    if e1 == 0 and e2 == 0:
        return None, None, "두 군 모두 0건"
    if e1 == n1 and e2 == n2:
        return None, None, "두 군 모두 100%"
    a, b, c, d = e1, n1 - e1, e2, n2 - e2
    memo = ""
    if 0 in (a, b, c, d):
        a, b, c, d = a + .5, b + .5, c + .5, d + .5
        memo = "0건 보정(0.5)"
    if measure == "RR":
        return math.log((a / (a + b)) / (c / (c + d))), 1 / a - 1 / (a + b) + 1 / c - 1 / (c + d), memo
    if measure == "OR":
        return math.log((a * d) / (b * c)), 1 / a + 1 / b + 1 / c + 1 / d, memo
    raise ValueError(measure)


def es_surv(hr, lo, hi):
    hr, lo, hi = map(num, (hr, lo, hi))
    if None in (hr, lo, hi) or min(hr, lo, hi) <= 0 or hi <= lo:
        return None, None, "HR·신뢰구간이 없음"
    return math.log(hr), ((math.log(hi) - math.log(lo)) / (2 * Z)) ** 2, ""


# ── 합치기 ───────────────────────────────────────────────────────────
def pool(y, v):
    """역분산 고정효과 + DerSimonian-Laird 랜덤효과."""
    k = len(y)
    if k == 0:
        return None
    w = [1 / x for x in v]
    sw = sum(w)
    fe = sum(wi * yi for wi, yi in zip(w, y)) / sw
    q = sum(wi * (yi - fe) ** 2 for wi, yi in zip(w, y))
    if k > 1:
        c = sw - sum(wi ** 2 for wi in w) / sw
        tau2 = max(0.0, (q - (k - 1)) / c) if c > 0 else 0.0
        i2 = max(0.0, (q - (k - 1)) / q) * 100 if q > 0 else 0.0
        # Q 의 p 값 (카이제곱, 자유도 k-1)
        p_q = _chi2_sf(q, k - 1)
    else:
        tau2, i2, p_q = 0.0, 0.0, None
    ws = [1 / (vi + tau2) for vi in v]
    sws = sum(ws)
    re_ = sum(wi * yi for wi, yi in zip(ws, y)) / sws
    return dict(k=k, fe=fe, fe_se=math.sqrt(1 / sw), re=re_, re_se=math.sqrt(1 / sws),
                tau2=tau2, q=q, i2=i2, p_q=p_q,
                w_fe=[x / sw * 100 for x in w], w_re=[x / sws * 100 for x in ws])


def pick(p, method):
    """pool 결과에서 고른 방법의 (추정값, 표준오차, 가중치%)."""
    if method == "고정효과":
        return p["fe"], p["fe_se"], p["w_fe"]
    return p["re"], p["re_se"], p["w_re"]


def bucher(ya, sa, yb, sb):
    """A vs 공통, B vs 공통 → A vs B (같은 척도)."""
    d = ya - yb
    se = math.sqrt(sa ** 2 + sb ** 2)
    return d, se, pnorm2(d / se) if se > 0 else None


def leave_one_out(y, v, method):
    out = []
    for i in range(len(y)):
        yy = y[:i] + y[i + 1:]
        vv = v[:i] + v[i + 1:]
        if not yy:
            out.append(None)
            continue
        p = pool(yy, vv)
        m, s, _ = pick(p, method)
        out.append((m, s, p["i2"]))
    return out


def show(measure, y):
    """계산 척도 → 보여 줄 값 (비율은 되돌림)."""
    return math.exp(y) if measure in RATIO else y


def ci(measure, y, se):
    return show(measure, y), show(measure, y - Z * se), show(measure, y + Z * se)


def null_value(measure):
    return 1.0 if measure in RATIO else 0.0


def fmt(measure, x, signed=None):
    if x is None:
        return "—"
    if measure in RATIO:
        return f"{x:.2f}"
    if measure == "RD":
        x = x * 100       # 위험차는 %p 로 보여 준다
    s = f"{abs(x):.2f}" if measure == "SMD" else f"{abs(x):.1f}"
    sign = "+" if x > 0 else "−" if x < 0 else ""
    return (sign if signed is not False else ("−" if x < 0 else "")) + s


def fmt_ci(measure, y, se):
    m, lo, hi = ci(measure, y, se)
    return f"{fmt(measure, m)} [{fmt(measure, lo)}, {fmt(measure, hi)}]"


# ── 결론 문장 (정해진 규칙) ──────────────────────────────────────────
def verdict(measure, y, se, lower_better, name_a, name_b, what):
    """A − B (또는 A/B) 추정값으로 한 줄 결론. 신뢰구간이 기준선을 포함하면 '차이 없음'."""
    m, lo, hi = ci(measure, y, se)
    nul = null_value(measure)
    if lo <= nul <= hi:
        return f"{what}: 두 성분 사이 **차이가 확인되지 않습니다** (신뢰구간이 기준선을 포함)."
    a_lower = m < nul
    better = a_lower == lower_better
    word = "더 낮게" if a_lower else "더 높게"
    good = "유리" if better else "불리"
    # 조사(이/가, 을/를)가 이름 끝 글자에 따라 달라서 '쪽이'로 쓴다
    return f"{what}: **{name_a}** 쪽이 **{name_b}**보다 {word} 나옵니다 — {name_a} 쪽이 **{good}**합니다."


# ── 카이제곱 꼬리확률 (scipy 없이) ──────────────────────────────────
def _chi2_sf(x, k):
    if x <= 0:
        return 1.0
    return _gammaincc(k / 2.0, x / 2.0)


def _gammaincc(a, x):
    """정규화 상부 불완전 감마 함수 Q(a, x)."""
    if x < a + 1:
        # 급수
        ap, s, de = a, 1.0 / a, 1.0 / a
        for _ in range(500):
            ap += 1
            de *= x / ap
            s += de
            if abs(de) < abs(s) * 1e-15:
                break
        p = s * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return max(0.0, 1.0 - p)
    # 연분수
    b = x + 1 - a
    c = 1 / 1e-300
    d = 1 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        de = d * c
        h *= de
        if abs(de - 1) < 1e-15:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))
