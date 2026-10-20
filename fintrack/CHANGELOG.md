# Changelog

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
