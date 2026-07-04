import os
import json
import smtplib
import ssl
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

# ─── Configurazione ────────────────────────────────────────────────────────────

ORIGINE      = "VENEZIA MESTRE"
DESTINAZIONE = "ROMA TERMINI"

# Date monitorate: 1–6 settembre 2025
DATE_VIAGGIO = [
    "01/09/2026",
    "02/09/2026",
    "03/09/2026",
    "04/09/2026",
    "05/09/2026",
    "06/09/2026",
]

ORA_DA            = 6    # partenze dalle 06:00
ORA_A             = 22   # fino alle 22:00
SOLO_FRECCIAROSSA = True
SOLO_DIRETTI      = True  # nessun cambio

EMAIL_MITTENTE      = os.environ["EMAIL_MITTENTE"]
EMAIL_DESTINATARIO  = os.environ["EMAIL_DESTINATARIO"]
GMAIL_APP_PASSWORD  = os.environ["GMAIL_APP_PASSWORD"]
FORCE_EMAIL         = os.environ.get("FORCE_EMAIL", "false").lower() == "true"

SENDER_NAME = "Monitor Frecciarossa"
LOGO_URL    = "https://raw.githubusercontent.com/poggiodelpapa/bullet-traint/main/btanew.png"
BRAND_COLOR = "#017a8e"

# ─── ID stazioni ──────────────────────────────────────────────────────────────

HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept":        "application/json",
    "User-Agent":    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/124.0.0.0 Safari/537.36",
    "Origin":        "https://www.lefrecce.it",
    "Referer":       "https://www.lefrecce.it/",
}

STAZIONE_ID_CACHE = {
    "VENEZIA MESTRE": 830002589,
    "ROMA TERMINI":   830008409,
}

def get_station_id(nome_stazione):
    cached = STAZIONE_ID_CACHE.get(nome_stazione.upper())
    if cached:
        return cached
    url  = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website/locations/search"
    resp = requests.get(url, params={"name": nome_stazione, "limit": 5},
                        headers=HEADERS_BASE, timeout=15)
    resp.raise_for_status()
    risultati = resp.json()
    if not risultati:
        raise ValueError(f"Stazione non trovata: {nome_stazione}")
    for r in risultati:
        if r.get("name", "").upper() == nome_stazione.upper():
            return r["id"]
    return risultati[0]["id"]

# ─── Chiamata API Trenitalia ───────────────────────────────────────────────────

def cerca_treni(data_viaggio):
    id_origine = get_station_id(ORIGINE)
    id_dest    = get_station_id(DESTINAZIONE)

    url      = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website/ticket/solutions"
    data_iso = datetime.strptime(data_viaggio, "%d/%m/%Y").strftime("%Y-%m-%d")
    departure_time = f"{data_iso}T{ORA_DA:02d}:00:00.000+02:00"

    payload = {
        "departureLocationId": id_origine,
        "arrivalLocationId":   id_dest,
        "departureTime":       departure_time,
        "adults":   1,
        "children": 0,
        "criteria": {
            "frecceOnly":    False,
            "regionalOnly":  False,
            "noChanges":     SOLO_DIRETTI,   # ← filtra diretti lato API
            "order":         "DEPARTURE_DATE",
            "limit":         10,
            "offset":        0,
        },
        "advancedSearchRequest": {"bestFare": False},
    }

    resp = requests.post(url, json=payload, headers=HEADERS_BASE, timeout=30)
    print(f"  [{data_viaggio}] API → HTTP {resp.status_code}")
    resp.raise_for_status()

    data         = resp.json()
    soluzioni_raw = data.get("solutions", [])

    soluzioni = []
    for item in soluzioni_raw:
        if isinstance(item, dict) and "solution" in item:
            sol = item["solution"]
            sol["_messages"] = item.get("messages", [])
            soluzioni.append(sol)
        else:
            soluzioni.append(item)

    return soluzioni

# ─── Parsing e filtro ─────────────────────────────────────────────────────────

def parse_soluzioni(soluzioni_raw, data_viaggio):
    risultati = []

    for sol in soluzioni_raw:
        dep_str = sol.get("departureTime", "")
        arr_str = sol.get("arrivalTime", "")

        try:
            dep_dt = datetime.fromisoformat(dep_str)
        except Exception:
            continue

        try:
            arr_dt = datetime.fromisoformat(arr_str)
        except Exception:
            arr_dt = None

        ora_partenza = dep_dt.hour + dep_dt.minute / 60
        if not (ORA_DA <= ora_partenza < ORA_A):
            continue

        nodes = sol.get("nodes", [])

        # Filtro Frecciarossa
        if SOLO_FRECCIAROSSA:
            is_fr = any(
                "FRECCIAROSSA" in n.get("train", {}).get("trainCategory", "").upper()
                for n in nodes
            )
            if not is_fr:
                continue

        # Filtro diretti: nessun cambio (nodes == 1 treno)
        cambi = max(0, len(nodes) - 1)
        if SOLO_DIRETTI and cambi > 0:
            continue

        nomi_treni = " | ".join(
            f"{n.get('train', {}).get('trainCategory', '')} "
            f"{n.get('train', {}).get('name', '')}".strip()
            for n in nodes
        )

        status      = sol.get("status", "")
        acquistabile = (status == "SALEABLE")

        price_obj = sol.get("price", {}) or {}
        min_price = price_obj.get("amount", 0) or 0

        durata = sol.get("duration", "—")

        risultati.append({
            "data":        data_viaggio,
            "treno":       nomi_treni or "—",
            "partenza":    dep_dt.strftime("%H:%M"),
            "arrivo":      arr_dt.strftime("%H:%M") if arr_dt else "—",
            "durata":      str(durata),
            "prezzo":      f"{float(min_price):.2f} €" if min_price else "N/D",
            "cambi":       cambi,
            "acquistabile": acquistabile,
        })

    return risultati

