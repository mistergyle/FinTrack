# Changelog

## 0.11.5

- Umsätze (mobil): Das Sortier-Dropdown ist durch zwei Icons ersetzt – Kalender = „Datum · neueste zuerst“ (Standard), Euro = „Betrag · Ausgaben zuerst“. Auf dem Desktop bleibt die Sortierung über die Spaltenüberschriften.

## 0.11.4

- Konten (mobil): „Duplikate bereinigen“ und „Konto hinzufügen“ stehen nebeneinander in einer Zeile; die doppelte Überschrift „Konten“ entfällt auf dem Handy.

## 0.11.3

- App-Icon wieder in der Kopfzeile (rechtsbündig); dient auch als Browser-Tab-Icon.

## 0.11.2

- Kopfzeile und Menü: der zusätzliche Abstand für die Notch / Statusleiste oben entfällt (im Home-Assistant-Add-on sitzt darüber bereits die App-Leiste).

## 0.11.1

- Konten: „Bearbeiten“ und „Löschen“ sind jetzt – wie bei den Kategorien – platzsparende Icons (✎ und rotes Papierkorb-Icon).

## 0.11.0

- Kontofilter (Übersicht und Umsätze): Statt des Dropdowns werden die Konten als antippbare Tags angezeigt. Mehrere Konten lassen sich gleichzeitig auswählen („Alle Konten“ = kein Filter). Auch „Gefilterte löschen“ und die Auswertungen berücksichtigen die Mehrfachauswahl.

## 0.10.0

- Neuer Kontotyp „Andere“ mit beliebigem CSV-Import: Beim ersten Import öffnet sich ein Dialog zur Spalten-Zuordnung (Trennzeichen, Zeichensatz, Kopfzeile, Datum, Betrag als eine Spalte oder Soll/Haben, Empfänger, Verwendungszweck, Buchungstext, Valuta, IBAN, Währung) mit Live-Vorschau. Die Zuordnung wird am Konto gespeichert und bei späteren Importen anhand der Spaltenüberschriften automatisch angewendet – auch ohne Kontoauswahl. Über „Spalten-Zuordnung“ am Konto lässt sie sich jederzeit ändern.

## 0.9.1

- Mobil: Zeitraum-Leiste repariert (‹ Zeitraum › in einer Zeile, nichts läuft mehr über den Rand); Konto-, Kategorie- und Sortier-Auswahl über volle Breite. „Heute“ steckt auf dem Handy im Datepicker.

## 0.9.0

- Neue Kategorien: „Bildung und Beruf“ (Weiterbildung, Dienstreise / Spesen, Sonstige), „Gesundheit“ (Apotheke / Medikamente, Arztbehandlung, Brille / Kontaktlinsen, Sonstige) und „Kinder“ (Kinderbetreuung, Schulgeld, Schulausflug, Kinderbekleidung, Spielwaren, Taschengeld, Freizeitaktivitäten, Pflegeprodukte, Unterhaltszahlungen, Sonstige). „Bildung“ wird zu „Bildung und Beruf“. Bestehende Installationen mit angewendeter Vorlage bekommen sie beim Start einmalig; passende Standardregeln wandern auf die Unterkategorien.

## 0.8.1

- Datepicker: Die Jahreszahl in der Überschrift ist antippbar und wählt das ganze Jahr (statt Button „Ganzes Jahr“).

## 0.8.0

- Umsätze sortieren: Klick auf die Spaltenüberschriften „Datum“ und „Betrag“ (auf dem Handy per Auswahlfeld).
- Neue Zeitraum-Auswahl: Antippen des Zeitraums öffnet einen Datepicker mit Monatsraster und Jahreswechsel; in Übersicht und Umsätze lässt sich auch „Ganzes Jahr“ wählen. Der Umschalter Monat/Jahr entfällt.

## 0.7.1

- Automatische Kategorisierung: „Nahkauf“ und „Bäcker/Baecker“ (auch mitten im Namen, z. B. „Hinnerbaecker“) landen in „Lebensmittel / Getränke“. Die Regeln werden beim Start auch in bestehende Datenbanken eingetragen, vorhandene Umsätze (ohne manuell gesetzte Kategorie) werden neu kategorisiert.

