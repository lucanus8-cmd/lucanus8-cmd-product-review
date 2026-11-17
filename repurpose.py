"""약물 재창출 — Open Targets 공개 API 로 '약 → 표적 단백질 → 질환' 지도를 만든다.

- 키·비밀번호 필요 없음. 밖으로 나가는 것은 약·질환 이름(영문)과 Open Targets 번호뿐이다.
- 연결 점수·근거 종류별 점수·임상 단계·임상 상태·중단 이유는 모두 Open Targets 값을 그대로 쓴다.
- 이 파일은 화면(streamlit)을 쓰지 않는다. 화면은 scouting.py 의 _repurpose_view() 가 그린다.

화면이 쓰는 것: find_drug · find_disease · fetch_drug · fetch_disease · drug_graph · disease_graph
"""
import json
import re
import urllib.request

API = "https://api.platform.opentargets.org/api/v4/graphql"
UA = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (product-review)"}

ONGOING = {"RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"}
STOPPED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
FAIL_REASON = {"Negative": "효과 부족", "Safety_Sideeffects": "안전성"}

# 단계: 높을수록 앞선 단계
_STAGE = {"APPROVAL": 9, "PHASE_4": 8, "PHASE_3": 7, "PHASE_2_3": 6, "PHASE_2": 5, "PHASE_1_2": 4,
          "PHASE_1": 3, "EARLY_PHASE_1": 2, "PRECLINICAL": 1, "UNKNOWN": 0, "WITHDRAWAL": -1}
_STAGE_KO = {"APPROVAL": "허가", "PHASE_4": "4상", "PHASE_3": "3상", "PHASE_2_3": "2/3상", "PHASE_2": "2상",
             "PHASE_1_2": "1/2상", "PHASE_1": "1상", "EARLY_PHASE_1": "초기 1상", "PRECLINICAL": "전임상",
             "UNKNOWN": "단계 미상", "WITHDRAWAL": "시장 철수"}
DATATYPE_KO = {"genetic_association": "유전", "genetic_literature": "유전 (문헌)", "somatic_mutation": "체세포 변이",
               "clinical": "임상 (알려진 약)", "known_drug": "임상 (알려진 약)", "affected_pathway": "경로",
               "rna_expression": "발현", "literature": "문헌", "animal_model": "동물 모델"}
ACTION_KO = {"INHIBITOR": "억제", "ANTAGONIST": "차단", "AGONIST": "작용", "PARTIAL AGONIST": "부분 작용",
             "INVERSE AGONIST": "역작용", "BLOCKER": "차단", "OPENER": "열기", "ACTIVATOR": "활성화",
             "MODULATOR": "조절", "POSITIVE ALLOSTERIC MODULATOR": "양성 조절",
             "NEGATIVE ALLOSTERIC MODULATOR": "음성 조절", "POSITIVE MODULATOR": "양성 조절",
             "NEGATIVE MODULATOR": "음성 조절", "BINDING AGENT": "결합", "RELEASING AGENT": "방출 유도",
             "STABILISER": "안정화", "DEGRADER": "분해", "SUBSTRATE": "기질", "CROSS-LINKING AGENT": "가교",
             "DISRUPTING AGENT": "교란", "HYDROLYTIC ENZYME": "가수분해", "EXOGENOUS PROTEIN": "보충 단백질",
             "EXOGENOUS GENE": "보충 유전자", "VACCINE ANTIGEN": "백신 항원", "CHELATING AGENT": "킬레이트",
             "ANTISENSE INHIBITOR": "안티센스 억제", "RNAI INHIBITOR": "RNA 간섭 억제", "OXIDATIVE ENZYME": "산화 효소",
             "PROTEOLYTIC ENZYME": "단백질 분해 효소", "ALLOSTERIC ANTAGONIST": "알로스테릭 차단"}
CLASS_KO = {"Enzyme": "효소", "Ion channel": "이온 통로", "Membrane receptor": "막 수용체", "Transporter": "수송체",
            "Transcription factor": "전사 인자", "Epigenetic regulator": "후성 조절 인자",
            "Secreted protein": "분비 단백질", "Structural protein": "구조 단백질",
            "Other cytosolic protein": "세포질 단백질", "Other nuclear protein": "핵 단백질",
            "Other membrane protein": "막 단백질", "Surface antigen": "표면 항원",
            "Auxiliary transport protein": "보조 수송 단백질", "Unclassified protein": "미분류 단백질",
            "Adhesion": "접착 단백질", "Kinase": "인산화 효소", "Protease": "단백질 분해 효소"}


