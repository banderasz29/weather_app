from __future__ import annotations
import streamlit as st

st.set_page_config(
    page_title="Fahrenheit ↔ Celsius átváltó", page_icon="🌡️", layout="centered"
)
st.title("🌡️ Fahrenheit ↔ Celsius átváltó (azonnali)")

# --- Guard flag az esemény-hurok elkerülésére ---
if "_updating" not in st.session_state:
    st.session_state._updating = False

# --- Alapértelmezett értékek ---
if "celsius" not in st.session_state:
    st.session_state.celsius = 0.0
if "fahrenheit" not in st.session_state:
    st.session_state.fahrenheit = st.session_state.celsius * 9.0 / 5.0 + 32.0


# --- Konverziós függvények ---
def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


# --- on_change callback-ek a kétirányú szinkronhoz ---
def on_celsius_change():
    if st.session_state._updating:
        return
    try:
        c = float(st.session_state.celsius)
    except Exception:
        return
    st.session_state._updating = True
    st.session_state.fahrenheit = round(c_to_f(c), 2)
    st.session_state._updating = False


def on_fahrenheit_change():
    if st.session_state._updating:
        return
    try:
        f = float(st.session_state.fahrenheit)
    except Exception:
        return
    st.session_state._updating = True
    st.session_state.celsius = round(f_to_c(f), 2)
    st.session_state._updating = False


st.caption(
    "Írj be egy értéket az egyik mezőbe – a másik automatikusan frissül. Tizedesek támogatottak."
)

c1, c2 = st.columns(2)
with c1:
    st.number_input(
        "Celsius (°C)",
        key="celsius",
        value=float(st.session_state.celsius),
        step=0.1,
        format="%.2f",
        on_change=on_celsius_change,
    )
with c2:
    st.number_input(
        "Fahrenheit (°F)",
        key="fahrenheit",
        value=float(st.session_state.fahrenheit),
        step=0.1,
        format="%.2f",
        on_change=on_fahrenheit_change,
    )

st.divider()
st.subheader("Gyors infó")
st.markdown(
    """
- **Képletek:**  
  - Fahrenheit = `Celsius × 9/5 + 32`  
  - Celsius = `(Fahrenheit − 32) × 5/9`
- A beviteli mezők **kétirányúan szinkronizáltak**: bármelyik módosítása frissíti a másikat.
- Az értékeket **2 tizedesre kerekítjük** a jobb olvashatóságért.
    """
)

# Opcionális kis referencia-tábla
with st.expander("Kis referencia (egyszerű példák)"):
    ref_rows = [
        ("0 °C", f"{c_to_f(0):.2f} °F"),
        ("100 °C", f"{c_to_f(100):.2f} °F"),
        ("-40 °C", f"{c_to_f(-40):.2f} °F  (érdekesség: -40 °C = -40 °F)"),
        ("32 °F", f"{f_to_c(32):.2f} °C"),
        ("212 °F", f"{f_to_c(212):.2f} °C"),
    ]
    for c_val, f_val in ref_rows:
        st.write(f"- {c_val} ↔ {f_val}")
