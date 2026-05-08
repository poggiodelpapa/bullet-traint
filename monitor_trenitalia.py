import os
import json
import smtplib
import ssl
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

# ─── Configurazione ────────────────────────────────────────────────────────────

ORIGINE_ID      = "830001170"   # Venezia Mestre (codice UIC Trenitalia)
DEST_ID         = "830000219"   # Roma Termini   (codice UIC Trenitalia)
DATA_VIAGGIO    = "30/07/2025"
ORA_DA          = 9             # solo treni che partono dalle 09:00
ORA_A           = 15            # fino alle 15:00
SOLO_FRECCIAROSSA = True

EMAIL_MITTENTE      = os.environ["EMAIL_MITTENTE"]
EMAIL_DESTINATARIO  = os.environ["EMAIL_DESTINATARIO"]
GMAIL_APP_PASSWORD  = os.environ["GMAIL_APP_PASSWORD"]
FORCE_EMAIL         = os.environ.get("FORCE_EMAIL", "false").lower() == "true"

SENDER_NAME = "Monitor Frecciarossa"

# ─── Chiamata API Trenitalia ────────────────────────────────────────────────────

def cerca_treni():
    """
    Chiama l'API pubblica di Trenitalia (stessa usata dal sito lefrecce.it)
    e restituisce la lista di soluzioni trovate.
    """
    url = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website/ticket/solutions"

    payload = {
        "departureLocationId": ORIGINE_ID,
        "arrivalLocationId":   DEST_ID,
        "departureTime":       f"{DATA_VIAGGIO}T09:00:00",
        "adults":              1,
        "children":            0,
        "criteria": {
            "frecceOnly":      False,
            "regionalOnly":    False,
            "noChanges":       False,
            "order":           "DEPARTURE_DATE",
            "limit":           10,
            "offset":          0
        },
        "selectedCategories": []
    }

    headers = {
        "Content-Type":  "application/json",
        "User-Agent":    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0.0.0 Safari/537.36",
        "Referer":       "https://www.lefrecce.it/",
        "Origin":        "https://www.lefrecce.it",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ─── Parsing risultati ─────────────────────────────────────────────────────────

def parse_soluzioni(data):
    """
    Filtra le soluzioni per:
    - Solo Frecciarossa (se SOLO_FRECCIAROSSA=True)
    - Partenza tra ORA_DA e ORA_A
    - Acquistabile (non 'non acquistabile')
    """
    soluzioni = data.get("solutions", [])
    trovati = []

    for sol in soluzioni:
        # Estrai ora di partenza
        dep_str = sol.get("departureTime", "")  # es. "2025-07-30T09:38:00"
        try:
            dep_dt = datetime.fromisoformat(dep_str)
        except Exception:
            continue

        ora_partenza = dep_dt.hour + dep_dt.minute / 60

        if not (ORA_DA <= ora_partenza < ORA_A):
            continue

        # Verifica che sia Frecciarossa
        if SOLO_FRECCIAROSSA:
            legs = sol.get("legs", [])
            is_frecciarossa = any(
                "FRECCIAROSSA" in (leg.get("trainCategory", "") or "").upper()
                for leg in legs
            )
            if not is_frecciarossa:
                continue

        # Verifica acquistabilità
        # Il campo può chiamarsi 'saleable', 'status', 'bookable', ecc.
        # Proviamo tutte le varianti note
        saleable = (
            sol.get("saleable") or
            sol.get("bookable") or
            sol.get("purchasable") or
            (sol.get("status", "").upper() not in ("NOT_SALEABLE", "NOT_AVAILABLE", ""))
        )

        # Raccogliamo info utili
        arr_str   = sol.get("arrivalTime", "")
        prezzo    = sol.get("minPrice", {}).get("amount", "N/D")
        valuta    = sol.get("minPrice", {}).get("currency", "EUR")
        cambi     = len(sol.get("legs", [])) - 1
        treni_str = " / ".join(
            f"{leg.get('trainCategory','')} {leg.get('trainNumber','')}".strip()
            for leg in sol.get("legs", [])
        )

        trovati.append({
            "treno":       treni_str,
            "partenza":    dep_str,
            "arrivo":      arr_str,
            "prezzo":      f"{prezzo} {valuta}",
            "cambi":       cambi,
            "acquistabile": bool(saleable),
            "raw":         sol,
        })

    return trovati


# ─── Email ─────────────────────────────────────────────────────────────────────

def build_email_html(soluzioni_ok):
    righe = ""
    for s in soluzioni_ok:
        dep = s["partenza"].replace("T", " ")[:16]
        arr = s["arrivo"].replace("T", " ")[:16]
        righe += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee;">{s['treno']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{dep}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{arr}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{s['prezzo']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">
                {'Diretto' if s['cambi'] == 0 else f"{s['cambi']} cambio/i"}
            </td>
        </tr>"""

    return f"""
    <html><body style="font-family:sans-serif;max-width:700px;margin:auto;">
    <h2>🚄 Frecciarossa disponibile! Venezia Mestre → Roma Termini</h2>
    <p>Data: <strong>{DATA_VIAGGIO}</strong> | Partenze tra le {ORA_DA}:00 e le {ORA_A}:00</p>

    <table style="width:100%;border-collapse:collapse;font-size:0.9em;">
        <thead>
            <tr style="background:#d62e2e;color:white;">
                <th style="padding:10px;text-align:left;">Treno</th>
                <th style="padding:10px;text-align:left;">Partenza</th>
                <th style="padding:10px;text-align:left;">Arrivo</th>
                <th style="padding:10px;text-align:left;">Prezzo</th>
                <th style="padding:10px;text-align:left;">Cambi</th>
            </tr>
        </thead>
        <tbody>{righe}</tbody>
    </table>

    <br>
    <a href="https://www.lefrecce.it" style="background:#d62e2e;color:white;padding:12px 24px;
       border-radius:6px;text-decoration:none;display:inline-block;font-size:1em;">
       ✈ Acquista ora su lefrecce.it →
    </a>

    <p style="color:#888;font-size:0.8em;margin-top:30px;">
        Monitoraggio automatico via GitHub Actions — {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC
    </p>
    </body></html>
    """


def send_email(soluzioni_ok):
    msg = MIMEMultipart("alternative")
    msg["From"]    = f"{SENDER_NAME} <{EMAIL_MITTENTE}>"
    msg["To"]      = EMAIL_DESTINATARIO
    msg["Subject"] = f"🚄 Frecciarossa disponibile! VE Mestre → Roma — {DATA_VIAGGIO}"

    n = len(soluzioni_ok)
    body_plain = (
        f"OTTIMA NOTIZIA! {n} Frecciarossa acquistabile/i trovato/i.\n\n"
        f"Tratta: Venezia Mestre → Roma Termini\n"
        f"Data: {DATA_VIAGGIO} | Fascia oraria: {ORA_DA}:00 – {ORA_A}:00\n\n"
    )
    for s in soluzioni_ok:
        body_plain += (
            f"  🚄 {s['treno']}\n"
            f"     Partenza: {s['partenza']}\n"
            f"     Arrivo:   {s['arrivo']}\n"
            f"     Prezzo:   {s['prezzo']}\n\n"
        )
    body_plain += "Acquista subito su: https://www.lefrecce.it\n"

    msg.attach(MIMEText(body_plain, "plain"))
    msg.attach(MIMEText(build_email_html(soluzioni_ok), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(EMAIL_MITTENTE, GMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_MITTENTE, EMAIL_DESTINATARIO, msg.as_string())
    print("✅ Email inviata!")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"🔍 Cerco Frecciarossa {DATA_VIAGGIO} — "
          f"Venezia Mestre → Roma Termini — "
          f"fascia {ORA_DA}:00/{ORA_A}:00")

    try:
        data = cerca_treni()
    except requests.HTTPError as e:
        print(f"❌ Errore HTTP dall'API Trenitalia: {e}")
        raise
    except Exception as e:
        print(f"❌ Errore imprevisto: {e}")
        raise

    # Salva risposta grezza per debug
    with open("last_response.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("💾 Risposta API salvata in last_response.json")

    soluzioni = parse_soluzioni(data)
    print(f"📋 Soluzioni Frecciarossa nella fascia oraria: {len(soluzioni)}")

    soluzioni_ok = [s for s in soluzioni if s["acquistabile"]]
    print(f"✅ Di cui acquistabili: {len(soluzioni_ok)}")

    for s in soluzioni:
        stato = "✅ ACQUISTABILE" if s["acquistabile"] else "❌ non disponibile"
        print(f"   {stato} | {s['treno']} | {s['partenza'][:16]} → {s['arrivo'][:16]} | {s['prezzo']}")

    if soluzioni_ok or FORCE_EMAIL:
        if not soluzioni_ok and FORCE_EMAIL:
            print("🧪 FORCE_EMAIL attivo — invio email di test anche senza disponibilità.")
            # Crea una riga finta per il test
            soluzioni_ok = [{
                "treno":        "FRECCIAROSSA 9999 (TEST)",
                "partenza":     f"{DATA_VIAGGIO.replace('/', '-'[::-1])}T10:00:00",
                "arrivo":       f"{DATA_VIAGGIO.replace('/', '-'[::-1])}T13:52:00",
                "prezzo":       "TEST EUR",
                "cambi":        0,
                "acquistabile": True,
                "raw":          {},
            }]
        send_email(soluzioni_ok)
    else:
        print("😴 Nessun Frecciarossa acquistabile al momento. Nessuna email inviata.")


if __name__ == "__main__":
    main()
