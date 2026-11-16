"""ClinicalTrials.gov 공개 API 접속 시험 + 두 약의 위약 대조 임상으로 간이 메타분석·간접 비교.

- 1) API 에 접속되는지(응답 코드·시간)
- 2) 약마다 2형 당뇨 3상, 결과가 올라온 임상을 받아서
     1차 평가변수 'HbA1c 기준 대비 변화(24주, %)' 중 약(정해진 용량) vs 위약 차이를 뽑는다.
     임상의 통계분석(조정된 차이와 신뢰구간)이 있으면 그것을, 없으면 군별 평균·산포로 계산.
- 3) 약마다 랜덤효과(DerSimonian-Laird)로 합치고, 위약을 공통 비교군으로 Bucher 간접 비교.
읽기만 한다. 공개 자료만 적는다.
"""
import math
import os
import re
import sys
import time

import requests

API = "https://clinicaltrials.gov/api/v2/studies"
DRUGS = [d.strip() for d in (os.environ.get("DRUGS") or "empagliflozin:10,dapagliflozin:10").split(",") if d.strip()]
COND = os.environ.get("COND") or "type 2 diabetes"
WEEK = os.environ.get("WEEK") or "24"
OTHER = ("linagliptin", "sitagliptin", "saxagliptin", "metformin", "glimepiride", "glipizide",
         "pioglitazone", "insulin", "exenatide", "dapagliflozin", "empagliflozin", "canagliflozin",
         "ertugliflozin", "liraglutide", "semaglutide")
Z = 1.959964