# ── 받기 ─────────────────────────────────────────────────────────────
def gql(query, variables=None, timeout=25):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if out.get("errors") and not out.get("data"):
        raise RuntimeError("Open Targets: " + str(out["errors"][0].get("message", ""))[:200])
    return out.get("data") or {}


def _search(q, entity, size=10):
    d = gql("query($q:String!,$e:[String!]!,$n:Int!){search(queryString:$q, entityNames:$e, "
            "page:{index:0,size:$n}){hits{id name entity}}}", {"q": q, "e": [entity], "n": size})
    return [(h["id"], h["name"]) for h in ((d.get("search") or {}).get("hits") or []) if h.get("entity") == entity]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def find_drug(q):
    """영문 약 이름 → [(ChEMBL 번호, 이름)] — 이름이 똑같은 것을 맨 앞에."""
    q = (q or "").strip()
    if not q:
        return []
    hits = _search(q, "drug", 8)
    n = _norm(q)
    hits.sort(key=lambda h: (_norm(h[1]) != n, not _norm(h[1]).startswith(n)))
    return hits


def find_disease(terms):
    """영문 질환 이름(여러 개 가능) → [(번호, 이름)] — 겹치는 것은 한 번만, 이름이 똑같은 것을 앞에."""
    out, seen = [], set()
    for t in [x for x in terms if (x or "").strip()][:3]:
        hits = _search(t.strip(), "disease", 8)
        n = _norm(t)
        hits.sort(key=lambda h: _norm(h[1]) != n)
        for h in hits:
            if h[0] not in seen:
                seen.add(h[0])
                out.append(h)
    exact = {_norm(t) for t in terms}
    out.sort(key=lambda h: _norm(h[1]) not in exact)
    return out[:12]


_REPORT = ("clinicalReports{id trialOverallStatus clinicalStage trialPhase trialStartDate "
           "trialStopReasonCategories title url source}")


def fetch_drug(chembl_id, max_targets=8, per_target=30):
    """약 하나의 작용 표적, 표적별 연결 질환, 이 약의 적응증·임상을 받는다."""
    d = gql("query($id:String!){drug(chemblId:$id){id name maximumClinicalStage drugType "
            "drugWarnings{warningType toxicityClass country year} "
            "mechanismsOfAction{rows{mechanismOfAction actionType "
            "targets{id approvedSymbol approvedName targetClass{label level}}}} "
            "indications{rows{maxClinicalStage disease{id name} " + _REPORT + "}}}}", {"id": chembl_id})
    drug = d.get("drug")
    if not drug:
        return None
    targets, seen = [], set()
    for row in ((drug.get("mechanismsOfAction") or {}).get("rows") or []):
        for t in row.get("targets") or []:
            if t.get("id") and t["id"] not in seen:
                seen.add(t["id"])
                targets.append({"id": t["id"], "symbol": t.get("approvedSymbol") or t["id"],
                                "name": t.get("approvedName") or "", "cls": _cls(t.get("targetClass")),
                                "action": row.get("actionType") or "", "moa": row.get("mechanismOfAction") or ""})
    targets = targets[:max_targets]
    assoc = {}
    if targets:
        d2 = gql("query($ids:[String!]!,$n:Int!){targets(ensemblIds:$ids){id associatedDiseases(page:{index:0,size:$n})"
                 "{rows{score datatypeScores{id score} disease{id name}}}}}",
                 {"ids": [t["id"] for t in targets], "n": per_target})
        for t in d2.get("targets") or []:
            assoc[t["id"]] = [{"id": r["disease"]["id"], "name": r["disease"]["name"], "score": r.get("score") or 0,
                               "dts": {x["id"]: x["score"] for x in r.get("datatypeScores") or []}}
                              for r in ((t.get("associatedDiseases") or {}).get("rows") or []) if r.get("disease")]
    ind = {}
    for r in ((drug.get("indications") or {}).get("rows") or []):
        dz = r.get("disease") or {}
        if dz.get("id"):
            ind[dz["id"]] = {"stage": r.get("maxClinicalStage") or "UNKNOWN", "name": dz.get("name", ""),
                             "reports": r.get("clinicalReports") or []}
    return {"id": drug["id"], "name": drug.get("name", ""), "stage": drug.get("maximumClinicalStage") or "",
            "warnings": drug.get("drugWarnings") or [], "targets": targets, "assoc": assoc, "ind": ind}


