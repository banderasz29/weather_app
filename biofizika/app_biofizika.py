from __future__ import annotations
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import csv, io, json, os, re
from datetime import datetime
import streamlit as st

# ─────────────────────────────────────────────────────────
# PDF (ReportLab)
from io import BytesIO
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image as RLImage,
    PageBreak,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.lib.units import cm
from reportlab.lib import utils
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ─────────────────────────────────────────────────────────
# Alapmappák + robusztus útvonalkeresés (CSV-k a biofizika/app mappában)
APP_DIR: Path = Path(__file__).resolve().parent  # a biofizika/ app mappa


def _resolve_file(filename: str, env_var: Optional[str] = None) -> Path:
    """
    Robusztus fájlkeresés:
    1) környezeti változó (ha megadva)
    2) app mappa (APP_DIR)
    3) current working dir (Path.cwd())
    4) app szülője (APP_DIR.parent)
    5) app 'data/' almappája (APP_DIR / 'data')
    6) CWD 'biofizika/' almappája (Path.cwd() / 'biofizika')
    Első találatot adja vissza; ha semmi nincs, APP_DIR/filename-re esik vissza.
    """
    tried: list[Path] = []

    # 1) környezeti változó
    if env_var:
        p_env = os.getenv(env_var)
        if p_env:
            p = Path(p_env).expanduser().resolve()
            tried.append(p)
            if p.exists():
                return p

    # 2–6) kandidátok sorban
    candidates = [
        APP_DIR / filename,
        Path.cwd() / filename,
        APP_DIR.parent / filename,
        APP_DIR / "data" / filename,
        Path.cwd() / "biofizika" / filename,
    ]

    for p in candidates:
        tried.append(p)
        if p.exists():
            return p

    # nincs találat → barátságos üzenet + fallback az APP_DIR-re
    st.warning(
        "Nem találom a fájlt: "
        f"'{filename}'. Próbált útvonalak:\n" + "\n".join(str(x) for x in tried)
    )
    return APP_DIR / filename


def _resolve_dir(dirname: str) -> Path:
    """
    Robusztus könyvtár-keresés képekhez:
    1) APP_DIR/dirname
    2) Path.cwd()/dirname
    3) APP_DIR.parent/dirname
    Ha egyik sem létezik, az APP_DIR/dirname-et adja vissza (létrehozás nélkül).
    """
    candidates = [
        APP_DIR / dirname,
        Path.cwd() / dirname,
        APP_DIR.parent / dirname,
    ]
    for p in candidates:
        if p.exists() and p.is_dir():
            return p
    return APP_DIR / dirname


# CSV-k: maradnak a biofizika/app mappában
FILE_SUBJECTS: Path = _resolve_file("subject.csv", env_var="SUBJECT_CSV_PATH")
FILE_ELM: Path = _resolve_file(
    "elmeleti_kerdes_valaszok.csv", env_var="QUESTIONS_CSV_PATH"
)
# Képek könyvtára (válasz-illusztrációk)
PIC_A_DIR: Path = _resolve_dir("pic_answers")

PAGE_TITLE = "Biofizika – önértékelő teszt"
PAGE_ICON = "⚛️"
st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")

# Opcionális: útvonal debug kijelzés (oldalsávban kapcsolható)
_show_paths = st.sidebar.checkbox("🔎 CSV/útvonal debug", value=False)
if _show_paths:
    from textwrap import indent

    info = (
        f"APP_DIR: {APP_DIR}\n"
        f"CWD:     {Path.cwd()}\n"
        f"subject.csv: {FILE_SUBJECTS}\n"
        f"elmeleti_kerdes_valaszok.csv: {FILE_ELM}\n"
        f"pic_answers dir: {PIC_A_DIR}\n"
    )
    st.info("Útvonalak:\n" + indent(info, "  "))


# ─────────────────────────────────────────────────────────
# Segédfüggvények – CSV
def detect_dialect(path: Path) -> Optional[csv.Dialect]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            sample = f.read(4096)
            return csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
    except Exception:
        return None


def first_existing(d: Dict[str, str], *cands: str) -> Optional[str]:
    lm = {k.strip().lower(): k for k in d.keys()}
    for c in cands:
        if c in lm:
            return lm[c]
    return None


