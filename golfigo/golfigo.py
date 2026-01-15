
from __future__ import annotations
import os
import io
import csv
import json
import re
import sqlite3
from typing import List, Tuple, Optional

import streamlit as st
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PWTimeout,
    Error as PWError,
)

# ============================ Secretek & beállítások ============================
USER = st.secrets["GOLFIGO_USER"]
PWD = st.secrets["GOLFIGO_PASSWORD"]

LOGIN_URL = st.secrets.get("LOGIN_URL", "https://mgsz.golfigo.com/hu/auth/login/")
HCP_URL = st.secrets.get(
    "HCP_URL", "https://mgsz.golfigo.com/hu/166-500-8775/account/hcp-records/"
)

# Zscaler-kompatibilis Edge/Chromium 143-as UA mint alapértelmezés
DEFAULT_EDGE_UA_143 = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)
APPROVED_UA = st.secrets.get("APPROVED_UA", DEFAULT_EDGE_UA_143)
APPROVED_ENGINE = st.secrets.get(
    "APPROVED_ENGINE", "chromium"
).lower()  # chromium|webkit|firefox


# ============================ Segédfüggvények ============================
def ensure_snapshots_dir() -> str:
    os.makedirs("snapshots", exist_ok=True)
    return "snapshots"


def save_html(path: str, html: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def parse_first_table(html: str) -> Tuple[List[str], List[List[str]]]:
    """Kinyeri az első <table>-t (vagy fallback: bármely <tr> struktúra)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        rows: List[List[str]] = []
        for tr in soup.select("tr"):
            tds = [
                td.get_text(strip=True).replace("\n", " ") for td in tr.find_all("td")
            ]
            if tds:
                rows.append(tds)
        if not rows:
            return [], []
        n = max(len(r) for r in rows)
        headers = [f"col_{i+1}" for i in range(n)]
        rows = [r + [""] * (n - len(r)) for r in rows]
        return headers, rows

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    rows: List[List[str]] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        rows.append([td.get_text(strip=True).replace("\n", " ") for td in tds])

    if not headers and rows:
        n = max(len(r) for r in rows)
        headers = [f"col_{i+1}" for i in range(n)]
        rows = [r + [""] * (len(headers) - len(r)) for r in rows]
    else:
        rows = [r + [""] * (len(headers) - len(r)) for r in rows]
    return headers, rows


def detect_and_sort_by_datetime(
    headers: List[str], rows: List[List[str]]
) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Megpróbáljuk megtalálni a dátumoszlopot:
    - minden oszlopra to_datetime (dayfirst=True, errors='coerce')
    - amelyikben a legtöbb érvényes dátum keletkezik, azt választjuk
    - arra rendezünk DESC (legújabb elöl)
    Visszatér: rendezett DataFrame, és a választott oszlop neve (vagy None).
    """
    if not headers or not rows:
        return pd.DataFrame(), None

    df = pd.DataFrame(rows, columns=headers)

    # Előfeldolgozás: ponttal végződő YYYY.MM.DD. minták letakarítása a próbához
    def _clean_date_strings(s: pd.Series) -> pd.Series:
        return s.astype(str).str.strip().str.replace(r"\.$", "", regex=True)

    best_col = None
    best_ok = -1
    parsed_cache = {}

    for col in df.columns:
        s = _clean_date_strings(df[col])
        parsed = pd.to_datetime(s, errors="coerce", dayfirst=True, utc=False)
        ok_count = parsed.notna().sum()
        parsed_cache[col] = parsed
        if ok_count > best_ok:
            best_ok = ok_count
            best_col = col

    if best_col is None or best_ok == 0:
        # Nem találtunk használható dátumoszlopot – visszaadjuk az eredetit
        return df, None

    df["_parsed_dt"] = parsed_cache[best_col]
    df = df.sort_values(by="_parsed_dt", ascending=False, kind="stable").drop(
        columns=["_parsed_dt"]
    )
    return df, best_col


def save_to_csv(headers: List[str], rows: List[List[str]], path: str) -> bytes:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    if headers:
        w.writerow(headers)
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    with open(path, "wb") as f:
        f.write(data)
    return data


def save_to_sqlite(
    df: pd.DataFrame, db_path: str, table: str = "hcp_records_sorted"
) -> None:
    if df.empty:
        raise ValueError("Üres DataFrame, nem tudok SQLite táblát létrehozni.")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            # Felülírjuk a táblát a rendezett eredménnyel
            df.to_sql(table, conn, if_exists="replace", index=False)
    finally:
        conn.close()


# ============================ Playwright login & letöltés ============================
def _try_accept_cookies(page, diag: dict, timeout_ms: int = 5000) -> None:
    """HU/EN cookie-banner gombok best-effort bezárása (OneTrust is)."""
    try:
        candidates = [
            "#onetrust-accept-btn-handler",
            "button#onetrust-accept-btn-handler",
            "button:has-text('Elfogadom')",
            "button:has-text('Rendben')",
            "button:has-text('Összes elfogadása')",
            "button:has-text('Accept')",
            "button:has-text('I agree')",
            "button:has-text('Allow all')",
        ]
        for sel in candidates:
            btn = page.locator(sel)
            if btn.count() > 0:
                try:
                    btn.first.click(timeout=timeout_ms)
                    diag.setdefault("cookie_accepted", []).append(sel)
                    page.wait_for_timeout(200)
                except Exception:
                    pass
        overlay = page.locator("[role='dialog'], .modal, .overlay")
        if overlay.count() > 0:
            try:
                close_btn = overlay.locator(
                    "button:has-text('Close'), [aria-label='Close'], .close"
                )
                if close_btn.count() > 0:
                    close_btn.first.click(timeout=1500)
                else:
                    page.keyboard.press("Escape")
            except Exception:
                pass
    except (PWTimeout, PWError):
        pass


def _select_frame_with_login(page, diag: dict):
    """Ha a login űrlap iframe-ben van, próbálja megtalálni a releváns frame-et."""
    frames = page.frames
    diag["frames"] = [f.url for f in frames]
    for fr in frames:
        url = (fr.url or "").lower()
        if any(k in url for k in ("/auth", "login", "signin", "identity")):
            diag["login_frame"] = url
            return fr
    diag["login_frame"] = "main"
    return page.main_frame


def _fill_and_submit_login(
    frame, username: str, password: str, diag: dict, timeout_ms: int
) -> None:
    """Kitölti a kifejezetten E-mail + Jelszó mezőt, majd beküldi az űrlapot. (Flight kódot ignoráljuk.)"""
    # E-mail (konkrét placeholder → label → role → szűk CSS fallback)
    email_priority = [
        frame.get_by_placeholder("E-mail cím"),
        frame.get_by_placeholder(re.compile(r"e-?mail", re.I)),
        frame.get_by_label(re.compile(r"e-?mail", re.I)),
        frame.get_by_role("textbox", name=re.compile(r"e-?mail", re.I)),
        frame.locator(
            "input[type='email'], input[type='text'][name*='mail' i], input[type='text'][id*='mail' i]"
        ),
    ]
    email_loc = None
    for loc in email_priority:
        try:
            if loc.count() > 0:
                loc.first.wait_for(state="visible", timeout=timeout_ms)
                email_loc = loc.first
                break
        except Exception:
            continue
    if not email_loc:
        diag["email_selector_used"] = False
        raise PWTimeout("Nem találtam látható E-mail mezőt.")
    email_loc.fill(username, timeout=timeout_ms)
    diag["email_selector_used"] = True

    # Jelszó
    password_priority = [
        frame.get_by_label(re.compile(r"jelszó|password", re.I)),
        frame.get_by_placeholder(re.compile(r"jelszó|password", re.I)),
        frame.get_by_role("textbox", name=re.compile(r"jelszó|password", re.I)),
        frame.locator(
            "input[type='password'], input[name*='pass' i], input[id*='pass' i]"
        ),
    ]
    pass_loc = None
    for loc in password_priority:
        try:
            if loc.count() > 0:
                loc.first.wait_for(state="visible", timeout=timeout_ms)
                pass_loc = loc.first
                break
        except Exception:
            continue
    if not pass_loc:
        diag["password_selector_used"] = False
        raise PWTimeout("Nem találtam látható Jelszó mezőt.")
    pass_loc.fill(password, timeout=timeout_ms)
    diag["password_selector_used"] = True

    # Submit (HU/EN variánsok)
    submitted = False
    for sel in [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Belépés')",
        "button:has-text('Bejelentkezés')",
        "button:has-text('Login')",
        "button:has-text('Sign in')",
    ]:
        btn = frame.locator(sel).first
        if btn.count() > 0:
            try:
                btn.wait_for(state="visible", timeout=int(timeout_ms / 2))
                btn.click(timeout=timeout_ms)
                diag["submit_selector"] = sel
                submitted = True
                break
            except Exception:
                continue
    if not submitted:
        try:
            pass_loc.press("Enter", timeout=timeout_ms)
            diag["submit_selector"] = "password_enter"
        except Exception:
            pass


def _looks_logged_in(page) -> bool:
    """Durva heurisztika: látszik-e belépett állapotú elem."""
    hints = [
        "Fiók",
        "Profil",
        "Kijelentkezés",
        "Kilépés",
        "Account",
        "Profile",
        "Sign out",
        "Logout",
    ]
    for h in hints:
        if page.get_by_text(re.compile(h, re.I)).count() > 0:
            return True
    return False


def playwright_fetch_hcp_sorted(
    login_url: str,
    hcp_url: str,
    username: str,
    password: str,
    diag: dict,
    headful: bool = False,
    slow_mo_ms: int = 0,
    timeout_ms: int = 45000,
    ignore_https_errors: bool = False,
) -> Tuple[pd.DataFrame, Optional[str]]:
    """
    Belépés Playwright-tel, majd CSAK a hcp_url oldal tartalmát töltjük le,
    kinyerjük az első táblát és dátum szerint sorba rendezzük.
    Visszatér: rendezett DataFrame + a választott dátumoszlop neve (ha volt).
    """
    snaps = ensure_snapshots_dir()
    with sync_playwright() as p:
        # Motor kiválasztása
        if APPROVED_ENGINE == "chromium":
            browser = p.chromium.launch(headless=not headful, slow_mo=slow_mo_ms)
        elif APPROVED_ENGINE == "webkit":
            browser = p.webkit.launch(headless=not headful, slow_mo=slow_mo_ms)
        elif APPROVED_ENGINE == "firefox":
            browser = p.firefox.launch(headless=not headful, slow_mo=slow_mo_ms)
        else:
            browser = p.chromium.launch(headless=not headful, slow_mo=slow_mo_ms)

        context = browser.new_context(
            locale="hu-HU",
            user_agent=APPROVED_UA,
            ignore_https_errors=ignore_https_errors,
        )
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)

        # Diagnosztika
        diag.update(
            {
                "engine": APPROVED_ENGINE,
                "user_agent": APPROVED_UA,
                "login_url": login_url,
                "hcp_url": hcp_url,
                "headful": headful,
                "slow_mo_ms": slow_mo_ms,
                "timeout_ms": timeout_ms,
            }
        )

        try:
            # 1) Login oldal (csak belépéshez)
            page.goto(login_url, wait_until="domcontentloaded")
            # Zscaler unsupported browser blokkolás korai detektálása
            try:
                title = page.title()
                html = page.content()
                if ("Zscaler" in title) or ("this web browser is not approved" in html):
                    diag["zscaler_block"] = {
                        "engine": APPROVED_ENGINE,
                        "user_agent": APPROVED_UA,
                        "url": page.url,
                    }
                    raise RuntimeError(
                        "Zscaler policy blokkolja a böngészőt/UA-t (unsupported browser)."
                    )
            except Exception:
                pass

            _try_accept_cookies(page, diag)
            try:
                page.wait_for_load_state("networkidle")
            except PWTimeout:
                pass

            # Nem mentünk login oldal tartalmat – csak belépünk
            frame = _select_frame_with_login(page, diag)
            _fill_and_submit_login(frame, username, password, diag, timeout_ms)

            # 2) HCP oldal – CSAK innen olvasunk
            page.goto(hcp_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle")
            except PWTimeout:
                pass

            diag["post_login_url"] = page.url
            login_ok = ("auth/login" not in page.url) or _looks_logged_in(page)
            diag["login_ok"] = bool(login_ok)

            if not login_ok:
                raise RuntimeError(
                    "Sikertelen bejelentkezés – visszairányított a belépő oldalra vagy nincs belépett állapot."
                )

            # HTML mentés csak a HCP oldalról (kérés szerint)
            html = page.content()
            save_html(os.path.join("snapshots", "hcp_playwright.html"), html)
            page.screenshot(path=os.path.join("snapshots", "hcp.png"), full_page=True)

            # Táblázat kinyerés + rendezés
            headers, rows = parse_first_table(html)
            if not rows:
                raise RuntimeError(
                    "A HCP oldalon nem találtam adatsort tartalmazó táblát."
                )

            df_sorted, dt_col = detect_and_sort_by_datetime(headers, rows)
            if dt_col:
                diag["sorted_by"] = dt_col
            else:
                diag["sorted_by"] = None

            browser.close()
            return df_sorted, dt_col

        except Exception as e:
            # Hiba esetén csak a hcp/error állapotot mentjük
            try:
                page.screenshot(
                    path=os.path.join("snapshots", "error.png"), full_page=True
                )
                save_html(os.path.join("snapshots", "error_dom.html"), page.content())
            except Exception:
                pass
            browser.close()
            raise e


# ============================ Streamlit UI ============================
st.set_page_config(page_title="HCP letöltő – Playwright", layout="wide")
st.title("HCP rekordok – böngészős (Playwright) letöltés — HCP oldalra korlátozva")

# Futtatási beállítások
col0, col1, col2, col3 = st.columns([1, 1, 1, 1.4])
with col0:
    headful = st.checkbox(
        "Mutasd a böngészőt (headful)",
        value=False,
        help="Ha bejelölöd, látod a Playwright böngészőablakát.",
    )
with col1:
    slow_mo_ms = st.number_input(
        "Lassítás (ms)",
        min_value=0,
        max_value=2000,
        value=0,
        step=100,
        help="Minden lépés közötti késleltetés diagnosztikához.",
    )
with col2:
    timeout_ms = st.number_input(
        "Időtúllépés (ms)", min_value=10000, max_value=120000, value=45000, step=5000
    )
with col3:
    ignore_https_errors = st.checkbox(
        "HTTPS hibák figyelmen kívül hagyása",
        value=False,
        help="Csak diagnosztikai célra, élesben ne használd!",
    )

# Kimeneti utak (mindkettő kötelezően készül)
default_db = os.path.join(os.getcwd(), "data", "hcp.sqlite")
default_csv = os.path.join(os.getcwd(), "data", "hcp_records_sorted.csv")
colA, colB = st.columns(2)
with colA:
    db_path = st.text_input("SQLite fájl (rendezett adatok)", default_db)
with colB:
    csv_path = st.text_input("CSV fájl (rendezett adatok)", default_csv)

st.divider()
btn_fetch = st.button("🔄 HCP letöltés + rendezés + mentés (CSV + SQLite)")

df: Optional[pd.DataFrame] = None
diagnostics: dict = {}

try:
    if btn_fetch:
        with st.spinner("Belépés és HCP adatok letöltése..."):
            df_sorted, dt_col = playwright_fetch_hcp_sorted(
                LOGIN_URL,
                HCP_URL,
                USER,
                PWD,
                diagnostics,
                headful=headful,
                slow_mo_ms=slow_mo_ms,
                timeout_ms=timeout_ms,
                ignore_https_errors=ignore_https_errors,
            )

            # Jelzés a bejelentkezésről
            post_login_url = diagnostics.get("post_login_url")
            if diagnostics.get("login_ok"):
                st.success(
                    f"✅ Sikeres bejelentkezés. Aktuális oldal: {post_login_url or 'ismeretlen'}"
                )
            else:
                if diagnostics.get("zscaler_block"):
                    st.error(
                        "❌ Sikertelen bejelentkezés – Zscaler böngésző policy blokkolta az oldalt."
                    )
                else:
                    st.error(
                        "❌ Sikertelen bejelentkezés – visszairányítás vagy belépett állapot hiánya."
                    )

            # Rendezett adatok kötelező mentése CSV + SQLite
            if df_sorted.empty:
                st.warning("Nem találtam táblázatot/adatsorokat a HCP oldalon.")
            else:
                if dt_col:
                    st.info(f"Rendezés dátum szerint: **{dt_col}** (legújabb elöl)")
                else:
                    st.warning(
                        "Nem találtam egyértelmű dátumoszlopot – az eredeti sorrendet tartom meg."
                    )

                # CSV mentés
                csv_bytes = df_sorted.to_csv(
                    index=False, sep=";", encoding="utf-8"
                ).encode("utf-8")
                os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
                with open(csv_path, "wb") as f:
                    f.write(csv_bytes)
                st.success(f"📁 CSV elmentve: `{csv_path}`")
                st.download_button(
                    "📥 CSV letöltése",
                    data=csv_bytes,
                    file_name=os.path.basename(csv_path),
                    mime="text/csv",
                )

                # SQLite mentés
                save_to_sqlite(df_sorted, db_path, table="hcp_records_sorted")
                st.success(
                    f"🗄️ SQLite elmentve: `{db_path}`, tábla: `hcp_records_sorted`"
                )

                df = df_sorted

    # Táblázat megjelenítés (ha van)
    if df is not None and not df.empty:
        st.subheader("Rendezett HCP táblázat")
        q = st.text_input("Szűrés (részszó):", "")
        if q:
            mask = pd.Series(False, index=df.index)
            for c in df.columns:
                mask = mask | df[c].astype(str).str.contains(q, case=False, na=False)
            st.dataframe(df[mask], use_container_width=True, height=480)
        else:
            st.dataframe(df, use_container_width=True, height=480)
    else:
        st.info("Még nincs megjeleníthető adat. Kattints a fenti gombra a letöltéshez.")

    if diagnostics:
        st.divider()
        st.subheader("Diagnosztika")
        st.code(json.dumps(diagnostics, indent=2, ensure_ascii=False))
        st.caption(
            "Snapshotok: ./snapshots/hcp_playwright.html, hcp.png, (hiba esetén) error.png/error_dom.html"
        )

except PWTimeout as e:
    st.error(
        "Playwright időtúllépés. Növeld az időkorlátot, kapcsold be a headful módot és/vagy a slow-mo-t, majd nézd a snapshotokat."
    )
    st.exception(e)
except PWError as e:
    st.error("Playwright hiba.")
    st.exception(e)
except Exception as e:
    st.error("Váratlan hiba.")
    st.exception(e)