## 0.7.0

- Umsätze: „Gefilterte löschen“ löscht alle Umsätze, die zu Monat/Jahr, Konto, Kategorie und Suche passen. Vorher Bestätigung mit Anzahl und Summe; ohne Filter muss „LÖSCHEN“ getippt werden.

## 0.6.2

- Entfernt: Unterkategorie „Sonstige Unkategorisierte Ausgaben“. Nicht erkannte Ausgaben landen direkt in „Unkategorisierte Ausgaben“. Bestehende Umsätze werden beim Start automatisch dorthin verschoben.
- Beim Löschen einer Unterkategorie wandern ihre Umsätze zur Oberkategorie, statt keine Kategorie zu haben.

## 0.6.1

- Behoben: Umsätze, die erst per FinTS abgerufen und danach per CSV importiert wurden (oder umgekehrt), erschienen doppelt, weil die Texte unterschiedlich getrennt sind. CSV-Importe gleichen jetzt wie der FinTS-/API-Abruf mit vorhandenen Umsätzen ab (gleiches Datum und Betrag, ähnlicher Text).
- Neu: Konten → „Duplikate bereinigen“ entfernt bereits vorhandene Dubletten. Identische Käufe aus derselben Quelle (z. B. zwei gleiche Käufe am selben Tag) bleiben erhalten.

## 0.6.0

- Übersicht: Sankey-Diagramm „Wohin fließen die Einnahmen?“ (Einnahmequellen → Einnahmen → Ausgabenkategorien, Sparen, Übrig). Antippen einer Kategorie öffnet die zugehörigen Umsätze. Funktioniert für Monat und Jahr, auch mobil.

## 0.5.1

- Kategorie-Verwaltung: Löschen-Symbol als schlichter roter Papierkorb.

## 0.5.0

- Übersicht und Umsätze: Umschalter „Monat | Jahr“, ganzes Jahr auswählbar (z. B. 2026). Im Jahr werden Monatsbudgets mal 12 gerechnet, der Vergleichswert ist das Vorjahr, das Diagramm zeigt alle 12 Monate.

## 0.4.1

- Kategorien verwalten: Hinzufügen, Bearbeiten und Löschen als Symbole in derselben Zeile wie der Kategoriename.

## 0.4.0

- Unterkategorien (optional, eine Ebene): Oberkategorien summieren ihre eigenen Umsätze und alle Unterkategorien auf (Übersicht, Diagramme, Budgets, Umsatzfilter).
- Neue Kategorie-Vorlage (Einnahmen, Wohnen, Lebenshaltung, Auto und öffentliche Verkehrsmittel, Kommunikation, Versicherungen, Freizeit, Reise, Bank, Sparen und Anlegen, Unkategorisierte Ausgaben). Neue Installationen starten damit; bestehende Installationen wenden sie unter „Regeln → Kategorien“ per „Vorlage anwenden“ an (Umsätze, Budgets und eigene Regeln bleiben erhalten, Standardkategorien werden umbenannt und einsortiert).
- Kategorien können in der App angelegt, umbenannt, verschoben und gelöscht werden.
- Budgets: Ein Budget auf der Oberkategorie hat Vorrang, sonst gilt die Summe der Unterkategorie-Budgets.

## 0.3.1

- Behoben: Der Button „Per comdirect-API abrufen“ reagierte nicht, wenn beim Konto die Zugangsnummer fehlte. Jetzt erscheint ein Hinweis und der Konto-Dialog öffnet sich.
- Der comdirect-Abruf schreibt Fortschritt und Fehler ins Add-on-Protokoll.

## 0.3.0

- Mobile-optimierte Oberfläche: Burger-Menü (Schublade), Umsätze und Budgets als Karten, Bottom-Sheet-Dialoge, größere Touch-Flächen, schmale Diagramme.

## 0.2.0
- Als Home-Assistant-Add-on verpackt (Ingress, Seitenleiste, Konfiguration über die Add-on-Optionen)
- Direktzugriff nur noch mit Passwort; über Ingress übernimmt Home Assistant die Anmeldung
- comdirect REST-API-Anbindung

## 0.1.0
- Erste Version: CSV-Import ING/comdirect, Kategorisierung, Budgets, Fixkosten
