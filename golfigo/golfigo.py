from __future__ import annotations
import os
import io
import csv
import json
import re
import sqlite3
from typing import List, Tuple, Optional, Any

import streamlit as st
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PWTimeout,
    Error as PWError,
)

# ==========================================================================================
# 1) TITKOK BETÖLTÉSE – Streamlit secrets + környezeti változó fallback
# ==========================================================================================
REQUIRED_KEYS = ["GOLFIGO_USER", "GOLFIGO_PASSWORD", "LOGIN_URL", "HCP_URL"]


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    # 1) Streamlit secrets
    if name in st.secrets:
        return st.secrets[name]
    # 2) Környezeti változó
    return os.environ.get(name, default)


missing = [k for k in REQUIRED_KEYS if not get_secret(k)]
if missing:
    st.error(
        "❌ Hiányzó kulcs(ok) a titkok közül (sem Streamlit secrets, sem környezeti változó): "
        + ", ".join(missing)
        + "\n\nEllenőrizd, hogy a ./.streamlit/secrets.toml létezik-e és jó-e a formátum,"
        " vagy add meg ezeket környezeti változóként."
    )
    st.stop()
else:
    st.success("✅ Titkok betöltve (Streamlit secrets vagy környezeti változók).")

# ==========================================================================================
# 2) BEÁLLÍTÁSOK
# ==========================================================================================
USER = get_secret("GOLFIGO_USER")
PWD = get_secret("GOLFIGO_PASSWORD")
LOGIN_URL = get_secret("LOGIN_URL")
HCP_URL = get_secret("HCP_URL")

DEFAULT_EDGE_UA_143 = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)
APPROVED_UA = get_secret("APPROVED_UA", DEFAULT_EDGE_UA_143)
APPROVED_ENGINE = (get_secret("APPROVED_ENGINE", "chromium") or "chromium").lower()

# ==========================================================================================
# 3) SEGÉDFÜGGVÉNYEK – mentés, parse, rendezés, SQLite
# ==========================================================================================


