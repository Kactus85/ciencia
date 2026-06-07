#!/usr/bin/env python3
"""
Radar Científico — Impermeabilización (versión GRATIS, mensual)
Reúne papers e investigación reciente de tu rubro y los envía a
antonio@kactusempresa.cl con el mismo diseño "Briefing".

Fuentes (todas gratuitas, sin claves):
  - OpenAlex (API abierta de ~250M papers) por palabra clave y carril
  - Revistas con RSS abierto (MDPI Coatings/Materials/Buildings, ScienceDirect)
  - Google News RSS para industria (Sika, BASF...), IA-construcción y patentes
Traducción de resúmenes al español con deep-translator (Google Translate libre).
Resiliente: si una fuente o la traducción falla, la salta y el correo igual sale.

Sólo necesita los secrets de Gmail (los mismos que ya tienes):
  GMAIL_USER, GMAIL_APP_PASSWORD
"""

import os
import re
import html
import time
import json
import calendar
import smtplib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

import feedparser

try:
    from deep_translator import GoogleTranslator
    _HAS_TRANSLATOR = True
except Exception:
    _HAS_TRANSLATOR = False

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

TO_EMAIL = "antonio@kactusempresa.cl"

DIAS_ATRAS = 45          # ventana de "reciente" (mensual, con holgura)
POR_CARRIL = 3           # papers por carril
POR_EXTRA = 2            # ítems por fuente extra (industria/IA/patentes)
TRADUCIR = True          # traducir resúmenes al español (gratis)

# --- CARRILES ACADÉMICOS (búsquedas en OpenAlex) ---
# Cada carril: etiqueta, color y una búsqueda (palabras clave en inglés).
CARRILES = [
    {"label": "Materiales", "color": "#1f6b4a",
     "query": "waterproofing membrane self-healing concrete hydrophobic coating polyurethane polyurea"},
    {"label": "Métodos & diagnóstico", "color": "#1f3a5f",
     "query": "waterproofing injection leak detection concrete infrared thermography moisture non-destructive testing"},
    {"label": "IA & digital", "color": "#4b3a78",
     "query": "deep learning crack detection concrete computer vision building defect predictive maintenance waterproofing"},
    {"label": "Sostenibilidad & normativa", "color": "#2f6d6d",
     "query": "sustainable waterproofing durability life cycle assessment membrane green roof standards"},
]

# --- REVISTAS con RSS abierto (se mezclan en el carril Materiales/Métodos) ---
REVISTAS_RSS = [
    {"name": "Coatings (MDPI)", "url": "https://www.mdpi.com/rss/journal/coatings"},
    {"name": "Buildings (MDPI)", "url": "https://www.mdpi.com/rss/journal/buildings"},
    {"name": "Materials (MDPI)", "url": "https://www.mdpi.com/rss/journal/materials"},
]
REVISTAS_KEYWORDS = ["waterproof", "membrane", "coating", "concrete", "hydrophobic",
                     "moisture", "sealing", "durability", "crack", "polyurethane", "polyurea"]

# --- FUENTES EXTRA (Google News RSS — industria, IA, patentes) ---
# Google News RSS es gratis y estable. hl/gl/ceid = idioma/país.
def _gnews(q):
    base = "https://news.google.com/rss/search?q="
    return base + urllib.parse.quote(q) + "&hl=es-419&gl=CL&ceid=CL:es-419"

EXTRA_FEEDS = [
    {"label": "Industria & fabricantes", "color": "#8a6d2f",
     "url": _gnews('(Sika OR BASF OR "GCP Applied" OR Tremco) impermeabilización OR waterproofing')},
    {"label": "IA aplicada a la construcción", "color": "#8a6d2f",
     "url": _gnews('inteligencia artificial construcción OR "AI construction" grietas OR impermeabilización')},
    {"label": "Patentes del rubro", "color": "#8a6d2f",
     "url": _gnews('patente impermeabilización OR "waterproofing patent" OR "membrane patent"')},
]

# ============================================================================
# TRADUCCIÓN (gratis, resiliente)
# ============================================================================

_trad_cache = {}


def traducir_es(texto):
    if not texto or not TRADUCIR or not _HAS_TRANSLATOR:
        return texto
    if texto in _trad_cache:
        return _trad_cache[texto]
    try:
        # Google Translate (vía deep-translator) limita a ~5000 chars
        out = GoogleTranslator(source="auto", target="es").translate(texto[:4800])
        _trad_cache[texto] = out or texto
        return _trad_cache[texto]
    except Exception as e:
        print(f"    (traducción falló, queda en inglés: {e})")
        return texto

# ============================================================================
# OPENALEX (papers por palabra clave)
# ============================================================================

