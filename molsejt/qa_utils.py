import csv
import random
from typing import Dict, List


def beolvas_csv_dict(filename: str) -> Dict[str, List[str]]:
    """Beolvassa a CSV-t: oszlopok: question, answers (answers '|' elválasztóval)."""
    d: Dict[str, List[str]] = {}
    with open(filename, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            q = row["question"].strip()
            answers = [a.strip() for a in row["answers"].split("|") if a.strip()]
            d[q] = answers
    return d


def valassz_kerdeseket(qa: Dict[str, List[str]], n: int = 12) -> List[str]:
    if n > len(qa):
        raise ValueError(
            "Nagyobb számot adtál meg, mint ahány kérdés rendelkezésre áll."
        )
    return random.sample(list(qa.keys()), n)
