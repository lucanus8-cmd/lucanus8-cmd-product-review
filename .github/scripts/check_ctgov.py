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
# 워크플로 입력 하나로 여러 설정을 받는다: "COND=...|OUT=...|WEEK=12|PHASES=3,4|LABEL=..."
for kv in (os.environ.get("OPTS") or "").split("|"):
    if "=" in kv:
        k, v = kv.split("=", 1)
        os.environ[k.strip()] = v.strip()
DRUGS = [d.strip() for d in (os.environ.get("DRUGS") or "empagliflozin:10,dapagliflozin:10").split(",") if d.strip()]
COND = os.environ.get("COND", "type 2 diabetes")
WEEK = os.environ.get("WEEK") or "24"
OUT = os.environ.get("OUT") or r"hba1c|a1c|glycosylated h|glycated h"   # 평가변수 이름(정규식)
EXCL = os.environ.get("EXCL") or "fasting"                                # 이름에 있으면 뺌
PHASES = ["PHASE" + x.strip() for x in (os.environ.get("PHASES") or "3").split(",")]
LABEL = os.environ.get("LABEL") or "HbA1c 변화(%p)"
OTHER = ("linagliptin", "sitagliptin", "saxagliptin", "metformin", "glimepiride", "glipizide",
         "pioglitazone", "insulin", "exenatide", "dapagliflozin", "empagliflozin", "canagliflozin",
         "ertugliflozin", "liraglutide", "semaglutide", "ezetimibe", "bempedoic", "atorvastatin",
         "simvastatin", "rosuvastatin", "pravastatin", "pitavastatin", "fluvastatin", "lovastatin",
         "fenofibrate", "evolocumab", "alirocumab", "inclisiran", "niacin", "colesevelam")
Z = 1.959964


