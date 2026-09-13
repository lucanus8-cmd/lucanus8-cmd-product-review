import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import re
import os
import shutil
from pathlib import Path
from io import BytesIO
from openpyxl import Workbook
from openpyxl.chart import BarChart, PieChart, LineChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils.dataframe import dataframe_to_rows

st.set_page_config(page_title="의약품 매출 분석", layout="wide", page_icon="💊")

# 비밀번호 잠금 (배포 시 st.secrets에 APP_PASSWORD 설정 시에만 작동)
def _check_password():
    try:
        pw = st.secrets.get("APP_PASSWORD", None)
    except Exception:
        pw = None
    if not pw:
        return True  # 비밀번호 미설정(로컬) → 통과
    if st.session_state.get("_authed"):
        return True
    st.title("🔒 의약품 매출 분석")
    entered = st.text_input("비밀번호를 입력하세요", type="password")
    if entered == "":
        st.stop()
    if entered == pw:
        st.session_state["_authed"] = True
        st.rerun()
    else:
        st.error("비밀번호가 올바르지 않습니다.")
        st.stop()
    return False

_check_password()

st.title("💊 의약품 매출 분석 대시보드")
st.caption("자료원: IQVIA · UBIST")

# 저장 폴더 (exe로 실행 시엔 쓰기 가능한 사용자 폴더 사용)
import sys as _sys, tempfile as _tmp
if getattr(_sys, "frozen", False):
    _base = Path(os.environ.get("LOCALAPPDATA", _tmp.gettempdir())) / "의약품분석"
else:
    _base = Path(__file__).parent
DATA_DIR = _base / "saved_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
IQVIA_SAVED = DATA_DIR / "iqvia.xlsx"
UBIST_SAVED = DATA_DIR / "ubist.xlsx"
IQVIA_CACHE = DATA_DIR / "iqvia.pkl"   # 빠른 로딩용 캐시 (pickle)
UBIST_CACHE = DATA_DIR / "ubist.pkl"

# ── 엑셀 파싱 (느림 — 업로드 시 1회만 실행) ──────────────────────────────────

def parse_iqvia(path):
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    lc_qtr = [c for c in df.columns if re.match(r"LC-\d{2}Q\d", c)]
    lc_yr  = [c for c in df.columns if re.match(r"\d{4}년$", c)]
    du_qtr = [c for c in df.columns if re.match(r"DU-\d{2}Q\d", c)]
    du_yr  = [c for c in df.columns if re.match(r"DU_\d{4}년", c)]
    # 안 쓰는 지표(CU·Unit·Price)만 제거 (LC=매출액, DU=처방량은 유지)
    drop_cols = [c for c in df.columns if re.match(r"(CU|Unit|Price)[-_]", c)]
    df = df.drop(columns=drop_cols, errors="ignore")
    for c in lc_qtr + lc_yr + du_qtr + du_yr:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df.copy()   # 단편화 해소

def parse_ubist(path):
    raw = pd.read_excel(path, sheet_name=0, header=None)
    row0 = raw.iloc[0].tolist()
    row1 = raw.iloc[1].tolist()
    METRICS = ("처방조제액(원)", "처방건수_P", "처방량_P")
    cols = []
    for r0, r1 in zip(row0, row1):
        r0 = str(r0).strip() if pd.notna(r0) else ""
        r1 = str(r1).strip() if pd.notna(r1) else ""
        if r0 in METRICS:
            # 기간 정규화: "2021년 1분기" → "2021Q1", "2021년" → "2021Q0"(연)
            mq = re.search(r"(\d{4})\s*년\s*(\d)\s*분기", r1)
            my = re.search(r"(\d{4})\s*년", r1)
            if mq:
                cols.append(f"{r0}_{mq.group(1)}Q{mq.group(2)}")
            elif my:
                cols.append(f"{r0}_{my.group(1)}Q0")
            else:
                cols.append(f"{r0}_{r1}")
        else:
            cols.append(r1 if r1 else r0)
    df = raw.iloc[2:].copy()
    df.columns = cols
    df = df.rename(columns={"제품": "제품명", "제조사": "제조사명"})
    seen, final_cols = {}, []
    for c in df.columns:
        if c not in seen:
            seen[c] = 0; final_cols.append(c)
        else:
            seen[c] += 1; final_cols.append(f"{c}_{seen[c]}")
    df.columns = final_cols
    num_cols = [c for c in df.columns if any(m in c for m in ("처방조제액", "처방건수", "처방량"))]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df = df.dropna(subset=["제품명"])
    df = df[df["제품명"].astype(str).str.strip() != ""]
    # 분기 컬럼으로부터 연간 합계 컬럼 생성 (기존 연간 분석 호환)
    for metric in METRICS:
        qcols = {}
        for c in df.columns:
            m = re.match(rf"{re.escape(metric)}_(\d{{4}})Q([1-4])$", c)
            if m:
                qcols.setdefault(m.group(1), []).append(c)
        for yr, cs in qcols.items():
            df[f"{metric}_{yr}년"] = df[cs].sum(axis=1)
    # 성분 텍스트에서 용량 추출 (예: "itopride HCl 50mg [코드]" → "50mg")
    if "성분" in df.columns:
        # mg, mcg, g, ml, IU, % 및 특수 단위 기호(㎎ ㎍ ㎖ ㎕ ㏉ 등) 인식
        dose_pat = re.compile(
            r"(\d+\.?\d*\s?(?:mg|mcg|µg|ug|g|ml|mL|L|IU|단위|%|㎎|㎍|㎕|㎖|㏖|㎏|㎗))",
            re.IGNORECASE)
        df["용량"] = df["성분"].astype(str).apply(
            lambda s: "/".join(dict.fromkeys(m.replace(" ", "") for m in dose_pat.findall(s))) or "")
    return df.copy()

def build_cache(which):
    """엑셀 → pickle 캐시 변환 (업로드 시 1회)"""
    if which == "iqvia":
        df = parse_iqvia(IQVIA_SAVED)
        df.to_pickle(IQVIA_CACHE)
    else:
        df = parse_ubist(UBIST_SAVED)
        df.to_pickle(UBIST_CACHE)

# ── 빠른 로드 (pickle 캐시에서) ─────────────────────────────────────────────

@st.cache_data(show_spinner="IQVIA 로딩 중…")
def load_iqvia(cache_mtime):
    df = pd.read_pickle(IQVIA_CACHE)
    lc_qtr = [c for c in df.columns if re.match(r"LC-\d{2}Q\d", c)]
    lc_yr  = [c for c in df.columns if re.match(r"\d{4}년$", c)]
    return df, lc_qtr, lc_yr

@st.cache_data(show_spinner="UBIST 로딩 중…")
def load_ubist(cache_mtime):
    df = pd.read_pickle(UBIST_CACHE)
    # 연간 컬럼만 기본 num_cols로 (분기·연간 혼합 중복합산 방지)
    num_cols = [c for c in df.columns if re.search(r"(처방조제액|처방건수|처방량).*_\d{4}년$", c)]
    return df, num_cols

# UBIST 지표 컬럼 선택 (gran: "연간" | "분기별")
def u_cols(df, metric, gran="연간"):
    if gran == "분기별":
        pat = rf"{re.escape(metric)}.*_(\d{{4}})Q([1-4])$"
    else:
        pat = rf"{re.escape(metric)}.*_(\d{{4}})년$"
    cols = [c for c in df.columns if re.search(pat, c)]
    def _key(c):
        m = re.search(r"_(\d{4})Q([1-4])$", c) or re.search(r"_(\d{4})년$", c)
        return (m.group(1), m.group(2) if "Q" in c else "0")
    return sorted(cols, key=_key)

def u_period_label(c):
    m = re.search(r"_(\d{4})Q([1-4])$", c)
    if m:
        return f"{m.group(1)} {m.group(2)}Q"
    m = re.search(r"_(\d{4})년$", c)
    return f"{m.group(1)}년" if m else c

# ── CAGR 계산 ────────────────────────────────────────────────────────────────

def calc_cagr(values, labels):
    """연도별 값 시계열에서 CAGR(%) 계산.
    첫 양수 연도를 시작점, 마지막 연도를 끝점으로 사용."""
    pairs = [(lbl, v) for lbl, v in zip(labels, values) if v and v > 0]
    if len(pairs) < 2:
        return None
    (start_lbl, start_v) = pairs[0]
    (end_lbl, end_v) = pairs[-1]
    n = int(re.search(r"(\d{4})", end_lbl).group(1)) - int(re.search(r"(\d{4})", start_lbl).group(1))
    if n <= 0 or start_v <= 0:
        return None
    return ((end_v / start_v) ** (1 / n) - 1) * 100

# ── 재사용 동적 필터 ─────────────────────────────────────────────────────────

def _is_active(v):
    """필터 값이 유효한지 (리스트면 비어있지 않음, 문자열이면 공백 아님)"""
    if isinstance(v, list):
        return len(v) > 0
    return bool(str(v).strip())

def render_filters(state_key, fields, placeholder="검색어 입력", options_map=None):
    """동적 AND 필터 UI. options_map에 있는 항목은 다중선택 드롭다운으로 표시."""
    options_map = options_map or {}
    if state_key not in st.session_state:
        st.session_state[state_key] = [{"field": fields[0], "value": ""}]
    for idx, flt in enumerate(st.session_state[state_key]):
        c1, c2, c3 = st.columns([1.3, 3, 0.6])
        with c1:
            new_field = st.selectbox(
                "기준", fields,
                index=fields.index(flt["field"]) if flt["field"] in fields else 0,
                key=f"{state_key}_f_{idx}", label_visibility="collapsed")
            if new_field != flt["field"]:
                # 항목이 바뀌면 값 타입 초기화
                flt["value"] = [] if new_field in options_map else ""
            flt["field"] = new_field
        with c2:
            if flt["field"] in options_map:
                cur = flt["value"] if isinstance(flt["value"], list) else []
                cur = [v for v in cur if v in options_map[flt["field"]]]
                flt["value"] = st.multiselect(
                    "선택", options_map[flt["field"]], default=cur,
                    key=f"{state_key}_v_{idx}", label_visibility="collapsed",
                    placeholder="목록에서 선택 (여러 개 가능)")
            else:
                val = flt["value"] if isinstance(flt["value"], str) else ""
                flt["value"] = st.text_input(
                    "검색어", value=val, placeholder=placeholder,
                    key=f"{state_key}_v_{idx}", label_visibility="collapsed")
        with c3:
            if st.button("🗑️", key=f"{state_key}_d_{idx}", help="삭제"):
                st.session_state[state_key].pop(idx)
                st.rerun()
    ca, cb = st.columns(2)
    with ca:
        if st.button("➕ 조건 추가", key=f"{state_key}_add", use_container_width=True):
            st.session_state[state_key].append({"field": fields[0], "value": ""})
            st.rerun()
    with cb:
        if st.button("🔄 초기화", key=f"{state_key}_clr", use_container_width=True):
            st.session_state[state_key] = [{"field": fields[0], "value": ""}]
            st.rerun()
    return [f for f in st.session_state[state_key] if _is_active(f["value"])]