def extract_qid(question: str) -> Optional[str]:
    """x.xx formátumú sorszám kinyerése a kérdésből"""
    if not question:
        return None
    m = re.search(r"\b(\d+\.\d+)\b", question)
    if m:
        return m.group(1)
    return None


def split_answers(cell: Optional[str]) -> List[str]:
    if not cell:
        return []
    s = cell.strip()
    if not s:
        return []
    # több sor → soronként válasz
    if "\n" in s:
        return [x.strip() for x in s.split("\n") if x.strip()]
    # pontosvesszővel elválasztott
    if ";" in s:
        return [x.strip() for x in s.split(";") if x.strip()]
    return [s]


# ─────────────────────────────────────────────────────────
# subject.csv betöltése
def load_subjects(path: Path) -> List[str]:
    if not path.exists():
        st.error(f"Hiányzik: {path}")
        st.stop()
    dialect = detect_dialect(path)
    with open(path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, dialect=dialect) if dialect else csv.reader(f))
    subjects = []
    for row in rows:
        if row and row[0].strip():
            subjects.append(row[0].strip())
    return subjects


# ─────────────────────────────────────────────────────────
# kérdések betöltése az elméleti CSV-ből
def load_questions(
    path: Path, theme_number: str
) -> Tuple[List[str], Dict[str, List[str]], Dict[str, str]]:
    """
    Visszaad:
    questions : lista a megjelenítendő kérdésekből (szűrve témára)
    answers_map : { kérdés: [válaszok] }
    qid_map : { kérdés: qid ('x.xx') }
    """
    dialect = detect_dialect(path)
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, dialect=dialect) if dialect else csv.DictReader(f)
        rows = list(reader)
    if not rows:
        return [], {}, {}
    q_col = first_existing(rows[0], "question", "kérdés", "kerdes", "q")
    a_col = first_existing(rows[0], "answer", "válasz", "valasz", "a")
    if not q_col or not a_col:
        st.error(
            "Az elmeleti_kerdes_valaszok.csv-ben hiányzik a 'question' és/vagy 'answer' oszlop."
        )
        st.stop()
    qa_map: Dict[str, List[str]] = {}
    qid_map: Dict[str, str] = {}
    question_list: List[str] = []
    for r in rows:
        q = (r.get(q_col, "") or "").strip()
        a_raw = r.get(a_col, "") or ""
        if not q:
            continue
        qid = extract_qid(q)
        if not qid:
            continue
        # Téma szerinti szűrés → x.xx -> első számjegy(ek)
        if not qid.startswith(theme_number + "."):
            continue
        qa_map[q] = split_answers(a_raw)
        qid_map[q] = qid
        question_list.append(q)
    return question_list, qa_map, qid_map


# ─────────────────────────────────────────────────────────
# képek betöltése a válaszokhoz
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def find_answer_images(qid: str) -> List[Path]:
    """Visszaadja az összes képet, amely a qid-hez tartozik:
    - pontos egyezés: qid.png/jpg/jpeg/webp/gif
    - több kép: qid_*.png/jpg/jpeg/webp/gif
    """
    out: List[Path] = []
    # pontos egyezés
    for ext in IMG_EXTS:
        p = PIC_A_DIR / f"{qid}{ext}"
        if p.exists():
            out.append(p)
    # több kép: qid_*.ext
    for ext in IMG_EXTS:
        for p in PIC_A_DIR.glob(f"{qid}_*{ext}"):
            out.append(p)
    return sorted(out, key=lambda x: x.name.lower())


# ─────────────────────────────────────────────────────────
# PDF segédek + magyar font regisztrálása
def register_hungarian_font() -> Tuple[str, str, bool]:
    """
    Visszaad: (regular_font_name, bold_font_name, is_unicode_ready)
    Ha megtalálja a DejaVu Sans TTF-eket az app mappában, azokat regisztrálja.
    Különben Helvetica-ra esik vissza (ami nem biztos, hogy tartalmazza az ű/ő/í karaktereket PDF-ben).
    """
    regular = APP_DIR / "DejaVuSans.ttf"
    bold = APP_DIR / "DejaVuSans-Bold.ttf"
    try:
        if regular.exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans", str(regular)))
            if bold.exists():
                pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(bold)))
                return "DejaVuSans", "DejaVuSans-Bold", True
            else:
                return "DejaVuSans", "DejaVuSans", True
        else:
            return "Helvetica", "Helvetica-Bold", False
    except Exception:
        return "Helvetica", "Helvetica-Bold", False