def get(params):
    t = time.time()
    r = requests.get(API, params=params, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    return r, time.time() - t


def studies(drug):
    base = {"query.intr": drug, "query.cond": COND, "pageSize": 100, "format": "json"}
    out, tok, used_agg = [], None, True
    for _ in range(10):
        p = dict(base, aggFilters="phase:3,results:with") if used_agg else dict(base)
        if tok:
            p["pageToken"] = tok
        r, _ = get(p)
        if r.status_code == 400 and used_agg and not out:
            used_agg = False
            continue
        r.raise_for_status()
        j = r.json()
        out += j.get("studies", [])
        tok = j.get("nextPageToken")
        if not tok:
            break
    if not used_agg:
        out = [s for s in out if s.get("hasResults")
               and "PHASE3" in (s["protocolSection"].get("designModule", {}).get("phases") or [])]
    return out


def fnum(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def words_in(t):
    t = t.lower()
    return {w for w in OTHER if w in t}


def pick_groups(groups, drug, dose):
    dg = [g for g in groups if drug in g["title"].lower() and not g["title"].lower().startswith("placebo")
          and re.search(rf"(?<![\d.]){dose}\s*mg", g["title"].lower())]
    pg = [g for g in groups if "placebo" in g["title"].lower() and drug not in g["title"].lower()]
    if not pg:
        pg = [g for g in groups if g["title"].lower().startswith("placebo")]
    for d in dg:
        for p in pg:
            extra = words_in(d["title"]) - words_in(p["title"]) - {drug}
            if not extra:
                return d, p
    return None, None


def is_a1c(o):
    t = (o.get("title", "") + " " + o.get("description", "")).lower()
    return (("hba1c" in t or "a1c" in t or "glycosylated h" in t or "glycated h" in t)
            and "change" in t and "fasting" not in o.get("title", "").lower())


def week_ok(o):
    tf = (o.get("timeFrame", "") + " " + o.get("title", "")).lower()
    return re.search(rf"(week|wk)s?\s*{WEEK}\b|\b{WEEK}\s*(week|wk)", tf) is not None


def unit_ok(o):
    u = (o.get("unitOfMeasure") or "").lower()
    return "%" in u or "percent" in u


def arm_md(o, gd, gp):
    meas = {}
    try:
        for m in o["classes"][0]["categories"][0]["measurements"]:
            meas[m["groupId"]] = m
        n = {c["groupId"]: fnum(c["value"]) for c in o["denoms"][0]["counts"]}
    except Exception:
        return None
    a, b = meas.get(gd), meas.get(gp)
    if not a or not b or not n.get(gd) or not n.get(gp):
        return None
    disp = (o.get("dispersionType") or "").upper()

    def sd(m, k):
        if "DEVIATION" in disp:
            return fnum(m.get("spread"))
        if "ERROR" in disp:
            s = fnum(m.get("spread"))
            return s * math.sqrt(k) if s is not None else None
        if "CONFIDENCE" in disp:
            lo, hi = fnum(m.get("lowerLimit")), fnum(m.get("upperLimit"))
            return (hi - lo) / (2 * Z) * math.sqrt(k) if lo is not None and hi is not None else None
        return None
    va, vb = fnum(a.get("value")), fnum(b.get("value"))
    sa, sb = sd(a, n[gd]), sd(b, n[gp])
    if None in (va, vb, sa, sb):
        return None
    return va - vb, math.sqrt(sa ** 2 / n[gd] + sb ** 2 / n[gp]), int(n[gd]), int(n[gp])


def analysis_md(o, gd, gp):
    for a in o.get("analyses") or []:
        if set(a.get("groupIds") or []) != {gd, gp}:
            continue
        v, lo, hi = fnum(a.get("paramValue")), fnum(a.get("ciLowerLimit")), fnum(a.get("ciUpperLimit"))
        pct = fnum(a.get("ciPctValue")) or 95
        if None in (v, lo, hi) or abs(pct - 95) > 0.1:
            continue
        return v, (hi - lo) / (2 * Z)
    return None


def extract(s, drug, dose):
    ps = s["protocolSection"]
    nct = ps["identificationModule"]["nctId"]
    title = ps["identificationModule"].get("briefTitle", "")
    rs = s.get("resultsSection", {})
    outs = rs.get("outcomeMeasuresModule", {}).get("outcomeMeasures", [])
    prim = [o for o in outs if o.get("type") == "PRIMARY" and is_a1c(o)]
    if not prim:
        return nct, title, "1차 평가변수에 HbA1c 변화 없음"
    prim = [o for o in prim if week_ok(o)]
    if not prim:
        return nct, title, f"HbA1c 1차 평가가 {WEEK}주가 아님"
    prim = [o for o in prim if unit_ok(o)]
    if not prim:
        return nct, title, "단위가 % 가 아님"
    for o in prim:
        groups = o.get("groups", [])
        gd, gp = pick_groups(groups, drug, dose)
        if not gd:
            continue
        arm = arm_md(o, gd["id"], gp["id"])
        an = analysis_md(o, gd["id"], gp["id"])
        if an:
            md, se = an
            if arm and arm[0] * md < 0 and abs(arm[0]) > 0.05:
                md = -md
            src = "임상 통계분석"
            n1, n2 = (arm[2], arm[3]) if arm else (None, None)
        elif arm:
            md, se, n1, n2 = arm
            src = "군별 평균·산포"
        else:
            return nct, title, "숫자 형식을 못 읽음"
        return dict(nct=nct, title=title, md=md, se=se, n1=n1, n2=n2, src=src,
                    arms=f"{gd['title'][:45]} vs {gp['title'][:35]}")
    return nct, title, f"{dose}mg 군과 위약 군을 못 찾음(다른 약 병용 비교 등)"


def pool(rows):
    y = [r["md"] for r in rows]
    v = [r["se"] ** 2 for r in rows]
    w = [1 / x for x in v]
    fe = sum(wi * yi for wi, yi in zip(w, y)) / sum(w)
    q = sum(wi * (yi - fe) ** 2 for wi, yi in zip(w, y))
    k = len(y)
    c = sum(w) - sum(wi ** 2 for wi in w) / sum(w)
    tau2 = max(0.0, (q - (k - 1)) / c) if k > 1 else 0.0
    ws = [1 / (vi + tau2) for vi in v]
    re_ = sum(wi * yi for wi, yi in zip(ws, y)) / sum(ws)
    se = math.sqrt(1 / sum(ws))
    i2 = max(0.0, (q - (k - 1)) / q) * 100 if q > 0 else 0.0
    return dict(k=k, md=re_, se=se, tau2=tau2, q=q, i2=i2, fe=fe, fe_se=math.sqrt(1 / sum(w)),
                wt=[x / sum(ws) * 100 for x in ws])


def ci(m, s):
    return f"{m:+.2f} [{m - Z * s:+.2f}, {m + Z * s:+.2f}]"


def main():
    r, dt = get({"query.intr": "empagliflozin", "pageSize": 1, "countTotal": "true", "format": "json"})
    print(f"접속 시험: HTTP {r.status_code} · {dt:.1f}초 · 전체 건수 {r.json().get('totalCount') if r.ok else '-'}")
    if not r.ok:
        print(r.text[:300])
        return 1
    res = {}
    for d in DRUGS:
        drug, dose = (d.split(":") + ["10"])[:2]
        drug = drug.lower()
        t0 = time.time()
        ss = studies(drug)
        print(f"\n===== {drug} {dose}mg vs 위약 · 2형 당뇨 3상 결과 있는 임상 {len(ss)}개 ({time.time() - t0:.1f}초)")
        ok, why = [], {}
        for s in ss:
            e = extract(s, drug, dose)
            if isinstance(e, dict):
                ok.append(e)
            else:
                why.setdefault(e[2], []).append(e[0])
        for k, v in sorted(why.items(), key=lambda x: -len(x[1])):
            print(f"   빠짐 {len(v):>2}개: {k}  예 {', '.join(v[:3])}")
        print(f"   쓸 수 있는 임상 {len(ok)}개")
        if not ok:
            continue
        p = pool(ok)
        for e, w in sorted(zip(ok, p["wt"]), key=lambda x: x[0]["nct"]):
            n = f"{e['n1']}/{e['n2']}" if e["n1"] else "-"
            print(f"   {e['nct']} · 인원 {n:>9} · 차이 {ci(e['md'], e['se'])} · 가중치 {w:4.1f}% · {e['src']}"
                  f"\n      {e['title'][:90]}\n      ({e['arms']})")
        print(f"   ▶ 랜덤효과 합친 차이(%p): {ci(p['md'], p['se'])} · 임상 {p['k']}개 · "
              f"I² {p['i2']:.0f}% · τ² {p['tau2']:.4f} · 고정효과 {ci(p['fe'], p['fe_se'])}")
        res[drug] = p
    if len(res) == 2:
        (a, pa), (b, pb) = res.items()
        md, se = pa["md"] - pb["md"], math.sqrt(pa["se"] ** 2 + pb["se"] ** 2)
        pv = math.erfc(abs(md / se) / math.sqrt(2))
        print(f"\n===== 간접 비교 (위약 공통, Bucher): {a} − {b}")
        print(f"   HbA1c 변화 차이 {ci(md, se)} %p · p={pv:.2f}  (음수면 {a} 쪽이 더 낮춤)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
