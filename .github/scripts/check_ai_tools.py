"""C 방식(Gemini 가 사이트 검색 도구를 골라 쓰기) 시험 — 사이트 부담·속도.
사이트(Streamlit Cloud)에는 쓰지 않는다. 매출 숫자는 적지 않는다(도구 결과 중 매출이 든 것은 길이만 적음).

1. 자료는 따로 띄운 작업에서 만든다(만드는 메모리는 재지 않음 — 사이트에선 완성본을 받음).
2. 사이트와 같은 모듈을 불러온 프로세스에서, 복잡한 질문 10개에 대해 Gemini 가 부를 법한 도구 순서를 그대로 돌린다.
   처음(표를 처음 올림) / 두 번째(이미 올라와 있음) / 3명이 동시에 — 각각 메모리·시간.
3. GEMINI_API_KEY 가 있으면 실제 Gemini 에 도구를 쥐여 주고 질문 3개를 해 본다(어떤 도구를 불렀는지만 적음)."""
import base64, json, os, subprocess, sys, threading, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

PLANS = [
    ("과민성 방광 약 중 특허 곧 끝나고 시장 큰 것",
     [("disease_search", {"disease": "과민성 방광"}),
      ("candidate_filter", {"min_size_eok": 30, "patent_by_year": 2030, "disease": "과민성 방광"}),
      ("patent_expiry", {"ingredient": "미라베그론"}), ("sales_summary", {"ingredient": "미라베그론"})]),
    ("에독사반 제네릭 언제 나왔고 원료사는",
     [("find_ingredient", {"name": "에독사반"}), ("price_summary", {"ingredient": "에독사반"}),
      ("dmf_info", {"ingredient": "에독사반"}), ("patent_expiry", {"ingredient": "에독사반"})]),
    ("고지혈증 시장에서 성장 큰 성분",
     [("candidate_filter", {"min_size_eok": 100, "min_cagr": 5, "disease": "고지혈증, 이상지질혈증"}),
      ("sales_summary", {"ingredient": "로수바스타틴", "source": "UBIST"}),
      ("sales_summary", {"ingredient": "에제티미브", "source": "UBIST"})]),
    ("트윈스타 허가·약가·매출",
     [("find_ingredient", {"name": "트윈스타"}), ("approval_info", {"ingredient": "텔미사르탄"}),
      ("price_summary", {"ingredient": "텔미사르탄"}), ("sales_summary", {"ingredient": "텔미사르탄"})]),
    ("당뇨 약 중 2028년까지 특허 만료 + 시장 30억 이상",
     [("candidate_filter", {"min_size_eok": 30, "patent_by_year": 2028, "disease": "당뇨"}),
      ("dmf_info", {"ingredient": "시타글립틴"}), ("dmf_info", {"ingredient": "다파글리플로진"})]),
    ("리바록사반 개발성 검토",
     [("approval_info", {"ingredient": "리바록사반"}), ("patent_expiry", {"ingredient": "리바록사반"}),
      ("price_summary", {"ingredient": "리바록사반"}), ("sales_summary", {"ingredient": "리바록사반"}),
      ("dmf_info", {"ingredient": "리바록사반"})]),
    ("골다공증 후보",
     [("disease_search", {"disease": "골다공증"}),
      ("candidate_filter", {"min_size_eok": 20, "disease": "골다공증"})]),
    ("천식·비염 성분 비교",
     [("disease_search", {"disease": "천식"}), ("disease_search", {"disease": "비염"}),
      ("sales_summary", {"ingredient": "몬테루카스트"}), ("patent_expiry", {"ingredient": "몬테루카스트"})]),
    ("로수바스타틴 vs 아토르바스타틴",
     [("sales_summary", {"ingredient": "로수바스타틴"}), ("sales_summary", {"ingredient": "아토르바스타틴"}),
      ("price_summary", {"ingredient": "로수바스타틴"}), ("price_summary", {"ingredient": "아토르바스타틴"})]),
    ("특허 2027년까지 끝나고 성장 10% 이상 큰 시장",
     [("candidate_filter", {"min_size_eok": 100, "min_cagr": 10, "patent_by_year": 2027})]),
]
SECRET_TOOLS = {"sales_summary", "candidate_filter"}     # 매출이 든 결과 → 내용 안 적음


def rss():
    with open("/proc/self/status") as f:
        for ln in f:
            if ln.startswith("VmRSS"):
                return int(ln.split()[1]) / 1024
    return 0.0


class Peak:
    def __init__(self):
        self.v, self.stop = rss(), False
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.stop:
            self.v = max(self.v, rss()); time.sleep(0.1)

    def reset(self):
        self.v = rss()