def make_filter_mask(df, active, field_map, require_any=True):
    """AND 마스크 생성. 값이 리스트면 isin(정확 일치), 문자열이면 contains."""
    mask = pd.Series(True, index=df.index)
    applied = 0
    for f in active:
        col = field_map.get(f["field"])
        if col and col in df.columns:
            if isinstance(f["value"], list):
                mask &= df[col].astype(str).isin([str(x) for x in f["value"]])
            else:
                mask &= df[col].astype(str).str.contains(f["value"].strip(), case=False, na=False)
            applied += 1
    if applied == 0 and not require_any:
        return pd.Series(False, index=df.index)
    return mask

def build_excel(blocks, meta_title=""):
    """blocks: [{name, df, chart, cat, val, title}] → xlsx bytes.
    chart ∈ {None, 'bar', 'pie', 'line'}"""
    wb = Workbook()
    wb.remove(wb.active)
    hdr_font = Font(bold=True, color="FFFFFF", name="맑은 고딕")
    hdr_fill = PatternFill("solid", fgColor="4C72B0")
    for b in blocks:
        df = b["df"]
        if df is None or df.empty:
            continue
        ws = wb.create_sheet(title=b["name"][:31])
        if b.get("title"):
            ws["A1"] = b["title"]
            ws["A1"].font = Font(bold=True, size=12, name="맑은 고딕")
            start_row = 3
        else:
            start_row = 1
        # 헤더
        for j, col in enumerate(df.columns, start=1):
            c = ws.cell(row=start_row, column=j, value=str(col))
            c.font = hdr_font; c.fill = hdr_fill
            c.alignment = Alignment(horizontal="center")
        # 데이터 (+ 숫자 서식)
        for i, (_, row) in enumerate(df.iterrows(), start=start_row + 1):
            for j, col in enumerate(df.columns, start=1):
                v = row[col]
                cell = ws.cell(row=i, column=j, value=(None if pd.isna(v) else v))
                if isinstance(v, (int, float)) and not pd.isna(v):
                    cname = str(col)
                    if "%" in cname or "CAGR" in cname or "비율" in cname:
                        cell.number_format = "0.0"
                    else:
                        cell.number_format = "#,##0"
        # 열 너비
        for j, col in enumerate(df.columns, start=1):
            ws.column_dimensions[ws.cell(row=start_row, column=j).column_letter].width = max(12, min(40, len(str(col)) + 4))
        # 차트
        chart_type = b.get("chart")
        if chart_type and b.get("cat") in df.columns and b.get("val") in df.columns:
            cat_idx = list(df.columns).index(b["cat"]) + 1
            val_idx = list(df.columns).index(b["val"]) + 1
            n = len(df)
            data_min = start_row
            data_max = start_row + n
            cats = Reference(ws, min_col=cat_idx, min_row=start_row + 1, max_row=data_max)
            vals = Reference(ws, min_col=val_idx, min_row=data_min, max_row=data_max)
            if chart_type == "pie":
                ch = PieChart()
            elif chart_type == "line":
                ch = LineChart()
            else:
                ch = BarChart(); ch.type = "col"
            ch.add_data(vals, titles_from_data=True)
            ch.set_categories(cats)
            ch.title = b.get("title", b["name"])
            ch.height = 8; ch.width = 16
            # 데이터 라벨(숫자) 표시
            ch.dataLabels = DataLabelList()
            ch.dataLabels.showVal = True
            ch.dataLabels.numFmt = "#,##0"
            if chart_type == "pie":
                ch.dataLabels.showPercent = True
                ch.dataLabels.showCatName = True
            anchor_col = ws.cell(row=1, column=len(df.columns) + 2).column_letter
            ws.add_chart(ch, f"{anchor_col}{start_row}")
    if not wb.sheetnames:
        wb.create_sheet("데이터없음")
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()

def build_report(summary, ri, ru):
    """검색 결과 기반 매출액 보고서(서술형 텍스트) 생성. ri=IQVIA, ru=UBIST 수집 dict."""
    from datetime import date
    L = []
    L.append("=" * 60)
    L.append("의약품 매출 분석 보고서")
    L.append("=" * 60)
    L.append(f"작성일: {date.today().isoformat()}")
    L.append(f"검색 조건: {summary}")
    L.append("")

    if ri:
        L.append("【 IQVIA — 매출액 분석 】")
        L.append(f"· 검색 품목 수: {ri['n']}개 팩")
        if ri.get("yearly"):
            yrs = ri["yearly"]
            first_y, last_y = yrs[0], yrs[-1]
            L.append(f"· 매출액 추이(백만원): " + ", ".join([f"{y[0][:-1]} {y[1]:,.0f}" for y in yrs]))
            L.append(f"· {first_y[0][:-1]}년 {first_y[1]:,.0f}백만원 → {last_y[0][:-1]}년 {last_y[1]:,.0f}백만원")
        if ri.get("cagr") is not None:
            L.append(f"· 연평균 성장률(CAGR, ~2025): {ri['cagr']:+.1f}%")
        if ri.get("reimb"):
            top_r = ri["reimb"][0]
            L.append(f"· 급여 구성: " + ", ".join([f"{r[0]} {r[2]:.1f}%" for r in ri["reimb"]]))
        if ri.get("etc_otc"):
            L.append(f"· 전문/일반: " + ", ".join([f"{e[0]} {e[1]:.1f}%" for e in ri["etc_otc"]]))
        if ri.get("top_atc"):
            L.append(f"· 주요 ATC 계열: " + ", ".join([f"{a[0]} ({a[1]:.1f}%)" for a in ri["top_atc"][:3]]))
        if ri.get("top_form"):
            tf = ri["top_form"][0]
            L.append(f"· 주력 제형: {tf[0]} ({tf[1]:.1f}%)")
        if ri.get("top_prod"):
            L.append("· 매출 상위 품목(최근연도, 백만원):")
            for p in ri["top_prod"][:5]:
                L.append(f"    - {p[0]}: {p[1]:,.0f}")
        L.append("")

    if ru:
        L.append("【 UBIST — 처방 분석 】")
        L.append(f"· 검색 품목 수: {ru['n']}개")
        if ru.get("rx_yearly"):
            L.append(f"· 처방조제액 추이(백만원): " + ", ".join([f"{y[0]} {y[1]:,.0f}" for y in ru["rx_yearly"]]))
        if ru.get("cagr_rx") is not None:
            L.append(f"· 처방조제액 CAGR(~2025): {ru['cagr_rx']:+.1f}%")
        if ru.get("cagr_cnt") is not None:
            L.append(f"· 처방건수 CAGR(~2025): {ru['cagr_cnt']:+.1f}%")
        L.append("")

    # 요약 코멘트
    L.append("【 요약 】")
    comments = []
    if ri and ri.get("cagr") is not None:
        if ri["cagr"] >= 20:
            comments.append(f"IQVIA 기준 연 {ri['cagr']:.0f}%의 고성장세를 보이고 있습니다.")
        elif ri["cagr"] >= 0:
            comments.append(f"IQVIA 기준 연 {ri['cagr']:.0f}%로 완만한 성장 중입니다.")
        else:
            comments.append(f"IQVIA 기준 매출이 연 {abs(ri['cagr']):.0f}% 감소하고 있습니다.")
    if ri and ri.get("top_form"):
        comments.append(f"주력 제형은 {ri['top_form'][0][0]}입니다.")
    if not comments:
        comments.append("검색 조건에 해당하는 데이터를 정리했습니다.")
    L.extend(["· " + c for c in comments])
    L.append("")
    L.append("(본 보고서는 검색된 데이터 기반 자동 생성되었습니다.)")
    return "\n".join(L)

