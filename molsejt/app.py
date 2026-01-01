import json
from datetime import datetime
import streamlit as st
from qa_utils import beolvas_csv_dict, valassz_kerdeseket

CSV_FAJL = "/Users/i0287148/Documents/python_test/molsejt/kerdes_valaszok.csv"
KUSZOB = 9  # legalább 9 helyes -> SIKERES

st.set_page_config(
    page_title="Molsejt Min Kérdések Kvíz", page_icon="🔬", layout="wide"
)
st.title("🔬 Molsejt Min Kérdések – Kvíz (önértékelős)")


@st.cache_data
def betolt_qa():
    return beolvas_csv_dict(CSV_FAJL)


qa = betolt_qa()

# --- State inicializálás ---
if "kor_kerdesei" not in st.session_state:
    st.session_state.kor_kerdesei = []
if "show_answer" not in st.session_state:
    st.session_state.show_answer = {}  # {kerdes: bool}
if "itel" not in st.session_state:
    st.session_state.itel = {}  # {kerdes: "helyes"|"hibas"|None}
if "osszegzes" not in st.session_state:
    st.session_state.osszegzes = None  # {"helyes_db": int, "sikeres": bool} vagy None


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


# --- Felső vezérlők ---
c1, c2 = st.columns([1, 1])
with c1:
    st.button(
        "🧪 Új kör indítása (12 kérdés)",
        type="primary",
        use_container_width=True,
        on_click=uj_kor,
    )
with c2:
    st.button("♻️ Teljes reset", use_container_width=True, on_click=reset_minden)

st.divider()

if not st.session_state.kor_kerdesei:
    st.info(
        "Kezdéshez kattints az **Új kör indítása (12 kérdés)** gombra! "
        "Minden kérdésnél előbb **megmutathatod a választ**, majd **önértékeled**, hogy helyes volt-e."
    )
else:
    st.subheader("Kérdések egy körben")

    # Futó eredmény
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

    for i, kerdes in enumerate(st.session_state.kor_kerdesei, start=1):
        st.markdown(f"**{i}.** {kerdes}")

        cols = st.columns([1, 2])
        with cols[0]:
            st.button(
                "👀 Válasz megjelenítése",
                key=f"show_{i}",
                on_click=mutasd_valaszt,
                args=(kerdes,),
                use_container_width=True,
            )

        with cols[1]:
            if st.session_state.show_answer.get(kerdes, False):
                st.success("Elfogadható válasz(ok):")
                for v in qa[kerdes]:
                    st.markdown(f"- {v}")

                valasztas = st.radio(
                    "Önértékelés:",
                    options=["Helyesnek ítélem", "Nem volt helyes"],
                    index=(
                        0
                        if st.session_state.itel.get(kerdes) is None
                        else (1 if st.session_state.itel.get(kerdes) == "helyes" else 2)
                    ),
                    key=f"radio_{i}",
                    horizontal=True,
                )
                # Mentés
                if valasztas == "Helyesnek ítélem":
                    st.session_state.itel[kerdes] = "helyes"
                elif valasztas == "Nem volt helyes":
                    st.session_state.itel[kerdes] = "hibas"
                else:
                    st.session_state.itel[kerdes] = None
            else:
                st.info(
                    "Kattints a „Válasz megjelenítése” gombra, és utána értékeld a válaszodat."
                )

        st.write("---")

    # --- Kiértékelés gomb ---
    if st.button("🏁 Teszt kiértékelése", type="primary"):
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
                f"✅ SIKERES TESZT — GRATULÁLOK!! {helyes_db} / {len(st.session_state.kor_kerdesei)} "
                f"(küszöb: {KUSZOB})"
            )
        else:
            st.error(
                f"❌ NO PROBLEM — KÖVETKEZŐ SIKERÜLNI FOG ! {helyes_db} / {len(st.session_state.kor_kerdesei)} "
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
                    "elfogadhato_valaszok": qa[k],
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
        )
