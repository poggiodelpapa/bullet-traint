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
DATA_VIAGGIO      = "30/07/2026"
ORA_DA            = 9    # partenze dalle 09:00
ORA_A             = 15   # fino alle 15:00
SOLO_FRECCIAROSSA = True

EMAIL_MITTENTE     = os.environ["EMAIL_MITTENTE"]
EMAIL_DESTINATARIO = os.environ["EMAIL_DESTINATARIO"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
FORCE_EMAIL        = os.environ.get("FORCE_EMAIL", "false").lower() == "true"

SENDER_NAME = "Monitor Frecciarossa"

# URL raw del logo nel repo (aggiorna con il tuo username/repo se diverso)
LOGO_URL = "https://raw.githubusercontent.com/poggiodelpapa/bullet-traint/main/btanew.png"
BRAND_COLOR = "#017a8e"


# ─── ID stazioni (verificati il 09/05/2026) ───────────────────────────────────

HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36",
    "Origin":       "https://www.lefrecce.it",
    "Referer":      "https://www.lefrecce.it/",
}

STAZIONE_ID_CACHE = {
    "VENEZIA MESTRE": 830002589,
    "ROMA TERMINI":   830008409,
}


def get_station_id(nome_stazione):
    """Restituisce l'ID stazione dalla cache; se non presente lo risolve via API."""
    cached = STAZIONE_ID_CACHE.get(nome_stazione.upper())
    if cached:
        return cached
    url = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website/locations/search"
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


# ─── Chiamata API Trenitalia (BFF POST) ───────────────────────────────────────