def _mpl_korean():
    """matplotlib 로드 (없으면 None 반환 → 워드 보고서 차트는 생략)"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    for f in ["Malgun Gothic", "맑은 고딕", "NanumGothic", "AppleGothic"]:
        try:
            plt.rcParams["font.family"] = f
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    return plt

def build_word(summary, blocks, report_text):
    """보고서 텍스트 + 표 + 그래프(이미지)를 담은 Word(.docx) bytes 생성."""
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    plt = _mpl_korean()

    KFONT = "맑은 고딕"

    def set_kfont(style):
        """스타일에 한글(eastAsia) 폰트까지 지정"""
        try:
            style.font.name = KFONT
            rpr = style.element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = rpr.makeelement(qn("w:rFonts"), {})
                rpr.append(rfonts)
            for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
                rfonts.set(qn(attr), KFONT)
        except Exception:
            pass

    doc = Document()
    # 모든 주요 스타일에 한글 폰트 적용
    for sname in ["Normal", "List Bullet", "Heading 1", "Heading 2", "Title"]:
        if sname in [s.name for s in doc.styles]:
            set_kfont(doc.styles[sname])
    doc.styles["Normal"].font.size = Pt(10)

    def kpar(text, style=None):
        """한글 폰트가 보장된 문단 추가"""
        p = doc.add_paragraph(style=style)
        run = p.add_run(text)
        run.font.name = KFONT
        run._element.rPr.rFonts.set(qn("w:eastAsia"), KFONT)
        return p

    title = doc.add_heading("의약품 매출 분석 보고서", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kpar(f"검색 조건: {summary}")

    # 서술형 요약 (build_report 텍스트에서 구분선/제목 제거하고 본문만)
    doc.add_heading("분석 요약", level=1)
    for line in report_text.splitlines():
        s = line.strip()
        if not s or set(s) <= {"="} or s == "의약품 매출 분석 보고서":
            continue
        if s.startswith("【") and s.endswith("】"):
            doc.add_heading(s.strip("【】 "), level=2)
        else:
            kpar(s, style="List Bullet" if s.startswith("·") else None)

    # 표 + 그래프
    doc.add_heading("상세 표 및 그래프", level=1)
    for b in blocks:
        df = b.get("df")
        if df is None or df.empty:
            continue
        doc.add_heading(b.get("title", b["name"]), level=2)

        # 그래프 이미지 (matplotlib 있을 때만)
        ctype = b.get("chart")
        if plt is not None and ctype and b.get("cat") in df.columns and b.get("val") in df.columns:
            cats = df[b["cat"]].astype(str).tolist()
            vals = pd.to_numeric(df[b["val"]], errors="coerce").fillna(0).tolist()
            fig, ax = plt.subplots(figsize=(6, 3.2), dpi=130)
            if ctype == "pie":
                ax.pie(vals, labels=cats, autopct="%1.1f%%", textprops={"fontsize": 8})
            elif ctype == "line":
                ax.plot(cats, vals, marker="o")
                for x, y in zip(cats, vals):
                    ax.annotate(f"{y:,.0f}", (x, y), fontsize=7, ha="center", va="bottom")
            else:
                bars = ax.bar(cats, vals, color="#4C72B0")
                for bar, y in zip(bars, vals):
                    ax.annotate(f"{y:,.0f}", (bar.get_x() + bar.get_width()/2, y),
                                fontsize=7, ha="center", va="bottom")
                ax.tick_params(axis="x", rotation=30)
            ax.set_title(b.get("title", ""), fontsize=9)
            fig.tight_layout()
            img = BytesIO()
            fig.savefig(img, format="png", bbox_inches="tight")
            plt.close(fig)
            img.seek(0)
            doc.add_picture(img, width=Inches(5.5))

        # 표
        def kcell(cell, text):
            cell.text = ""
            run = cell.paragraphs[0].add_run(str(text))
            run.font.name = KFONT
            run._element.rPr.rFonts.set(qn("w:eastAsia"), KFONT)

        tbl = doc.add_table(rows=1, cols=len(df.columns))
        tbl.style = "Light Grid Accent 1"
        for j, col in enumerate(df.columns):
            kcell(tbl.rows[0].cells[j], str(col))
        for _, row in df.iterrows():
            cells = tbl.add_row().cells
            for j, col in enumerate(df.columns):
                v = row[col]
                if isinstance(v, (int, float)) and pd.notna(v):
                    txt = f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.1f}"
                else:
                    txt = "" if pd.isna(v) else str(v)
                kcell(cells[j], txt)
        doc.add_paragraph("")

    bio = BytesIO()
    doc.save(bio)
    return bio.getvalue()

# 시장분석/성장탐색용 필터 항목 매핑
MKT_IQVIA_FIELDS = ["제품명", "성분명", "제조사", "용량", "ATC", "ETC/OTC", "급여구분"]
MKT_IQVIA_MAP = {"제품명": "제품명", "성분명": "성분명", "제조사": "회사명",
                 "용량": "용량", "ATC": "ATC 4(한글)", "ETC/OTC": "ETC/OTC", "급여구분": "급여구분"}
ETC_OTC_OPTS = ["ETHICAL", "OTC"]  # ETHICAL=전문약, OTC=일반약
MKT_UBIST_FIELDS = ["제품명", "성분명", "제조사", "용량", "ATC", "급여구분"]
MKT_UBIST_MAP = {"제품명": "제품명", "성분명": "성분", "제조사": "제조사명",
                 "용량": "용량", "ATC": "ATC", "급여구분": "급여구분"}

# ── 사이드바: 파일 업로드 ────────────────────────────────────────────────────

with st.sidebar:
    st.header("📂 데이터 관리")

    # IQVIA
    st.markdown("**IQVIA**")
    iqvia_status = f"✅ 저장됨 ({pd.Timestamp(IQVIA_SAVED.stat().st_mtime, unit='s').strftime('%y.%m.%d')})" if IQVIA_SAVED.exists() else "❌ 없음"
    st.caption(iqvia_status)
    iqvia_upload = st.file_uploader("새 IQVIA 파일 업로드", type=["xlsx"], key="iqvia_up")
    if iqvia_upload and st.session_state.get("iqvia_saved_id") != iqvia_upload.file_id:
        bytes_data = iqvia_upload.getvalue()
        if len(bytes_data) > 0:
            with open(IQVIA_SAVED, "wb") as f:
                f.write(bytes_data)
            st.session_state["iqvia_saved_id"] = iqvia_upload.file_id
            with st.spinner("IQVIA 변환 중… (최초 1회, 약 30초)"):
                build_cache("iqvia")
            st.success(f"IQVIA 저장 완료! ({len(bytes_data)//1024//1024:.1f}MB)")
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("파일이 비어있습니다. 다시 시도해주세요.")

    st.divider()

    # UBIST
    st.markdown("**UBIST**")
    ubist_status = f"✅ 저장됨 ({pd.Timestamp(UBIST_SAVED.stat().st_mtime, unit='s').strftime('%y.%m.%d')})" if UBIST_SAVED.exists() else "❌ 없음"
    st.caption(ubist_status)
    ubist_upload = st.file_uploader("새 UBIST 파일 업로드", type=["xlsx"], key="ubist_up")
    if ubist_upload and st.session_state.get("ubist_saved_id") != ubist_upload.file_id:
        bytes_data = ubist_upload.getvalue()
        if len(bytes_data) > 0:
            with open(UBIST_SAVED, "wb") as f:
                f.write(bytes_data)
            st.session_state["ubist_saved_id"] = ubist_upload.file_id
            with st.spinner("UBIST 변환 중…"):
                build_cache("ubist")
            st.success(f"UBIST 저장 완료! ({len(bytes_data)//1024//1024:.1f}MB)")
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("파일이 비어있습니다. 다시 시도해주세요.")

    st.divider()
    st.caption("한 번 업로드하면 다음 실행 시 자동 로드됩니다.")

if not IQVIA_SAVED.exists() and not UBIST_SAVED.exists():
    st.info("왼쪽 사이드바에서 IQVIA 또는 UBIST 파일을 업로드하세요.\n\n한 번 업로드하면 이후 자동으로 불러옵니다.")
    st.stop()

# 데이터 로드 (parquet 캐시에서 — 빠름)
iqvia_df = lc_qtr = lc_yr = None
ubist_df = ubist_num_cols = None

if IQVIA_SAVED.exists():
    # 캐시 없거나 원본보다 오래됐으면 재생성
    if not IQVIA_CACHE.exists() or IQVIA_CACHE.stat().st_mtime < IQVIA_SAVED.stat().st_mtime:
        with st.spinner("IQVIA 캐시 생성 중… (최초 1회)"):
            build_cache("iqvia")
    iqvia_df, lc_qtr, lc_yr = load_iqvia(IQVIA_CACHE.stat().st_mtime)
if UBIST_SAVED.exists():
    if not UBIST_CACHE.exists() or UBIST_CACHE.stat().st_mtime < UBIST_SAVED.stat().st_mtime:
        with st.spinner("UBIST 캐시 생성 중… (최초 1회)"):
            build_cache("ubist")
    ubist_df, ubist_num_cols = load_ubist(UBIST_CACHE.stat().st_mtime)

# ATC 선택 옵션 목록 (다중선택용)
@st.cache_data
def _atc_opts(cache_mtime, kind):
    if kind == "iqvia" and iqvia_df is not None and "ATC 4(한글)" in iqvia_df.columns:
        return sorted(iqvia_df["ATC 4(한글)"].dropna().astype(str).unique().tolist())
    if kind == "ubist" and ubist_df is not None and "ATC" in ubist_df.columns:
        return sorted(ubist_df["ATC"].dropna().astype(str).unique().tolist())
    return []

iqvia_atc_opts = _atc_opts(IQVIA_CACHE.stat().st_mtime if IQVIA_SAVED.exists() else 0, "iqvia") if iqvia_df is not None else []
ubist_atc_opts = _atc_opts(UBIST_CACHE.stat().st_mtime if UBIST_SAVED.exists() else 0, "ubist") if ubist_df is not None else []

# 제품명 선택 목록
@st.cache_data
def _prod_opts(cache_mtime, kind):
    if kind == "iqvia" and iqvia_df is not None and "제품명" in iqvia_df.columns:
        return sorted(iqvia_df["제품명"].dropna().astype(str).unique().tolist())
    if kind == "ubist" and ubist_df is not None and "제품명" in ubist_df.columns:
        return sorted(ubist_df["제품명"].dropna().astype(str).unique().tolist())
    return []

iqvia_prod_opts = _prod_opts(IQVIA_CACHE.stat().st_mtime if IQVIA_SAVED.exists() else 0, "iqvia") if iqvia_df is not None else []
ubist_prod_opts = _prod_opts(UBIST_CACHE.stat().st_mtime if UBIST_SAVED.exists() else 0, "ubist") if ubist_df is not None else []

# ── 탭 구성 ──────────────────────────────────────────────────────────────────

tabs = st.tabs(["🔍 제품 대시보드", "📊 시장 분석", "↔️ IQVIA vs UBIST 비교", "🚀 성장 제품 탐색"])

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1: 제품 대시보드
# ═══════════════════════════════════════════════════════════════════════════
with tabs[0]:
    st.subheader("🔍 제품 대시보드")

    # 자료원 선택
    sc, ac = st.columns([2, 1])
    with sc:
        dash_source = st.radio("자료원 선택", ["IQVIA", "UBIST"], horizontal=True, key="dash_source")
    with ac:
        agg_unit = st.selectbox("집계 단위", ["분기별", "연간"], key="prod_agg")

    # 검색 기준 → 각 소스별 컬럼 매핑
    FILTER_FIELDS = ["제품명", "성분명", "제조사", "용량", "ATC", "급여구분"]
    IQVIA_FIELD = {"제품명": "제품명", "성분명": "성분명", "제조사": "회사명",
                   "용량": "용량", "ATC": "ATC 4(한글)", "급여구분": "급여구분"}
    # UBIST는 ATC 분류 체계(대괄호 코드)가 IQVIA와 달라 ATC 필터는 IQVIA에만 적용
    UBIST_FIELD = {"제품명": "제품명", "성분명": "성분", "제조사": "제조사명",
                   "용량": "용량", "ATC": None, "급여구분": "급여구분"}

    # 자료원별 제품명/ATC 목록
    if dash_source == "IQVIA":
        _opts = {"제품명": iqvia_prod_opts, "ATC": iqvia_atc_opts}
    else:
        _opts = {"제품명": ubist_prod_opts}

    st.markdown("**🔎 검색 조건** (제품명·ATC는 목록에서 선택 · 여러 조건은 AND)")
    active_filters = render_filters(
        "filters", FILTER_FIELDS,
        placeholder="예: upadacitinib / 15mg / 한국애브비…",
        options_map=_opts)

    if not active_filters:
        st.info("검색어를 한 개 이상 입력하면 매출 트렌드와 경쟁 현황이 표시됩니다.")

    # 검색 조건 요약 표시
    def _fmt_val(v):
        return ", ".join(v) if isinstance(v, list) else str(v)
    summary = " · ".join([f"{f['field']}={_fmt_val(f['value'])}" for f in active_filters])
    st.caption(f"적용된 조건: {summary}")

    export_blocks = []  # 엑셀 추출용 수집
    report_iqvia = {}   # 보고서용 IQVIA 수집
    report_ubist = {}   # 보고서용 UBIST 수집

    def apply_filters(df, field_map):
        """AND 조건으로 마스크 적용. 적용 가능한 조건이 하나도 없으면 빈 결과."""
        return df[make_filter_mask(df, active_filters, field_map, require_any=False)]

    # IQVIA 제품 검색
    if dash_source == "IQVIA" and active_filters and iqvia_df is not None:
        prod_i = apply_filters(iqvia_df, IQVIA_FIELD)

        if prod_i.empty:
            st.warning(f"IQVIA에서 조건에 맞는 결과 없음")
        else:
            st.success(f"IQVIA: 조건에 맞는 **{len(prod_i)}개 팩** 검색됨")
            with st.expander("📄 검색된 제품 목록 보기", expanded=True):
                _sc = [c for c in ["제품명", "회사명", "성분명", "용량", "팩명", "ATC 4(한글)", "급여구분"] if c in prod_i.columns]
                st.dataframe(prod_i[_sc].drop_duplicates(), use_container_width=True, hide_index=True)
            report_iqvia["n"] = len(prod_i)
            if lc_yr:
                _yv = prod_i[lc_yr].sum().values / 1e6
                report_iqvia["yearly"] = [(lc_yr[i], _yv[i]) for i in range(len(lc_yr))]
                report_iqvia["cagr"] = calc_cagr(prod_i[lc_yr].sum().values, lc_yr)

            # 매출 집계
            if agg_unit == "분기별" and lc_qtr:
                sales_series = prod_i[lc_qtr].sum()
                x_labels = lc_qtr
                title_suffix = "분기별"
            else:
                sales_series = prod_i[lc_yr].sum() if lc_yr else pd.Series()
                x_labels = lc_yr
                title_suffix = "연간"

            if not sales_series.empty:
                # 백만원 단위 (원 / 1e6)
                vals_mm = sales_series.values / 1e6
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=x_labels,
                    y=vals_mm,
                    name="매출액",
                    marker_color="#4C72B0",
                    text=[f"{v:,.0f}" for v in vals_mm],
                    textposition="outside",
                ))
                fig.update_layout(
                    title=f"[IQVIA] {title_suffix} 매출액 ({summary})",
                    xaxis_title="기간",
                    yaxis_title="매출액 (백만원)",
                    height=400,
                    xaxis_tickangle=-45
                )
                st.plotly_chart(fig, use_container_width=True)

                # 매출액 표 (백만원)
                st.markdown("**📋 매출액 표 (단위: 백만원)**")
                sales_table = pd.DataFrame({
                    "기간": x_labels,
                    "매출액(백만원)": (sales_series.values / 1e6).round(0).astype("int64"),
                })
                st.dataframe(
                    sales_table.style.format({"매출액(백만원)": "{:,}"}),
                    use_container_width=True, hide_index=True
                )
                export_blocks.append({"name": "IQVIA_매출액", "df": sales_table,
                                      "chart": "bar", "cat": "기간", "val": "매출액(백만원)",
                                      "title": f"[IQVIA] {title_suffix} 매출액 (백만원)"})

                # CAGR (연간 기준, 2025년까지)
                yr_vals = prod_i[lc_yr].sum().values if lc_yr else []
                cagr = calc_cagr(yr_vals, lc_yr) if lc_yr else None
                if cagr is not None:
                    valid_yrs = [lbl for lbl, v in zip(lc_yr, yr_vals) if v and v > 0]
                    st.metric(
                        f"📈 매출액 CAGR ({valid_yrs[0][:-1]}→{valid_yrs[-1][:-1]})",
                        f"{cagr:+.1f}%",
                        help="첫 매출 발생 연도부터 마지막 연도(2025년)까지 연평균 성장률. 진행 중인 2026년은 제외."
                    )

            # ── 처방량 (DU) ────────────────────────────────────────────────
            du_qtr = [c for c in prod_i.columns if re.match(r"DU-\d{2}Q\d", c)]
            du_yr = [c for c in prod_i.columns if re.match(r"DU_\d{4}년", c)]
            if du_qtr or du_yr:
                du_cols = du_qtr if (agg_unit == "분기별" and du_qtr) else du_yr
                du_series = prod_i[du_cols].sum() if du_cols else pd.Series()
                if not du_series.empty and du_series.sum() > 0:
                    st.markdown("**💊 처방량 (DU)**")
                    du_labels = [c.replace("DU_", "").replace("DU-", "") for c in du_cols]
                    figd = go.Figure(go.Bar(
                        x=du_labels, y=du_series.values, marker_color="#8172B3",
                        text=[f"{v:,.0f}" for v in du_series.values], textposition="outside"))
                    figd.update_layout(title=f"[IQVIA] {title_suffix} 처방량(DU) ({summary})",
                                       xaxis_title="기간", yaxis_title="처방량(DU)",
                                       height=380, xaxis_tickangle=-45)
                    st.plotly_chart(figd, use_container_width=True)
                    du_table = pd.DataFrame({"기간": du_labels,
                                             "처방량(DU)": du_series.values.round(0).astype("int64")})
                    st.dataframe(du_table.style.format({"처방량(DU)": "{:,}"}),
                                 use_container_width=True, hide_index=True)
                    export_blocks.append({"name": "IQVIA_처방량DU", "df": du_table,
                                          "chart": "bar", "cat": "기간", "val": "처방량(DU)",
                                          "title": f"[IQVIA] {title_suffix} 처방량(DU)"})
                    # 처방량 CAGR
                    du_cagr = calc_cagr(prod_i[du_yr].sum().values,
                                        [c.replace("DU_", "") for c in du_yr]) if du_yr else None
                    if du_cagr is not None:
                        st.metric("📈 처방량 CAGR (~2025)", f"{du_cagr:+.1f}%")

            # ── 검색된 제품별 매출액 표 (붙여넣기용) ───────────────────────
            if lc_yr:
                st.markdown("**📋 제품별 연도별 매출액 (단위: 백만원, CAGR 2025년까지)**")
                grp_cols = [c for c in ["제품명", "회사명", "성분명", "용량"] if c in prod_i.columns]
                prod_sales = prod_i.groupby(grp_cols)[lc_yr].sum().reset_index()
                # CAGR 계산
                prod_sales["CAGR(%)"] = prod_sales[lc_yr].apply(
                    lambda r: calc_cagr(r.values, lc_yr), axis=1
                )
                # 매출 큰 순 정렬
                prod_sales = prod_sales.sort_values(lc_yr[-1], ascending=False)
                # 백만원 변환
                disp = prod_sales.copy()
                for c in lc_yr:
                    disp[c] = (disp[c] / 1e6).round(0).astype("int64")
                disp = disp.rename(columns={c: f"{c[:-1]}(백만원)" for c in lc_yr})
                fmt = {f"{c[:-1]}(백만원)": "{:,}" for c in lc_yr}
                fmt["CAGR(%)"] = "{:+.1f}"
                st.dataframe(disp.style.format(fmt, na_rep="-"),
                             use_container_width=True, hide_index=True)
                st.caption("표를 드래그 선택 후 Ctrl+C → 엑셀에 Ctrl+V로 붙여넣기 가능")
                export_blocks.append({"name": "IQVIA_제품별매출", "df": disp,
                                      "chart": None, "title": "[IQVIA] 제품별 연도별 매출액 (백만원)"})
                # 보고서용 상위 품목
                _last = f"{lc_yr[-1][:-1]}(백만원)"
                report_iqvia["top_prod"] = [(r["제품명"], r[_last]) for _, r in disp.head(5).iterrows()]

            st.divider()

            # ── 급여/비급여 비율 + ATC 비율 ──────────────────────────────
            # 비율 산정 기준 금액: 선택된 기간 합계 (분기별이면 전체 분기 합, 연간이면 전체 연도 합)
            base_cols = lc_qtr if (agg_unit == "분기별" and lc_qtr) else lc_yr
            prod_i = prod_i.copy()
            prod_i["_매출합"] = prod_i[base_cols].sum(axis=1) if base_cols else 0

            pie_c1, pie_c2 = st.columns(2)

            # 급여/비급여 비율
            with pie_c1:
                if "급여구분" in prod_i.columns:
                    reimb = prod_i.groupby("급여구분")["_매출합"].sum()
                    reimb = reimb[reimb > 0].sort_values(ascending=False)
                    if not reimb.empty:
                        df_reimb = reimb.reset_index()
                        df_reimb.columns = ["급여구분", "매출액"]
                        fig_r = px.pie(df_reimb, values="매출액", names="급여구분",
                                       title="급여 / 비급여 비율", hole=0.35)
                        fig_r.update_traces(textinfo="percent+label")
                        st.plotly_chart(fig_r, use_container_width=True)

                        df_reimb["매출액(백만원)"] = (df_reimb["매출액"] / 1e6).round(0).astype("int64")
                        total_r = df_reimb["매출액"].sum()
                        df_reimb["비율(%)"] = (df_reimb["매출액"] / total_r * 100).round(1)
                        reimb_tbl = df_reimb[["급여구분", "매출액(백만원)", "비율(%)"]]
                        st.dataframe(
                            reimb_tbl.style.format({"매출액(백만원)": "{:,}", "비율(%)": "{:.1f}"}),
                            use_container_width=True, hide_index=True
                        )
                        export_blocks.append({"name": "IQVIA_급여구분", "df": reimb_tbl,
                                              "chart": "pie", "cat": "급여구분", "val": "매출액(백만원)",
                                              "title": "[IQVIA] 급여/비급여 비율"})
                        report_iqvia["reimb"] = [(r["급여구분"], r["매출액(백만원)"], r["비율(%)"]) for _, r in df_reimb.iterrows()]

                # ETC/OTC 비율 (보고서용)
                if "ETC/OTC" in prod_i.columns:
                    eo = prod_i.groupby("ETC/OTC")["_매출합"].sum()
                    eo = eo[eo > 0]
                    if not eo.empty:
                        eo_t = eo.sum()
                        report_iqvia["etc_otc"] = [(k, v / eo_t * 100) for k, v in eo.sort_values(ascending=False).items()]

            # ATC 비율
            with pie_c2:
                atc_pie_col = "ATC 4(한글)" if "ATC 4(한글)" in prod_i.columns else None
                if atc_pie_col:
                    atc_grp = prod_i.groupby(atc_pie_col)["_매출합"].sum()
                    atc_grp = atc_grp[atc_grp > 0].sort_values(ascending=False)
                    if not atc_grp.empty:
                        df_atc = atc_grp.reset_index()
                        df_atc.columns = ["ATC", "매출액"]
                        fig_a = px.pie(df_atc, values="매출액", names="ATC",
                                       title="ATC(계열)별 비율", hole=0.35)
                        fig_a.update_traces(textinfo="percent+label")
                        st.plotly_chart(fig_a, use_container_width=True)

                        df_atc["매출액(백만원)"] = (df_atc["매출액"] / 1e6).round(0).astype("int64")
                        total_a = df_atc["매출액"].sum()
                        df_atc["비율(%)"] = (df_atc["매출액"] / total_a * 100).round(1)
                        atc_tbl = df_atc[["ATC", "매출액(백만원)", "비율(%)"]]
                        st.dataframe(
                            atc_tbl.style.format({"매출액(백만원)": "{:,}", "비율(%)": "{:.1f}"}),
                            use_container_width=True, hide_index=True
                        )
                        export_blocks.append({"name": "IQVIA_ATC비율", "df": atc_tbl,
                                              "chart": "pie", "cat": "ATC", "val": "매출액(백만원)",
                                              "title": "[IQVIA] ATC별 비율"})
                        report_iqvia["top_atc"] = [(r["ATC"], r["비율(%)"]) for _, r in df_atc.iterrows()]

            # 주력 제형 (보고서용)
            if "NFC 1 DESC" in prod_i.columns:
                fm = prod_i.groupby("NFC 1 DESC")["_매출합"].sum()
                fm = fm[fm > 0]
                if not fm.empty:
                    fm_t = fm.sum()
                    report_iqvia["top_form"] = [(k, v / fm_t * 100) for k, v in fm.sort_values(ascending=False).items()]

    # UBIST 제품 검색
    if dash_source == "UBIST" and active_filters and ubist_df is not None:
        prod_u = apply_filters(ubist_df, UBIST_FIELD)

        if not prod_u.empty:
            st.success(f"UBIST: 조건에 맞는 **{len(prod_u)}개** 검색됨")
            with st.expander("📄 검색된 제품 목록 보기", expanded=True):
                _uc = [c for c in ["제품명", "제조사명", "성분", "용량", "ATC", "급여구분"] if c in prod_u.columns]
                st.dataframe(prod_u[_uc].drop_duplicates(), use_container_width=True, hide_index=True)
            st.markdown("**[UBIST] 처방 데이터**")

            ugran = agg_unit  # 분기별 / 연간
            rx_cols = u_cols(prod_u, "처방조제액(원)", ugran)
            cnt_cols = u_cols(prod_u, "처방건수_P", ugran)
            vol_cols = u_cols(prod_u, "처방량_P", ugran)
            plabel = "분기" if ugran == "분기별" else "연도"

            rx_sum = prod_u[rx_cols].sum() if rx_cols else pd.Series()
            cnt_sum = prod_u[cnt_cols].sum() if cnt_cols else pd.Series()
            vol_sum = prod_u[vol_cols].sum() if vol_cols else pd.Series()

            c1, c2 = st.columns(2)
            with c1:
                if not rx_sum.empty:
                    labels = [u_period_label(c) for c in rx_cols]
                    rx_mm = rx_sum.values / 1e6
                    fig2 = go.Figure(go.Bar(
                        x=labels, y=rx_mm, marker_color="#DD8452", name="처방조제액",
                        text=[f"{v:,.0f}" for v in rx_mm], textposition="outside",
                    ))
                    fig2.update_layout(title=f"[UBIST] {ugran} 처방조제액",
                                       yaxis_title="처방조제액 (백만원)", height=350,
                                       xaxis_tickangle=-45)
                    st.plotly_chart(fig2, use_container_width=True)
            with c2:
                if not cnt_sum.empty:
                    labels = [u_period_label(c) for c in cnt_cols]
                    fig3 = go.Figure(go.Bar(
                        x=labels, y=cnt_sum.values, marker_color="#55A868", name="처방건수",
                        text=[f"{v:,.0f}" for v in cnt_sum.values], textposition="outside"))
                    fig3.update_layout(title=f"[UBIST] {ugran} 처방건수",
                                       yaxis_title="처방건수", height=350, xaxis_tickangle=-45)
                    st.plotly_chart(fig3, use_container_width=True)

            # 처방량 차트
            if not vol_sum.empty and vol_sum.sum() > 0:
                labels = [u_period_label(c) for c in vol_cols]
                figv = go.Figure(go.Bar(
                    x=labels, y=vol_sum.values, marker_color="#8172B3", name="처방량",
                    text=[f"{v:,.0f}" for v in vol_sum.values], textposition="outside"))
                figv.update_layout(title=f"[UBIST] {ugran} 처방량",
                                   yaxis_title="처방량", height=350, xaxis_tickangle=-45)
                st.plotly_chart(figv, use_container_width=True)

            # CAGR용 연간 컬럼 (항상 연간)
            rx_cols_25 = u_cols(prod_u, "처방조제액(원)", "연간")
            cnt_cols_25 = u_cols(prod_u, "처방건수_P", "연간")

            # 처방조제액 표
            if not rx_sum.empty:
                st.markdown(f"**📋 처방조제액 표 ({plabel}별, 단위: 백만원)**")
                rx_table = pd.DataFrame({
                    plabel: [u_period_label(c) for c in rx_cols],
                    "처방조제액(백만원)": (rx_sum.values / 1e6).round(0).astype("int64"),
                })
                st.dataframe(
                    rx_table.style.format({"처방조제액(백만원)": "{:,}"}),
                    use_container_width=True, hide_index=True
                )
                export_blocks.append({"name": "UBIST_처방조제액", "df": rx_table,
                                      "chart": "bar", "cat": plabel, "val": "처방조제액(백만원)",
                                      "title": f"[UBIST] {ugran} 처방조제액 (백만원)"})
                # CAGR
                cagr_rx = calc_cagr(prod_u[rx_cols_25].sum().values,
                                    [re.search(r"\d{4}", c).group(0) + "년" for c in rx_cols_25])
                cagr_cnt = calc_cagr(prod_u[cnt_cols_25].sum().values,
                                     [re.search(r"\d{4}", c).group(0) + "년" for c in cnt_cols_25]) if cnt_cols_25 else None
                report_ubist["n"] = len(prod_u)
                report_ubist["rx_yearly"] = list(zip(rx_table[plabel], rx_table["처방조제액(백만원)"]))
                report_ubist["cagr_rx"] = cagr_rx
                report_ubist["cagr_cnt"] = cagr_cnt
                m1, m2 = st.columns(2)
                with m1:
                    if cagr_rx is not None:
                        st.metric("📈 처방조제액 CAGR (~2025)", f"{cagr_rx:+.1f}%",
                                  help="첫 발생 연도~2025년 연평균 성장률. 진행 중인 2026년 제외.")
                with m2:
                    if cagr_cnt is not None:
                        st.metric("📈 처방건수 CAGR (~2025)", f"{cagr_cnt:+.1f}%",
                                  help="첫 발생 연도~2025년 연평균 성장률. 진행 중인 2026년 제외.")

            # 제품별 처방 표 (붙여넣기용) — 항상 연간 기준
            if rx_cols_25:
                st.markdown("**📋 제품별 연도별 처방조제액 (단위: 백만원, CAGR ~2025)**")
                ugrp_cols = [c for c in ["제품명", "제조사명", "성분", "ATC", "급여구분"] if c in prod_u.columns]
                pu = prod_u.groupby(ugrp_cols)[rx_cols_25].sum().reset_index()
                pu["CAGR(%)"] = pu[rx_cols_25].apply(
                    lambda r: calc_cagr(r.values, [re.search(r"\d{4}", c).group(0) + "년" for c in rx_cols_25]),
                    axis=1
                )
                pu = pu.sort_values(rx_cols_25[-1], ascending=False)
                dispu = pu.copy()
                for c in rx_cols_25:
                    yr = re.search(r"\d{4}", c).group(0)
                    dispu[c] = (dispu[c] / 1e6).round(0).astype("int64")
                    dispu = dispu.rename(columns={c: f"{yr}(백만원)"})
                fmtu = {f"{re.search(r'[0-9]{4}', c).group(0)}(백만원)": "{:,}" for c in rx_cols_25}
                fmtu["CAGR(%)"] = "{:+.1f}"
                st.dataframe(dispu.style.format(fmtu, na_rep="-"),
                             use_container_width=True, hide_index=True)
                st.caption("CAGR은 2026년(진행 중) 제외, 2025년까지 기준. 표 드래그 선택 후 Ctrl+C로 복사 가능")
                export_blocks.append({"name": "UBIST_제품별처방", "df": dispu,
                                      "chart": None, "title": "[UBIST] 제품별 연도별 처방조제액 (백만원)"})

            st.divider()

            # 비율 기준: 전체 연도 처방조제액 합계
            prod_u = prod_u.copy()
            prod_u["_조제합"] = prod_u[rx_cols].sum(axis=1) if rx_cols else 0

            u_pie1, u_pie2 = st.columns(2)

            # 급여/비급여 비율
            with u_pie1:
                if "급여구분" in prod_u.columns:
                    ureimb = prod_u.groupby("급여구분")["_조제합"].sum()
                    ureimb = ureimb[ureimb > 0].sort_values(ascending=False)
                    if not ureimb.empty:
                        dfu = ureimb.reset_index()
                        dfu.columns = ["급여구분", "처방조제액"]
                        fig_ur = px.pie(dfu, values="처방조제액", names="급여구분",
                                        title="[UBIST] 급여 / 비급여 비율", hole=0.35)
                        fig_ur.update_traces(textinfo="percent+label")
                        st.plotly_chart(fig_ur, use_container_width=True)
                        dfu["처방조제액(백만원)"] = (dfu["처방조제액"] / 1e6).round(0).astype("int64")
                        dfu["비율(%)"] = (dfu["처방조제액"] / dfu["처방조제액"].sum() * 100).round(1)
                        ureimb_tbl = dfu[["급여구분", "처방조제액(백만원)", "비율(%)"]]
                        st.dataframe(
                            ureimb_tbl.style.format({"처방조제액(백만원)": "{:,}", "비율(%)": "{:.1f}"}),
                            use_container_width=True, hide_index=True
                        )
                        export_blocks.append({"name": "UBIST_급여구분", "df": ureimb_tbl,
                                              "chart": "pie", "cat": "급여구분", "val": "처방조제액(백만원)",
                                              "title": "[UBIST] 급여/비급여 비율"})

            # ATC 비율
            with u_pie2:
                if "ATC" in prod_u.columns:
                    uatc = prod_u.groupby("ATC")["_조제합"].sum()
                    uatc = uatc[uatc > 0].sort_values(ascending=False)
                    if not uatc.empty:
                        dfa = uatc.reset_index()
                        dfa.columns = ["ATC", "처방조제액"]
                        fig_ua = px.pie(dfa, values="처방조제액", names="ATC",
                                        title="[UBIST] ATC(계열)별 비율", hole=0.35)
                        fig_ua.update_traces(textinfo="percent+label")
                        st.plotly_chart(fig_ua, use_container_width=True)
                        dfa["처방조제액(백만원)"] = (dfa["처방조제액"] / 1e6).round(0).astype("int64")
                        dfa["비율(%)"] = (dfa["처방조제액"] / dfa["처방조제액"].sum() * 100).round(1)
                        uatc_tbl = dfa[["ATC", "처방조제액(백만원)", "비율(%)"]]
                        st.dataframe(
                            uatc_tbl.style.format({"처방조제액(백만원)": "{:,}", "비율(%)": "{:.1f}"}),
                            use_container_width=True, hide_index=True
                        )
                        export_blocks.append({"name": "UBIST_ATC비율", "df": uatc_tbl,
                                              "chart": "pie", "cat": "ATC", "val": "처방조제액(백만원)",
                                              "title": "[UBIST] ATC별 비율"})

    # ── 엑셀 추출 버튼 (Tab 1 전체 표·차트) ─────────────────────────────────
    if export_blocks:
        st.divider()
        st.markdown("### 📥 분석 결과 엑셀 추출")
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", summary)[:40]
        xlsx_bytes = build_excel(export_blocks, meta_title=summary)
        st.download_button(
            "📊 표 + 그래프 한번에 엑셀로 다운로드",
            data=xlsx_bytes,
            file_name=f"의약품분석_{safe_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        st.caption(f"포함 시트: {len(export_blocks)}개 (각 시트에 표 + 그래프 자동 포함)")

    # ── 매출액 보고서 자동 생성 ─────────────────────────────────────────────
    if report_iqvia or report_ubist:
        st.divider()
        st.markdown("### 📝 매출액 보고서 (자동 생성)")
        report_text = build_report(summary, report_iqvia, report_ubist)
        st.text_area("보고서 미리보기", report_text, height=320)
        safe_rname = re.sub(r'[:*?"<>|]', "_", summary)[:40]
        rc1, rc2 = st.columns(2)
        with rc1:
            word_bytes = build_word(summary, export_blocks, report_text)
            st.download_button(
                "📝 워드(.docx) 보고서 — 그래프·표 포함",
                data=word_bytes,
                file_name=f"매출보고서_{safe_rname}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with rc2:
            st.download_button(
                "📄 텍스트(.txt) 보고서",
                data=report_text.encode("utf-8-sig"),
                file_name=f"매출보고서_{safe_rname}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        st.caption("검색된 데이터만으로 자동 작성됩니다 (외부 전송 없음). 워드 파일에는 그래프와 표가 함께 들어갑니다.")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 2: 시장 분석
# ═══════════════════════════════════════════════════════════════════════════
with tabs[1]:
    st.subheader("📊 시장 분석 (ATC 계열별 경쟁 구도)")

    mkt_source = st.radio("자료원 선택", ["IQVIA", "UBIST"], horizontal=True, key="mkt_source")

    # 자료원별 설정
    if mkt_source == "IQVIA":
        src_df = iqvia_df
        atc_levels = [c for c in ["ATC 4(한글)", "ATC 3(한글)", "ATC 2(한글)", "ATC 1(한글)"] if iqvia_df is not None and c in iqvia_df.columns]
        mfr_field, prod_grp = "회사명", ["제품명", "성분명"]
        year_cols = lc_yr
        period_cols = lc_yr if lc_yr else []
        metric_name = "매출액"
        mkt_fields, mkt_map = MKT_IQVIA_FIELDS, MKT_IQVIA_MAP
        atc_opts_for_filter = iqvia_atc_opts
    else:
        src_df = ubist_df
        atc_levels = ["ATC"] if (ubist_df is not None and "ATC" in ubist_df.columns) else []
        mfr_field, prod_grp = "제조사명", ["제품명", "성분"]
        year_cols = sorted([c for c in (ubist_num_cols or []) if "처방조제액" in c])
        period_cols = year_cols
        metric_name = "처방조제액"
        mkt_fields, mkt_map = MKT_UBIST_FIELDS, MKT_UBIST_MAP
        atc_opts_for_filter = ubist_atc_opts

    if src_df is None:
        st.info(f"{mkt_source} 파일을 업로드하세요.")
    elif not atc_levels:
        st.warning("ATC 정보가 없습니다.")
    else:
        col_atc, col_period, col_top = st.columns([3, 2, 1])
        with col_atc:
            if len(atc_levels) > 1:
                atc_col = st.selectbox("ATC 분류 단계", atc_levels)
            else:
                atc_col = atc_levels[0]
            atc_list = sorted(src_df[atc_col].dropna().astype(str).unique().tolist())
            selected_atc = st.selectbox("ATC 선택", atc_list)
        with col_period:
            def _period_label(c):
                m = re.search(r"(\d{4})", c)
                return (m.group(1) + "년") if m else c
            if period_cols:
                period_disp = st.selectbox("기준 연도", [_period_label(c) for c in period_cols][::-1])
                metric_col = next((c for c in period_cols if _period_label(c) == period_disp), period_cols[-1])
            else:
                metric_col = None
        with col_top:
            top_n = st.number_input("상위 N개", min_value=5, max_value=30, value=10)

        atc_df = src_df[src_df[atc_col].astype(str) == selected_atc].copy()

        # 추가 필터 (선택)
        with st.expander("🔎 추가 필터 (제품명 · 성분명 · 제조사 · ATC · 급여구분 등)"):
            mkt_active = render_filters("mkt_filters", mkt_fields,
                                        placeholder="예: 특정 성분 / 제조사…",
                                        options_map={"ATC": atc_opts_for_filter, "ETC/OTC": ETC_OTC_OPTS})
        if mkt_active:
            atc_df = atc_df[make_filter_mask(atc_df, mkt_active, mkt_map)]
            summ = " · ".join([f"{f['field']}={_fmt_val(f['value'])}" for f in mkt_active])
            st.caption(f"추가 조건: {summ}")

        unit_label = "백만원"
        if atc_df.empty:
            st.warning("해당 조건에 데이터 없음")
        elif metric_col:
            prod_grp = [c for c in prod_grp if c in atc_df.columns]
            mfr_sales = atc_df.groupby(mfr_field)[metric_col].sum().sort_values(ascending=False)
            prod_sales2 = atc_df.groupby(prod_grp)[metric_col].sum().sort_values(ascending=False)
            total = mfr_sales.sum()
            st.metric(f"시장 합계 ({metric_name})", f"{total/1e6:,.0f} {unit_label}",
                      help=f"자료원: {mkt_source} · 기준: {_period_label(metric_col)}")

            c1, c2 = st.columns(2)
            with c1:
                top_mfr = (mfr_sales.head(top_n) / 1e6).reset_index()
                top_mfr.columns = [mfr_field, "값"]
                fig_mfr = px.pie(top_mfr, values="값", names=mfr_field,
                                 title=f"제조사별 점유율 (상위 {top_n})", hole=0.35)
                fig_mfr.update_traces(textinfo="percent+label")
                st.plotly_chart(fig_mfr, use_container_width=True)
            with c2:
                top_prod = (prod_sales2.head(top_n) / 1e6).reset_index()
                top_prod = top_prod.rename(columns={top_prod.columns[-1]: "값"})
                seong = "성분명" if "성분명" in top_prod.columns else ("성분" if "성분" in top_prod.columns else None)
                if seong:
                    top_prod["제품(성분)"] = top_prod["제품명"] + " (" + top_prod[seong].fillna("").astype(str) + ")"
                    ylab = "제품(성분)"
                else:
                    ylab = "제품명"
                fig_prod = px.bar(top_prod, x="값", y=ylab, orientation="h",
                                  title=f"제품별 {metric_name} 상위 {top_n} ({unit_label})",
                                  color="값", color_continuous_scale="Blues",
                                  text=top_prod["값"].round(0))
                fig_prod.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
                fig_prod.update_layout(yaxis=dict(autorange="reversed"), height=420,
                                       xaxis_title=f"{metric_name} ({unit_label})")
                st.plotly_chart(fig_prod, use_container_width=True)

            # 제품별 표 (제품명 + 성분)
            prod_tbl = (prod_sales2.head(top_n) / 1e6).round(0).reset_index()
            prod_tbl = prod_tbl.rename(columns={prod_tbl.columns[-1]: f"{metric_name}({unit_label})"})
            prod_tbl["점유율(%)"] = (prod_sales2.head(top_n) / total * 100).round(1).values
            st.dataframe(prod_tbl.style.format({f"{metric_name}({unit_label})": "{:,.0f}", "점유율(%)": "{:.1f}"}),
                         use_container_width=True, hide_index=True)

            # 제조사 점유율 표
            share_tbl = (mfr_sales.head(top_n) / 1e6).round(0).reset_index()
            share_tbl.columns = [mfr_field, f"{metric_name}({unit_label})"]
            share_tbl["점유율(%)"] = (mfr_sales.head(top_n) / total * 100).round(1).values
            st.dataframe(share_tbl.style.format({f"{metric_name}({unit_label})": "{:,.0f}", "점유율(%)": "{:.1f}"}),
                         use_container_width=True, hide_index=True)

            # ── 성분별 집계 ──────────────────────────────────────────────
            seong_col = "성분명" if "성분명" in atc_df.columns else ("성분" if "성분" in atc_df.columns else None)
            if seong_col:
                st.divider()
                st.markdown(f"**🧪 성분별 {metric_name} (상위 {top_n}, {unit_label})**")
                ing_sales = atc_df.groupby(seong_col)[metric_col].sum().sort_values(ascending=False)
                ing_total = ing_sales.sum()
                ic1, ic2 = st.columns([1.3, 1])
                with ic1:
                    top_ing = (ing_sales.head(top_n) / 1e6).reset_index()
                    top_ing.columns = ["성분", "값"]
                    fig_ing = px.bar(top_ing, x="값", y="성분", orientation="h",
                                     title=f"성분별 {metric_name} 상위 {top_n}",
                                     color="값", color_continuous_scale="Teal",
                                     text=top_ing["값"].round(0))
                    fig_ing.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
                    fig_ing.update_layout(yaxis=dict(autorange="reversed"), height=420,
                                          xaxis_title=f"{metric_name} ({unit_label})")
                    st.plotly_chart(fig_ing, use_container_width=True)
                with ic2:
                    ing_tbl = (ing_sales.head(top_n) / 1e6).round(0).reset_index()
                    ing_tbl.columns = ["성분", f"{metric_name}({unit_label})"]
                    ing_tbl["점유율(%)"] = (ing_sales.head(top_n) / ing_total * 100).round(1).values
                    st.dataframe(ing_tbl.style.format({f"{metric_name}({unit_label})": "{:,.0f}", "점유율(%)": "{:.1f}"}),
                                 use_container_width=True, hide_index=True)

                # ── 성분별 제형 분석 (IQVIA 전용) ────────────────────────
                nfc_candidates = [c for c in ["NFC 1 DESC", "NFC 2 DESC", "NFC 3 DESC"] if c in atc_df.columns]
                if nfc_candidates:
                    st.markdown("**💊 성분별 제형(NFC) 분석 — 어떤 제형이 가장 많이 팔리나**")
                    f1, f2 = st.columns([2, 1])
                    with f1:
                        ing_choice = st.selectbox("성분 선택", ing_sales.head(30).index.tolist(), key="mkt_ing")
                    with f2:
                        nfc_level = st.selectbox("제형 단계", nfc_candidates, key="mkt_nfc")
                    ing_df = atc_df[atc_df[seong_col] == ing_choice]
                    form_sales = ing_df.groupby(nfc_level)[metric_col].sum().sort_values(ascending=False)
                    form_sales = form_sales[form_sales > 0]
                    if not form_sales.empty:
                        ftotal = form_sales.sum()
                        fc1, fc2 = st.columns(2)
                        with fc1:
                            df_form = (form_sales / 1e6).reset_index()
                            df_form.columns = ["제형", "값"]
                            fig_form = px.pie(df_form, values="값", names="제형",
                                              title=f"[{ing_choice}] 제형별 비율", hole=0.35)
                            fig_form.update_traces(textinfo="percent+label")
                            st.plotly_chart(fig_form, use_container_width=True)
                        with fc2:
                            ftbl = (form_sales / 1e6).round(0).reset_index()
                            ftbl.columns = ["제형", f"{metric_name}({unit_label})"]
                            ftbl["점유율(%)"] = (form_sales / ftotal * 100).round(1).values
                            st.dataframe(ftbl.style.format({f"{metric_name}({unit_label})": "{:,.0f}", "점유율(%)": "{:.1f}"}),
                                         use_container_width=True, hide_index=True)
                        st.caption(f"📌 '{ing_choice}' 성분에서 가장 많이 팔리는 제형: **{form_sales.index[0]}** ({form_sales.iloc[0]/ftotal*100:.1f}%)")
                elif mkt_source == "UBIST":
                    st.caption("ℹ️ 제형(NFC) 정보는 IQVIA 자료에만 있습니다. 제형 분석은 IQVIA를 선택하세요.")

            # 연도별 시장 트렌드
            if year_cols:
                st.divider()
                st.markdown(f"**시장 규모 트렌드 ({unit_label})**")
                trend = pd.DataFrame({"연도": [_period_label(c) for c in year_cols],
                                      f"{metric_name}({unit_label})": [atc_df[c].sum() / 1e6 for c in year_cols]})
                fig_trend = px.line(trend, x="연도", y=f"{metric_name}({unit_label})",
                                    markers=True, text=trend[f"{metric_name}({unit_label})"].round(0),
                                    title=f"{selected_atc} 연도별 시장 규모")
                fig_trend.update_traces(texttemplate="%{text:,.0f}", textposition="top center")
                st.plotly_chart(fig_trend, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════
# TAB 3: IQVIA vs UBIST 비교
# ═══════════════════════════════════════════════════════════════════════════
with tabs[2]:
    st.subheader("↔️ IQVIA vs UBIST 비교")

    if iqvia_df is None or ubist_df is None:
        st.info("IQVIA와 UBIST 파일을 모두 업로드하세요.")
    else:
        st.markdown("**🔎 비교할 제품 선택**")
        st.caption("① 제품(브랜드)을 IQVIA 제품명 기준으로 고르면, UBIST에서 이름이 달라도(예: '린버크'→'린버크 서방정') 함께 찾습니다. ② 용량을 고르면 양쪽 모두 같은 용량으로 맞춰 비교합니다.")

        p1, p2 = st.columns([2, 1])
        with p1:
            cmp_pick = st.selectbox("제품(브랜드) — IQVIA 제품명 기준", [""] + iqvia_prod_opts, key="cmp_pick")

        if not cmp_pick:
            st.info("비교할 제품을 선택하세요.")
        else:
            # 브랜드 매칭: IQVIA는 정확히, UBIST는 이름에 브랜드명이 포함된 품목
            i_brand = iqvia_df[iqvia_df["제품명"].astype(str) == cmp_pick]
            u_brand = ubist_df[ubist_df["제품명"].astype(str).str.contains(re.escape(cmp_pick), na=False)]

            # 용량 옵션 (양쪽 용량 컬럼 합집합)
            dose_set = set()
            if "용량" in i_brand.columns:
                dose_set |= set(i_brand["용량"].dropna().astype(str))
            if "용량" in u_brand.columns:
                dose_set |= set(u_brand["용량"].dropna().astype(str))
            dose_set = sorted(d for d in dose_set if d.strip())
            with p2:
                cmp_dose = st.multiselect("용량 (선택, 비우면 전체)", dose_set, key="cmp_dose")

            i_prod, u_prod = i_brand, u_brand
            if cmp_dose:
                if "용량" in i_prod.columns:
                    i_prod = i_prod[i_prod["용량"].astype(str).isin(cmp_dose)]
                if "용량" in u_prod.columns:
                    u_prod = u_prod[u_prod["용량"].astype(str).isin(cmp_dose)]
            cmp_summary = cmp_pick + (f" ({', '.join(cmp_dose)})" if cmp_dose else "")

            # 매칭된 실제 제품명 표시
            i_names = sorted(i_prod["제품명"].astype(str).unique().tolist())
            u_names = sorted(u_prod["제품명"].astype(str).unique().tolist())
            st.caption(f"🔵 IQVIA 매칭: {', '.join(i_names) if i_names else '없음'}  |  "
                       f"🟠 UBIST 매칭: {', '.join(u_names) if u_names else '없음'}")

            if i_prod.empty and u_prod.empty:
                st.warning("양쪽 데이터 모두 검색 결과 없음")
            else:
                common_years = ["2021", "2022", "2023", "2024", "2025"]

                rows, vrows = [], []
                for yr in common_years:
                    yr_col_i = f"{yr}년"
                    rx_col_u = f"처방조제액(원)_{yr}년"
                    du_col_i = f"DU_{yr}년"
                    vol_col_u = f"처방량_P_{yr}년"
                    i_val = i_prod[yr_col_i].sum() / 1e6 if (not i_prod.empty and yr_col_i in i_prod.columns) else None
                    u_val = u_prod[rx_col_u].sum() / 1e6 if (not u_prod.empty and rx_col_u in u_prod.columns) else None
                    rows.append({"연도": yr, "IQVIA 매출액(백만원)": i_val, "UBIST 처방조제액(백만원)": u_val})
                    iv = i_prod[du_col_i].sum() if (not i_prod.empty and du_col_i in i_prod.columns) else None
                    uv = u_prod[vol_col_u].sum() if (not u_prod.empty and vol_col_u in u_prod.columns) else None
                    vrows.append({"연도": yr, "IQVIA 처방량(DU)": iv, "UBIST 처방량": uv})

                df_cmp = pd.DataFrame(rows)
                df_vol = pd.DataFrame(vrows)

                # ── 매출액 vs 처방조제액 ──
                st.markdown("**💰 매출액(IQVIA) vs 처방조제액(UBIST) — 단위: 백만원**")
                fig = go.Figure()
                if df_cmp["IQVIA 매출액(백만원)"].notna().any():
                    yv = df_cmp["IQVIA 매출액(백만원)"]
                    fig.add_trace(go.Bar(name="IQVIA 매출액", x=df_cmp["연도"], y=yv,
                                         marker_color="#4C72B0",
                                         text=[f"{v:,.0f}" if pd.notna(v) else "" for v in yv],
                                         textposition="outside"))
                if df_cmp["UBIST 처방조제액(백만원)"].notna().any():
                    yv2 = df_cmp["UBIST 처방조제액(백만원)"]
                    fig.add_trace(go.Bar(name="UBIST 처방조제액", x=df_cmp["연도"], y=yv2,
                                         marker_color="#DD8452",
                                         text=[f"{v:,.0f}" if pd.notna(v) else "" for v in yv2],
                                         textposition="outside"))
                fig.update_layout(title=f"IQVIA vs UBIST 매출/처방조제액 ({cmp_summary})",
                                  barmode="group", yaxis_title="금액 (백만원)", height=400)
                st.plotly_chart(fig, use_container_width=True)
                df_cmp_disp = df_cmp.copy()
                for c in ["IQVIA 매출액(백만원)", "UBIST 처방조제액(백만원)"]:
                    df_cmp_disp[c] = df_cmp_disp[c].round(0)
                st.dataframe(df_cmp_disp.style.format(
                    {"IQVIA 매출액(백만원)": "{:,.0f}", "UBIST 처방조제액(백만원)": "{:,.0f}"}, na_rep="-"),
                    use_container_width=True, hide_index=True)

                # ── 처방량 비교 ──
                if df_vol["IQVIA 처방량(DU)"].notna().any() or df_vol["UBIST 처방량"].notna().any():
                    st.markdown("**💊 처방량 비교 — IQVIA(DU) vs UBIST(처방량)**")
                    figv = go.Figure()
                    if df_vol["IQVIA 처방량(DU)"].notna().any():
                        vv = df_vol["IQVIA 처방량(DU)"]
                        figv.add_trace(go.Bar(name="IQVIA 처방량(DU)", x=df_vol["연도"], y=vv,
                                              marker_color="#4C72B0",
                                              text=[f"{v:,.0f}" if pd.notna(v) else "" for v in vv],
                                              textposition="outside"))
                    if df_vol["UBIST 처방량"].notna().any():
                        vv2 = df_vol["UBIST 처방량"]
                        figv.add_trace(go.Bar(name="UBIST 처방량", x=df_vol["연도"], y=vv2,
                                              marker_color="#8172B3",
                                              text=[f"{v:,.0f}" if pd.notna(v) else "" for v in vv2],
                                              textposition="outside"))
                    figv.update_layout(title=f"IQVIA vs UBIST 처방량 ({cmp_summary})",
                                       barmode="group", yaxis_title="처방량", height=400)
                    st.plotly_chart(figv, use_container_width=True)
                    df_vol_disp = df_vol.copy()
                    for c in ["IQVIA 처방량(DU)", "UBIST 처방량"]:
                        df_vol_disp[c] = df_vol_disp[c].round(0)
                    st.dataframe(df_vol_disp.style.format(
                        {"IQVIA 처방량(DU)": "{:,.0f}", "UBIST 처방량": "{:,.0f}"}, na_rep="-"),
                        use_container_width=True, hide_index=True)
                    st.caption("※ IQVIA 처방량(DU)과 UBIST 처방량은 단위·산출 기준이 달라 절대값보다 추세 비교로 보세요.")

                # 엑셀 추출
                cmp_blocks = [{"name": "매출_처방조제액", "df": df_cmp_disp, "chart": "bar",
                               "cat": "연도", "val": "IQVIA 매출액(백만원)",
                               "title": f"매출/처방조제액 비교 ({cmp_summary})"}]
                if df_vol["IQVIA 처방량(DU)"].notna().any() or df_vol["UBIST 처방량"].notna().any():
                    cmp_blocks.append({"name": "처방량비교", "df": df_vol_disp, "chart": "bar",
                                       "cat": "연도", "val": "IQVIA 처방량(DU)",
                                       "title": f"처방량 비교 ({cmp_summary})"})
                xlsx_cmp = build_excel(cmp_blocks)
                safe_cmp = re.sub(r'[\\/:*?"<>|]', "_", cmp_summary)[:40]
                st.download_button("📊 비교 결과 엑셀 다운로드", data=xlsx_cmp,
                                   file_name=f"IQVIA_UBIST_비교_{safe_cmp}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   use_container_width=True)

                st.caption("IQVIA = 소매 판매 데이터(의약품 출하 기준), UBIST = 처방·조제 기준. 두 지표 간 갭은 재고 효과·채널 차이를 반영합니다.")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 4: 성장 제품 탐색
# ═══════════════════════════════════════════════════════════════════════════
with tabs[3]:
    st.subheader("🚀 성장 제품 탐색")

    source = st.radio("자료원 선택", ["IQVIA", "UBIST"], horizontal=True)

    if source == "IQVIA":
        if iqvia_df is None:
            st.info("IQVIA 파일을 업로드하세요.")
        elif len(lc_yr) < 2:
            st.warning("연간 데이터 2개 이상 필요")
        else:
            with st.expander("🔎 필터 (제품명 · 성분명 · 제조사 · 용량 · ATC · ETC/OTC · 급여구분)"):
                gi_active = render_filters("grow_iqvia_filters", MKT_IQVIA_FIELDS,
                                           options_map={"ATC": iqvia_atc_opts, "ETC/OTC": ETC_OTC_OPTS})
            src_i = iqvia_df[make_filter_mask(iqvia_df, gi_active, MKT_IQVIA_MAP)] if gi_active else iqvia_df

            c0, c1, c2, c3 = st.columns(4)
            with c0:
                gi_unit = st.selectbox("분석 단위", ["제품별", "성분별"], key="grow_i_unit")
            with c1:
                base_yr = st.selectbox("기준 연도", lc_yr[:-1], index=len(lc_yr)-2)
            with c2:
                comp_yr = st.selectbox("비교 연도", [y for y in lc_yr if y > base_yr], index=0)
            with c3:
                min_base = st.number_input("최소 기준연도 매출 (백만원)", value=100.0, step=50.0)

            if gi_unit == "성분별":
                gcols = [c for c in ["성분명", "ATC 4(한글)"] if c in src_i.columns]
                ykey = "성분명"
            else:
                gcols = [c for c in ["제품명", "회사명", "성분명", "ATC 4(한글)"] if c in src_i.columns]
                ykey = "제품명"
            hov = [c for c in gcols if c != ykey]

            grp = src_i.groupby(gcols)[[base_yr, comp_yr]].sum().reset_index()
            grp = grp[grp[base_yr] >= min_base * 1e6]
            grp["YoY 성장률(%)"] = (grp[comp_yr] - grp[base_yr]) / grp[base_yr].replace(0, float("nan")) * 100
            grp[base_yr] = (grp[base_yr] / 1e6).round(0)
            grp[comp_yr] = (grp[comp_yr] / 1e6).round(0)
            grp["YoY 성장률(%)"] = grp["YoY 성장률(%)"].round(1)
            grp = grp.sort_values("YoY 성장률(%)", ascending=False)
            grp = grp.rename(columns={base_yr: f"{base_yr[:-1]}(백만원)", comp_yr: f"{comp_yr[:-1]}(백만원)"})
            bcol_i, ccol_i = f"{base_yr[:-1]}(백만원)", f"{comp_yr[:-1]}(백만원)"

            top_tab, bot_tab = st.tabs([f"📈 고성장 {gi_unit} Top 20", f"📉 하락 {gi_unit} Bottom 20"])
            with top_tab:
                top20 = grp.head(20)
                fig_top = px.bar(top20, x="YoY 성장률(%)", y=ykey, orientation="h",
                                 color="YoY 성장률(%)", color_continuous_scale="Greens",
                                 hover_data=hov + [bcol_i, ccol_i],
                                 text=top20["YoY 성장률(%)"],
                                 title=f"고성장 {gi_unit} Top 20 ({base_yr[:-1]}→{comp_yr[:-1]})")
                fig_top.update_traces(texttemplate="%{text:+.1f}%", textposition="outside")
                fig_top.update_layout(yaxis=dict(autorange="reversed"), height=550)
                st.plotly_chart(fig_top, use_container_width=True)
                st.dataframe(top20, use_container_width=True, hide_index=True)

            with bot_tab:
                bot20 = grp.tail(20).sort_values("YoY 성장률(%)")
                fig_bot = px.bar(bot20, x="YoY 성장률(%)", y=ykey, orientation="h",
                                 color="YoY 성장률(%)", color_continuous_scale="Reds_r",
                                 hover_data=hov + [bcol_i, ccol_i],
                                 text=bot20["YoY 성장률(%)"],
                                 title=f"하락 {gi_unit} Bottom 20 ({base_yr[:-1]}→{comp_yr[:-1]})")
                fig_bot.update_traces(texttemplate="%{text:+.1f}%", textposition="outside")
                fig_bot.update_layout(yaxis=dict(autorange="reversed"), height=550)
                st.plotly_chart(fig_bot, use_container_width=True)
                st.dataframe(bot20, use_container_width=True, hide_index=True)

    else:  # UBIST
        if ubist_df is None:
            st.info("UBIST 파일을 업로드하세요.")
        else:
            rx_yr_cols = sorted([c for c in ubist_num_cols if "처방조제액" in c])
            if len(rx_yr_cols) < 2:
                st.warning("연간 데이터 2개 이상 필요")
            else:
                yr_labels = [re.search(r"(\d{4})", c).group(1) for c in rx_yr_cols]
                with st.expander("🔎 필터 (제품명 · 성분명 · 제조사 · ATC · 급여구분)"):
                    gu_active = render_filters("grow_ubist_filters", MKT_UBIST_FIELDS,
                                               options_map={"ATC": ubist_atc_opts})
                src_u = ubist_df[make_filter_mask(ubist_df, gu_active, MKT_UBIST_MAP)] if gu_active else ubist_df

                c0, c1, c2 = st.columns(3)
                with c0:
                    gu_unit = st.selectbox("분석 단위", ["제품별", "성분별"], key="grow_u_unit")
                with c1:
                    base_idx = st.selectbox("기준 연도", yr_labels[:-1], index=len(yr_labels)-2)
                with c2:
                    comp_choices = [y for y in yr_labels if y > base_idx]
                    comp_idx = st.selectbox("비교 연도", comp_choices)

                base_col = f"처방조제액(원)_{base_idx}년"
                comp_col = f"처방조제액(원)_{comp_idx}년"
                min_base_u = st.number_input("최소 기준연도 처방조제액 (백만원)", value=50.0, step=10.0)

                if gu_unit == "성분별":
                    ugrow_cols = [c for c in ["성분", "ATC"] if c in src_u.columns]
                    uykey = "성분"
                else:
                    ugrow_cols = [c for c in ["제품명", "제조사명", "성분", "ATC"] if c in src_u.columns]
                    uykey = "제품명"
                uhov = [c for c in ugrow_cols if c != uykey]
                grp_u = src_u.groupby(ugrow_cols)[[base_col, comp_col]].sum().reset_index()
                grp_u = grp_u[grp_u[base_col] >= min_base_u * 1e6]
                grp_u["YoY 성장률(%)"] = (grp_u[comp_col] - grp_u[base_col]) / grp_u[base_col].replace(0, float("nan")) * 100
                grp_u[base_col] = (grp_u[base_col] / 1e6).round(0)
                grp_u[comp_col] = (grp_u[comp_col] / 1e6).round(0)
                grp_u["YoY 성장률(%)"] = grp_u["YoY 성장률(%)"].round(1)
                grp_u = grp_u.sort_values("YoY 성장률(%)", ascending=False)
                grp_u = grp_u.rename(columns={base_col: f"{base_idx}(백만원)", comp_col: f"{comp_idx}(백만원)"})
                bcol_u, ccol_u = f"{base_idx}(백만원)", f"{comp_idx}(백만원)"

                top_tab_u, bot_tab_u = st.tabs([f"📈 고성장 {gu_unit} Top 20", f"📉 하락 {gu_unit} Bottom 20"])
                with top_tab_u:
                    top20_u = grp_u.head(20)
                    fig_u = px.bar(top20_u, x="YoY 성장률(%)", y=uykey, orientation="h",
                                   color="YoY 성장률(%)", color_continuous_scale="Greens",
                                   hover_data=[c for c in uhov + [bcol_u, ccol_u] if c in top20_u.columns],
                                   text=top20_u["YoY 성장률(%)"],
                                   title=f"고성장 {gu_unit} Top 20 ({base_idx}→{comp_idx})")
                    fig_u.update_traces(texttemplate="%{text:+.1f}%", textposition="outside")
                    fig_u.update_layout(yaxis=dict(autorange="reversed"), height=550)
                    st.plotly_chart(fig_u, use_container_width=True)
                    st.dataframe(top20_u, use_container_width=True, hide_index=True)
                with bot_tab_u:
                    bot20_u = grp_u.tail(20).sort_values("YoY 성장률(%)")
                    fig_ub = px.bar(bot20_u, x="YoY 성장률(%)", y=uykey, orientation="h",
                                    color="YoY 성장률(%)", color_continuous_scale="Reds_r",
                                    hover_data=[c for c in uhov + [bcol_u, ccol_u] if c in bot20_u.columns],
                                    text=bot20_u["YoY 성장률(%)"],
                                    title=f"하락 {gu_unit} Bottom 20 ({base_idx}→{comp_idx})")
                    fig_ub.update_traces(texttemplate="%{text:+.1f}%", textposition="outside")
                    fig_ub.update_layout(yaxis=dict(autorange="reversed"), height=550)
                    st.plotly_chart(fig_ub, use_container_width=True)
                    st.dataframe(bot20_u, use_container_width=True, hide_index=True)
