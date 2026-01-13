from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

QUESTION_HEADER_RE = re.compile(r"(?m)^\s*(\d+)\.\s+(?P<q>.+?)!\s*$")


def _answers_from_cell(cell: Optional[str]) -> List[str]:
    if cell is None:
        return []
    txt = cell.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not txt:
        return []

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

    return [txt]


def _parse_numbered_text_strict(text: str) -> Dict[str, List[str]]:
    matches = list(QUESTION_HEADER_RE.finditer(text))
    if not matches:
        return {}

    qa: Dict[str, List[str]] = {}
    for i, m in enumerate(matches):
        question_line = m.group("q").strip() + "!"
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        if re.search(r"(?m)^\s*-\s+", block):
            answers = _answers_from_cell(block)
        else:
            raw = block.strip()
            answers = [raw] if raw else []
        qa[question_line] = answers
    return qa


def _parse_row_q_and_as(row: List[str]) -> Optional[Tuple[str, List[str]]]:
    if not row or all(not (c and c.strip()) for c in row):
        return None

    first_idx = None
    for i, c in enumerate(row):
        if c and c.strip():
            first_idx = i
            break
    if first_idx is None:
        return None

    first_cell = row[first_idx].strip()
    if "!" not in first_cell:
        return None

    q_part, after_bang = first_cell.split("!", 1)
    question_line = (q_part.strip() + "!").strip()
    if not re.match(r"^\s*\d+\.\s+.+!\s*$", question_line):
        return None

    after_bang = after_bang.lstrip()
    if after_bang.startswith(","):
        after_bang = after_bang[1:].lstrip()

    parts: List[str] = []
    if after_bang:
        parts.append(after_bang)

    for cell in row[first_idx + 1 :]:
        if cell and cell.strip():
            parts.append(cell.strip())

    answer_text = " ".join(parts).strip()
    answers = _answers_from_cell(answer_text)

    seen = set()
    uniq: List[str] = []
    for ans in answers:
        key = ans.strip().lower()
        if key and key not in seen:
            seen.add(key)
            uniq.append(ans.strip())

    return (question_line, uniq)


def beolvas_csv_dict(filename: str) -> Dict[str, List[str]]:
    path = Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"Nem található a fájl: {path.resolve()}")

    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=",")
            qa: Dict[str, List[str]] = {}
            any_row = False

            for row in reader:
                any_row = True
                parsed = _parse_row_q_and_as(row)
                if not parsed:
                    continue

                question, answers = parsed
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

    except csv.Error:
        text = path.read_text(encoding="utf-8-sig")
        qa_fb = _parse_numbered_text_strict(text)
        if qa_fb:
            return qa_fb
        raise

    if any_row and not qa:
        text = path.read_text(encoding="utf-8-sig")
        qa_fb = _parse_numbered_text_strict(text)
        if qa_fb:
            return qa_fb
        raise ValueError("Nem sikerült kérdés–válasz párokat beolvasni.")

    if not any_row:
        text = path.read_text(encoding="utf-8-sig")
        qa_fb = _parse_numbered_text_strict(text)
        if qa_fb:
            return qa_fb
        raise ValueError("A CSV üresnek tűnik.")

    return qa


def valassz_kerdeseket(qa: Dict[str, List[str]], n: int = 10) -> List[str]:
    import random

    if n > len(qa):
        raise ValueError(
            "Nagyobb számot adtál meg, mint ahány kérdés rendelkezésre áll."
        )
    return random.sample(list(qa.keys()), n)