def cerca_treni():
    """
    Usa l'endpoint BFF di lefrecce.it con POST JSON.
    Documentazione: https://github.com/SimoDax/Trenitalia-API/wiki/Nuove-API-Trenitalia-lefrecce.it
    """
    id_origine = get_station_id(ORIGINE)
    id_dest    = get_station_id(DESTINAZIONE)
    print(f"   {ORIGINE} (ID {id_origine}) → {DESTINAZIONE} (ID {id_dest})")

    url = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website/ticket/solutions"

    data_iso = datetime.strptime(DATA_VIAGGIO, "%d/%m/%Y").strftime("%Y-%m-%d")
    departure_time = f"{data_iso}T{ORA_DA:02d}:00:00.000+02:00"

    payload = {
        "departureLocationId": id_origine,
        "arrivalLocationId":   id_dest,
        "departureTime":       departure_time,
        "adults":              1,
        "children":            0,
        "criteria": {
            "frecceOnly":   False,
            "regionalOnly": False,
            "noChanges":    False,
            "order":        "DEPARTURE_DATE",
            "limit":        30,
            "offset":       0,
        },
        "advancedSearchRequest": {
            "bestFare": False,
        },
    }

    resp = requests.post(url, json=payload, headers=HEADERS_BASE, timeout=30)
    print(f"   API soluzioni → HTTP {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()

    soluzioni_raw = data.get("solutions", [])
    # L'API restituisce un array flat di oggetti ticket diretti (senza wrapper)
    print(f"   Soluzioni totali ricevute: {len(soluzioni_raw)}")
    return soluzioni_raw


# ─── Parsing e filtro ──────────────────────────────────────────────────────────

def parse_soluzioni(soluzioni_raw):
    """
    Struttura risposta (da documentazione SimoDax):
    solution.departureTime  → stringa ISO
    solution.arrivalTime    → stringa ISO
    solution.status         → "SALEABLE" | "INHIBITED" | "NOT_SALEABLE"
    solution.price.amount   → float prezzo minimo
    solution.nodes[]        → treni nella soluzione
      node.train.trainCategory → "Frecciarossa", "Regionale", ecc.
      node.train.name          → numero treno
    """
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

        # Nomi treni dai nodes
        nodes = sol.get("nodes", [])
        nomi_treni = " | ".join(
            f"{n.get('train', {}).get('trainCategory', '')} "
            f"{n.get('train', {}).get('name', '')}".strip()
            for n in nodes
        )

        if SOLO_FRECCIAROSSA:
            is_fr = any(
                "FRECCIAROSSA" in n.get("train", {}).get("trainCategory", "").upper()
                for n in nodes
            )
            if not is_fr:
                continue

        # Acquistabilità: status == "SALEABLE"
        status       = sol.get("status", "")
        acquistabile = (status == "SALEABLE")
        # Prezzo
        price_obj = sol.get("price", {}) or {}
        min_price = price_obj.get("amount", 0) or 0

        # Durata e cambi
        durata = sol.get("duration", "—")
        cambi  = max(0, len(nodes) - 1)

        risultati.append({
            "treno":        nomi_treni or "—",
            "partenza":     dep_dt.strftime("%d/%m/%Y %H:%M"),
            "arrivo":       arr_dt.strftime("%H:%M") if arr_dt else "—",
            "durata":       str(durata),
            "prezzo":       f"{float(min_price):.2f} €" if min_price else "N/D",
            "cambi":        cambi,
            "acquistabile": acquistabile,
        })

    return risultati


# ─── Email ─────────────────────────────────────────────────────────────────────

def build_card(s):
    """Genera una card per ogni treno — leggibile sia su desktop che mobile."""
    cambi_str = "🔄 " + str(s["cambi"]) + " cambio/i" if s["cambi"] else "✅ Diretto"
    dep_parts  = s["partenza"].split(" ")  # ["30/07/2026", "09:38"]
    dep_data   = dep_parts[0] if len(dep_parts) > 1 else ""
    dep_ora    = dep_parts[1] if len(dep_parts) > 1 else dep_parts[0]

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0"
           style="background:#f0f9fa;border-radius:10px;margin-bottom:14px;
                  border-left:5px solid {BRAND_COLOR};overflow:hidden;">
      <!-- Nome treno -->
      <tr>
        <td colspan="2"
            style="padding:12px 16px 6px;font-weight:bold;
                   font-size:1em;color:{BRAND_COLOR};">
          🚄 {s['treno']}
        </td>
      </tr>
      <!-- Orari: grande e leggibile -->
      <tr>
        <td style="padding:4px 16px 10px;">
          <table cellpadding="0" cellspacing="0">
            <tr>
              <td style="text-align:center;padding-right:12px;">
                <div style="font-size:1.6em;font-weight:bold;color:#111;line-height:1;">{dep_ora}</div>
                <div style="font-size:0.72em;color:#666;margin-top:2px;">{dep_data}</div>
                <div style="font-size:0.75em;color:#888;">Venezia M.</div>
              </td>
              <td style="padding:0 10px;color:#aaa;font-size:1.2em;">→</td>
              <td style="text-align:center;">
                <div style="font-size:1.6em;font-weight:bold;color:#111;line-height:1;">{s['arrivo']}</div>
                <div style="font-size:0.72em;color:#666;margin-top:2px;">{dep_data}</div>
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
      <!-- Prezzo -->
      <tr>
        <td style="background:{BRAND_COLOR};padding:10px 16px;border-radius:0 0 0 5px;">
          <span style="color:white;font-size:0.8em;">Prezzo minimo</span><br>
          <span style="color:white;font-size:1.4em;font-weight:bold;">{s['prezzo']}</span>
        </td>
      </tr>
    </table>"""


def build_email_html(soluzioni_ok):
    cards = "".join(build_card(s) for s in soluzioni_ok)

    return f"""<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
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
            📅 {DATA_VIAGGIO} &nbsp;·&nbsp; 🕐 {ORA_DA}:00 – {ORA_A}:00
          </p>
        </td>
      </tr>

      <!-- Cards treni -->
      <tr>
        <td style="padding:20px 16px 8px;">
          {cards}
        </td>
      </tr>

      <!-- CTA -->
      <tr>
        <td style="padding:8px 20px 28px;text-align:center;">
          <a href="https://www.lefrecce.it"
             style="background:{BRAND_COLOR};color:white;
                    padding:14px 0;border-radius:10px;text-decoration:none;
                    font-size:1em;font-weight:bold;display:block;
                    text-align:center;">
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
            {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
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

    soluzioni_raw = cerca_treni()

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
            "treno":        "FRECCIAROSSA 9411 (TEST)",
            "partenza":     f"{DATA_VIAGGIO} 09:38",
            "arrivo":       "13:30",
            "durata":       "3h 52min",
            "prezzo":       "— TEST —",
            "cambi":        0,
            "acquistabile": True,
        }]
        send_email(fake)
    else:
        print("\n😴 Nessun Frecciarossa acquistabile. Nessuna email inviata.")


if __name__ == "__main__":
    main()