def _rl_img_scaled(path: Path, max_width: float) -> Optional[RLImage]:
    """Kép beolvasása és méretezése a megadott max szélességre (arányt tartva)."""
    try:
        img_reader = utils.ImageReader(str(path))
        iw, ih = img_reader.getSize()
        if iw == 0 or ih == 0:
            return None
        scale = min(1.0, max_width / float(iw))
        w = float(iw) * scale
        h = float(ih) * scale
        return RLImage(str(path), width=w, height=h)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────
# KLASSZIKUS FLOW + felső magassági korlát
# - képek teljes hasznos szélességre skálázva (_rl_img_scaled)
# - nincs KeepInFrame, nincs automatikus oldaltörés
# - HA túl magas lenne a kép: lekorlátozzuk egy felső határra (hasznos oldal-magasság X%-a)
def build_pdf(
    theme_label: str,
    theme_number: str,
    questions: List[str],
    qa_map: Dict[str, List[str]],
    qid_map: Dict[str, str],
    font_reg: str,
    font_bold: str,
) -> bytes:
    """PDF építése: 1 témához minden kérdés + válaszok + képek, A4, egységes tipó.
    Kérések szerint: nincs kérdésfejléc és nincs 'Elfogadható válasz(ok):', a megoldás sorkizárt.
    """
    buf = BytesIO()
    # Dokumentum
    MARG = 2 * cm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARG,
        rightMargin=MARG,
        topMargin=MARG,
        bottomMargin=MARG,
    )
    width, height = A4
    usable_w = width - 2 * MARG
    usable_h = height - 2 * MARG
    # felső magassági korlát: a hasznos oldal magasságának 70%-a
    MAX_IMG_H_FRAC = 0.70
    max_img_h = usable_h * MAX_IMG_H_FRAC

    # Stílusok
    styles = getSampleStyleSheet()
    style_title = styles["Title"]
    style_title.fontName = font_bold
    style_title.fontSize = 18
    style_title.spaceAfter = 12

    # KÉRDÉS: félkövér (bold), nagyobb méret, jobb elkülönítés
    style_q = ParagraphStyle(
        name="Question",
        parent=styles["BodyText"],
        fontName=font_bold,  # ← félkövér kérdés
        fontSize=12,
        leading=15,
        alignment=TA_LEFT,  # ha a kérdést is sorkizártra szeretnéd: TA_JUSTIFY
        spaceAfter=6,
    )

    style_ans = ParagraphStyle(
        name="Answer",
        parent=styles["BodyText"],
        fontName=font_reg,
        fontSize=11,
        leading=14,
        leftIndent=10,
        spaceAfter=2,
        alignment=TA_JUSTIFY,  # sorkizárt megoldás
    )

    style_meta = ParagraphStyle(
        name="Meta",
        parent=styles["BodyText"],
        fontName=font_reg,
        fontSize=9,
        textColor="#666",
        spaceAfter=6,
    )

    story: List = []
    # Fejléc (cím + meta)
    now = datetime.now().strftime("%Y.%m.%d %H:%M")
    story.append(Paragraph(f"{PAGE_TITLE}", style_title))
    story.append(
        Paragraph(f"Téma: <b>{theme_label}</b> (szám: {theme_number})", style_meta)
    )
    story.append(Paragraph(f"Generálva: {now}", style_meta))
    story.append(Spacer(1, 6))

    # Kérdések – fejlécek és "Elfogadható válasz(ok)" nélkül
    for idx, q in enumerate(questions, start=1):
        qid = qid_map.get(q, "")
        # Kérdés szövege (FÉLKÖVÉR)
        story.append(Paragraph(q.replace("\n", "<br/>"), style_q))
        # Válaszok – sorkizárt bekezdések
        ans_list = qa_map.get(q, [])
        if ans_list:
            for a in ans_list:
                safe_a = a.replace("\n", "<br/>")
                story.append(Paragraph(f"{safe_a}", style_ans))
        else:
            story.append(Paragraph("(Nincs válasz rögzítve)", style_ans))
        # Képek a válaszhoz — KLASSZIKUS FLOW (teljes szélesség + felső magassági korlát)
        imgs = find_answer_images(qid) if qid else []
        if imgs:
            story.append(Spacer(1, 4))
            for p in imgs:
                rlimg = _rl_img_scaled(p, usable_w)
                if rlimg:
                    # felső magassági korlát alkalmazása
                    if rlimg.drawHeight > max_img_h:
                        scale = max_img_h / float(rlimg.drawHeight)
                        rlimg.drawWidth *= scale
                        rlimg.drawHeight *= scale
                    story.append(rlimg)
            story.append(Spacer(1, 6))
        # blokk-záró térköz
        story.append(Spacer(1, 10))

    doc.build(story)
    return buf.getvalue()