def save_html(path: str, html: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def parse_first_table(html: str) -> Tuple[List[str], List[List[str]]]:
    """
    Klasszikus <table> parser, thead/thead nélküli esetekre is.
    Ha nincs <table>, még megpróbál sima <tr><td> sorokat keresni.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")

    if not table:
        # FALLBACK: <tr><td> a DOM-ban (fejléc nélkül)
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


def parse_thead_and_rows_without_table(html: str) -> Tuple[List[str], List[List[str]]]:
    """
    Általános parser olyan oldalakhoz, ahol a fejlécek <thead><th>...</th></thead> alatt vannak,
    a sorok pedig <tr><td>...</td></tr> formában, de nincs <table> wrapper.
    """
    soup = BeautifulSoup(html, "html.parser")

    thead = soup.find("thead")
    headers: List[str] = []
    if thead:
        headers = [
            th.get_text(strip=True)
            for th in thead.find_all("th")
            if th.get_text(strip=True)
        ]

    rows: List[List[str]] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        row = [td.get_text(strip=True).replace("\n", " ") for td in tds]
        if row:
            rows.append(row)

    if not rows:
        return [], []

    if not headers:
        n = max(len(r) for r in rows)
        headers = [f"col_{i+1}" for i in range(n)]

    width = max(len(headers), max(len(r) for r in rows))
    if len(headers) < width:
        headers = headers + [f"col_{i+1}" for i in range(len(headers) + 1, width + 1)]
    rows = [r + [""] * (width - len(r)) for r in rows]

    return headers, rows


def parse_vue_data_block(
    html: str, data_attr: str = "data-v-4ddb4b4e"
) -> Tuple[List[str], List[List[str]]]:
    """
    Célzott parser: <thead data-v-XXXX><th>..</th></thead> + <tr data-v-XXXX><td>..</td></tr>
    TABLE wrapper nélkül. A 'data_attr' alapértelmezésben: data-v-4ddb4b4e.
    """
    soup = BeautifulSoup(html, "html.parser")

    thead = soup.find("thead", attrs={data_attr: True})
    headers: List[str] = []
    if thead:
        headers = [
            th.get_text(strip=True)
            for th in thead.find_all("th")
            if th.get_text(strip=True)
        ]

    rows: List[List[str]] = []
    for tr in soup.find_all("tr", attrs={data_attr: True}):
        tds = tr.find_all("td")
        if not tds:
            continue
        row = [td.get_text(strip=True).replace("\n", " ") for td in tds]
        if row:
            rows.append(row)

    if not rows:
        return [], []

    if not headers:
        n = max(len(r) for r in rows)
        headers = [f"col_{i+1}" for i in range(n)]

    width = max(len(headers), max(len(r) for r in rows))
    if len(headers) < width:
        headers = headers + [f"col_{i+1}" for i in range(len(headers) + 1, width + 1)]
    rows = [r + [""] * (width - len(r)) for r in rows]

    return headers, rows


def extract_thead_tr_from_dom(frame) -> Tuple[List[str], List[List[str]]]:
    """
    Közvetlenül a renderelt DOM-ból veszi ki a thead th és tr td szövegeket,
    TABLE wrapper nélkül is működik. Ha nincs adat, üres listákkal tér vissza.
    """
    res = frame.evaluate(
        """
        () => {
            const sanitize = s => (s || '').replace(/\s+/g, ' ').trim();

            const ths = Array.from(document.querySelectorAll('thead th')).map(th => sanitize(th.textContent));
            const rows = [];
            for (const tr of Array.from(document.querySelectorAll('tr'))) {
                const tds = Array.from(tr.querySelectorAll('td')).map(td => sanitize(td.textContent));
                if (tds.length) rows.push(tds);
            }
            return { headers: ths, rows };
        }
        """
    )
    headers = res.get("headers") or []
    rows = res.get("rows") or []
    if not rows:
        return [], []
    if not headers:
        n = max(len(r) for r in rows)
        headers = [f"col_{i+1}" for i in range(n)]
    width = max(len(headers), max(len(r) for r in rows))
    if len(headers) < width:
        headers += [f"col_{i+1}" for i in range(len(headers) + 1, width + 1)]
    rows = [r + [""] * (width - len(r)) for r in rows]
    return headers, rows


def detect_and_sort_by_datetime(headers: List[str], rows: List[List[str]]):
    """
    Megpróbálja automatikusan felismerni a legjobb dátumoszlopot és csökkenő sorrendbe rendez.
    Ha nem talál értelmes dátumot, változatlan sorrendet ad vissza (None jelzéssel).
    """
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
# 4) Nuxt JSON + DOM-grid kinyerés + hálózati JSON (Playwright)
# ==========================================================================================


def try_extract_hcp_from_nuxt_json(page) -> Tuple[List[str], List[List[str]]]:
    candidate = page.evaluate(
        """
        () => {
            const root = window.__NUXT__ || window.$nuxt || null;
            if (!root) return null;

            const buckets = [];
            const pushIfList = (x, label) => {
                if (Array.isArray(x) && x.length) buckets.push({label, data: x});
            };

            try {
                if (root.state) {
                    for (const [k, v] of Object.entries(root.state)) pushIfList(v, 'state.'+k);
                }
                if (root.data) {
                    const d = Array.isArray(root.data) ? root.data : [root.data];
                    d.forEach((obj, i) => {
                        if (obj && typeof obj === 'object') {
                            for (const [k, v] of Object.entries(obj)) pushIfList(v, `data[${i}].${k}`);
                        }
                    });
                }
                if (root.payload && typeof root.payload === 'object') {
                    for (const [k, v] of Object.entries(root.payload)) {
                        if (Array.isArray(v)) pushIfList(v, 'payload.'+k);
                        if (v && typeof v === 'object' && Array.isArray(v.data)) pushIfList(v.data, 'payload.data');
                    }
                }
            } catch (e) {}

            const scored = buckets
              .map(b => {
                const objCnt = b.data.filter(x => x && typeof x === 'object' && !Array.isArray(x)).length;
                return {label: b.label, data: b.data, score: objCnt * 2 + b.data.length};
              })
              .sort((a, b) => b.score - a.score);

            return scored.length ? scored[0] : null;
        }
        """
    )
    if not candidate or not candidate.get("data"):
        return [], []

    rows = candidate["data"]

    keys: List[str] = []
    seen = set()
    for r in rows:
        if isinstance(r, dict):
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    keys.append(k)

    if not keys:
        headers = ["value"]
        data = [[str(x).strip()] for x in rows]
        return headers, data

    headers = keys
    norm_rows: List[List[str]] = []
    for r in rows:
        if isinstance(r, dict):
            norm_rows.append([str(r.get(k, "")).strip() for k in headers])
        else:
            norm_rows.append([str(r).strip()] + [""] * (len(headers) - 1))
    return headers, norm_rows


def try_extract_grid_like_from_dom(frame) -> Tuple[List[str], List[List[str]]]:
    result = frame.evaluate(
        """
        () => {
            const sanitize = s => (s || '').replace(/\s+/g, ' ').trim();

            const byRole = [];
            const rowNodes = Array.from(document.querySelectorAll('[role="row"]'))
                .filter(r => r.querySelector('[role="cell"]'));
            if (rowNodes.length) {
                for (const r of rowNodes) {
                    const cells = Array.from(r.querySelectorAll('[role="cell"]')).map(td => sanitize(td.textContent));
                    if (cells.length) byRole.push(cells);
                }
            }

            const byVTable = [];
            const vtables = Array.from(document.querySelectorAll('.v-data-table, .v-table, .v-data-table__wrapper'));
            for (const t of vtables) {
                const rows = Array.from(t.querySelectorAll('tbody tr, .v-data-table__tr, .v-table__tr'));
                for (const r of rows) {
                    const cells = Array.from(r.querySelectorAll('td, .v-data-table__td, .v-table__td')).map(td => sanitize(td.textContent));
                    if (cells.length) byVTable.push(cells);
                }
            }

            const byList = [];
            const listItems = Array.from(document.querySelectorAll('.v-list .v-list-item, .v-list-item'));
            if (listItems.length) {
                for (const li of listItems) {
                    const cols = Array.from(li.querySelectorAll('.v-list-item__content, .v-list-item__title, .v-list-item__subtitle, .v-col, .col'))
                        .map(n => sanitize(n.textContent))
                        .filter(Boolean);
                    if (cols.length) byList.push(cols);
                }
            }

            return { byRole, byVTable, byList };
        }
        """
    )

    candidates = []
    for label in ["byRole", "byVTable", "byList"]:
        mat = result.get(label) or []
        if not mat:
            continue
        rows_ok = [r for r in mat if isinstance(r, list) and len(r) >= 2]
        if len(rows_ok) >= 2:
            widest = max(len(r) for r in rows_ok)
            score = len(rows_ok) * 10 + widest
            candidates.append((label, mat, score, widest))

    if not candidates:
        return [], []

    label, mat, _, widest = sorted(candidates, key=lambda x: x[2], reverse=True)[0]
    headers = [f"col_{i+1}" for i in range(widest)]
    norm_rows: List[List[str]] = []
    for r in mat:
        if not isinstance(r, list):
            continue
        row = r + [""] * (widest - len(r))
        norm_rows.append(row[:widest])
    return headers, norm_rows


def harvest_arrays_from_json(obj: Any) -> List[List[dict]]:
    """Rekurzívan kigyűjt minden olyan listát, amely objektumokat tartalmaz."""
    found: List[List[dict]] = []

    def _walk(x: Any):
        if isinstance(x, dict):
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            # ha a lista nagy része dict, vegyük fel
            dict_items = [e for e in x if isinstance(e, dict)]
            if dict_items and len(dict_items) >= max(2, int(0.5 * len(x))):
                found.append(dict_items)
            for v in x:
                _walk(v)

    _walk(obj)
    return found


def try_extract_from_captured_json(
    json_payloads: List[Any],
) -> Tuple[List[str], List[List[str]]]:
    """Megpróbál kinyerni egy releváns táblát a rögzített JSON válaszokból."""
    best_headers: List[str] = []
    best_rows: List[List[str]] = []
    best_score = -1
    for payload in json_payloads:
        candidates = harvest_arrays_from_json(payload)
        for arr in candidates:
            # kulcs-union
            keys: List[str] = []
            seen = set()
            for r in arr:
                for k in r.keys():
                    if k not in seen:
                        seen.add(k)
                        keys.append(k)
            if not keys:
                continue
            rows = []
            for r in arr:
                rows.append([str(r.get(k, "")).strip() for k in keys])
            # score: több sor + több oszlop jobb
            score = len(rows) * 10 + len(keys)
            if score > best_score:
                best_score = score
                best_headers = keys
                best_rows = rows
    return best_headers, best_rows


# ==========================================================================================
# 5) LOGIN + HCP LETÖLTÉS (várakozások + DOM diagnosztika + hálózati napló)
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

        # --- HÁLÓZATI NAPLÓZÁS JSON-hoz ---
        captured_json: List[Any] = []

        def on_response(resp):
            try:
                ctype = (resp.headers.get("content-type") or "").lower()
                url = resp.url.lower()
                if ("json" in ctype) and any(
                    k in url for k in ["hcp", "handicap", "record", "score", "results"]
                ):
                    try:
                        data = resp.json()
                    except Exception:
                        try:
                            data = json.loads(resp.text())
                        except Exception:
                            data = None
                    if data is not None:
                        captured_json.append({"url": resp.url, "data": data})
            except Exception:
                pass

        context.on("response", on_response)

        # --- LOGIN ---
        page.goto(login_url, wait_until="domcontentloaded")
        # Cookiek
        for sel in [
            "#onetrust-accept-btn-handler",
            "button:has-text('Elfogadom')",
            "button:has-text('Rendben')",
            "button:has-text('Összes elfogadása')",
            "button:has-text('Accept')",
        ]:
            try:
                if page.locator(sel).count() > 0:
                    page.locator(sel).first.click(timeout=2500)
                    page.wait_for_timeout(200)
            except:
                pass

        frame = page.main_frame
        # Stabil login
        email_loc = frame.get_by_placeholder("E-mail cím")
        email_loc.wait_for(state="visible", timeout=timeout_ms)
        email_loc.click()
        email_loc.fill(username)
        email_loc.dispatch_event("input")
        email_loc.press("Tab")
        pass_loc = frame.locator("input[type='password']")
        pass_loc.wait_for(state="visible", timeout=timeout_ms)
        pass_loc.click()
        pass_loc.fill(password)
        pass_loc.dispatch_event("input")
        pass_loc.press("Tab")
        submit = frame.get_by_role(
            "button", name=re.compile("Belépés|Bejelentkezés|Login", re.I)
        )
        submit.wait_for(state="visible", timeout=timeout_ms)
        for _ in range(30):
            if submit.is_enabled():
                break
            frame.wait_for_timeout(100)
        if not submit.is_enabled():
            browser.close()
            raise RuntimeError(
                "A BELÉPÉS gomb nem aktiválódott — lehet, hogy a validáció nem futott le."
            )
        submit.click()

        # --- HCP PAGE ---
        page.goto(hcp_url, wait_until="domcontentloaded")
        # Várjunk a hálózat elcsendesedésére + fejléc megjelenésére
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except:
            page.wait_for_timeout(500)
        try:
            page.wait_for_selector("thead th:has-text('Előző HCP')", timeout=5000)
        except:
            page.wait_for_timeout(1000)

        # Tab aktiválás, ha van
        try:
            tab = page.get_by_role(
                "tab", name=re.compile("HCP|Eredmény|Eredmények", re.I)
            )
            if tab and tab.is_visible():
                tab.click()
                page.wait_for_timeout(600)
        except:
            pass

        # Görgetés lazy-load miatt
        try:
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(400)
        except:
            pass

        # Load more gombok
        try:
            while True:
                btn = page.get_by_role(
                    "button", name=re.compile("Tovább|Több|Load more", re.I)
                )
                if not btn or not btn.is_visible():
                    break
                btn.click()
                page.wait_for_timeout(700)
        except:
            pass

        # DIAG FŐ FRAME-BEN
        diag_counts = page.evaluate(
            """
            () => {
                const q = sel => Array.from(document.querySelectorAll(sel));
                const c = sel => q(sel).length;
                return {
                    frames: window.frames?.length || 0,
                    thead_th: c('thead th'),
                    tr: c('tr'),
                    td: c('td'),
                    data_v_head: c('thead[data-v-4ddb4b4e] th'),
                    data_v_tr: c('tr[data-v-4ddb4b4e]'),
                    aria_rows: c('[role="row"]'),
                    aria_cells: c('[role="cell"]'),
                    vtable: c('.v-data-table, .v-table, .v-data-table__wrapper'),
                    time: new Date().toISOString()
                };
            }
            """
        )
        diag["dom_probe_main"] = diag_counts

        # Snapshotok (HTML debug; lehet, hogy nem tükrözi a renderelt DOM-ot)
        html = page.content()
        save_html("hcp_playwright.html", html)
        page.screenshot(path="hcp.png", full_page=True)

        # 1) Nuxt JSON a fő frame-ben
        headers, rows = try_extract_hcp_from_nuxt_json(page)

        # 1/b) Közvetlen DOM thead/tr kiolvasás (fő frame)
        if not rows:
            h_dom, r_dom = extract_thead_tr_from_dom(page)
            if r_dom:
                headers, rows = h_dom, r_dom

        # 1/c) Keretek (iframe-ek) bejárása – amelyikben több td van, ott próbálkozunk
        if not rows and len(page.frames) > 1:
            best = (None, 0)
            for fr in page.frames:
                try:
                    cnt = fr.evaluate("() => document.querySelectorAll('td').length")
                except Exception:
                    cnt = 0
                if cnt and cnt > best[1]:
                    best = (fr, cnt)
            if best[0] is not None:
                fr = best[0]
                h_dom2, r_dom2 = extract_thead_tr_from_dom(fr)
                if r_dom2:
                    headers, rows = h_dom2, r_dom2
                else:
                    # DOM-grid próbálkozás frame-ben
                    h_grid2, r_grid2 = try_extract_grid_like_from_dom(fr)
                    if r_grid2:
                        headers, rows = h_grid2, r_grid2

        # 2) DOM-grid (fő frame)
        if not rows:
            h2, r2 = try_extract_grid_like_from_dom(page)
            if r2:
                headers, rows = h2, r2

        # 3) CÉLZOTT: data-v-4ddb4b4e (HTML-ből)
        if not rows:
            h_v, r_v = parse_vue_data_block(html, data_attr="data-v-4ddb4b4e")
            if r_v:
                headers, rows = h_v, r_v

        # 4) ÁLTALÁNOS: thead + tr/td (HTML-ből)
        if not rows:
            h3, r3 = parse_thead_and_rows_without_table(html)
            if r3:
                headers, rows = h3, r3

        # 5) KLASSZIKUS: <table> parser (HTML-ből)
        if not rows:
            headers, rows = parse_first_table(html)

        # 6) HÁLÓZATI JSON – utolsó mentsvár: próbáljuk a rögzített JSON-ból
        if not rows and captured_json:
            # írása fájlba diagnosztikához
            try:
                with open("network_log.json", "w", encoding="utf-8") as f:
                    json.dump(captured_json, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            # próbáljuk kinyerni
            only_payloads = [c.get("data") for c in captured_json if "data" in c]
            hJ, rJ = try_extract_from_captured_json(only_payloads)
            if rJ:
                headers, rows = hJ, rJ

        if not rows:
            browser.close()
            raise RuntimeError(
                "A HCP oldalon nem találtam adatsort tartalmazó táblát. "
                f"Main DOM: thead_th={diag.get('dom_probe_main',{}).get('thead_th')}, "
                f"tr={diag.get('dom_probe_main',{}).get('tr')}, td={diag.get('dom_probe_main',{}).get('td')}. "
                f"Nézd meg a hcp.png képernyőmentést és a network_log.json fájlt (ha létrejött)."
            )

        df_sorted, dt_col = detect_and_sort_by_datetime(headers, rows)
        browser.close()
        return df_sorted, dt_col, diag


# ==========================================================================================
# 6) STREAMLIT UI
# ==========================================================================================

st.title("🏌️‍♂️ GOLFiGO HCP Letöltés – Bejelentkezés + Rendezés + Mentés")

col0, col1, col2 = st.columns(3)
headful = col0.checkbox("Mutasd a böngészőt", value=False)
slow_mo_ms = col1.number_input("Lassítás (ms)", 0, 2000, 0, 100)
timeout_ms = col2.number_input("Időtúllépés (ms)", 10000, 120000, 45000, 5000)

# Mentési helyek: a golfigo.py mappájába
default_db = os.path.join(".", "hcp.sqlite")
default_csv = os.path.join(".", "hcp_records_sorted.csv")

colA, colB = st.columns(2)
db_path = colA.text_input("SQLite fájl", default_db)
csv_path = colB.text_input("CSV fájl", default_csv)

if st.button("🔄 HCP letöltés és mentés (CSV + SQLite)"):
    diagnostics = {}
    try:
        df_sorted, dt_col, diagnostics = playwright_fetch_hcp_sorted(
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

        # MENTÉS CSV – golfigo.py mappába
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
        df_sorted.to_csv(csv_path, sep=";", index=False, encoding="utf-8")
        st.success(f"CSV mentve: {csv_path}")
        st.download_button(
            "📥 CSV letöltése",
            df_sorted.to_csv(index=False, sep=";").encode("utf-8"),
            file_name=os.path.basename(csv_path),
            mime="text/csv",
        )

        # MENTÉS SQLITE – golfigo.py mappába
        save_to_sqlite(df_sorted, db_path)
        st.success(f"SQLite mentve: {db_path}")

        # TÁBLÁZAT KIÍRÁSA
        st.dataframe(df_sorted, use_container_width=True, height=450)

        # DIAGNOSZTIKA KIÍRÁSA
        st.caption("🔎 DOM diagnosztika (fő frame számlálók):")
        st.json(diagnostics.get("dom_probe_main", {}))

        # Ha készült hálózati napló, jelezzük
        if os.path.exists("network_log.json"):
            st.info(
                "📁 Hálózati napló elmentve: network_log.json (a golfigo.py mappában)"
            )

    except Exception as e:
        st.error("❌ Hiba történt a feldolgozás során.")
        st.exception(e)
