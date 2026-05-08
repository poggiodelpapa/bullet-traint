import os
import json
import smtplib
import ssl
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

# ─── Configurazione ────────────────────────────────────────────────────────────

ORIGINE           = "VENEZIA MESTRE"
DESTINAZIONE      = "ROMA TERMINI"
DATA_VIAGGIO      = "30/07/2025"
ORA_DA            = 9    # partenze dalle 09:00
ORA_A             = 15   # fino alle 15:00
SOLO_FRECCIAROSSA = True

EMAIL_MITTENTE     = os.environ["EMAIL_MITTENTE"]
EMAIL_DESTINATARIO = os.environ["EMAIL_DESTINATARIO"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
FORCE_EMAIL        = os.environ.get("FORCE_EMAIL", "false").lower() == "true"

SENDER_NAME = "Monitor Frecciarossa"


# ─── Sessione con cookie (necessaria per l'API) ────────────────────────────────

def crea_sessione():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.lefrecce.it/",
    })
    try:
        session.get("https://www.lefrecce.it/", timeout=15)
        print("✅ Sessione inizializzata")
    except Exception as e:
        print(f"⚠️  Sessione non inizializzata: {e}")
    return session


# ─── Chiamata API Trenitalia ───────────────────────────────────────────────────

def cerca_treni(session):
    """
    Usa l'endpoint /msite/api/solutions di lefrecce.it.
    Accetta nomi stazione testuali e ora intera (granularità = 1 ora),
    quindi iteriamo su ogni ora della fascia richiesta.
    """
    soluzioni_trovate = []
    viste = set()

    for ora in range(ORA_DA, ORA_A):
        url = "https://www.lefrecce.it/msite/api/solutions"
        params = {
            "origin":       ORIGINE,
            "destination":  DESTINAZIONE,
            "arflag":       "A",
            "adate":        DATA_VIAGGIO,
            "atime":        str(ora),
            "adultno":      1,
            "childno":      0,
            "direction":    "A",
            "frecce":       "false",
            "onlyRegional": "false",
        }
        try:
            resp = session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            print(f"   ore {ora:02d}:00 → {len(data)} soluzioni")
        except Exception as e:
            print(f"   ore {ora:02d}:00 → Errore: {e}")
            continue

        for sol in data:
            sid = sol.get("idsolution", "")
            if sid and sid in viste:
                continue
            viste.add(sid)
            soluzioni_trovate.append(sol)

    return soluzioni_trovate


# ─── Parsing e filtro ──────────────────────────────────────────────────────────

def parse_soluzioni(soluzioni_raw):
    risultati = []

    for sol in soluzioni_raw:
        dep_ts = sol.get("departuretime", 0)
        arr_ts = sol.get("arrivaltime", 0)
        dep_dt = datetime.fromtimestamp(dep_ts / 1000) if dep_ts else None
        arr_dt = datetime.fromtimestamp(arr_ts / 1000) if arr_ts else None

        if dep_dt is None:
            continue

        ora_partenza = dep_dt.hour + dep_dt.minute / 60
        if not (ORA_DA <= ora_partenza < ORA_A):
            continue

        treni = sol.get("trainlist", [])
        nomi_treni = " | ".join(t.get("trainidentifier", "") for t in treni)

        if SOLO_FRECCIAROSSA:
            is_fr = any(
                "FRECCIAROSSA" in t.get("trainidentifier", "").upper()
                for t in treni
            )
            if not is_fr:
                continue

        # saleable + bookable + prezzo > 0 → acquistabile
        min_price    = sol.get("minprice", 0)
        saleable     = sol.get("saleable", False)
        bookable     = sol.get("bookable", False)
        acquistabile = bool(saleable) and bool(bookable) and (min_price > 0)

        risultati.append({
            "treno":        nomi_treni,
            "partenza":     dep_dt.strftime("%d/%m/%Y %H:%M"),
            "arrivo":       arr_dt.strftime("%H:%M") if arr_dt else "—",
            "durata":       sol.get("duration", "—"),
            "prezzo":       f"{min_price:.2f} €" if min_price else "N/D",
            "cambi":        sol.get("changesno", 0),
            "acquistabile": acquistabile,
        })

    return risultati


# ─── Email ─────────────────────────────────────────────────────────────────────

