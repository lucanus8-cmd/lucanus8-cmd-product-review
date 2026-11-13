"""1상 임상시험 비용 어림 계산.

대상자 수 · 기 수 · 채혈 포인트 · 분석 성분 수 · 마지막 채혈 시점, 다섯 개로
총 비용을 대략 잡는다. 견적을 대신하는 것이 아니라 규모를 가늠하는 용도다.

단가는 이 파일에 적지 않는다. 저장소가 공개 상태이고 단가는 실제 견적에서
나온 값이라, 화면이 Streamlit Secrets([phase1_cost])에서 읽어 넘겨준다.

    임상 CRO = cro_base + cro_per_period × 기 + cro_per_subject × 대상자
    분석     = (lab_base + lab_per_sample × 검체 수) × 성분 수
    병원     = 병원 단가(채혈 시점) × 대상자 × 기      ← 실시기관 비용 + 사례비
    검체 수  = 대상자 × 기 × 기당 채혈 포인트
"""

# (키, 화면 이름, 설명, 입력칸 증감 단위)
PRICE_KEYS = [
    ("cro_base", "임상 CRO 기본",
     "인원·기 수와 상관없이 드는 돈 (계획서, 자료관리 구축, 통계, 결과보고서, 기관 기본)", 1_000_000),
    ("cro_per_period", "임상 CRO · 1기당",
     "기가 하나 늘 때 붙는 돈 (모니터링 방문, 관리 기간)", 100_000),
    ("cro_per_subject", "임상 CRO · 대상자 1명당",
     "대상자가 1명 늘 때 붙는 돈 (자료 점검, 방문·기간 증가분)", 10_000),
    ("lab_base", "분석 기본",
     "검체 수와 상관없이 드는 돈 (분석법 조건 설정, 검증, 보고서)", 1_000_000),
    ("lab_per_sample", "분석 · 검체 1개당",
     "시료 분석에 재분석·관리비를 얹은 값", 1_000),
    ("site_24h", "병원 · 1명 1기당 (채혈 24시간 이하)",
     "실시기관 비용과 사례비를 합친 값. 입원 중에 채혈이 끝나는 경우", 10_000),
    ("site_72h", "병원 · 1명 1기당 (채혈 72시간)",
     "실시기관 비용과 사례비를 합친 값", 10_000),
    ("site_240h", "병원 · 1명 1기당 (채혈 240시간 이상)",
     "실시기관 비용과 사례비를 합친 값. 외래 방문이 여러 번인 경우", 10_000),
]
KEYS = [k for k, _, _, _ in PRICE_KEYS]

# 병원 단가의 기준점(시간). 그 사이는 직선으로 잇고, 바깥은 끝값으로 둔다.
ANCHORS = (24.0, 72.0, 240.0)


def clean(prices):
    """단가 묶음을 숫자로 맞춘다. 없거나 숫자가 아니면 0."""
    out = {}
    for k in KEYS:
        try:
            v = float((prices or {}).get(k, 0) or 0)
        except (TypeError, ValueError):
            v = 0.0
        out[k] = v if v > 0 else 0.0
    return out


def ready(prices):
    """계산할 만큼 단가가 들어 있나 — 전부 0 이면 아니다."""
    p = clean(prices)
    return sum(p.values()) > 0


def site_unit(hours, prices):
    """마지막 채혈 시점(투약 후 시간)에 따른 병원 단가 — 1명·1기당."""
    p = clean(prices)
    a, b, c = ANCHORS
    try:
        h = float(hours)
    except (TypeError, ValueError):
        h = a
    if h <= a:
        return p["site_24h"]
    if h >= c:
        return p["site_240h"]
    if h <= b:
        return p["site_24h"] + (p["site_72h"] - p["site_24h"]) * (h - a) / (b - a)
    return p["site_72h"] + (p["site_240h"] - p["site_72h"]) * (h - b) / (c - b)


def estimate(subjects, periods, points, analytes, hours, prices):
    """총 비용과 블록별 금액. 모두 원 단위, VAT 별도."""
    p = clean(prices)
    n = max(int(subjects or 0), 0)
    k = max(int(periods or 0), 0)
    pts = max(int(points or 0), 0)
    an = max(int(analytes or 0), 0)
    samples = n * k * pts
    cro = p["cro_base"] + p["cro_per_period"] * k + p["cro_per_subject"] * n
    lab = (p["lab_base"] + p["lab_per_sample"] * samples) * an
    unit = site_unit(hours, p)
    site = unit * n * k
    total = cro + lab + site
    return {"samples": samples, "cro": cro, "lab": lab, "site": site,
            "site_unit": unit, "total": total, "vat_total": total * 1.1}
