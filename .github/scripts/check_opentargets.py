"""Open Targets Platform 공개 API 의 구조를 확인하고, 약물 재창출 화면에 쓸 질의를 시험한다.

공개 자료(약·단백질·질환 연결)만 적는다. 키 필요 없음. 읽기만 한다.

    WORDS="sildenafil;chronic cough" python .github/scripts/check_opentargets.py
"""
import json
import os
import sys
import time
import urllib.request

API = "https://api.platform.opentargets.org/api/v4/graphql"
W = ((os.environ.get("WORDS") or "").strip() or "sildenafil;chronic cough").split(";")
DRUG, DIS = (W + ["", ""])[:2]


def gql(q, v=None):
    body = json.dumps({"query": q, "variables": v or {}}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json",
                                                         "User-Agent": "Mozilla/5.0 (product-review)"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    out["_sec"] = round(time.time() - t, 2)
    return out


def show(title, q, v=None, cut=2500):
    print(f"\n===== {title}")
    try:
        r = gql(q, v)
        print(f"({r.pop('_sec')}초)")
        print(json.dumps(r, ensure_ascii=False)[:cut])
        return r
    except Exception as e:
        print("실패:", type(e).__name__, str(e)[:300])
        try:
            print(e.read().decode()[:1500])
        except Exception:
            pass
        return None


def fields(tname):
    r = show(f"구조: {tname}", "query($n:String!){__type(name:$n){fields{name args{name} type{name kind ofType{name kind ofType{name}}}}}}",
             {"n": tname}, cut=0)
    if r and r.get("data", {}).get("__type"):
        for f in r["data"]["__type"]["fields"]:
            t = f["type"]
            tn = t.get("name") or (t.get("ofType") or {}).get("name") or ((t.get("ofType") or {}).get("ofType") or {}).get("name")
            print(f"   {f['name']}({','.join(a['name'] for a in f['args'])}) : {tn}")


def deep(tname, depth=2, seen=None):
    """타입의 칸을 하위 타입까지 펼쳐 적는다."""
    seen = seen if seen is not None else set()
    if tname in seen or depth < 0:
        return
    seen.add(tname)
    try:
        r = gql("query($n:String!){__type(name:$n){kind fields{name args{name} type{name kind ofType{name kind ofType{name kind ofType{name}}}}}}}", {"n": tname})
    except Exception as e:
        print("  실패", tname, e)
        return
    t = (r.get("data") or {}).get("__type")
    if not t or not t.get("fields"):
        return
    print(f"\n----- {tname}")
    subs = []
    for f in t["fields"]:
        x = f["type"]
        while x.get("ofType"):
            x = x["ofType"]
        print(f"   {f['name']}({','.join(a['name'] for a in f['args'])}) : {x.get('name')} [{x.get('kind')}]")
        if x.get("kind") == "OBJECT":
            subs.append(x["name"])
    for s_ in subs:
        deep(s_, depth - 1, seen)


def main():
    seen = set()
    for t in ("Drug", "MechanismsOfAction", "clinicalIndicationsFromDrugImp", "clinicalTargets",
              "clinicalIndicationsFromDiseaseImp", "SearchResults", "MappingResults"):
        deep(t, 2, seen)
    for q in (DIS, "cough", "chronic cough", "refractory chronic cough", "Raynaud"):
        show(f"질환 찾기 '{q}'", "query($q:String!){search(queryString:$q, entityNames:[\"disease\"], page:{index:0,size:10}){total hits{id name entity}}}",
             {"q": q}, cut=1500)
    show("mapIds", "query($q:[String!]!){mapIds(queryTerms:$q, entityNames:[\"disease\",\"drug\"]){mappings{term hits{id name entity}}}}",
         {"q": ["chronic cough", "cough", "Raynaud disease", "sildenafil", "gabapentin"]}, cut=3000)
    return 0


if __name__ == "__main__":
    sys.exit(main())
