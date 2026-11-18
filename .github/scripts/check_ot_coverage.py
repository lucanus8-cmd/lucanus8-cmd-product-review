"""미국 FDA 신약(새 성분)이 Open Targets 에 얼마나 들어 있는지 센다. 사이트와 상관없다.

FDA 신약 목록은 openFDA(Drugs@FDA) 에서 받는다: 최초 허가(ORIG) 이면서 새 성분(TYPE 1) 인 NDA,
그리고 최초 허가된 BLA(바이오의약품). 공개 자료만 다룬다. 읽기만 한다.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (product-review)"}
OT = "https://api.platform.opentargets.org/api/v4/graphql"
FDA = "https://api.fda.gov/drug/drugsfda.json"
YEARS = [2026, 2025, 2024, 2023]
SALT = r"\b(hydrochloride|dihydrochloride|hcl|sodium|potassium|calcium|magnesium|mesylate|maleate|tosylate|" \
       r"succinate|tartrate|citrate|fumarate|phosphate|sulfate|acetate|bromide|besylate|hydrobromide|monohydrate|" \
       r"dihydrate|anhydrous|free base|meglumine|disodium|trihydrate)\b"


def get_json(url, data=None, timeout=40):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fda_pages(search):
    out, skip = [], 0
    while True:
        q = urllib.parse.quote(search, safe=':+[]*"')
        url = f"{FDA}?search={q}&limit=1000&skip={skip}"
        try:
            j = get_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:          # 결과 없음
                break
            raise
        out += j.get("results", [])
        total = j.get("meta", {}).get("results", {}).get("total", 0)
        skip += 1000
        if skip >= total or skip >= 25000:
            break
        time.sleep(0.3)
    return out


def first_orig(app):
    """최초 허가(ORIG, AP) 날짜와 분류"""
    for s in app.get("submissions") or []:
        if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP":
            return s.get("submission_status_date", ""), s.get("submission_class_code") or ""
    return "", ""


def ingredient(app):
    names = []
    for p in app.get("products") or []:
        for a in p.get("active_ingredients") or []:
            n = (a.get("name") or "").strip()
            if n and n not in names:
                names.append(n)
    if not names:
        names = (app.get("openfda") or {}).get("generic_name") or []
    return names


def brand(app):
    for p in app.get("products") or []:
        if p.get("brand_name"):
            return p["brand_name"]
    return ""


def novel_list():
    """{연도: [(허가일, 상표, 성분들, NDA/BLA)]}"""
    apps = {}
    for a in fda_pages('submissions.submission_class_code:"TYPE 1"'):
        apps[a.get("application_number")] = a
    try:
        for a in fda_pages("application_number:BLA*"):
            apps[a.get("application_number")] = a
    except Exception as e:
        print("BLA 목록 못 읽음:", e)
    by_year = {y: [] for y in YEARS}
    for no, a in apps.items():
        d, cls = first_orig(a)
        if len(d) < 8:
            continue
        y = int(d[:4])
        if y not in by_year:
            continue
        kind = "BLA" if str(no).startswith("BLA") else "NDA"
        if kind == "NDA" and not cls.upper().startswith("TYPE 1"):
            continue           # 최초 허가가 새 성분이 아님
        by_year[y].append((d, brand(a), ingredient(a), kind))
    return by_year


def clean(name):
    n = re.sub(SALT, " ", name.lower())
    return re.sub(r"\s+", " ", n).strip()


def ot_find(name):
    q = "query($q:String!){search(queryString:$q, entityNames:[\"drug\"], page:{index:0,size:5}){hits{id name}}}"
    hits = get_json(OT, json.dumps({"query": q, "variables": {"q": name}}).encode(), 30)["data"]["search"]["hits"]
    n = re.sub(r"[^a-z]", "", name.lower())
    return next((h for h in hits if re.sub(r"[^a-z]", "", h["name"].lower()).startswith(n[:8])), None)


def ot_detail(cid):
    q = ("query($id:String!){drug(chemblId:$id){maximumClinicalStage mechanismsOfAction{rows{actionType}} "
         "indications{count rows{maxClinicalStage}}}}")
    d = get_json(OT, json.dumps({"query": q, "variables": {"id": cid}}).encode(), 30)["data"]["drug"] or {}
    ind = (d.get("indications") or {}).get("rows") or []
    return (d.get("maximumClinicalStage"), len((d.get("mechanismsOfAction") or {}).get("rows") or []),
            sum(1 for x in ind if x.get("maxClinicalStage") == "APPROVAL"))


def main():
    v = get_json(OT, json.dumps({"query": "{meta{dataVersion{year month}}}"}).encode(), 30)["data"]["meta"]["dataVersion"]
    print(f"Open Targets 자료판: 20{v['year']}-{v['month']}")
    by_year = novel_list()
    for y in YEARS:
        rows = sorted(by_year[y])
        nda = sum(1 for r in rows if r[3] == "NDA")
        print(f"\n######## {y}년 FDA 최초 허가 새 성분 {len(rows)}개 (화학 NDA {nda} · 바이오 BLA {len(rows) - nda})")
        stat = {"NDA": [0, 0, 0, 0], "BLA": [0, 0, 0, 0]}
        miss, not_appr = [], []
        for d, br, ings, kind in rows:
            name = clean(ings[0]) if ings else clean(br)
            try:
                h = ot_find(name)
            except Exception:
                h = None
            if not h:
                miss.append(f"{d[:4]}-{d[4:6]}-{d[6:]} {kind} {br} ({', '.join(ings)[:60]})")
                continue
            st = stat[kind]
            st[0] += 1
            try:
                stage, moa, ind = ot_detail(h["id"])
            except Exception:
                stage, moa, ind = "", 0, 0
            st[1] += stage == "APPROVAL"
            st[2] += moa > 0
            st[3] += ind > 0
            if stage != "APPROVAL":
                not_appr.append(f"{d[:4]}-{d[4:6]}-{d[6:]} {br} → Open Targets 단계 {stage}")
            time.sleep(0.15)
        for kind in ("NDA", "BLA"):
            tot = sum(1 for r in rows if r[3] == kind)
            f, a, m, i = stat[kind]
            if tot:
                print(f"  {kind}: Open Targets 에 있음 {f}/{tot} ({100 * f / tot:.0f}%) · '허가'로 표시 {a} · "
                      f"작용 표적 있음 {m} · 허가 적응증 연결 {i}")
        for m in miss[:40]:
            print("   없음:", m)
        for m in not_appr[:25]:
            print("   있으나 허가 전 단계로 표시:", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
