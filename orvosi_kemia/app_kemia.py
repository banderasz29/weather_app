from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path
import streamlit as st

from qa_utils_kemia import beolvas_csv_dict, valassz_kerdeseket

CSV_FAJL = Path(__file__).with_name("kerdes_valaszok_kemia.csv")
KERDES_SZAM_KOR = 10
KUSZOB = 7
PIC_DIR = Path("/Users/i0287148/Documents/python_test/python_test/orvosi_kemia/pic")


def expand_answers(ans_list: list[str]) -> list[str]:
    out: list[str] = []
    for a in ans_list:
        s = a or ""
        if not s.strip():
            continue
        if "\n" in s:
            out.append(s)
        else:
            if "," in s or ";" in s:
                parts = [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
                out.extend(parts)
            else:
                out.append(s.strip())

    seen = set()
    uniq: list[str] = []
    for p in out:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def answers_bulleted_md(ans_list: list[str]) -> str:
    items = expand_answers(ans_list)
    lines: list[str] = []

    for item in items:
        if "\n" not in item:
            lines.append(f"- {item}")
        else:
            raw_lines = item.splitlines()
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
    m = re.match(r"^\s*(\d+)\.", kerdes)
    return m.group(1) if m else None


def find_question_images(qnum: str) -> list[Path]:
    """
    Keresd meg a qnum-hoz tartozó képfájlokat a PIC_DIR-ben:
      - <qnum>.png
      - <qnum>_*.png (pl. 3_1.png, 3_2.png)
    """
    images: list[Path] = []
    p_main = PIC_DIR / f"{qnum}.png"
    if p_main.exists():
        images.append(p_main)
    extras = sorted(PIC_DIR.glob(f"{qnum}_*.png"), key=lambda p: p.name)
    images.extend(extras)
    return images


st.set_page_config(page_title="Orvosi kémia Kvíz", page_icon="🧪", layout="wide")
st.title("🧪 Orvosi Kémia – Minimum Követelmény Kvíz (önértékelős)")


@st.cache_data
def betolt_qa(path: str | Path):
    return beolvas_csv_dict(str(path))


qa = betolt_qa(CSV_FAJL)

if "kor_kerdesei" not in st.session_state:
    st.session_state.kor_kerdesei = []  # list[str]
if "show_answer" not in st.session_state:
    st.session_state.show_answer = {}  # dict[str, bool]
if "itel" not in st.session_state:
    # itel: kérdés -> "helyes" | "hibas"
    st.session_state.itel = {}  # dict[str, str | None]
if "osszegzes" not in st.session_state:
    st.session_state.osszegzes = None  # dict | None


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

if not st.session_state.kor_kerdesei:
    st.info(
        f"Kezdéshez kattints az **Új kör indítása ({KERDES_SZAM_KOR} kérdés)** gombra! "
        "Minden kérdésnél előbb **megmutathatod a választ**, majd **önértékeled**, hogy helyes volt-e."
    )
else:
    st.subheader("Kérdések egy körben")

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
                key=f"btn_show_{i}",
                on_click=mutasd_valaszt,
                args=(kerdes,),
                use_container_width=True,
            )

        with cols[1]:
            if st.session_state.show_answer.get(kerdes, False):
                st.success("Elfogadható válasz(ok):")
                st.markdown(answers_bulleted_md(qa.get(kerdes, [])))

                # Kép(ek) a szöveges válasz ALATT
                qnum = extract_qnum(kerdes)
                if qnum:
                    imgs = find_question_images(qnum)
                    if imgs:
                        st.markdown(
                            "<div style='height: 0.5rem'></div>", unsafe_allow_html=True
                        )
                        for idx_img, img_path in enumerate(imgs, start=1):
                            st.image(
                                str(img_path),
                                caption=(
                                    f"Ábra #{qnum}"
                                    if idx_img == 1
                                    else f"Ábra #{qnum} ({idx_img})"
                                ),
                                use_container_width=True,
                            )

                current = st.session_state.itel.get(kerdes)
                radio_index = 0 if (current is None or current == "helyes") else 1

                valasztas = st.radio(
                    "Önértékelés:",
                    options=["Helyesnek ítélem", "Nem volt helyes"],
                    index=radio_index,
                    key=f"radio_{i}",
                    horizontal=True,
                )

                st.session_state.itel[kerdes] = (
                    "helyes" if valasztas == "Helyesnek ítélem" else "hibas"
                )
            else:
                st.info(
                    "Kattints a „Válasz megjelenítése” gombra, és utána értékeld a válaszodat."
                )

        st.write("---")

    if st.button("🏁 Teszt kiértékelése", type="primary", key="btn_evaluate_test"):
        helyes_db = sum(
            1
            for k in st.session_state.kor_kerdesei
            if st.session_state.itel.get(k) == "helyes"
        )
        sikeres = helyes_db >= KUSZOB
        st.session_state.osszegzes = {"helyes_db": helyes_db, "sikeres": sikeres}

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
                f"❌ SIKERTELEN TESZT — NO WORRIES {helyes_db} / {len(st.session_state.kor_kerdesei)} "
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