def fetch_disease(efo_id, max_targets=15):
    """질환 하나의 연결 표적, 표적별 약, 이 질환에 이미 쓰거나 시험한 약을 받는다."""
    d = gql("query($id:String!,$n:Int!){disease(efoId:$id){id name "
            "associatedTargets(page:{index:0,size:$n}){rows{score datatypeScores{id score} "
            "target{id approvedSymbol approvedName targetClass{label level}}}} "
            "drugAndClinicalCandidates{rows{maxClinicalStage drug{id name maximumClinicalStage} " + _REPORT + "}}}}",
            {"id": efo_id, "n": max_targets})
    dz = d.get("disease")
    if not dz:
        return None
    targets = []
    for r in ((dz.get("associatedTargets") or {}).get("rows") or []):
        t = r.get("target") or {}
        if t.get("id"):
            targets.append({"id": t["id"], "symbol": t.get("approvedSymbol") or t["id"], "name": t.get("approvedName") or "",
                            "cls": _cls(t.get("targetClass")), "score": r.get("score") or 0,
                            "dts": {x["id"]: x["score"] for x in r.get("datatypeScores") or []}})
    known = {}
    for r in ((dz.get("drugAndClinicalCandidates") or {}).get("rows") or []):
        dr = r.get("drug") or {}
        k = drug_key(dr.get("name"))
        if not k:
            continue
        cur = known.get(k)
        st_ = r.get("maxClinicalStage") or "UNKNOWN"
        reps = r.get("clinicalReports") or []
        if cur is None:
            known[k] = {"stage": st_, "reports": list(reps)}
        else:
            if _STAGE.get(st_, 0) > _STAGE.get(cur["stage"], 0):
                cur["stage"] = st_
            cur["reports"] += reps
    by_t = {}
    if targets:
        d2 = gql("query($ids:[String!]!){targets(ensemblIds:$ids){id drugAndClinicalCandidates{rows{maxClinicalStage "
                 "drug{id name maximumClinicalStage drugType}}}}}", {"ids": [t["id"] for t in targets]})
        for t in d2.get("targets") or []:
            rows = []
            for r in ((t.get("drugAndClinicalCandidates") or {}).get("rows") or []):
                dr = r.get("drug") or {}
                if dr.get("id"):
                    rows.append({"id": dr["id"], "name": dr.get("name", ""), "key": drug_key(dr.get("name")),
                                 "stage": dr.get("maximumClinicalStage") or "UNKNOWN", "type": dr.get("drugType") or ""})
            by_t[t["id"]] = rows
    return {"id": dz["id"], "name": dz.get("name", ""), "targets": targets, "known": known, "by_target": by_t}


# ── 정리 ─────────────────────────────────────────────────────────────
def _cls(tc):
    """표적 분류 → 가장 큰 분류(l1) 이름."""
    for x in tc or []:
        if x.get("level") == "l1":
            return x.get("label") or ""
    return (tc or [{}])[0].get("label", "") if tc else ""


def drug_key(name):
    """약 이름 → 성분 키 (사이트 특허·허가 자료와 같은 규칙: 첫 영문 낱말 3자 이상, 대문자)."""
    for tok in re.split(r"[^A-Za-z]+", str(name or "")):
        if len(tok) >= 3:
            return tok.upper()
    return ""


def stage_rank(s):
    return _STAGE.get(s or "", 0)


def stage_ko(s):
    return _STAGE_KO.get(s or "", s or "")


def action_ko(a):
    return ACTION_KO.get((a or "").upper(), (a or "").lower())


def class_ko(c):
    return CLASS_KO.get(c or "", c or "")


def trials(reports, with_done=False):
    """임상 기록 → (전체 수, 진행 중 목록, 중단 이유 목록[, 완료 수]). 같은 번호는 한 번만."""
    seen, ongoing, fails, n, done = set(), [], [], 0, 0
    for r in reports or []:
        rid = (r.get("id") or "").lower()
        if (r.get("source") or "") != "ClinicalTrials.gov" and not rid.startswith("nct"):
            continue
        if rid in seen:
            continue
        seen.add(rid)
        n += 1
        stt = (r.get("trialOverallStatus") or "").upper()
        if stt in ONGOING:
            ongoing.append(r)
        if stt == "COMPLETED":
            done += 1
        if stt in STOPPED:
            for c in r.get("trialStopReasonCategories") or []:
                if c in FAIL_REASON:
                    fails.append(FAIL_REASON[c])
    return (n, ongoing, fails, done) if with_done else (n, ongoing, fails)


