from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import streamlit as st

# Új beolvasó modul (CSV: questions/answers; szigorú kérdés 'szám.' + '!' a sor végén)
from qa_utils_kemia import beolvas_csv_dict, valassz_kerdeseket

# --- Konstansok / fájlok ---
CSV_FAJL = Path(__file__).with_name("kerdes_valaszok_kemia.csv")
KERDES_SZAM_KOR = 10  # egy körben ennyi kérdés
KUSZOB = 7  # legalább 7 helyes -> SIKERES

# Képek mappája – a fájlnév a kérdés sorszáma: pl. "88.png"
PIC_DIR = Path("/Users/i0287148/Documents/python_test/python_test/orvosi_kemai/pic")


# --- Segédfüggvények: megjelenítés és hasznos eszközök ---
def expand_answers(ans_list: list[str]) -> list[str]:
    """
    A beolvasott válaszok listáját opcionálisan tovább bontja:
      - csak az EGY SOROS elemeket bontjuk VESSZŐ (',') és PONTOSVESSZŐ (';') szerint,
      - a PERJELES ('/') alakokat (pl. 'kék/lila') NEM bontjuk,
      - a TÖBBSOROS elemeket érintetlenül hagyjuk (ASCII rajzok megőrzése).
    """
    out: list[str] = []
    for a in ans_list:
        s = a or ""
        if not s.strip():
            continue
        if "\n" in s:
            # Többsoros tartalom: hagyjuk egyben
            out.append(s)
        else:
            # Egy soros: ',' és ';' szerinti bontás (ha van)
            if "," in s or ";" in s:
                parts = [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
                out.extend(parts)
            else:
                out.append(s.strip())

    # Duplikátumok kiszűrése (case-insensitive)
    seen = set()
    uniq = []
    for p in out:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def answers_bulleted_md(ans_list: list[str]) -> str:
    """
    Markdown összeállítása:
      - egy soros elemek: "- elem"
      - többsoros elemek: "- első sor" + kódblokkba a további sorok (behúzások megmaradnak)
    Példa megjelenítés:
      - D-tejsav:

        ```
        COOH
          |
         H-C-OH
          |
         CH3
        ```
    """
    items = expand_answers(ans_list)
    lines: list[str] = []

    for item in items:
        if "\n" not in item:
            # egy soros
            lines.append(f"- {item}")
        else:
            raw_lines = item.splitlines()
            # első nem üres sor bullet cím
            idx = 0
            while idx < len(raw_lines) and not raw_lines[idx].strip():
                idx += 1
            if idx >= len(raw_lines):
                continue  # csak üres sorok
            first = raw_lines[idx].strip()
            rest = "\n".join(raw_lines[idx + 1 :])

            lines.append(f"- {first}")
            if rest.strip():
                lines.append("")
                lines.append("```")
                lines.append(rest.rstrip())
                lines.append("```")

    return "\n".join(lines)


def extract_qnum(kerdes: str) -> str | None:
    """
    Sorszám kinyerése a kérdés elejéről: '^\d+\.'
    Pl. '88. Rajzolja ... !' -> '88'
    Ha nincs szám a kérdés elején, None.
    """
    m = re.match(r"^\s*(\d+)\.", kerdes)
    return m.group(1) if m else None


# --- Streamlit alapbeállítás (kémiai jelképpel) ---
st.set_page_config(page_title="Orvosi kémia Kvíz", page_icon="🧪", layout="wide")
st.title("🧪 Orvosi Kémia – Minimum Követelmény Kvíz (önértékelős)")


# --- Adatbetöltés cache-el ---
@st.cache_data
def betolt_qa(path: str | Path):
    return beolvas_csv_dict(str(path))


qa = betolt_qa(CSV_FAJL)

# --- Session State inicializálás ---
if "kor_kerdesei" not in st.session_state:
    st.session_state.kor_kerdesei = []  # list[str]
if "show_answer" not in st.session_state:
    st.session_state.show_answer = {}  # dict[str, bool]
if "itel" not in st.session_state:
    # itel: kérdés -> "helyes" | "hibas"
    st.session_state.itel = {}  # dict[str, str | None]
if "osszegzes" not in st.session_state:
    st.session_state.osszegzes = None  # dict | None


# --- Callbackok ---
def uj_kor():
    st.session_state.kor_kerdesei = valassz_kerdeseket(qa, KERDES_SZAM_KOR)
    st.session_state.show_answer = {k: False for k in st.session_state.kor_kerdesei}
    st.session_state.itel = {k: None for k in st.session_state.kor_kerdesei}
    st.session_state.osszegzes = None


def reset_minden():
    st.session_state.kor_kerdesei = []
    st.session_state.show_answer = {}
    st.session_state.itel = {}
    st.session_state.osszegzes = None


def mutasd_valaszt(kerdes: str):
    st.session_state.show_answer[kerdes] = True


# --- Felső vezérlők (EGYEDI KEY-ek!) ---
c1, c2 = st.columns([1, 1])
with c1:
    st.button(
        f"🧪 Új kör indítása ({KERDES_SZAM_KOR} kérdés)",
        type="primary",
        use_container_width=True,
        on_click=uj_kor,
        key="btn_new_round",
    )
with c2:
    st.button(
        "♻️ Teljes reset",
        use_container_width=True,
        on_click=reset_minden,
        key="btn_full_reset",
    )

st.divider()

# --- Tartalom ---
if not st.session_state.kor_kerdesei:
    st.info(
        f"Kezdéshez kattints az **Új kör indítása ({KERDES_SZAM_KOR} kérdés)** gombra! "
        "Minden kérdésnél előbb **megmutathatod a választ**, majd **önértékeled**, hogy helyes volt-e."
    )
else:
    st.subheader("Kérdések egy körben")

    # --- Futó eredmény ---
    helyes_db = sum(
        1
        for k in st.session_state.kor_kerdesei
        if st.session_state.itel.get(k) == "helyes"
    )
    itelt_db = sum(
        1
        for k in st.session_state.kor_kerdesei
        if st.session_state.itel.get(k) in ("helyes", "hibas")
    )
    st.caption(
        f"Önértékelt kérdések: {itelt_db} / {len(st.session_state.kor_kerdesei)} — "
        f"Helyesnek ítélt: {helyes_db}"
    )

    # --- Kérdések kilistázása ---
    for i, kerdes in enumerate(st.session_state.kor_kerdesei, start=1):
        st.markdown(f"**{i}.** {kerdes}")

        cols = st.columns([1, 2])
        with cols[0]:
            st.button(
                "👀 Válasz megjelenítése",
                key=f"btn_show_{i}",
                on_click=mutasd_valaszt,
                args=(kerdes,),
                use_container_width=True,
            )

        with cols[1]:
            if st.session_state.show_answer.get(kerdes, False):
                st.success("Elfogadható válasz(ok):")
                # Bullet + fenced code a többsoros válaszokhoz
                st.markdown(answers_bulleted_md(qa.get(kerdes, [])))

                # --- KÉP MEGJELENÍTÉSE, ha létezik: <PIC_DIR>/<sorszám>.png ---
                qnum = extract_qnum(kerdes)
                if qnum:
                    img_path = PIC_DIR / f"{qnum}.png"
                    if img_path.exists():
                        st.image(
                            str(img_path),
                            caption=f"Megoldáshoz tartozó ábra (#{qnum})",
                            use_container_width=True,  # ✅ frissítve: use_column_width helyett
                        )

                # Alapértelmezett önértékelés: HELYES
                current = st.session_state.itel.get(kerdes)
                radio_index = 0 if (current is None or current == "helyes") else 1

                valasztas = st.radio(
                    "Önértékelés:",
                    options=["Helyesnek ítélem", "Nem volt helyes"],
                    index=radio_index,
                    key=f"radio_{i}",
                    horizontal=True,
                )

                # Mentés: két állapot (helyes / hibas)
                st.session_state.itel[kerdes] = (
                    "helyes" if valasztas == "Helyesnek ítélem" else "hibas"
                )
            else:
                st.info(
                    "Kattints a „Válasz megjelenítése” gombra, és utána értékeld a válaszodat."
                )

        st.write("---")

    # --- Kiértékelés gomb (EGYEDI KEY!) ---
    if st.button("🏁 Teszt kiértékelése", type="primary", key="btn_evaluate_test"):
        helyes_db = sum(
            1
            for k in st.session_state.kor_kerdesei
            if st.session_state.itel.get(k) == "helyes"
        )
        sikeres = helyes_db >= KUSZOB
        st.session_state.osszegzes = {"helyes_db": helyes_db, "sikeres": sikeres}

    # --- Eredmény kijelzése + JSON export ---
    if st.session_state.osszegzes is not None:
        helyes_db = st.session_state.osszegzes["helyes_db"]
        sikeres = st.session_state.osszegzes["sikeres"]
        if sikeres:
            st.success(
                f"✅ SIKERES TESZT — GRATULÁLUNK! {helyes_db} / {len(st.session_state.kor_kerdesei)} "
                f"(küszöb: {KUSZOB})"
            )
        else:
            st.error(
                f"❌ SIKERTELEN TESZT — {helyes_db} / {len(st.session_state.kor_kerdesei)} "
                f"(legalább {KUSZOB} szükséges)"
            )

        export = {
            "kor_id": datetime.utcnow().isoformat() + "Z",
            "kerdesek_szama": len(st.session_state.kor_kerdesei),
            "kuszob": KUSZOB,
            "helyes_db": helyes_db,
            "sikeres": sikeres,
            "reszletek": [
                {
                    "kerdes": k,
                    "elfogadhato_valaszok": qa.get(k, []),
                    "itel": st.session_state.itel.get(k),
                }
                for k in st.session_state.kor_kerdesei
            ],
        }
        st.download_button(
            label="📥 Eredmények letöltése (JSON)",
            data=json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="kviz_eredmeny_onertekeles.json",
            mime="application/json",
            use_container_width=True,
            key="btn_download_json",
        )