def _abstract_from_inverted(inv):
    """OpenAlex entrega el abstract como índice invertido; lo reconstruimos."""
    if not inv:
        return ""
    palabras = {}
    for palabra, posiciones in inv.items():
        for p in posiciones:
            palabras[p] = palabra
    return " ".join(palabras[i] for i in sorted(palabras))


def buscar_openalex(query, limite=POR_CARRIL):
    desde = (datetime.now(timezone.utc) - timedelta(days=DIAS_ATRAS)).strftime("%Y-%m-%d")
    params = {
        "search": query,
        "filter": f"from_publication_date:{desde},has_abstract:true",
        "sort": "relevance_score:desc",
        "per_page": str(limite * 3),
        "mailto": TO_EMAIL,  # buena práctica con OpenAlex
    }
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RadarCientifico/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"    OpenAlex falló: {e}")
        return []
    out = []
    for w in data.get("results", []):
        titulo = (w.get("title") or "").strip()
        if not titulo:
            continue
        abstract = _abstract_from_inverted(w.get("abstract_inverted_index"))
        if not abstract or len(abstract) < 60:
            continue
        venue = ""
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        venue = src.get("display_name") or ""
        oa = (w.get("open_access") or {}).get("is_oa")
        link = (w.get("primary_location") or {}).get("landing_page_url") or w.get("doi") or "#"
        out.append({
            "title": _limpiar(titulo, 160),
            "abstract": _limpiar(abstract, 300),
            "venue": venue or "OpenAlex",
            "year": str(w.get("publication_year") or ""),
            "tag": "Acceso abierto" if oa else "Resumen libre",
            "url": link,
        })
        if len(out) >= limite:
            break
    return out

# ============================================================================
# RSS (revistas + fuentes extra)
# ============================================================================

def _limpiar(t, n=300):
    if not t:
        return ""
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > n:
        t = t[:n].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    return t


def leer_rss(url, limite, keywords=None):
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"    RSS falló {url}: {e}")
        return []
    out = []
    for e in getattr(feed, "entries", []):
        titulo = _limpiar(e.get("title", ""), 160)
        if not titulo:
            continue
        resumen = _limpiar(e.get("summary", "") or e.get("description", ""), 300)
        if keywords:
            blob = (titulo + " " + resumen).lower()
            if not any(k in blob for k in keywords):
                continue
        out.append({
            "title": titulo,
            "abstract": resumen,
            "venue": _limpiar(getattr(feed.feed, "title", ""), 50) or "RSS",
            "year": str(datetime.now().year),
            "tag": "Resumen libre",
            "url": e.get("link", "#"),
        })
        if len(out) >= limite:
            break
    return out

# ============================================================================
# CORREO — diseño "Briefing"
# ============================================================================

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def _mes_es():
    s = datetime.now(ZoneInfo("America/Santiago")).strftime("%B %Y")
    repl = {'January': 'Enero', 'February': 'Febrero', 'March': 'Marzo', 'April': 'Abril',
            'May': 'Mayo', 'June': 'Junio', 'July': 'Julio', 'August': 'Agosto',
            'September': 'Septiembre', 'October': 'Octubre', 'November': 'Noviembre', 'December': 'Diciembre'}
    for en, es in repl.items():
        s = s.replace(en, es)
    return s


def _item(num, color, e):
    n = f"{num:02d}"
    meta = " &middot; ".join([x for x in (e.get("venue"), e.get("year"), e.get("tag")) if x])
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0;"><tr>'
        f'<td valign="top" width="48" style="font:300 24px/1 {SANS};color:#cfcfcf;padding:2px 14px 0 0;">{n}</td>'
        f'<td valign="top">'
        f'<div style="font:700 16px/1.36 {SANS};letter-spacing:-0.2px;color:#141414;margin:0 0 5px;">{e["title"]}</div>'
        f'<div style="font:14px/1.55 {SANS};color:#5a5a5a;margin:0 0 8px;">{e["es"]}</div>'
        f'<div style="font:12px/1.4 {SANS};color:#9a9a9a;">{meta}'
        f'&nbsp;&nbsp;&nbsp;<a href="{e["url"]}" style="font-weight:600;color:{color};text-decoration:none;">Leer &rarr;</a>'
        f'</div></td></tr></table>'
    )


def _bloque(label, color, items):
    if not items:
        return ""
    html_ = (
        f'<div style="margin:32px 0 4px;">'
        f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{color};"></span>'
        f'<span style="font:700 12px/1 {SANS};letter-spacing:2.5px;text-transform:uppercase;color:#111;margin-left:9px;vertical-align:2px;">{label}</span></div>'
    )
    for i, e in enumerate(items):
        html_ += _item(i + 1, color, e)
        if i < len(items) - 1:
            html_ += '<div style="border-top:1px solid #eee;margin:0 0 0 62px;"></div>'
    return html_


