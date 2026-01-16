# ==========================================================================================
# golfigo.py – TELJES VÉGLEGES VERZIÓ
# ==========================================================================================
# Funkciók:
#   ✔ GOLFiGO bejelentkezés – stabil input-event alapú Vue/Nuxt kompatibilis login
#   ✔ Flight kód mező figyelmen kívül hagyása (csak Email + Jelszó)
#   ✔ HCP rekordok letöltése (KIZÁRÓLAG a /hcp-records oldalról)
#   ✔ Táblázat kinyerése, dátum szerinti rendezés
#   ✔ Mentés kötelezően: CSV + SQLite
#   ✔ Streamlit UI + visszajelzések
#   ✔ Secrets ellenőrzése indulás előtt
# ==========================================================================================

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

# ==========================================================================================
# 1) SECRETS.TOML ELLENŐRZÉSE (kötelező)
# ==========================================================================================
required_keys = ["GOLFIGO_USER", "GOLFIGO_PASSWORD", "LOGIN_URL", "HCP_URL"]
missing_keys = [k for k in required_keys if k not in st.secrets]

if missing_keys:
    st.error(f"❌ Hiányzó kulcs(ok) a secrets.toml-ban: {', '.join(missing_keys)}")
    st.stop()
else:
    st.success("✅ Minden szükséges kulcs megvan a secrets.toml-ban.")


# ==========================================================================================
# 2) SECRETS + BEÁLLÍTÁSOK
# ==========================================================================================

USER = st.secrets["GOLFIGO_USER"]
PWD = st.secrets["GOLFIGO_PASSWORD"]

LOGIN_URL = st.secrets["LOGIN_URL"]
HCP_URL = st.secrets["HCP_URL"]

DEFAULT_EDGE_UA_143 = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)

APPROVED_UA = st.secrets.get("APPROVED_UA", DEFAULT_EDGE_UA_143)
APPROVED_ENGINE = st.secrets.get("APPROVED_ENGINE", "chromium").lower()


# ==========================================================================================
# 3) SEGÉDFÜGGVÉNYEK
# ==========================================================================================


def ensure_snapshots_dir() -> str:
    os.makedirs("snapshots", exist_ok=True)
    return "snapshots"