def build_pdf_all_themes(subjects: List[str], font_reg: str, font_bold: str) -> bytes:
    """
    Összes téma PDF: subject.csv soronként (1., 2., 3., ...),
    mindegyik témához beolvassuk a kérdéseket és egy nagy, egységes PDF-et készítünk.
    Nincs kérdésfejléc és nincs 'Elfogadható válasz(ok):', a megoldás sorkizárt.
    """
    buf = BytesIO()
    MARG = 2 * cm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARG,
        rightMargin=MARG,
        topMargin=MARG,
        bottomMargin=MARG,
    )
    width, height = A4
    usable_w = width - 2 * MARG
    usable_h = height - 2 * MARG
    # felső magassági korlát: a hasznos oldal magasságának 50%-a
    MAX_IMG_H_FRAC = 0.50
    max_img_h = usable_h * MAX_IMG_H_FRAC

    styles = getSampleStyleSheet()
    style_title = styles["Title"]
    style_title.fontName = font_bold
    style_title.fontSize = 18
    style_title.spaceAfter = 12

    style_h1 = styles["Heading1"]
    style_h1.fontName = font_bold
    style_h1.fontSize = 16
    style_h1.spaceBefore = 14
    style_h1.spaceAfter = 8

    # KÉRDÉS: félkövér
    style_q = ParagraphStyle(
        name="Question",
        parent=styles["BodyText"],
        fontName=font_bold,  # ← félkövér kérdés
        fontSize=12,
        leading=15,
        alignment=TA_LEFT,  # igény szerint TA_JUSTIFY
        spaceAfter=6,
    )

    style_ans = ParagraphStyle(
        name="Answer",
        parent=styles["BodyText"],
        fontName=font_reg,
        fontSize=11,
        leading=14,
        leftIndent=10,
        spaceAfter=2,
        alignment=TA_JUSTIFY,
    )

    style_meta = ParagraphStyle(
        name="Meta",
        parent=styles["BodyText"],
        fontName=font_reg,
        fontSize=9,
        textColor="#666",
        spaceAfter=6,
    )

    story: List = []
    now = datetime.now().strftime("%Y.%m.%d %H:%M")
    # Címlap/fejléc
    story.append(Paragraph(f"{PAGE_TITLE}", style_title))
    story.append(Paragraph(f"Összes téma kinyomtatva", style_meta))
    story.append(Paragraph(f"Generálva: {now}", style_meta))
    story.append(Spacer(1, 8))

    # Témák végigjárása
    for si, subj in enumerate(subjects, start=1):
        m = re.match(r"^\s*(\d+)", subj)
        if not m:
            # ha nem nyerhető ki szám, kihagyjuk
            continue
        theme_number = m.group(1)
        # Téma-fejléc (meghagyjuk, mert ezt nem kérted eltávolítani)
        story.append(Paragraph(f"{subj}", style_h1))
        story.append(Spacer(1, 4))

        # Téma kérdései
        questions, qa_map, qid_map = load_questions(FILE_ELM, theme_number)
        if not questions:
            story.append(Paragraph("(Ehhez a témához nem található kérdés.)", style_q))
            if si < len(subjects):
                story.append(PageBreak())
            continue

        for q in questions:
            qid = qid_map.get(q, "")
            # Kérdés (FÉLKÖVÉR)
            story.append(Paragraph(q.replace("\n", "<br/>"), style_q))
            # Válaszok – sorkizárt bekezdések
            ans_list = qa_map.get(q, [])
            if ans_list:
                for a in ans_list:
                    safe_a = a.replace("\n", "<br/>")
                    story.append(Paragraph(f"{safe_a}", style_ans))
            else:
                story.append(Paragraph("(Nincs válasz rögzítve)", style_ans))
            # Képek — KLASSZIKUS FLOW + felső magassági korlát
            imgs = find_answer_images(qid) if qid else []
            if imgs:
                story.append(Spacer(1, 4))
                for p in imgs:
                    rlimg = _rl_img_scaled(p, usable_w)
                    if rlimg:
                        if rlimg.drawHeight > max_img_h:
                            scale = max_img_h / float(rlimg.drawHeight)
                            rlimg.drawWidth *= scale
                            rlimg.drawHeight *= scale
                        story.append(rlimg)
                story.append(Spacer(1, 6))

        # témák között oldaltörés (utolsó után nem kötelező)
        if si < len(subjects):
            story.append(PageBreak())

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────
# Oldalsáv UI
st.sidebar.header("Beállítások")
subjects = load_subjects(FILE_SUBJECTS)
tema_full = st.sidebar.selectbox("Téma", options=subjects)