def main():
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    t = time.time()
    subprocess.run([sys.executable, "-c", "import warnings,logging;warnings.filterwarnings('ignore');logging.disable(30);"
                    "import bootstrap;print('자료 만들기:', bootstrap._do_build(include_sales=True))"], cwd=ROOT, check=False)
    print(f"(자료 만들기 {time.time()-t:.0f}초 — 따로 띄운 작업이라 아래 메모리에 안 들어감)")

    import streamlit  # noqa
    import pandas as pd
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    import ai_tools as at
    pk = Peak()
    base = rss()
    print(f"\n시작 (사이트 모듈 불러온 상태): {base:.0f} MB · 효능 자료: {'있음' if os.path.exists(os.environ.get('EE_SITE_BASE','/x') + 'ee_text.parquet') else '없음'}")

    def run_plan(q, steps, log=True):
        out = []
        for name, args in steps:
            t0 = time.time(); r = at.call(name, args); dt = time.time() - t0
            js = json.dumps(r, ensure_ascii=False, default=str)
            out.append((name, dt, len(js)))
            if log:
                shown = "(매출 포함 → 내용 생략)" if name in SECRET_TOOLS else js[:150]
                print(f"   {name:<17} {dt*1000:6.0f}ms · 결과 {len(js):5,}자 · 메모리 {rss():5.0f} MB · {shown}")
        return out

    for tag in ("① 처음 (표를 처음 올림)", "② 두 번째 (이미 올라와 있음)"):
        print(f"\n── {tag} ──")
        pk.reset(); t0 = time.time(); tot_chars = 0
        for q, steps in PLANS:
            if tag.startswith("①"):
                print(f" [{q}]")
            res = run_plan(q, steps, log=tag.startswith("①"))
            tot_chars += sum(c for _, _, c in res)
        print(f" → 10개 질문 · 도구 {sum(len(s) for _, s in PLANS)}번 · {time.time()-t0:.1f}초 · "
              f"최고 {pk.v:.0f} MB · 끝 {rss():.0f} MB · 도구 결과 합계 {tot_chars:,}자 (Gemini 에 보내는 양, 질문당 약 {tot_chars/10:,.0f}자)")

    print("\n── ③ 3명이 동시에 10개 질문씩 ──")
    pk.reset(); t0 = time.time()
    ths = [threading.Thread(target=lambda: [run_plan(q, s, log=False) for q, s in PLANS]) for _ in range(3)]
    [t.start() for t in ths]; [t.join() for t in ths]
    print(f" → {time.time()-t0:.1f}초 · 최고 {pk.v:.0f} MB · 끝 {rss():.0f} MB")
    print(f"\n정리: 도구 쓰느라 늘어난 메모리 = 끝 {rss():.0f} MB − 시작 {base:.0f} MB = {rss()-base:.0f} MB "
          f"(이 중 대부분은 사이트 화면도 같이 쓰는 공유 표)")

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("\n(GEMINI_API_KEY 없음 → 실제 Gemini 시험은 건너뜀)")
        return 0
    print("\n── ④ 실제 Gemini 에 도구 쥐여 주기 ──")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    tools = [types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in at.TOOLS])]
    sysmsg = ("너는 제약 제품 검토 보조다. 반드시 도구로 찾은 사이트 자료만 근거로 한국어로 답하라. "
              "자료에 없으면 '자료 없음'이라고 하라. 문장마다 [출처]를 달라.")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    for q in ["과민성 방광 약 중에 특허가 곧 끝나고 시장이 큰 성분은?", "에독사반 제네릭은 언제 나왔고 원료(DMF) 등록사는?",
              "고지혈증 약 중에 성장률이 높은 성분 3개 알려줘"]:
        pk.reset(); t0 = time.time(); calls = []
        contents = [types.Content(role="user", parts=[types.Part(text=q)])]
        ans = ""
        for turn in range(6):
            r = client.models.generate_content(model=model, contents=contents,
                                               config=types.GenerateContentConfig(system_instruction=sysmsg, tools=tools))
            cand = r.candidates[0].content
            fcs = [p.function_call for p in (cand.parts or []) if getattr(p, "function_call", None)]
            if not fcs:
                ans = r.text or ""
                break
            contents.append(cand)
            parts = []
            for fc in fcs:
                res = at.call(fc.name, dict(fc.args or {}))
                calls.append(f"{fc.name}({json.dumps(dict(fc.args or {}), ensure_ascii=False)})")
                parts.append(types.Part.from_function_response(name=fc.name, response={"result": json.loads(json.dumps(res, default=str))}))
            contents.append(types.Content(role="user", parts=parts))
        print(f" [{q}] Gemini {turn+1}번 · {time.time()-t0:.1f}초 · 최고 {pk.v:.0f} MB · 답 {len(ans)}자")
        for c in calls:
            print("    도구:", c[:150])
    return 0


if __name__ == "__main__":
    sys.exit(main())