def status_of(stage, reports):
    """(상태 글자, 종류) — 종류: ok 허가 / bad 중단·실패 / cand 임상 있음 / none 근거만."""
    n, ongoing, fails, done = trials(reports, with_done=True)
    if stage == "APPROVAL":
        return "허가", "ok"
    if stage == "WITHDRAWAL":
        return "시장 철수", "bad"
    if fails and not ongoing and not done:     # 끝난 임상 없이 효과 부족·안전성으로만 멈춤
        return "중단 · " + "/".join(sorted(set(fails))), "bad"
    if stage and stage != "UNKNOWN":
        return "임상 · " + stage_ko(stage), "cand"
    if n:
        return "임상 있음", "cand"
    return "근거만", "none"


def _layout_mid(mids, right_y):
    """가운데 줄(표적) 높이: 이어진 오른쪽 칸들의 평균 높이 순으로 고르게 놓는다."""
    order = sorted(mids, key=lambda m: -(sum(right_y[c] for c in m["kids"]) / len(m["kids"])) if m["kids"] else 0)
    top = max(right_y.values()) if right_y else 0
    n = len(order)
    for i, m in enumerate(order):
        m["y"] = top - (top / max(n - 1, 1)) * i if n > 1 else top / 2
    return order


def drug_graph(raw, n_targets=5, n_per=6, min_score=0.2, hide_approved=False, only_ongoing=False, max_nodes=18):
    """약 지도: 왼쪽 약 1개 → 가운데 표적 → 오른쪽 질환. 순수 계산(화면 없음)."""
    tgs = raw["targets"][:n_targets]
    dis = {}
    for t in tgs:
        k = 0
        for a in raw["assoc"].get(t["id"], []):
            if a["score"] < min_score or k >= n_per:
                continue
            info = raw["ind"].get(a["id"], {})
            stage = info.get("stage", "")
            label, kind = status_of(stage, info.get("reports"))
            n, ongoing, _ = trials(info.get("reports"))
            if hide_approved and kind == "ok":
                continue
            if only_ongoing and not ongoing:
                continue
            k += 1
            d = dis.setdefault(a["id"], {"id": a["id"], "name": a["name"], "score": a["score"], "dts": a["dts"],
                                         "label": label, "kind": kind, "stage": stage, "n_trials": n,
                                         "ongoing": ongoing, "reports": info.get("reports", []), "targets": []})
            d["targets"].append((t["id"], a["score"]))
            d["score"] = max(d["score"], a["score"])
    right = sorted(dis.values(), key=lambda x: -x["score"])[:max_nodes]
    keep = {d["id"] for d in right}
    ys = {d["id"]: float(len(right) - 1 - i) for i, d in enumerate(right)}
    for d in right:
        d["y"] = ys[d["id"]]
    mids = [dict(t, kids=[d["id"] for d in right if any(x[0] == t["id"] for x in d["targets"])]) for t in tgs]
    mids = _layout_mid([m for m in mids if m["kids"]] or mids, ys)
    mid_ids = {m["id"] for m in mids}
    edges = [(tid, d["id"], s) for d in right if d["id"] in keep for (tid, s) in d["targets"] if tid in mid_ids]
    return {"left": {"name": raw["name"], "id": raw["id"], "y": (max(ys.values()) / 2) if ys else 0},
            "mid": mids, "right": right, "edges": edges}


