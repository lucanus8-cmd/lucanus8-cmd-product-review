"""FDA 신약 허가 목록(Novel Drug Approvals)의 약이 Open Targets 에 얼마나 들어 있는지 센다. 사이트와 상관없다.

공개 자료만 다룬다. 읽기만 한다.
"""
import html
import json
import re
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (product-review)"}
OT = "https://api.platform.opentargets.org/api/v4/graphql"
YEARS = [2026, 2025, 2024, 2023]


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return r.read().decode("utf-8", "ignore")


def novel(year):
    """FDA 신약 허가 표 → [(상표, 성분, 허가일, 적응증)]"""
    url = f"https://www.fda.gov/drugs/novel-drug-approvals-fda/novel-drug-approvals-{year}"
    t = get(url)
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if len(cells) >= 4 and re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", cells[3] if len(cells) > 3 else ""):
            rows.append((cells[1], cells[2], cells[3], cells[4] if len(cells) > 4 else ""))
        elif len(cells) >= 4 and re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", " ".join(cells)):
            d = next(c for c in cells if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", c))
            rows.append((cells[1], cells[2], d, cells[-1]))
    return rows


def ot_find(name):
    body = json.dumps({"query": "query($q:String!){search(queryString:$q, entityNames:[\"drug\"], page:{index:0,size:3}){hits{id name}}}",
                       "variables": {"q": name}}).encode()
    req = urllib.request.Request(OT, data=body, headers={"Content-Type": "application/json", **UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        hits = json.loads(r.read())["data"]["search"]["hits"]
    n = re.sub(r"[^a-z]", "", name.lower())
    return next((h for h in hits if re.sub(r"[^a-z]", "", h["name"].lower()).startswith(n[:8])), None)


def ot_detail(cid):
    q = "query($id:String!){drug(chemblId:$id){maximumClinicalStage mechanismsOfAction{rows{actionType}} indications{count rows{maxClinicalStage}}}}"
    body = json.dumps({"query": q, "variables": {"id": cid}}).encode()
    req = urllib.request.Request(OT, data=body, headers={"Content-Type": "application/json", **UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())["data"]["drug"] or {}
    ind = (d.get("indications") or {}).get("rows") or []
    return (d.get("maximumClinicalStage"), len((d.get("mechanismsOfAction") or {}).get("rows") or []),
            sum(1 for x in ind if x.get("maxClinicalStage") == "APPROVAL"))


def main():
    v = json.loads(urllib.request.urlopen(urllib.request.Request(
        OT, data=json.dumps({"query": "{meta{dataVersion{year month}}}"}).encode(),
        headers={"Content-Type": "application/json", **UA}), timeout=30).read())["data"]["meta"]["dataVersion"]
    print(f"Open Targets 자료판: 20{v['year']}-{v['month']}")
    for y in YEARS:
        try:
            rows = novel(y)
        except Exception as e:
            print(f"\n{y}: FDA 표 못 읽음 {e}")
            continue
        print(f"\n######## {y}년 FDA 신약 {len(rows)}개")
        found = approved = with_moa = with_ind = 0
        miss = []
        for brand, ing, date, use in rows:
            name = re.split(r"[;,/]| and ", ing)[0].strip()
            try:
                h = ot_find(name)
            except Exception:
                h = None
            if not h:
                miss.append(f"{date} {brand} ({ing})")
                continue
            found += 1
            try:
                stage, moa, ind = ot_detail(h["id"])
            except Exception:
                stage, moa, ind = "", 0, 0
            approved += stage == "APPROVAL"
            with_moa += moa > 0
            with_ind += ind > 0
            time.sleep(0.2)
        n = len(rows) or 1
        print(f"Open Targets 에 있음 {found}/{len(rows)} ({100 * found / n:.0f}%) · '허가' 단계로 표시 {approved} · "
              f"작용 표적 있음 {with_moa} · 허가 적응증 연결 {with_ind}")
        for m in miss[:40]:
            print("   없음:", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
