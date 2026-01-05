# qa_utils_kemia.py
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional


# --- Szigorú kérdés-fejléc: sor elején "szám + .", a sor végén "!" ---
# Példák: "1. Definiálja az izotópok fogalmát!"
QUESTION_HEADER_RE = re.compile(r"(?m)^\s*(\d+)\.\s+(?P<q>.+?)!\s*$")


# ---------------------- Segédfüggvények ----------------------


def _detect_csv_dialect(path: Path) -> Optional[csv.Dialect]:
    """CSV dialektus autodetekció; hiba esetén None."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
    try:
        return csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
    except csv.Error:
        return None


def _find_column(row: dict, *candidates: str) -> Optional[str]:
    """Megkeresi a megadott oszlopnevek egyikét a row kulcsai között (case-insensitive)."""
    lower_map = {k.strip().lower(): k for k in row.keys() if isinstance(k, str)}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    return None


def _extract_question_strict(text: str) -> str:
    """
    A 'questions' cellából kinyeri a kérdés sort:
    - kötelező: sor elején "szám + ."
    - kötelező: ugyanazon sorban a végén "!"
    Ha nem illeszkedik, visszaadja a trimmelt egész szöveget (de a fallback parser csak a szigorú mintát fogadja el).
    """
    s = (text or "").strip()
    if not s:
        return s
    # csak az első sort vizsgáljuk, ha több sor lenne a cellában
    first_line = s.splitlines()[0]
    m = re.match(r"^\s*\d+\.\s+.+!\s*$", first_line)
    return first_line.strip() if m else s.strip()


def _answers_from_cell(cell: Optional[str]) -> List[str]:
    """
    A CSV 'answers' cellából lista:
      - ha vannak sor eleji '-' bulletok -> minden bullet egy elem (többsorosan is)
      - különben inline ' - ' | ';' | ',' szerinti bontás
      - egyébként 1 elem
    A '/' (pl. 'kék/lila') nem bont alternatívára.
    """
    if cell is None:
        return []
    txt = cell.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Többsoros bullet (sor elején "- ")
    if re.search(r"(?m)^\s*-\s+", txt):
        lines = txt.splitlines()
        answers: List[str] = []
        current: List[str] = []
        in_bullet = False
        for ln in lines:
            m = re.match(r"^\s*-\s+(.*)", ln)
            if m:
                if current:
                    answers.append("\n".join(current).rstrip())
                    current = []
                in_bullet = True
                current.append(m.group(1))
            else:
                if in_bullet:
                    current.append(ln.rstrip())
        if current:
            answers.append("\n".join(current).rstrip())
        return [a for a in answers if a.strip()]

    # ' - ' szerinti bontás (először ezt), majd ';' és ','
    if re.search(r"\s-\s+", txt):
        parts = re.split(r"\s-\s+", txt)
        parts = [p for p in parts if p.strip()]
        return [p.strip(" ,;") for p in parts if p.strip(" ,;")]

    if ";" in txt or "," in txt:
        parts = re.split(r"[;,]", txt)
        return [p.strip() for p in parts if p.strip()]

    return [txt] if txt else []


def _parse_numbered_text_strict(text: str) -> Dict[str, List[str]]:
    """
    Fallback parser számozott szövegre:
    - kérdés: csak az a sor, amely sor elején "szám + ." kezdettel indul ÉS "!"-re végződik.
    - válaszblokk: a kérdés sorát követő tartalom a következő szigorú kérdésig.
    Ez megakadályozza, hogy a válaszokban szereplő "1."-kezdetű felsorolások (amelyek tipikusan nem "!"-re végződnek)
    új kérdésként legyenek felismerve.
    """
    matches = list(QUESTION_HEADER_RE.finditer(text))
    if not matches:
        return {}

    qa: Dict[str, List[str]] = {}
    for i, m in enumerate(matches):
        question_line = m.group("q").strip() + "!"
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]

        # Válaszblokk: ha vannak '- ' bulletok, azok szerinti bontás; különben 1 elem a teljes blokk (trimmelve)
        if re.search(r"(?m)^\s*-\s+", block):
            answers = _answers_from_cell(block)  # ugyanaz a bulletos logika
        else:
            raw = block.strip()
            answers = [raw] if raw else []

        qa[question_line] = answers

    return qa


# ---------------------- Nyilvános API ----------------------


def beolvas_csv_dict(filename: str) -> Dict[str, List[str]]:
    """
    Beolvasás 'questions, answers' CSV-ből:
      - 'questions'/'question' oszlop: a kérdés sorát a sorszámtól az '!' végéig vesszük.
      - 'answers'/'answer' oszlop: bulletos vagy inline bontás (lásd _answers_from_cell).

    Ha a CSV nem található vagy nem értelmezhető, fallback a szigorú számozott szövegre:
      - csak az a sor kérdés, amely "szám + ." kezdetű és '!'‑re végződik.
    """
    path = Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"Nem található a fájl: {path.resolve()}")

    # Próbáljuk CSV-ként
    dialect = _detect_csv_dialect(path)
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = (
                csv.DictReader(f, dialect=dialect) if dialect else csv.DictReader(f)
            )
            rows = list(reader)
    except csv.Error:
        # Nem CSV: nyers szöveg szigorú számozással
        text = path.read_text(encoding="utf-8-sig")
        qa_fb = _parse_numbered_text_strict(text)
        if qa_fb:
            return qa_fb
        raise

    if not rows:
        # Üres CSV -> próbáljuk a nyers szöveget
        text = path.read_text(encoding="utf-8-sig")
        qa_fb = _parse_numbered_text_strict(text)
        if qa_fb:
            return qa_fb
        raise ValueError(
            "A CSV üresnek tűnik, és a számozott fallback sem talált kérdéseket."
        )

    # Oszlopok felderítése
    q_col = _find_column(rows[0], "questions", "question")
    a_col = _find_column(rows[0], "answers", "answer")

    qa: Dict[str, List[str]] = {}
    for row in rows:
        q_raw = row.get(q_col, "") if q_col else ""
        a_raw = row.get(a_col, "") if a_col else ""

        # Szoros kérdés kinyerés (sorszám + '.' ... '!' az első sorban)
        question = _extract_question_strict(q_raw)
        # Ha nem felel meg a szigorú mintának, a fallback szövegparszolásnál lesz kezelve;
        # CSV-ben viszont kényszerítjük a kérdéssor jelenlétét.
        if not re.match(r"^\s*\d+\.\s+.+!\s*$", question):
            # Kihagyjuk azokat a sorokat, amelyek nem kérdést tartalmaznak
            continue

        answers = _answers_from_cell(a_raw)
        if question in qa:
            merged = qa[question] + answers
            # Duplikátum-szűrés (case-insensitive)
            seen = set()
            uniq: List[str] = []
            for ans in merged:
                key = ans.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    uniq.append(ans.strip())
            qa[question] = uniq
        else:
            qa[question] = answers

    if not qa:
        # CSV volt, de semmi sem ment át a szigorú kérdés-feltételeken -> próbáljuk meg a nyers szöveget
        text = path.read_text(encoding="utf-8-sig")
        qa_fb = _parse_numbered_text_strict(text)
        if qa_fb:
            return qa_fb
        raise ValueError(
            "Nem sikerült kérdés–válasz párokat beolvasni (szigorú kérdésminta érvényesítve)."
        )

    return qa


def valassz_kerdeseket(qa: Dict[str, List[str]], n: int = 10) -> List[str]:
    """Véletlenszerűen kiválaszt n egyedi kérdést."""
    import random

    if n > len(qa):
        raise ValueError(
            "Nagyobb számot adtál meg, mint ahány kérdés rendelkezésre áll."
        )
    return random.sample(list(qa.keys()), n)