def get(params):
    t = time.time()
    r = requests.get(API, params=params, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    return r, time.time() - t


FIELDS = ("protocolSection.identificationModule,protocolSection.designModule,"
          "protocolSection.conditionsModule,resultsSection.outcomeMeasuresModule")


def studies(drug):
    base = {"query.intr": names(drug)[0], "pageSize": 100, "format": "json", "aggFilters": "results:with", "fields": FIELDS}
    if COND:
        base["query.cond"] = COND
    out, tok = [], None
    for _ in range(20):
        p = dict(base)
        if tok:
            p["pageToken"] = tok
        r, _ = get(p)
        r.raise_for_status()
        j = r.json()
        out += j.get("studies", [])
        tok = j.get("nextPageToken")
        if not tok:
            break
    return [s for s in out if set(s["protocolSection"].get("designModule", {}).get("phases") or []) & set(PHASES)]


def fnum(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def names(drug):
    return [x.strip() for x in drug.split("|") if x.strip()]


def has(t, drug):
    t = t.lower()
    return any(n in t for n in names(drug))


def same(w, drug):
    return any(w in n or n in w for n in names(drug))


def words_in(t, drug=""):
    """군 이름에 든 다른 약 이름들 (그 약 자신과 다른 이름은 뺌)."""
    t = t.lower()
    return {w for w in OTHER if w in t and not (drug and same(w, drug))}


def dose_ok(title, dose):
    """군 이름에 정한 용량이 있거나, 용량이 아예 안 적혀 있으면 통과."""
    t = title.lower()
    return re.search(rf"(?<![\d.]){dose}\s*mg", t) is not None or re.search(r"\d\s*mg", t) is None


def pick_groups(groups, drug, dose):
    dg = [g for g in groups if has(g["title"], drug) and not g["title"].lower().startswith("placebo")
          and dose_ok(g["title"], dose)]
    pg = [g for g in groups if "placebo" in g["title"].lower() and not has(g["title"], drug)]
    if not pg:
        pg = [g for g in groups if g["title"].lower().startswith("placebo")]
    for d in dg:
        for p in pg:
            extra = words_in(d["title"], drug) - words_in(p["title"], drug)
            if not extra:
                return d, p
    return None, None


def is_a1c(o):
    t = (o.get("title", "") + " " + o.get("description", "")).lower()
    return (re.search(OUT, t) is not None and "change" in t
            and not re.search(EXCL, o.get("title", "").lower()))


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
        return nct, title, "1차 평가변수에 해당 지표 변화 없음"
    prim = [o for o in prim if week_ok(o)]
    if not prim:
        return nct, title, f"1차 평가가 {WEEK}주가 아님"
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
    gt = " / ".join(g["title"][:40] for g in prim[0].get("groups", [])[:5])
    return nct, title, f"{dose}mg 군과 위약 군을 못 찾음(다른 약 병용 비교 등)", gt


def alone(groups, drug, dose, avoid=""):
    """그 약 단독군(같은 바탕 치료는 허용, avoid 약이 섞인 군은 뺌): (군, 그 군의 다른 약 이름들)"""
    for g in groups:
        t = g["title"].lower()
        if has(t, drug) and not t.startswith("placebo") and dose_ok(t, dose):
            w = words_in(t, drug)
            if avoid and any(same(x, avoid) for x in w):
                continue
            return g, w
    return None, None


def direct(ss, a, da, b, db):
    rows, seen = [], set()
    for s in ss:
        ps = s["protocolSection"]
        nct = ps["identificationModule"]["nctId"]
        if nct in seen:
            continue
        seen.add(nct)
        for o in s.get("resultsSection", {}).get("outcomeMeasuresModule", {}).get("outcomeMeasures", []):
            if o.get("type") != "PRIMARY" or not is_a1c(o) or not week_ok(o) or not unit_ok(o):
                continue
            ga, xa = alone(o.get("groups", []), a, da, b)
            gb, xb = alone(o.get("groups", []), b, db, a)
            if not ga or not gb or ga["id"] == gb["id"] or {w for w in xa if not same(w, b)} != {w for w in xb if not same(w, a)} \
                    or any(same(w, b) for w in xa) or any(same(w, a) for w in xb):
                continue
            arm = arm_md(o, ga["id"], gb["id"])
            an = analysis_md(o, ga["id"], gb["id"])
            if an:
                md, se = an
                if arm and arm[0] * md < 0 and abs(arm[0]) > 0.5:
                    md = -md
                src = "임상 통계분석"
            elif arm:
                md, se = arm[0], arm[1]
                src = "군별 평균·산포"
            else:
                continue
            rows.append(dict(nct=nct, title=ps["identificationModule"].get("briefTitle", ""), md=md, se=se,
                             src=src, arms=f"{ga['title'][:45]} vs {gb['title'][:45]}",
                             n1=arm[2] if arm else None, n2=arm[3] if arm else None))
            break
    return rows


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
    res, allss, names = {}, [], []
    for d in DRUGS:
        drug, dose = (d.split(":") + ["10"])[:2]
        drug = drug.lower()
        t0 = time.time()
        ss = studies(drug)
        allss += ss
        names.append((drug, dose))
        print(f"\n===== {drug} {dose}mg vs 위약 · {COND or '질환 제한 없음'} · {'/'.join(PHASES)} · 결과 있는 임상 {len(ss)}개 ({time.time() - t0:.1f}초)")
        ok, why = [], {}
        for s in ss:
            e = extract(s, drug, dose)
            if isinstance(e, dict):
                ok.append(e)
            else:
                why.setdefault(e[2], []).append(e[0])
                if len(e) > 3:
                    print(f"   (군 못 찾음) {e[0]} {e[1][:60]}\n      군: {e[3]}")
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
        print(f"   ▶ 랜덤효과 합친 차이({LABEL}): {ci(p['md'], p['se'])} · 임상 {p['k']}개 · "
              f"I² {p['i2']:.0f}% · τ² {p['tau2']:.4f} · 고정효과 {ci(p['fe'], p['fe_se'])}")
        res[drug] = p
    if len(res) == 2:
        (a, pa), (b, pb) = res.items()
        md, se = pa["md"] - pb["md"], math.sqrt(pa["se"] ** 2 + pb["se"] ** 2)
        pv = math.erfc(abs(md / se) / math.sqrt(2))
        print(f"\n===== 간접 비교 (위약 공통, Bucher): {a} − {b}")
        print(f"   {LABEL} 차이 {ci(md, se)} · p={pv:.2f}  (음수면 {a} 쪽이 더 낮춤)")
    if len(names) == 2:
        (a, da), (b, db) = names
        rows = direct(allss, a, da, b, db)
        print(f"\n===== 직접 비교: 같은 임상 안에 {a} {da}mg 단독군과 {b} {db}mg 단독군이 다 있는 임상 {len(rows)}개")
        for e in rows:
            n = f"{e['n1']}/{e['n2']}" if e["n1"] else "-"
            print(f"   {e['nct']} · 인원 {n} · {a} − {b} {ci(e['md'], e['se'])} · {e['src']}"
                  f"\n      {e['title'][:90]}\n      ({e['arms']})")
        if rows:
            p = pool(rows)
            print(f"   ▶ 직접 비교 합친 차이: {ci(p['md'], p['se'])} · 임상 {p['k']}개 · I² {p['i2']:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
