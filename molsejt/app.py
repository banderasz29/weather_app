from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import streamlit as st

# Saját modul: a CSV beolvasó és kérdésválasztó függvények
from qa_utils import beolvas_csv_dict, valassz_kerdeseket

# --- Konstansok / fájlok ---
# A CSV az app.py mellett legyen; így biztosan megtaláljuk
CSV_FAJL = Path(__file__).with_name("kerdes_valaszok.csv")
KUSZOB = 9  # legalább 9 helyes -> SIKERES


# --- Segédfüggvények: megjelenítés ---
def expand_answers(ans_list: list[str]) -> list[str]:
    """
    Alternatívák bontása VESSZŐ (',') és PONTOSVESSZŐ (';') szerint.
    A perjeles ('/') alak – pl. 'kék/lila' – EGY válasz marad.
    Példa:
      "Lugol-oldat; jód oldat" -> ["Lugol-oldat", "jód oldat"]
      "Agaróz gél, agaróz"     -> ["Agaróz gél", "agaróz"]
      "kék/lila"               -> ["kék/lila"]
    """
    out: list[str] = []
    for a in ans_list:
        s = (a or "").strip()
        if not s:
            continue
        # Csak ',' és ';' szerint bontunk; a '/' érintetlen marad
        parts = [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
        out.extend(parts)

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
    Markdown bullet lista összeállítása az (csak ',' és ';' alapján szétbontott) válaszokból.
    """
    items = expand_answers(ans_list)
    return "\n".join(f"- {item}" for item in items)


# --- Streamlit alapbeállítás ---
st.set_page_config(page_title="Miolsejt Kvíz", page_icon="🔬", layout="wide")
st.title("🔬 Molsejt Minimum Követelmény Kvíz (önértékelős)")


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
    st.session_state.kor_kerdesei = valassz_kerdeseket(qa, 12)
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
        "🧪 Új kör indítása (12 kérdés)",
        type="primary",
        use_container_width=True,
        on_click=uj_kor,
        key="btn_new_round",  # egyedi kulcs
    )
with c2:
    st.button(
        "♻️ Teljes reset",
        use_container_width=True,
        on_click=reset_minden,
        key="btn_full_reset",  # egyedi kulcs
    )

st.divider()

# --- Tartalom ---
if not st.session_state.kor_kerdesei:
    st.info(
        "Kezdéshez kattints az **Új kör indítása (12 kérdés)** gombra! "
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
                key=f"btn_show_{i}",  # egyedi gombkulcs kérdésenként
                on_click=mutasd_valaszt,
                args=(kerdes,),
                use_container_width=True,
            )

        with cols[1]:
            if st.session_state.show_answer.get(kerdes, False):
                st.success("Elfogadható válasz(ok):")
                # Bulletpontos megjelenítés (',', ';' mentén bontás; '/' NEM bontódik)
                st.markdown(answers_bulleted_md(qa.get(kerdes, [])))

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
                f"✅ SIKERES TESZT — GRATULÁLOK ! {helyes_db} / {len(st.session_state.kor_kerdesei)} "
                f"(küszöb: {KUSZOB})"
            )
        else:
            st.error(
                f"❌ SIKERTELEN TESZT — NO PROBLEM {helyes_db} / {len(st.session_state.kor_kerdesei)} "
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
            key="btn_download_json",  # egyedi kulcs
        )
