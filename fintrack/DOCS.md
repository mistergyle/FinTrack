# FinTrack

Selbst gehostete Finanzübersicht: Umsätze von **ING** und **comdirect** importieren, automatisch
kategorisieren, Budgets und Fixkosten auswerten. Alle Daten bleiben auf deinem Home Assistant.

## Öffnen

Nach dem Start erscheint **FinTrack** in der Seitenleiste (nur für Administratoren). Die Anmeldung
übernimmt Home Assistant, ein zweites Passwort ist nicht nötig.

## Konfiguration

Alle Werte sind optional. Änderungen werden nach einem Neustart des Add-ons wirksam.

| Option | Bedeutung |
|---|---|
| `password` | Nur für den **Direktzugriff** (freigegebener Port 8000). Leer = Direktzugriff gesperrt. Über die Seitenleiste wird es nie verlangt. |
| `fints_product_id` | Registrierte FinTS-Produkt-ID (Deutsche Kreditwirtschaft) für den automatischen FinTS-Abruf. |
| `comdirect_client_id` / `comdirect_client_secret` | OAuth-Zugangsdaten der comdirect REST-API. Beide nötig. |
| `cookie_secure` | Nur Direktzugriff: aktivieren, wenn die App über HTTPS ausgeliefert wird. |
| `log_level` | `debug`, `info`, `warning` oder `error`. |

## Konten anbinden

1. **CSV-Import (funktioniert immer):** ING: Umsatzanzeige → Export (CSV). comdirect: Umsätze → Download → CSV.
   In FinTrack unter *Konten* ein Konto anlegen und die Datei importieren. Doppelte Umsätze werden erkannt.
   Andere Banken: Kontotyp *Andere* wählen, beliebige CSV importieren und einmalig die Spalten zuordnen –
   die Zuordnung wird gespeichert und bei künftigen Dateien automatisch erkannt (änderbar über *Spalten-Zuordnung*).
2. **comdirect-API (optional):** `comdirect_client_id` und `comdirect_client_secret` eintragen, beim Konto die
   Zugangsnummer hinterlegen, dann *Per comdirect-API abrufen*. PIN und TAN-Freigabe werden bei jedem Abruf
   abgefragt und nie gespeichert. **Mehrfach falsche TANs sperren den Online-Zugang.**
3. **FinTS (optional):** `fints_product_id` eintragen, Zugangsnummer beim Konto hinterlegen.

Die comdirect-API- und FinTS-Anbindung wurden nur gegen Attrappen getestet, nicht gegen echte Banken.

## Daten und Backup

Die Datenbank liegt in `/data/finanz.db` und ist **Teil der Home-Assistant-Backups**. Sie ist nicht
verschlüsselt, schütze also deine Backups.

## Direktzugriff (optional)

Unter *Netzwerk* einen Host-Port für `8000/tcp` setzen und in den Optionen ein langes Passwort vergeben.
Für Zugriff von außen bitte HTTPS (Reverse-Proxy) verwenden und `cookie_secure` aktivieren.