def build_email_html(soluzioni_ok):
    righe = ""
    for s in soluzioni_ok:
        cambi_str = "Diretto" if s["cambi"] == 0 else f"{s['cambi']} cambio/i"
        righe += f"""
        <tr>
            <td style="padding:10px;border-bottom:1px solid #eee;">{s['treno']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{s['partenza']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{s['arrivo']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{s['durata']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;font-weight:bold;color:#d62e2e;">{s['prezzo']}</td>
            <td style="padding:10px;border-bottom:1px solid #eee;">{cambi_str}</td>
        </tr>"""

    return f"""<html><body style="font-family:sans-serif;max-width:750px;margin:auto;color:#222;">
<h2 style="color:#d62e2e;">🚄 Frecciarossa disponibile!</h2>
<p>
  <strong>Tratta:</strong> {ORIGINE} → {DESTINAZIONE}<br>
  <strong>Data:</strong> {DATA_VIAGGIO} &nbsp;|&nbsp;
  <strong>Fascia:</strong> {ORA_DA}:00 – {ORA_A}:00
</p>
<table style="width:100%;border-collapse:collapse;font-size:0.9em;">
  <thead>
    <tr style="background:#d62e2e;color:white;">
      <th style="padding:10px;text-align:left;">Treno</th>
      <th style="padding:10px;text-align:left;">Partenza</th>
      <th style="padding:10px;text-align:left;">Arrivo</th>
      <th style="padding:10px;text-align:left;">Durata</th>
      <th style="padding:10px;text-align:left;">Prezzo min.</th>
      <th style="padding:10px;text-align:left;">Cambi</th>
    </tr>
  </thead>
  <tbody>{righe}</tbody>
</table>
<br>
<a href="https://www.lefrecce.it"
   style="background:#d62e2e;color:white;padding:12px 28px;border-radius:6px;
          text-decoration:none;display:inline-block;font-size:1em;font-weight:bold;">
  🎫 Acquista ora su lefrecce.it →
</a>
<p style="color:#aaa;font-size:0.75em;margin-top:30px;">
  Monitoraggio automatico via GitHub Actions — {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC
</p>
</body></html>"""


def send_email(soluzioni_ok):
    msg = MIMEMultipart("alternative")
    msg["From"]    = f"{SENDER_NAME} <{EMAIL_MITTENTE}>"
    msg["To"]      = EMAIL_DESTINATARIO
    msg["Subject"] = f"🚄 Frecciarossa disponibile! {ORIGINE} → {DESTINAZIONE} — {DATA_VIAGGIO}"

    body_plain = (
        f"OTTIMA NOTIZIA! {len(soluzioni_ok)} Frecciarossa acquistabile/i trovato/i.\n\n"
        f"Tratta: {ORIGINE} → {DESTINAZIONE}\n"
        f"Data: {DATA_VIAGGIO} | Fascia: {ORA_DA}:00 – {ORA_A}:00\n\n"
    )
    for s in soluzioni_ok:
        body_plain += (
            f"  🚄 {s['treno']}\n"
            f"     Partenza : {s['partenza']}\n"
            f"     Arrivo   : {s['arrivo']}\n"
            f"     Durata   : {s['durata']}\n"
            f"     Prezzo   : {s['prezzo']}\n"
            f"     Cambi    : {s['cambi']}\n\n"
        )
    body_plain += "👉 Acquista subito su: https://www.lefrecce.it\n"

    msg.attach(MIMEText(body_plain, "plain"))
    msg.attach(MIMEText(build_email_html(soluzioni_ok), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(EMAIL_MITTENTE, GMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_MITTENTE, EMAIL_DESTINATARIO, msg.as_string())
    print("✅ Email inviata!")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"🔍 Cerco Frecciarossa {DATA_VIAGGIO}")
    print(f"   {ORIGINE} → {DESTINAZIONE} | fascia {ORA_DA}:00–{ORA_A}:00\n")

    session = crea_sessione()
    soluzioni_raw = cerca_treni(session)

    with open("last_response.json", "w", encoding="utf-8") as f:
        json.dump(soluzioni_raw, f, ensure_ascii=False, indent=2)
    print(f"\n💾 {len(soluzioni_raw)} soluzioni salvate in last_response.json")

    soluzioni    = parse_soluzioni(soluzioni_raw)
    soluzioni_ok = [s for s in soluzioni if s["acquistabile"]]

    print(f"\n📋 Frecciarossa nella fascia oraria: {len(soluzioni)}")
    print(f"✅ Di cui acquistabili: {len(soluzioni_ok)}\n")

    for s in soluzioni:
        stato = "✅ ACQUISTABILE" if s["acquistabile"] else "❌ non disponibile"
        print(f"   {stato} | {s['treno']} | {s['partenza']} → {s['arrivo']} | {s['prezzo']}")

    if soluzioni_ok:
        send_email(soluzioni_ok)
    elif FORCE_EMAIL:
        print("\n🧪 FORCE_EMAIL attivo — invio email di test.")
        fake = [{
            "treno":   "FRECCIAROSSA 9411 (TEST)",
            "partenza": f"{DATA_VIAGGIO} 09:38",
            "arrivo":  "13:30",
            "durata":  "3h 52min",
            "prezzo":  "— TEST —",
            "cambi":   0,
        }]
        send_email(fake)
    else:
        print("\n😴 Nessun Frecciarossa acquistabile. Nessuna email inviata.")


if __name__ == "__main__":
    main()
