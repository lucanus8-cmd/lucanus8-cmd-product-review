"""생물학적동등성시험 피험자 수 계산 (TOST · 평균생동 ABE).

화면과 분리해 둔다 — 숫자만 다루므로 따로 돌려 검산할 수 있다.
scipy 를 쓰지 않는다(의존성·메모리를 늘리지 않으려고). 필요한 분포 함수는
Numerical Recipes 방식으로 직접 넣고 교과서 값으로 맞췄다.

검산 (tests/check_be_calc.py 로 다시 돌릴 수 있다)
  · 2x2 · CV 30% · GMR 0.95 · 검정력 80% → 40명 · 0.815845
    (PowerTOST 문서 예제와 소수 여섯째 자리까지 일치)
  · 설계 12개 × CV 20·25·30·35·40 (60점)과, GMR·검정력·α·생동한계를
    바꾼 24점을 biostatistics.ca 의 PowerTOST 계산기에 직접 넣어 받은 값과
    대조 — 인원·달성검정력 모두 전부 일치.

설계 상수(bk·자유도·배수)는 PowerTOST 원본 소스(DesignHelpers.R)에서
확인한 값만 쓴다. 추측으로 채우지 않는다.
"""
import math

# ── 설계 ────────────────────────────────────────────────────────────────
#   SE² = bk · σ² / N        (N 은 완료 기준 '전체' 피험자 수)
#   step = 순서(군) 수 — 인원은 이 수의 배수로만 나온다
#   cv   = 넣어야 하는 CV 의 종류 (개체내 / 전체)
#   rare = 생동시험에서 거의 쓰지 않는 설계 → 목록 아래로 내린다
#
# key 는 PowerTOST·biostatistics.ca 가 쓰는 이름 그대로 두어 대조할 수 있게 한다.
# PowerTOST 의 2x2 와 2x2x2 는 '같은 설계'다(bk·자유도가 같고 인원도 같다).
# 그래서 2x2 하나로 둔다.
DESIGNS = {
    "2x2": {
        "label": "2×2 교차", "seq": "RT / TR", "bk": 2.0,
        "df": lambda n: n - 2, "step": 2, "cv": "개체내", "rare": False,
        "dfs": "n − 2",
        "note": "가장 흔한 설계. 한 사람이 시험약과 대조약을 한 번씩 받는다.",
    },
    "2x2x3": {
        "label": "2×2×3 반복", "seq": "RTR / TRT", "bk": 1.5,
        "df": lambda n: 2 * n - 3, "step": 2, "cv": "개체내", "rare": False,
        "dfs": "2n − 3",
        "note": "대조약을 두 번 투여해 개체내 변동을 더 정확히 본다. 3기간.",
    },
    "2x2x4": {
        "label": "2×2×4 완전반복", "seq": "RTRT / TRTR", "bk": 1.0,
        "df": lambda n: 3 * n - 4, "step": 2, "cv": "개체내", "rare": False,
        "dfs": "3n − 4",
        "note": "둘 다 두 번씩. 인원이 가장 적지만 기간·채혈이 가장 많다.",
    },
    "2x3x3": {
        "label": "부분반복 2×3×3", "seq": "TRR / RTR / RRT", "bk": 1.5,
        "df": lambda n: 2 * n - 3, "step": 3, "cv": "개체내", "rare": False,
        "dfs": "2n − 3",
        "note": "대조약만 두 번. 순서가 3가지라 인원은 3의 배수가 된다.",
    },
    "2x4x4": {
        "label": "2×4×4 완전반복", "seq": "4순서 4기간", "bk": 1.0,
        "df": lambda n: 3 * n - 4, "step": 4, "cv": "개체내", "rare": False,
        "dfs": "3n − 4",
        "note": "완전반복을 4순서로 나눈 것. 인원은 4의 배수.",
    },
    "parallel": {
        "label": "병렬 (두 군)", "seq": "두 군 · 기간 1", "bk": 4.0,
        "df": lambda n: n - 2, "step": 2, "cv": "전체", "rare": False,
        "dfs": "n − 2",
        "note": "교차가 아니라 두 집단 비교. 반감기가 아주 긴 약 등에 쓴다.",
    },
    "3x3": {
        "label": "3×3 라틴방격 (3제제)", "seq": "ABC / BCA / CAB", "bk": 2.0,
        "df": lambda n: 2 * n - 4, "step": 3, "cv": "개체내", "rare": False,
        "dfs": "2n − 4",
        "note": "제제가 3개일 때. 순서 3가지, 기간 3개.",
    },
    "3x6x3": {
        "label": "3×6×3 Williams (3제제)", "seq": "6순서 3기간", "bk": 2.0,
        "df": lambda n: 2 * n - 4, "step": 6, "cv": "개체내", "rare": False,
        "dfs": "2n − 4",
        "note": "제제 3개를 6순서로 균형 배치. 인원은 6의 배수.",
    },
    "4x4": {
        "label": "4×4 Williams (4제제)", "seq": "4순서 4기간", "bk": 2.0,
        "df": lambda n: 3 * n - 6, "step": 4, "cv": "개체내", "rare": False,
        "dfs": "3n − 6",
        "note": "제제가 4개일 때. 인원은 4의 배수.",
    },
    "2x4x2": {
        "label": "Balaam 2×4×2", "seq": "RT / TR / RR / TT", "bk": 8.0,
        "df": lambda n: n - 2, "step": 4, "cv": "개체내", "rare": True,
        "dfs": "n − 2",
        "note": "기간 2개로 개체내 변동까지 보려는 설계. RR·TT 군은 시험약과 "
                "대조약을 둘 다 받지 않아 T/R 비교에 직접 쓰이지 않는다 "
                "→ 같은 CV에서 2×2보다 약 4배의 인원이 든다.",
    },
    "2x2x2r": {
        "label": "Liu 반복교차 2×2×2r", "seq": "TT|RR / RR|TT", "bk": 1.0,
        "df": lambda n: 3 * n - 2, "step": 2, "cv": "개체내", "rare": True,
        "dfs": "3n − 2",
        "note": "한 기간에 같은 약을 두 번 투여·측정한다. 기간을 4개로 늘리지 "
                "않고 반복설계의 효율을 얻으려는 설계 (Liu, 1995).",
    },
    "paired": {
        "label": "대응표본 (교차 아님)", "seq": "짝지은 두 값", "bk": 2.0,
        "df": lambda n: n - 1, "step": 1, "cv": "개체내", "rare": True,
        "dfs": "n − 1",
        "note": "순서를 무작위로 배정하지도, 기간을 나누지도 않는다. 약의 효과와 "
                "기간 효과를 갈라낼 수 없어 생동시험으로는 인정되지 않는다.",
    },
}

