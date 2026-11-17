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


def main():
    show("버전", "{meta{apiVersion{x y z} dataVersion{year month iteration}}}")
    for t in ("Query", "Drug", "Target", "Disease", "MechanismOfActionRow", "IndicationRow", "Indications",
              "KnownDrug", "KnownDrugs", "AssociatedDisease", "AssociatedTarget", "ScoredComponent", "TargetClass"):
        fields(t)
    r = show("약 찾기", "query($q:String!){search(queryString:$q, entityNames:[\"drug\"], page:{index:0,size:5}){hits{id name entity description}}}",
             {"q": DRUG})
    did = None
    try:
        did = r["data"]["search"]["hits"][0]["id"]
    except Exception:
        pass
    if did:
        show("약 정보", """query($id:String!){drug(chemblId:$id){id name synonyms tradeNames drugType maximumClinicalTrialPhase
            isApproved hasBeenWithdrawn blackBoxWarning
            mechanismsOfAction{rows{mechanismOfAction actionType targetName targets{id approvedSymbol approvedName}}}
            indications{count approvedIndications rows{disease{id name} maxPhaseForIndication}}}}""", {"id": did}, cut=4000)
    r = show("질환 찾기", "query($q:String!){search(queryString:$q, entityNames:[\"disease\"], page:{index:0,size:8}){hits{id name entity}}}",
             {"q": DIS})
    eid = None
    try:
        eid = r["data"]["search"]["hits"][0]["id"]
    except Exception:
        pass
    if eid:
        show("질환 → 표적", """query($id:String!){disease(efoId:$id){id name
            associatedTargets(page:{index:0,size:8}){count rows{score datatypeScores{id score}
              target{id approvedSymbol approvedName targetClass{label level}}}}}}""", {"id": eid}, cut=4000)
        show("질환 → 알려진 약", """query($id:String!){disease(efoId:$id){knownDrugs(size:20){count uniqueDrugs
            rows{drug{id name isApproved maximumClinicalTrialPhase} phase status mechanismOfAction
              target{approvedSymbol} ctIds}}}}""", {"id": eid}, cut=3000)
    show("표적 → 질환 (PDE5A)", """query{target(ensemblId:"ENSG00000138735"){id approvedSymbol approvedName targetClass{label level}
        associatedDiseases(page:{index:0,size:8}){count rows{score datatypeScores{id score} disease{id name}}}}}""", cut=4000)
    show("표적 → 알려진 약 (PDE5A)", """query{target(ensemblId:"ENSG00000138735"){knownDrugs(size:20){count uniqueDrugs
        rows{drug{id name isApproved maximumClinicalTrialPhase} phase status disease{id name} mechanismOfAction}}}}""", cut=3000)
    return 0


if __name__ == "__main__":
    sys.exit(main())
