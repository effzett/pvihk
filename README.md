# PVIHK — Prüfungs- und Korrektorenverteilung für IHK-Prüfungsausschüsse

**PVIHK** ist ein Desktop-Tool zur automatischen Verteilung von Prüflingen auf Korrektoren in IHK-Prüfungsausschüssen. Es löst das Zuordnungsproblem als Integer Linear Program (ILP) mit dem freien CBC-Solver und erzeugt einen druckfertigen PDF-Tagesplan.

---

## Das Problem

Bei IHK-Abschlussprüfungen müssen Prüflinge auf Korrektoren-Paare verteilt werden. Dabei gelten mehrere Constraints gleichzeitig:

- Jede Prüfung wird von genau 2 Korrektoren bewertet
- Pro Prüfungstermin muss mindestens ein Korrektor des Paares anwesend sein
- Die Prüfungslast soll gleichmäßig auf alle Korrektoren verteilt werden
- Prüflinge ohne Präsenzprüfung (reine Klausurkorrektur, Prefix `X_`) werden gesondert behandelt

Das manuelle Lösen dieses Problems — besonders bei eingeschränkten Verfügbarkeiten — ist aufwendig und fehleranfällig. PVIHK findet das Optimum automatisch.

---

## Features

- **Automatische Optimierung** per ILP (PuLP + CBC-Solver)
- **Faire Lastverteilung** unter allen Korrektoren
- **Verfügbarkeitssteuerung** pro Korrektor und Tag
- **X_-Kandidaten** (reine Klausurkorrektur, keine Präsenz) werden korrekt behandelt
- **Paar-Bündelung** (optional): minimiert die Anzahl verschiedener Korrektorpaare, um Weitergabe-Komplexität zu reduzieren
- **Harte Partner-Schranke** (optional): begrenzt wie viele verschiedene Partner ein Korrektor maximal hat
- **PDF-Ausgabe**: druckfertiger Tagesplan mit Zeitslots, Versandlisten und Weitergabe-Übersicht
- **Persistente Einstellungen**: Konfiguration wird als JSON gespeichert
- **Plattformübergreifend**: läuft auf macOS und Windows

---

## Voraussetzungen

```
Python 3.10+
PySide6
PuLP
fpdf2
```

Installation der Abhängigkeiten:

```bash
pip install PySide6 pulp fpdf2
```

---

## Starten

```bash
python pvihk.py
```

Oder als ausführbare Datei (PyInstaller, Windows/macOS).

---

## Bedienung

### 1. Korrektoren eintragen

Im linken Bereich die Namen der Korrektoren eingeben. Pro Korrektor wird gesetzt:
- **Tag 1 verfügbar** (Checkbox)
- **Tag 2 verfügbar** (Checkbox)

### 2. Prüflinge eintragen

Im rechten Bereich die Namen der Prüflinge eingeben. Prüflinge die nur eine Klausurkorrektur erhalten (keine Präsenzprüfung) erhalten das Prefix `X_`, z.B. `X_Fernstudent`.

### 3. Optimierung starten

Schaltfläche **Berechnen** — der Solver läuft, das Ergebnis erscheint als Tagesplan und kann als PDF gespeichert werden.

---

## Einstellungen

Über **Einstellungen → Präferenzen** erreichbar:

| Einstellung | Beschreibung |
|---|---|
| **Prüfungszeiten Tag 1** | Zeitslots für den ersten Prüfungstag (z.B. `09:00`, `10:00`, ...) |
| **Prüfungszeiten Tag 2** | Zeitslots für den zweiten Prüfungstag |
| **Lambda Paar-Bündelung** | Gewichtung der Weitergabe-Minimierung (0.0 = aus, höhere Werte = stärkere Bündelung) |
| **Max. Partner (aktiv/inaktiv)** | Harte Obergrenze wie viele verschiedene Partner ein Korrektor haben darf |

Die Anzahl der Zeitslots muss mindestens so groß sein wie die Anzahl der Prüflinge pro Tag. PVIHK warnt, wenn das nicht der Fall ist.

---

## Paar-Bündelung (Lambda-Parameter)

Ohne Bündelung kann ein Korrektor mit 5 Prüfungen an 5 verschiedene Partner weitergeben — jede Klausur wandert zu einer anderen Person. Mit Lambda > 0 minimiert der Solver die Anzahl verschiedener Paare im System, was die Weitergabe-Logistik vereinfacht.

- **Lambda = 0.0**: keine Bündelung, minimaler Rechenaufwand (Standard)
- **Lambda = 0.5**: moderate Bündelung (empfohlen)
- **Lambda = 3.0**: starke Bündelung, längere Rechenzeit bei großen Gruppen

---

## X_-Kandidaten

Prüflinge mit dem Prefix `X_` erhalten Korrektoren zugewiesen, erscheinen aber nicht im Tagesplan (keine Anwesenheit erforderlich). Sie sind in den Versand- und Weitergabelisten des PDFs enthalten und mit `*` markiert.

---

## Tests

```bash
pip install pytest
pytest test_pvihk.py -v
```

Das Testscript prüft die Kernfunktion `berechne_korrektorenverteilung()` ohne GUI — PySide6 wird gemockt. 11 Tests decken Solver-Korrektheit, Verfügbarkeit, Zeitslot-Validierung, Paar-Bündelung und PDF-Erzeugung ab.

---

## Verwandtes Projekt

**[PIHK](https://github.com/effzett/pihk)** — Prüfungssimulation: berechnet rückwärts welche Punktzahlen zu einer gewünschten Note führen, unter Berücksichtigung der mehrstufigen Rundungsregeln der IHK.

---

## Autor

Frank Zimmermann · [zenmeister.de](https://zenmeister.de)

Entwickelt für den praktischen Einsatz in IHK-Prüfungsausschüssen. Contributions willkommen.
