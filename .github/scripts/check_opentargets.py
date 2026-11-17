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


_TQ = ("query($n:String!){__type(name:$n){fields{name args{name type{kind}} "
       "type{name kind ofType{name kind ofType{name kind ofType{name kind ofType{name kind}}}}}}}}")
_CACHE = {}


def _fields(tname):
    if tname not in _CACHE:
        r = gql(_TQ, {"n": tname})
        _CACHE[tname] = ((r.get("data") or {}).get("__type") or {}).get("fields") or []
    return _CACHE[tname]


def sel(tname, depth=2):
    """타입의 칸을 자동으로 골라 질의 문장을 만든다(인자가 필요한 칸은 뺀다)."""
    parts = []
    for f in _fields(tname):
        if any(a["type"]["kind"] == "NON_NULL" for a in f["args"]) or f["name"] in (
                "molblock", "similarEntities", "literatureOcurrences", "adverseEvents", "pharmacogenomics",
                "evidences", "associatedDiseases", "associatedTargets", "credibleSets", "interactions",
                "associationTimeSeries", "drugAndClinicalCandidates", "indications", "mechanismsOfAction",
                "childMolecules", "parentMolecule", "descendants", "ancestors", "children", "parents",
                "resolvedAncestors", "phenotypes", "transcripts", "geneOntology", "proteinCodingCoordinates",
                "baselineExpression", "mousePhenotypes", "tractability", "safetyLiabilities", "pathways",
                "homologues", "chemicalProbes", "dbXrefs", "dbXRefs", "description", "functionDescriptions"):
            continue
        x = f["type"]
        while x.get("ofType"):
            x = x["ofType"]
        if x["kind"] in ("SCALAR", "ENUM"):
            parts.append(f["name"])
        elif x["kind"] == "OBJECT" and depth > 0:
            sub = sel(x["name"], depth - 1)
            if sub:
                parts.append(f"{f['name']}{{{sub}}}")
    return " ".join(parts)


def main():
    for t in ("ClinicalIndicationFromDrug", "ClinicalTargetFromTarget", "ClinicalIndicationFromDisease",
              "DrugWarning", "DrugLabelAndSource", "SearchResult", "MechanismOfActionRow"):
        try:
            print(f"\n----- {t}: {sel(t, 1)}")
        except Exception as e:
            print(t, "실패", e)
    s1 = sel("ClinicalIndicationFromDrug", 1)
    show("약 → 적응증 (sildenafil)", "query{drug(chemblId:\"CHEMBL192\"){name maximumClinicalStage drugType "
         "synonyms{" + sel("DrugLabelAndSource", 0) + "} tradeNames{" + sel("DrugLabelAndSource", 0) + "} "
         "drugWarnings{" + sel("DrugWarning", 0) + "} "
         "mechanismsOfAction{rows{mechanismOfAction actionType targetName targets{id approvedSymbol approvedName}}} "
         "indications{count rows{" + s1 + "}}}}", cut=6000)
    show("표적 → 약 (PDE5A)", "query{target(ensemblId:\"ENSG00000138735\"){drugAndClinicalCandidates{count rows{"
         + sel("ClinicalTargetFromTarget", 1) + "}}}}", cut=4000)
    show("질환 → 약 (Cough)", "query{disease(efoId:\"HP_0012735\"){name drugAndClinicalCandidates{count rows{"
         + sel("ClinicalIndicationFromDisease", 1) + "}}}}", cut=4000)
    show("질환 → 표적 (Cough)", """query{disease(efoId:"HP_0012735"){associatedTargets(page:{index:0,size:10}){count rows{score
        datatypeScores{id score} target{id approvedSymbol approvedName targetClass{label level}}}}}}""", cut=4000)
    show("표적 → 질환 (CACNA2D1)", """query{target(ensemblId:"ENSG00000153956"){approvedSymbol approvedName
        associatedDiseases(page:{index:0,size:10}){count rows{score disease{id name}}}}}""", cut=2500)
    show("검색 hit 칸", "query{search(queryString:\"gabapentin\", entityNames:[\"drug\"], page:{index:0,size:3}){hits{"
         + sel("SearchResult", 0) + "}}}", cut=2500)
    return 0


if __name__ == "__main__":
    sys.exit(main())
