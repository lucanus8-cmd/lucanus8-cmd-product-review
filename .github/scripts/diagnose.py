"""앱이 왜 안 켜지는지 가려낸다 — 결과를 전부 로그로 찍는다(아티팩트 안 받아도 보이게).

가르려는 것:
  (A) 앱 서버(컨테이너)가 아예 안 돌고 있다  → /_stcore/health 가 ok 가 아니다
  (B) 서버는 도는데 브라우저가 화면 조각(JS)을 못 받는다 → health 는 ok, 요청 실패 목록에 js
  (C) 서버는 도는데 실시간 연결(WebSocket)이 안 붙는다 → health ok, 소켓 없음/닫힘
"""
import json
import os
import time
import urllib.request

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""


def plain_get(path, timeout=30):
    """브라우저 없이 그냥 HTTP 로 받아본다."""
    u = URL + path
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(400)
            return r.status, dict(r.headers), body
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}, b""


print("=" * 70)
print("1) 브라우저 없이 서버 상태만 확인")
print("=" * 70)
for path in ("/_stcore/health", "/healthz", "/"):
    st, hdr, body = plain_get(path)
    snip = body[:120].decode("utf-8", "replace").replace("\n", " ")
    print(f"  {path:20s} → 상태 {st}  서버 {hdr.get('Server', hdr.get('error', '?'))}")
    print(f"  {'':20s}   내용: {snip!r}")

print()
print("=" * 70)
print("2) 실제 브라우저로 접속 — 실패한 요청과 콘솔 오류를 전부 남긴다")
print("=" * 70)

failed = []     # 아예 못 받은 요청
bad = []        # 받았지만 오류 코드
console = []    # 콘솔 메시지
sockets = []    # 실시간 연결

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"],
                                channel="chromium")
    ctx = browser.new_context(
        viewport={"width": 1400, "height": 900}, locale="ko-KR",
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"))
    page = ctx.new_page()

    page.on("requestfailed", lambda r: failed.append((r.url, r.failure)))
    page.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
    page.on("console", lambda m: console.append((m.type, m.text[:300])))
    page.on("pageerror", lambda e: console.append(("pageerror", str(e)[:300])))

    def on_ws(ws):
        sockets.append(["open", ws.url, None])
        ws.on("close", lambda _: sockets.append(["close", ws.url, None]))
        ws.on("socketerror", lambda e: sockets.append(["error", ws.url, str(e)[:200]]))
    page.on("websocket", on_ws)

    t0 = time.time()
    page.goto(URL + "/", wait_until="domcontentloaded", timeout=90_000)
    print(f"  최초 HTML 도착: {time.time() - t0:.1f}초, 제목 {page.title()!r}")

    # 화면이 뜨는지 최대 120초 지켜본다 (10초마다 상태 보고)
    rendered = False
    for i in range(12):
        time.sleep(10)
        got = page.evaluate("""() => ({
            app:  !!document.querySelector("[data-testid='stApp']"),
            view: !!document.querySelector("[data-testid='stAppViewContainer']"),
            pwbox: !!document.querySelector("input[type='password']"),
            spin: !!document.querySelector("[data-testid='stSpinner']"),
            scripts: document.querySelectorAll('script[src]').length,
            bodylen: (document.body.innerText || '').length,
        })""")
        print(f"  {(i + 1) * 10:>3}초: {json.dumps(got, ensure_ascii=False)}")
        if got["app"] or got["view"] or got["pwbox"]:
            rendered = True
            print("  → 앱 화면이 떴습니다")
            break

    print()
    print(f"  최종 판정: {'화면 떴음' if rendered else '화면 안 뜸'}")
    print(f"  본문 글자: {(page.inner_text('body') or '')[:300]!r}")

    html = page.content()
    print(f"  HTML 길이: {len(html)}")
    # 셸 페이지인지 앱 페이지인지 구분되는 흔적
    for mark in ("Hosted with Streamlit", "get this app back up", "stApp",
                 "/~/+/static/js", "streamlit-cloud", "oauth", "Please wait"):
        if mark.lower() in html.lower():
            print(f"    HTML 안에 {mark!r} 있음")

    if PW and rendered:
        try:
            page.fill("input[type='password']", PW)
            page.keyboard.press("Enter")
            time.sleep(8)
            print(f"  로그인 후 화면: {(page.inner_text('body') or '')[:200]!r}")
        except Exception as e:
            print(f"  로그인 시도 실패: {type(e).__name__}")

    browser.close()

print()
print("=" * 70)
print("3) 요약")
print("=" * 70)
print(f"  못 받은 요청 {len(failed)}건")
for u, f in failed[:25]:
    print(f"    ✗ {f} ← {u[:140]}")
print(f"  오류 코드 응답 {len(bad)}건")
for s, u in bad[:25]:
    print(f"    ✗ {s} ← {u[:140]}")
print(f"  실시간 연결(WebSocket) {len(sockets)}건")
for s in sockets[:15]:
    print(f"    · {s[0]} {s[1][:120]} {s[2] or ''}")
print(f"  콘솔 메시지 {len(console)}건")
for t, m in console[:30]:
    print(f"    · [{t}] {m}")
