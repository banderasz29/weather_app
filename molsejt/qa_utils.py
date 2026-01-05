from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional


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


# --- Válaszok bontása: ';' és ' - '; sor eleji '-' -> bulletblokk; '/' nem bont ---
def _split_line_bullets_multiline(text: str) -> List[str]:
    """Sor eleji '- ' bulletok szerinti több soros bontás."""
    if not text.strip():
        return []
    if not re.search(r"(?m)^\s*-\s+", text):
        return []
    lines = text.splitlines()
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


def _split_inline_hyphen_semicolon(text: str) -> List[str]:
    """Inline bontás: ' - ' majd ';' (a '/' nem bont)."""
    s = (text or "").strip()
    if not s:
        return []
    if re.search(r"\s-\s+", s):
        parts = re.split(r"\s-\s+", s)
        return [p.strip(" ;") for p in parts if p.strip(" ;")]
    if ";" in s:
        parts = s.split(";")
        return [p.strip() for p in parts if p.strip()]
    return [s]


def _answers_from_cell(cell: Optional[str]) -> List[str]:
    """
    A CSV 'answer' cella szövegéből lista:
      - ha vannak sor eleji '-' bulletok -> minden bullet egy elem (többsorosan is),
      - különben inline ' - ' vagy ';' szerint bont,
      - perjel ('/') NEM okoz bontást,
      - különben egy elem.
    """
    if cell is None:
        return []
    txt = cell.replace("\r\n", "\n").replace("\r", "\n").strip()

    bullets = _split_line_bullets_multiline(txt)
    if bullets:
        return bullets

    inline = _split_inline_hyphen_semicolon(txt)
    if inline:
        return inline

    return [txt] if txt else []


def beolvas_csv_dict(filename: str) -> Dict[str, List[str]]:
    """
    Beolvasás 'question,answer' CSV-ből:
      - 'question' oszlop: a kérdés (ahogy van),
      - 'answer' oszlop: válaszok bontása ';' és ' - ' szeparátorok szerint,
        sor eleji '-' bulletok többsoros blokkot képeznek, a '/' nem bont.
    Visszaad: { kérdés: [válasz1, válasz2, ...] }
    """
    path = Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"Nem található a fájl: {path.resolve()}")

    dialect = _detect_csv_dialect(path)
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, dialect=dialect) if dialect else csv.DictReader(f)
        rows = list(reader)

    if not rows:
        raise ValueError("A CSV üresnek tűnik.")

    q_col = _find_column(rows[0], "question", "questions")
    a_col = _find_column(rows[0], "answer", "answers")

    if q_col is None or a_col is None:
        raise KeyError("A CSV nem tartalmaz 'question' és 'answer' fejlécet.")

    qa: Dict[str, List[str]] = {}
    for row in rows:
        question = (row.get(q_col, "") or "").strip()
        answer_raw = row.get(a_col, "") or ""

        if not question:
            continue

        answers = _answers_from_cell(answer_raw)

        # duplikált kérdések esetén egyesítjük az egyedi válaszokat
        if question in qa:
            merged = qa[question] + answers
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
        raise ValueError("Nem sikerült kérdés–válasz párokat beolvasni a CSV-ből.")

    return qa


def valassz_kerdeseket(qa: Dict[str, List[str]], n: int = 12) -> List[str]:
    """Véletlenszerűen kiválaszt n egyedi kérdést."""
    import random

    if n > len(qa):
        raise ValueError(
            "Nagyobb számot adtál meg, mint ahány kérdés rendelkezésre áll."
        )
    return random.sample(list(qa.keys()), n)
