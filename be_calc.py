"""생물학적동등성시험 피험자 수 계산 (TOST · 평균생동).

화면과 분리해 둔다 — 숫자만 다루므로 따로 돌려 검산할 수 있다.
scipy 를 쓰지 않는다(의존성을 늘리지 않으려고). 필요한 분포 함수는
Numerical Recipes 방식으로 직접 넣고, 교과서 값으로 검산했다.

검산 기준: 2×2 교차 · CV 30% · T/R 0.95 · 검정력 80%
         → 40명, 검정력 0.815845 (PowerTOST 문서 예제와 일치)
"""
import math

# ── 설계 ────────────────────────────────────────────────────────────────
# 표기는 '순서 × 기간'. 분산 계수(bk)와 자유도는 PowerTOST 설계 목록에서 확인한 값.
#   SE² = bk · σ² / N
# 순서×기간만으로는 설계가 하나로 안 정해진다(부분반복도 3×3, 라틴방격도 3×3).
# 그래서 순서 문자열을 함께 들고 다닌다.
DESIGNS = {
    "2x2": {"label": "2×2 교차", "seq": "RT / TR", "bk": 2.0,
            "df": lambda n: n - 2, "step": 2, "cv": "개체내",
            "note": "가장 흔한 설계. 한 사람이 시험약과 대조약을 한 번씩."},
    "2x3": {"label": "2×3 반복", "seq": "RTR / TRT", "bk": 1.5,
            "df": lambda n: 2 * n - 3, "step": 2, "cv": "개체내",
            "note": "대조약을 두 번 투여해 개체내 변동을 더 정확히 본다."},
    "2x4": {"label": "2×4 완전반복", "seq": "RTRT / TRTR", "bk": 1.0,
            "df": lambda n: 3 * n - 4, "step": 2, "cv": "개체내",
            "note": "둘 다 두 번씩. 인원이 가장 적지만 기간·채혈이 가장 많다."},
    "3x3": {"label": "3×3 부분반복", "seq": "TRR / RTR / RRT", "bk": 1.5,
            "df": lambda n: 2 * n - 3, "step": 3, "cv": "개체내",
            "note": "대조약만 두 번. 인원은 3의 배수가 된다."},
    "2x1": {"label": "2×1 병렬", "seq": "두 군 (기간 1)", "bk": 4.0,
            "df": lambda n: n - 2, "step": 2, "cv": "전체",
            "note": "교차가 아니라 두 집단 비교. 반감기가 아주 긴 약 등에 쓴다."},
}
DESIGN_ORDER = ["2x2", "2x3", "2x4", "3x3", "2x1"]


# ── 분포 함수 ───────────────────────────────────────────────────────────
def _gser(a, x):
    ap, s, d = a, 1.0 / a, 1.0 / a
    for _ in range(500):
        ap += 1
        d *= x / ap
        s += d
        if abs(d) < abs(s) * 1e-14:
            break
    return s * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a, x):
    tiny = 1e-300
    b, c, d = x + 1 - a, 1 / tiny, 1 / (x + 1 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        de = d * c
        h *= de
        if abs(de - 1) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammp(a, x):
    if x <= 0:
        return 0.0
    return _gser(a, x) if x < a + 1 else 1.0 - _gcf(a, x)


def _betacf(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        de = d * c
        h *= de
        if abs(de - 1) < 1e-14:
            break
    return h


def betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def t_cdf(t, df):
    p = 0.5 * betai(df / 2, 0.5, df / (df + t * t))
    return 1 - p if t > 0 else p


def t_ppf(p, df):
    lo, hi = -100.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def chi2_cdf(x, df):
    return gammp(df / 2, x / 2)


def chi2_ppf(p, df):
    lo, hi = 0.0, max(1000.0, df * 10)
    for _ in range(300):
        mid = (lo + hi) / 2
        if chi2_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def chi2_pdf(x, df):
    if x <= 0:
        return 0.0
    k = df / 2
    return math.exp((k - 1) * math.log(x) - x / 2 - k * math.log(2) - math.lgamma(k))


# ── 검정력 · 피험자 수 ──────────────────────────────────────────────────
def power(design, n, cv, ratio=0.95, alpha=0.05, limit=1.25, steps=400):
    """TOST 의 검정력. n 은 완료 기준 전체 피험자 수."""
    d0 = DESIGNS.get(design)
    if d0 is None or cv <= 0 or n < 2:
        return 0.0
    df = d0["df"](n)
    if df < 1:
        return 0.0
    s = math.sqrt(math.log(1 + cv ** 2))
    se = s * math.sqrt(d0["bk"] / n)
    t = t_ppf(1 - alpha, df)
    D, d = math.log(limit), math.log(ratio)

    def f(u):
        sh = se * math.sqrt(u / df)
        hi = (D - t * sh - d) / se
        lo = (-D + t * sh - d) / se
        return max(norm_cdf(hi) - norm_cdf(lo), 0.0) * chi2_pdf(u, df)

    a, b = chi2_ppf(1e-10, df), chi2_ppf(1 - 1e-10, df)
    h = (b - a) / steps
    tot = f(a) + f(b)
    for i in range(1, steps):
        tot += f(a + i * h) * (4 if i % 2 else 2)
    return min(max(tot * h / 3, 0.0), 1.0)


def sample_size(design, cv, target=0.80, ratio=0.95, alpha=0.05,
                limit=1.25, nmax=1000):
    """검정력을 넘는 가장 작은 N(설계의 순서 수 배수). 못 찾으면 None."""
    d0 = DESIGNS.get(design)
    if d0 is None or cv <= 0:
        return None
    step = d0["step"]
    n = step * 2
    while n <= nmax:
        if power(design, n, cv, ratio, alpha, limit) >= target:
            return n
        n += step
    return None


def enroll(n, dropout, step=2):
    """탈락을 감안한 등재 인원 — 설계의 순서 수 배수로 올린다."""
    if n is None:
        return None
    if dropout <= 0:
        return n
    need = math.ceil(n / (1 - dropout))
    if need % step:
        need += step - (need % step)
    return need


def plan(design, cv_auc, cv_cmax, target=0.80, ratio=0.95, alpha=0.05,
         limit=1.25, dropout=0.0):
    """AUC·Cmax 중 더 많이 필요한 쪽으로 정한다. 화면이 쓰는 입구."""
    out = {"design": design, "parts": [], "n": None, "enroll": None,
           "driver": None, "power": None}
    for name, cv in (("AUC", cv_auc), ("Cmax", cv_cmax)):
        if cv and cv > 0:
            n = sample_size(design, cv, target, ratio, alpha, limit)
            p = power(design, n, cv, ratio, alpha, limit) if n else None
            out["parts"].append({"name": name, "cv": cv, "n": n, "power": p})
    done = [p for p in out["parts"] if p["n"]]
    if not done:
        return out
    top = max(done, key=lambda p: p["n"])
    out["n"], out["driver"], out["power"] = top["n"], top["name"], top["power"]
    out["enroll"] = enroll(top["n"], dropout, DESIGNS[design]["step"])
    return out