def disease_graph(raw, n_targets=6, n_per=3, min_score=0.0, approved_only=True, only_ongoing=False,
                  keep_drug=None, rank_drug=None, max_nodes=18):
    """질환 지도: 왼쪽 질환 1개 → 가운데 표적 → 오른쪽 약. keep_drug/rank_drug 로 사이트 자료(특허·허가)를 반영."""
    drugs, tgs = {}, []
    for t in raw["targets"]:
        if t["score"] < min_score or len(tgs) >= n_targets:
            continue
        best = {}
        for r in raw["by_target"].get(t["id"], []):
            k = r["key"]
            if not k:
                continue
            if approved_only and r["stage"] != "APPROVAL":
                continue
            if keep_drug and not keep_drug(k):
                continue
            if k not in best or stage_rank(r["stage"]) > stage_rank(best[k]["stage"]):
                best[k] = r
        cand = []
        for k, r in best.items():
            kn = raw["known"].get(k, {})
            n, ongoing, fails = trials(kn.get("reports"))
            if only_ongoing and not ongoing:
                continue
            cand.append((k, r, kn, n, ongoing, fails))
        if not cand:
            continue                      # 쓸 수 있는 약이 없는 표적(유전 연구로만 이어진 것)은 건너뛴다
        tgs.append(t)
        cand.sort(key=lambda c: ((rank_drug(c[0]) if rank_drug else 0), -stage_rank(c[1]["stage"]), c[1]["name"]))
        for k, r, kn, n, ongoing, fails in cand[:n_per]:
            d = drugs.setdefault(k, {"key": k, "id": r["id"], "name": r["name"], "stage": r["stage"],
                                     "dz_stage": kn.get("stage", ""), "n_trials": n, "ongoing": ongoing,
                                     "fails": fails, "reports": kn.get("reports", []), "targets": []})
            d["targets"].append((t["id"], t["score"]))
    right = list(drugs.values())
    t_order = {t["id"]: i for i, t in enumerate(tgs)}       # 같은 표적의 약끼리 모아 선이 덜 엉키게
    right.sort(key=lambda d: (min(t_order.get(t, 99) for t, _ in d["targets"]), (rank_drug(d["key"]) if rank_drug else 0)))
    right = right[:max_nodes]
    ys = {d["key"]: float(len(right) - 1 - i) for i, d in enumerate(right)}
    for d in right:
        d["y"] = ys[d["key"]]
    mids = [dict(t, kids=[d["key"] for d in right if any(x[0] == t["id"] for x in d["targets"])]) for t in tgs]
    mids = _layout_mid([m for m in mids if m["kids"]] or mids, ys)
    mid_ids = {m["id"] for m in mids}
    edges = [(tid, d["key"], s) for d in right for (tid, s) in d["targets"] if tid in mid_ids]
    return {"left": {"name": raw["name"], "id": raw["id"], "y": (max(ys.values()) / 2) if ys else 0},
            "mid": mids, "right": right, "edges": edges}