# subject.csv sorai pl.: "2. Váltóáram"
m = re.match(r"^\s*(\d+)", tema_full)
if not m:
    st.error("A subject.csv sorai nem tartalmazzák a témaszámot a sor elején!")
    st.stop()
tema_szam = m.group(1)

# session_state kulcsok biztosítása
if "theme" not in st.session_state:
    st.session_state["theme"] = None
if "show_answer" not in st.session_state:
    st.session_state.show_answer = {}
if "mark" not in st.session_state:
    st.session_state.mark = {}
if "summary" not in st.session_state:
    st.session_state.summary = None

if st.sidebar.button("📥 Betöltés / frissítés"):
    st.session_state["theme"] = tema_szam
    # új betöltéskor tisztítjuk a válasz/értékelés állapotát
    st.session_state.show_answer = {}
    st.session_state.mark = {}
    st.session_state.summary = None

if not st.session_state["theme"]:
    st.info("Válassz témát, majd kattints a Betöltés gombra.")
    st.stop()

tema_szam = st.session_state["theme"]

# ─────────────────────────────────────────────────────────
# kérdések betöltése
questions, qa_map, qid_map = load_questions(FILE_ELM, tema_szam)
if not questions:
    st.warning("Ehhez a témához nem található kérdés.")
    st.stop()

# UI állapot init
for q in questions:
    st.session_state.show_answer.setdefault(q, False)
    st.session_state.mark.setdefault(q, None)

# ─────────────────────────────────────────────────────────
# Fejléc
st.title(f"{PAGE_ICON} {PAGE_TITLE}")
st.caption(f"Téma: **{tema_full}** • Kérdések száma: **{len(questions)}**")
st.divider()

# ─────────────────────────────────────────────────────────
# Kérdések listája (app UI – változatlanul hagyva)
for idx, q in enumerate(questions, start=1):
    bg = (
        "#eaffea"
        if st.session_state.mark.get(q) == "helyes"
        else "#ffecec" if st.session_state.mark.get(q) == "hibas" else "#ffffff"
    )
    st.markdown(
        f"""
        <div style="border:1px solid #ccc;border-radius:8px;padding:14px;background:{bg}">
        <b>{idx}. kérdés:</b><br>{q}
        </div>
        """,
        unsafe_allow_html=True,
    )
    cA, cB = st.columns([1, 3])
    with cA:
        if st.button("👀 Válasz megjelenítése", key=f"show_{idx}"):
            st.session_state.show_answer[q] = True
    with cB:
        if st.session_state.show_answer[q]:
            st.success("Megoldás(ok):")
            for i, ans in enumerate(qa_map[q], 1):
                st.markdown(f"**{i})** {ans}")
            # válaszképek (csak akkor, ha van qid)
            qid = qid_map.get(q)
            if qid:
                imgs = find_answer_images(qid)
                if imgs:
                    st.info("Megoldáshoz tartozó kép(ek):")
                    for img in imgs:
                        st.image(str(img), use_container_width=True)
        # önértékelés
        val = st.radio(
            "Önértékelés:",
            ["Helyesnek ítélem", "Nem volt helyes"],
            index=0 if st.session_state.mark[q] in (None, "helyes") else 1,
            key=f"eval_{idx}",
            horizontal=True,
        )
        st.session_state.mark[q] = "helyes" if val == "Helyesnek ítélem" else "hibas"
    st.write("---")