def save_html(path: str, html: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def parse_first_table(html: str) -> Tuple[List[str], List[List[str]]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")

    if not table:
        rows = []
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
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        rows.append([td.get_text(strip=True).replace("\n", " ") for td in tds])

    if not headers and rows:
        n = max(len(r) for r in rows)
        headers = [f"col_{i+1}" for i in range(n)]
        rows = [r + [""] * (n - len(r)) for r in rows]

    return headers, rows


def detect_and_sort_by_datetime(headers: List[str], rows: List[List[str]]):
    if not headers or not rows:
        return pd.DataFrame(), None

    df = pd.DataFrame(rows, columns=headers)

    def clean(s):
        return s.astype(str).str.strip().str.replace(r"\.$", "", regex=True)

    best_col = None
    best_ok = -1
    parsed_cache = {}

    for col in df.columns:
        c = clean(df[col])
        p = pd.to_datetime(c, errors="coerce", dayfirst=True)
        ok = p.notna().sum()
        parsed_cache[col] = p
        if ok > best_ok:
            best_ok = ok
            best_col = col

    if best_ok <= 0:
        return df, None

    df["_dt"] = parsed_cache[best_col]
    df = df.sort_values("_dt", ascending=False).drop(columns=["_dt"])
    return df, best_col


def save_to_sqlite(df: pd.DataFrame, db_path: str, table="hcp_records_sorted") -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    df.to_sql(table, conn, if_exists="replace", index=False)
    conn.close()


# ==========================================================================================
# 4) LOGIN (EMAIL + PASSWORD) — *MŰKÖDŐ, EVENT‑ALAPÚ*
# ==========================================================================================


def _try_accept_cookies(page, timeout_ms=5000):
    for sel in [
        "#onetrust-accept-btn-handler",
        "button:has-text('Elfogadom')",
        "button:has-text('Rendben')",
        "button:has-text('Összes elfogadása')",
        "button:has-text('Accept')",
    ]:
        btn = page.locator(sel)
        if btn.count() > 0:
            try:
                btn.first.click(timeout=timeout_ms)
                page.wait_for_timeout(200)
            except:
                pass


def _stable_login(frame, username, password, timeout_ms, diag):

    # ========== EMAIL FIELD ==========
    email_loc = frame.get_by_placeholder("E-mail cím")
    email_loc.wait_for(state="visible", timeout=timeout_ms)
    email_loc.click()
    email_loc.fill(username)
    email_loc.dispatch_event("input")
    email_loc.press("Tab")

    diag["email_event_triggered"] = True

    # ========== PASSWORD FIELD ==========
    pass_loc = frame.locator("input[type='password']")
    pass_loc.wait_for(state="visible", timeout=timeout_ms)
    pass_loc.click()
    pass_loc.fill(password)
    pass_loc.dispatch_event("input")
    pass_loc.press("Tab")

    diag["password_event_triggered"] = True

    # ========== SUBMIT BUTTON ==========
    submit = frame.get_by_role(
        "button", name=re.compile("Belépés|Bejelentkezés|Login", re.I)
    )
    submit.wait_for(state="visible", timeout=timeout_ms)

    # Várjuk meg amíg engedélyezett lesz
    for _ in range(30):
        if submit.is_enabled():
            break
        frame.wait_for_timeout(100)

    if not submit.is_enabled():
        raise RuntimeError(
            "A BELÉPÉS gomb nem aktiválódott — valószínűleg a JS validáció nem futott le."
        )

    submit.click()
    diag["submit_clicked"] = True


# ==========================================================================================
# 5) PLAYWRIGHT FLOW — LOGIN + HCP LETÖLTÉS
# ==========================================================================================


def playwright_fetch_hcp_sorted(
    login_url,
    hcp_url,
    username,
    password,
    diag,
    headful=False,
    slow_mo_ms=0,
    timeout_ms=45000,
    ignore_https_errors=False,
):

    snaps = ensure_snapshots_dir()

    with sync_playwright() as p:

        browser = getattr(p, APPROVED_ENGINE).launch(
            headless=not headful, slow_mo=slow_mo_ms
        )

        context = browser.new_context(
            locale="hu-HU",
            user_agent=APPROVED_UA,
            ignore_https_errors=ignore_https_errors,
        )

        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        # --- LOGIN ---
        page.goto(login_url, wait_until="domcontentloaded")
        _try_accept_cookies(page)

        frame = page.main_frame
        _stable_login(frame, username, password, timeout_ms, diag)

        # --- HCP PAGE ---
        page.goto(hcp_url, wait_until="domcontentloaded")

        html = page.content()
        save_html("snapshots/hcp_playwright.html", html)
        page.screenshot(path="snapshots/hcp.png", full_page=True)

        headers, rows = parse_first_table(html)
        if not rows:
            raise RuntimeError("A HCP oldalon nem találtam adatot.")

        df_sorted, dt_col = detect_and_sort_by_datetime(headers, rows)

        browser.close()
        return df_sorted, dt_col


# ==========================================================================================
# STREAMLIT UI
# ==========================================================================================

st.title("🏌️‍♂️ GOLFiGO HCP Letöltés – Bejelentkezés + Rendezés + Mentés")

col0, col1, col2 = st.columns(3)
headful = col0.checkbox("Mutasd a böngészőt", value=False)
slow_mo_ms = col1.number_input("Lassítás (ms)", 0, 2000, 0, 100)
timeout_ms = col2.number_input("Időtúllépés (ms)", 10000, 120000, 45000, 5000)

default_db = os.path.join("data", "hcp.sqlite")
default_csv = os.path.join("data", "hcp_records_sorted.csv")

colA, colB = st.columns(2)
db_path = colA.text_input("SQLite fájl", default_db)
csv_path = colB.text_input("CSV fájl", default_csv)

if st.button("🔄 HCP letöltés és mentés (CSV + SQLite)"):

    diagnostics = {}

    try:
        df_sorted, dt_col = playwright_fetch_hcp_sorted(
            LOGIN_URL,
            HCP_URL,
            USER,
            PWD,
            diagnostics,
            headful=headful,
            slow_mo_ms=slow_mo_ms,
            timeout_ms=timeout_ms,
        )

        if dt_col:
            st.success(f"📅 Rendezve dátum szerint: **{dt_col}**")
        else:
            st.warning("Nem találtam egyértelmű dátumoszlopot — eredeti sorrend marad.")

        # MENTÉS CSV
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
        df_sorted.to_csv(csv_path, sep=";", index=False, encoding="utf-8")
        st.success(f"CSV mentve: {csv_path}")

        st.download_button(
            "📥 CSV letöltése",
            df_sorted.to_csv(index=False, sep=";").encode("utf-8"),
            file_name=os.path.basename(csv_path),
            mime="text/csv",
        )

        # MENTÉS SQLITE
        save_to_sqlite(df_sorted, db_path)
        st.success(f"SQLite mentve: {db_path}")

        # TÁBLÁZAT KIÍRÁSA
        st.dataframe(df_sorted, use_container_width=True, height=450)

    except Exception as e:
        st.error("❌ Hiba történt a feldolgozás során.")
        st.exception(e)