# ─── Email ────────────────────────────────────────────────────────────────────

def build_card(s):
    cambi_str = "🔄 " + str(s["cambi"]) + " cambio/i" if s["cambi"] else "✅ Diretto"
    # Giorno della settimana in italiano
    try:
        dt = datetime.strptime(s["data"], "%d/%m/%Y")
        giorni = ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
        giorno_label = giorni[dt.weekday()]
    except Exception:
        giorno_label = ""

    return f"""
<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#f0f9fa;border-radius:10px;margin-bottom:14px;
              border-left:5px solid {BRAND_COLOR};overflow:hidden;">
  <tr>
    <td colspan="2"
        style="padding:12px 16px 6px;font-weight:bold;
               font-size:1em;color:{BRAND_COLOR};">
      🚄 {s['treno']}
    </td>
  </tr>
  <tr>
    <td style="padding:4px 16px 10px;">
      <table cellpadding="0" cellspacing="0">
        <tr>
          <td style="text-align:center;padding-right:12px;">
            <div style="font-size:1.6em;font-weight:bold;color:#111;line-height:1;">{s['partenza']}</div>
            <div style="font-size:0.72em;color:#666;margin-top:2px;">{giorno_label} {s['data']}</div>
            <div style="font-size:0.75em;color:#888;">Venezia M.</div>
          </td>
          <td style="padding:0 10px;color:#aaa;font-size:1.2em;">→</td>
          <td style="text-align:center;">
            <div style="font-size:1.6em;font-weight:bold;color:#111;line-height:1;">{s['arrivo']}</div>
            <div style="font-size:0.72em;color:#666;margin-top:2px;">{giorno_label} {s['data']}</div>
            <div style="font-size:0.75em;color:#888;">Roma T.</div>
          </td>
          <td style="padding-left:16px;">
            <div style="font-size:0.78em;color:#555;">⏱ {s['durata']}</div>
            <div style="font-size:0.78em;color:#555;margin-top:4px;">{cambi_str}</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td style="background:{BRAND_COLOR};padding:10px 16px;border-radius:0 0 0 5px;">
      <span style="color:white;font-size:0.8em;">Prezzo minimo</span><br>
      <span style="color:white;font-size:1.4em;font-weight:bold;">{s['prezzo']}</span>
    </td>
  </tr>
</table>"""


def build_date_section(data, treni):
    try:
        dt = datetime.strptime(data, "%d/%m/%Y")
        giorni = ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
        header = f"{giorni[dt.weekday()]} {data}"
    except Exception:
        header = data

    cards = "".join(build_card(s) for s in treni)
    return f"""
<tr>
  <td style="padding:16px 16px 4px;">
    <div style="font-size:1em;font-weight:bold;color:#555;
                border-bottom:2px solid {BRAND_COLOR};padding-bottom:6px;margin-bottom:10px;">
      📅 {header}
    </div>
    {cards}
  </td>
</tr>"""


def build_email_html(risultati_per_data):
    sezioni = ""
    for data in DATE_VIAGGIO:
        treni = risultati_per_data.get(data, [])
        if treni:
            sezioni += build_date_section(data, treni)

    date_range = f"{DATE_VIAGGIO[0]} – {DATE_VIAGGIO[-1]}"

    return f"""<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
</head>
<body style="margin:0;padding:0;background:#f4f4f4;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:20px 0;">
  <tr><td align="center" style="padding:0 12px;">
    <table width="100%" cellpadding="0" cellspacing="0"
           style="max-width:600px;background:#ffffff;border-radius:14px;overflow:hidden;
                  box-shadow:0 2px 16px rgba(0,0,0,0.10);">

      <!-- Header -->
      <tr>
        <td style="background:{BRAND_COLOR};padding:28px 24px;text-align:center;">
          <img src="{LOGO_URL}" alt="Logo" width="68" height="68"
               style="border-radius:50%;display:block;margin:0 auto 14px;
                      background:white;padding:4px;">
          <div style="color:white;font-size:1.3em;font-weight:bold;">
            🚄 Frecciarossa disponibile!
          </div>
        </td>
      </tr>

      <!-- Info tratta -->
      <tr>
        <td style="background:#e4f5f7;padding:12px 20px;
                   border-bottom:3px solid {BRAND_COLOR};text-align:center;">
          <p style="margin:0;color:#333;font-size:0.9em;line-height:1.6;">
            <strong>{ORIGINE} → {DESTINAZIONE}</strong><br>
            📅 {date_range} &nbsp;·&nbsp; Solo Frecciarossa diretti
          </p>
        </td>
      </tr>

      <!-- Sezioni per data -->
      {sezioni}

      <!-- CTA -->
      <tr>
        <td style="padding:8px 20px 28px;text-align:center;">
          <a href="https://www.lefrecce.it"
             style="background:{BRAND_COLOR};color:white;
                    padding:14px 0;border-radius:10px;text-decoration:none;
                    font-size:1em;font-weight:bold;display:block;text-align:center;">
            🎫 Acquista ora su lefrecce.it →
          </a>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background:#f0f9fa;padding:12px 20px;
                   border-top:1px solid #cde8ec;text-align:center;">
          <p style="margin:0;color:#aaa;font-size:0.72em;">
            Monitoraggio automatico via GitHub Actions —
            {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body></html>"""