def construir_html(secciones, fuentes):
    periodo = _mes_es()
    body = "".join(_bloque(s["label"], s["color"], s["items"]) for s in secciones)
    total = sum(len(s["items"]) for s in secciones)
    intro = (f"{total} hallazgos del mes en materiales, métodos, IA y sostenibilidad "
             f"aplicados a la impermeabilización. Título original en inglés; resumen en español.")
    fuentes_txt = " &middot; ".join(fuentes) if fuentes else "—"
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Radar Científico</title></head>
<body style="margin:0;padding:0;background:#ffffff;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#ffffff;">
  <tr><td style="padding:34px 36px 20px;border-bottom:2px solid #111;">
    <table role="presentation" width="100%"><tr>
      <td valign="bottom">
        <div style="font:800 22px/1 {SANS};letter-spacing:-0.6px;color:#111;">Radar Científico</div>
        <div style="font:500 12px/1.4 {SANS};color:#8a8a86;margin:6px 0 0;">Impermeabilización · ciencia, materiales y tecnología</div>
      </td>
      <td valign="bottom" align="right"><div style="font:600 11px/1.4 {SANS};letter-spacing:1.5px;text-transform:uppercase;color:#999;">{periodo}<br><span style="color:#bbb;">Mensual</span></div></td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:14px 36px 0;"><div style="font:14px/1.5 {SANS};color:#6a6a66;">{intro}</div></td></tr>
  <tr><td style="padding:0 36px 28px;">{body}</td></tr>
  <tr><td style="border-top:2px solid #111;padding:22px 36px;">
    <div style="font:700 11px/1.4 {SANS};letter-spacing:1.5px;text-transform:uppercase;color:#111;">Radar Científico &middot; edición mensual</div>
    <div style="font:11px/1.7 {SANS};color:#9a9a9a;margin:8px 0 0;">Fuentes: {fuentes_txt}</div>
  </td></tr>
</table></body></html>"""

# ============================================================================
# ENVÍO
# ============================================================================

def enviar(asunto, html_content):
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        print("Faltan credenciales de Gmail (GMAIL_USER / GMAIL_APP_PASSWORD).")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"] = user
        msg["To"] = TO_EMAIL
        msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pw)
            s.sendmail(user, TO_EMAIL, msg.as_string())
        print(f"Correo enviado a {TO_EMAIL}")
        return True
    except Exception as e:
        print(f"Error enviando el correo: {e}")
        return False

# ============================================================================
# ENSAMBLAJE
# ============================================================================

def generar():
    print("Generando Radar Científico (mensual, gratis)...\n")
    fuentes_set, fuentes = set(), []

    def track(n):
        if n and n not in fuentes_set:
            fuentes_set.add(n); fuentes.append(n)

    secciones = []

    # Carriles académicos: OpenAlex + (para los 2 primeros) revistas RSS
    for idx, c in enumerate(CARRILES):
        print(f"  Carril: {c['label']}")
        items = buscar_openalex(c["query"], POR_CARRIL)
        # refuerzo con revistas RSS sólo en Materiales y Métodos
        if idx < 2 and len(items) < POR_CARRIL:
            for rev in REVISTAS_RSS:
                if len(items) >= POR_CARRIL:
                    break
                items += leer_rss(rev["url"], POR_CARRIL - len(items), REVISTAS_KEYWORDS)
        for e in items:
            track(e["venue"])
            e["es"] = traducir_es(e["abstract"])
        secciones.append({"label": c["label"], "color": c["color"], "items": items})

    # Fuentes extra (Google News): ya vienen en español, no se traducen
    for ex in EXTRA_FEEDS:
        print(f"  Extra: {ex['label']}")
        items = leer_rss(ex["url"], POR_EXTRA)
        for e in items:
            e["es"] = e["abstract"]
            e["tag"] = "Industria/Patente"
        track("Google News")
        # agrupamos todas las extra en una sola sección al final
        ex["_items"] = items

    extra_items = []
    for ex in EXTRA_FEEDS:
        extra_items += ex.get("_items", [])
    if extra_items:
        secciones.append({"label": "Industria & Patentes", "color": "#8a6d2f", "items": extra_items})

    total = sum(len(s["items"]) for s in secciones)
    print(f"\nTotal de ítems: {total}")
    html_content = construir_html(secciones, fuentes)

    with open("radar_cientifico.html", "w", encoding="utf-8") as fh:
        fh.write(html_content)

    if total == 0:
        print("Ninguna fuente entregó resultados. No se envía correo.")
        return

    print("Enviando...")
    enviar(f"Radar Científico — Impermeabilización · {_mes_es()}", html_content)


if __name__ == "__main__":
    generar()