# ── 한글 병명 사전 (AI 없이, 정해진 표로만 바꾼다) ─────────────────────────
# 왼쪽: 한글 (띄어쓰기 무시), 오른쪽: Open Targets 에서 찾을 영문 이름. 없으면 영문으로 넣으라고 안내한다.
KO_DISEASE = {
    "기침": "cough", "만성기침": "cough", "감기": "common cold", "독감": "influenza", "인플루엔자": "influenza",
    "폐렴": "pneumonia", "결핵": "tuberculosis", "코로나19": "COVID-19", "코로나": "COVID-19",
    "천식": "asthma", "만성폐쇄성폐질환": "chronic obstructive pulmonary disease", "copd": "chronic obstructive pulmonary disease",
    "특발성폐섬유증": "idiopathic pulmonary fibrosis", "폐섬유증": "idiopathic pulmonary fibrosis",
    "알레르기비염": "allergic rhinitis", "알레르기성비염": "allergic rhinitis",
    "고혈압": "hypertension", "폐동맥고혈압": "pulmonary arterial hypertension", "폐고혈압": "pulmonary hypertension",
    "당뇨": "type 2 diabetes mellitus", "당뇨병": "type 2 diabetes mellitus", "제2형당뇨병": "type 2 diabetes mellitus",
    "제1형당뇨병": "type 1 diabetes mellitus", "비만": "obesity",
    "고지혈증": "hyperlipidemia", "이상지질혈증": "dyslipidemia", "고콜레스테롤혈증": "hypercholesterolemia",
    "고요산혈증": "hyperuricemia", "통풍": "gout",
    "심부전": "heart failure", "협심증": "angina pectoris", "심근경색": "myocardial infarction",
    "관상동맥질환": "coronary artery disease", "심방세동": "atrial fibrillation", "뇌졸중": "stroke",
    "레이노": "Raynaud disease", "레이노현상": "Raynaud disease", "레이노병": "Raynaud disease",
    "알츠하이머": "Alzheimer disease", "알츠하이머병": "Alzheimer disease", "치매": "dementia",
    "파킨슨병": "Parkinson disease", "파킨슨": "Parkinson disease", "뇌전증": "epilepsy", "간질": "epilepsy",
    "편두통": "migraine disorder", "두통": "headache", "우울증": "major depressive disorder",
    "불안장애": "anxiety disorder", "조현병": "schizophrenia", "양극성장애": "bipolar disorder", "불면증": "insomnia",
    "adhd": "attention deficit hyperactivity disorder", "주의력결핍과잉행동장애": "attention deficit hyperactivity disorder",
    "자폐": "autism spectrum disorder", "신경병증성통증": "neuropathic pain", "섬유근육통": "fibromyalgia",
    "하지불안증후군": "restless legs syndrome", "다발성경화증": "multiple sclerosis",
    "루게릭병": "amyotrophic lateral sclerosis", "근위축성측삭경화증": "amyotrophic lateral sclerosis",
    "알코올의존": "alcohol dependence", "니코틴의존": "nicotine dependence", "요통": "low back pain",
    "아토피피부염": "atopic eczema", "아토피": "atopic eczema", "건선": "psoriasis", "두드러기": "urticaria",
    "여드름": "acne", "탈모": "alopecia",
    "류마티스관절염": "rheumatoid arthritis", "골관절염": "osteoarthritis", "퇴행성관절염": "osteoarthritis",
    "골다공증": "osteoporosis", "루푸스": "systemic lupus erythematosus", "전신홍반루푸스": "systemic lupus erythematosus",
    "강직성척추염": "ankylosing spondylitis", "베체트병": "Behcet disease", "쇼그렌증후군": "Sjogren syndrome",
    "전신경화증": "systemic sclerosis", "경피증": "systemic sclerosis",
    "크론병": "Crohn disease", "궤양성대장염": "ulcerative colitis", "과민성대장증후군": "irritable bowel syndrome",
    "위식도역류질환": "gastroesophageal reflux disease", "역류성식도염": "gastroesophageal reflux disease",
    "소화성궤양": "peptic ulcer disease", "위궤양": "peptic ulcer disease", "변비": "constipation", "설사": "diarrhea",
    "구토": "vomiting", "메스꺼움": "nausea", "간경변": "liver cirrhosis", "지방간": "non-alcoholic fatty liver disease",
    "비알코올성지방간": "non-alcoholic fatty liver disease", "비알코올성지방간염": "non-alcoholic steatohepatitis",
    "b형간염": "hepatitis B", "c형간염": "hepatitis C", "hiv": "HIV infection", "대상포진": "herpes zoster",
    "만성신장병": "chronic kidney disease", "만성콩팥병": "chronic kidney disease",
    "전립선비대증": "benign prostatic hyperplasia", "과민성방광": "overactive bladder", "발기부전": "erectile dysfunction",
    "월경통": "dysmenorrhea", "생리통": "dysmenorrhea", "자궁내막증": "endometriosis",
    "다낭성난소증후군": "polycystic ovary syndrome", "불임": "infertility", "난임": "infertility",
    "녹내장": "glaucoma", "황반변성": "age-related macular degeneration", "안구건조증": "dry eye syndrome",
    "백내장": "cataract", "갑상선기능저하증": "hypothyroidism", "갑상선기능항진증": "hyperthyroidism",
    "빈혈": "anemia", "철결핍성빈혈": "iron deficiency anemia", "혈우병": "hemophilia", "저나트륨혈증": "hyponatremia",
    "폐암": "lung cancer", "유방암": "breast cancer", "위암": "gastric cancer", "대장암": "colorectal cancer",
    "간암": "hepatocellular carcinoma", "췌장암": "pancreatic cancer", "전립선암": "prostate cancer",
    "갑상선암": "thyroid cancer", "백혈병": "leukemia", "림프종": "lymphoma", "다발골수종": "multiple myeloma",
    "다발성골수종": "multiple myeloma",
}
# 화면에 한글 병명을 덧붙일 때 쓰는 거꾸로 표 (영문 소문자 → 한글). 같은 영문이면 먼저 나온 한글을 쓴다.
EN_KO = {}
for _k, _v in KO_DISEASE.items():
    if re.search(r"[가-힣]", _k):
        EN_KO.setdefault(_v.lower(), _k)


def ko_disease(q):
    """한글 병명 → 영문 이름 (사전). 영문이면 그대로, 사전에 없으면 빈 값."""
    q = (q or "").strip()
    if not re.search(r"[가-힣]", q):
        return q
    k = re.sub(r"\s+", "", q).lower()
    if k in KO_DISEASE:
        return KO_DISEASE[k]
    hits = sorted((x for x in KO_DISEASE if x in k or (len(k) >= 2 and k in x)), key=len, reverse=True)
    return KO_DISEASE[hits[0]] if hits else ""


def ko_name(en):
    """영문 병명 → 사전에 있는 한글 병명 (없으면 빈 값). 화면 덧붙임용."""
    return EN_KO.get((en or "").strip().lower(), "")