# 화면에 보일 순서 — 자주 쓰는 설계부터, 드문 설계는 맨 아래
DESIGN_ORDER = ["2x2", "2x2x3", "2x2x4", "2x3x3", "2x4x4", "parallel",
                "3x3", "3x6x3", "4x4", "2x4x2", "2x2x2r", "paired"]


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
def power(design, n, cv, gmr=0.95, alpha=0.05, lo=0.8, hi=1.25, steps=None):
    """TOST 의 검정력. n 은 '완료 기준 전체' 피험자 수. 못 구하면 0.0.

    steps 는 적분 구간 분할 수. 자유도가 아주 작으면(인원이 한 자리) χ² 밀도가
    0 근처로 몰려 400 분할로는 소수 둘째 자리가 흔들린다 → 그때만 촘촘하게 쓴다.
    자유도 10 이상에서는 400 분할로 외부 계산기와 소수 둘째 자리까지 일치한다.
    """
    d0 = DESIGNS.get(design)
    if d0 is None or not n or cv <= 0 or n < 2:
        return 0.0
    df = d0["df"](n)
    if df < 2:
        return 0.0
    if steps is None:
        steps = 400 if df >= 10 else 8000
    s = math.sqrt(math.log(1 + cv ** 2))
    se = s * math.sqrt(d0["bk"] / n)
    t = t_ppf(1 - alpha, df)
    U, L, d = math.log(hi), math.log(lo), math.log(gmr)

    def f(u):
        sh = se * math.sqrt(u / df)
        return max(norm_cdf((U - t * sh - d) / se)
                   - norm_cdf((L + t * sh - d) / se), 0.0) * chi2_pdf(u, df)

    a, b = chi2_ppf(1e-10, df), chi2_ppf(1 - 1e-10, df)
    h = (b - a) / steps
    tot = f(a) + f(b)
    for i in range(1, steps):
        tot += f(a + i * h) * (4 if i % 2 else 2)
    return min(max(tot * h / 3, 0.0), 1.0)


def sample_size(design, cv, target=0.80, gmr=0.95, alpha=0.05,
                lo=0.8, hi=1.25, nmax=2000):
    """검정력을 넘는 가장 작은 N(설계의 배수). 못 찾으면 None."""
    d0 = DESIGNS.get(design)
    if d0 is None or cv <= 0 or not 0 < target < 1:
        return None
    # 순서 수의 배수로만 올린다. 외부 계산기(PowerTOST)는 인원이 2명인 답도
    # 내놓으므로(CV 가 아주 낮을 때) 시작점을 낮춰 그 답을 건너뛰지 않는다.
    step = d0["step"]
    n = step
    while n <= nmax:
        if power(design, n, cv, gmr, alpha, lo, hi) >= target:
            return n
        n += step
    return None


def enroll(n, dropout, step=2):
    """탈락을 감안한 등록 인원 — 설계의 배수로 올린다."""
    if not n:
        return None
    if dropout <= 0:
        return n
    if dropout >= 1:
        return None
    need = math.ceil(n / (1 - dropout))
    if need % step:
        need += step - (need % step)
    return need