# ─────────────────────────────────────────────────────────
# Összesítés és export + PDF
def summarize():
    total = len(questions)
    done = sum(1 for x in questions if st.session_state.mark.get(x) is not None)
    good = sum(1 for x in questions if st.session_state.mark.get(x) == "helyes")
    st.session_state.summary = {"total": total, "done": done, "good": good}


# Összesítés gomb
st.button("📊 Összesítés", on_click=summarize)

# Magyar font regisztrálása (PDF-hez)
font_reg, font_bold, unicode_ok = register_hungarian_font()
if not unicode_ok:
    st.warning(
        "A PDF‑hez nem találtam DejaVu Sans TTF‑et az alkalmazás mappájában. "
        "Helyezd el a 'DejaVuSans.ttf' és opcionálisan a 'DejaVuSans-Bold.ttf' fájlokat, "
        "különben előfordulhat, hogy az ű/ő/í karakterek nem jelennek meg helyesen a PDF‑ben."
    )

# PDF generálás gomb – AKTUÁLIS TÉMA (fejléc nélkül, sorkizárt megoldások)
if st.button("🖨️ PDF generálása (aktuális téma)"):
    with st.spinner("PDF készítése..."):
        pdf_bytes = build_pdf(
            theme_label=tema_full,
            theme_number=tema_szam,
            questions=questions,
            qa_map=qa_map,
            qid_map=qid_map,
            font_reg=font_reg,
            font_bold=font_bold,
        )
    st.success("PDF elkészült. Használd a letöltés gombot!")
    st.download_button(
        "⬇️ PDF letöltése (aktuális téma)",
        data=pdf_bytes,
        file_name=f"biofizika_{tema_szam}_tema.pdf",
        mime="application/pdf",
        use_container_width=True,
    )

# PDF generálás gomb – ÖSSZES TÉMA (fejléc nélkül, sorkizárt megoldások)
if st.button("🖨️ PDF generálása (ÖSSZES TÉMA)"):
    with st.spinner("PDF készítése az összes témából..."):
        pdf_bytes_all = build_pdf_all_themes(
            subjects=subjects, font_reg=font_reg, font_bold=font_bold
        )
    st.success("Összes témát tartalmazó PDF elkészült.")
    st.download_button(
        "⬇️ PDF letöltése (összes téma)",
        data=pdf_bytes_all,
        file_name=f"biofizika_osszes_tema.pdf",
        mime="application/pdf",
        use_container_width=True,
    )

# JSON export ha volt összesítés
if st.session_state.summary:
    s = st.session_state.summary
    st.info(
        f"Összes kérdés: **{s['total']}**, "
        f"Önértékelt: **{s['done']}**, "
        f"Helyesnek ítélt: **{s['good']}**"
    )
    export = {
        "tema": tema_full,
        "tema_szam": tema_szam,
        "osszes_kerdes": s["total"],
        "onertekeltek": s["done"],
        "helyesnek_iteltek": s["good"],
        "reszletek": [
            {
                "kerdes": q,
                "qid": qid_map.get(q),
                "valaszok": qa_map[q],
                "itel": st.session_state.mark.get(q),
            }
            for q in questions
        ],
    }
    st.download_button(
        "📥 Export JSON",
        json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8"),
        "biofizika_eredmeny.json",
        "application/json",
    )

# CSV export – mindig elérhető
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["index", "tema", "qid", "question", "mark", "answers"])
for i, q in enumerate(questions, 1):
    ans_join = " \n".join(qa_map[q])
    mk = st.session_state.mark.get(q)
    w.writerow([i, tema_full, qid_map.get(q), q, mk, ans_join])
st.download_button("⬇️ Export CSV", buf.getvalue(), "biofizika_eredmeny.csv", "text/csv")