def send_email(risultati_per_data, totale):
    msg = MIMEMultipart("alternative")
    msg["From"]    = f"{SENDER_NAME} <{EMAIL_MITTENTE}>"
    msg["To"]      = EMAIL_DESTINATARIO
    date_range     = f"{DATE_VIAGGIO[0]} – {DATE_VIAGGIO[-1]}"
    msg["Subject"] = f"🚄 Frecciarossa disponibile! {ORIGINE} → {DESTINAZIONE} ({date_range})"

    # Testo plain
    body_plain = (
        f"OTTIMA NOTIZIA! {totale} Frecciarossa diretto/i acquistabile/i trovato/i.\n\n"
        f"Tratta: {ORIGINE} → {DESTINAZIONE}\n"
        f"Date:   {date_range}\n\n"
    )
    for data in DATE_VIAGGIO:
        treni = risultati_per_data.get(data, [])
        if treni:
            body_plain += f"── {data} ──\n"
            for s in treni:
                body_plain += (
                    f"  🚄 {s['treno']}\n"
                    f"     Partenza : {s['partenza']}\n"
                    f"     Arrivo   : {s['arrivo']}\n"
                    f"     Durata   : {s['durata']}\n"
                    f"     Prezzo   : {s['prezzo']}\n\n"
                )

    body_plain += "👉 Acquista subito su: https://www.lefrecce.it\n"

    msg.attach(MIMEText(body_plain, "plain"))
    msg.attach(MIMEText(build_email_html(risultati_per_data), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(EMAIL_MITTENTE, GMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_MITTENTE, EMAIL_DESTINATARIO, msg.as_string())
    print("✅ Email inviata!")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"🔍 Cerco Frecciarossa diretti: {ORIGINE} → {DESTINAZIONE}")
    print(f"   Date: {', '.join(DATE_VIAGGIO)}\n")

    tutti_i_risultati = {}    # data → lista soluzioni raw (per debug JSON)
    risultati_per_data = {}   # data → lista treni acquistabili

    for data in DATE_VIAGGIO:
        print(f"\n📅 {data}")
        try:
            soluzioni_raw = cerca_treni(data)
        except Exception as e:
            print(f"  ⚠️  Errore API per {data}: {e}")
            continue

        tutti_i_risultati[data] = soluzioni_raw

        soluzioni = parse_soluzioni(soluzioni_raw, data)
        ok        = [s for s in soluzioni if s["acquistabile"]]

        print(f"   Frecciarossa diretti trovati: {len(soluzioni)} | Acquistabili: {len(ok)}")
        for s in soluzioni:
            stato = "✅" if s["acquistabile"] else "❌"
            print(f"   {stato} {s['treno']} | {s['partenza']} → {s['arrivo']} | {s['prezzo']}")

        if ok:
            risultati_per_data[data] = ok

    # Salva JSON di debug
    with open("last_response.json", "w", encoding="utf-8") as f:
        json.dump(tutti_i_risultati, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Risposte API salvate in last_response.json")

    totale_ok = sum(len(v) for v in risultati_per_data.values())
    print(f"\n{'='*50}")
    print(f"✅ Totale Frecciarossa acquistabili: {totale_ok} su {len(DATE_VIAGGIO)} date")

    if risultati_per_data:
        send_email(risultati_per_data, totale_ok)
    elif FORCE_EMAIL:
        print("\n🧪 FORCE_EMAIL attivo — invio email di test.")
        fake_data = DATE_VIAGGIO[0]
        fake = {fake_data: [{
            "data":    fake_data,
            "treno":   "FRECCIAROSSA 9411 (TEST)",
            "partenza": "09:38",
            "arrivo":   "13:30",
            "durata":   "3h 52min",
            "prezzo":   "— TEST —",
            "cambi":    0,
            "acquistabile": True,
        }]}
        send_email(fake, 1)
    else:
        print("\n😴 Nessun Frecciarossa acquistabile. Nessuna email inviata.")

if __name__ == "__main__":
    main()
