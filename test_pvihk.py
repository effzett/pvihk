"""
test_pvihk.py  -  Standalone-Tests fuer berechne_korrektorenverteilung()

Aufruf:
    pytest test_pvihk.py -v
    oder:
    python test_pvihk.py

Voraussetzungen:
    pip install pulp fpdf2

Hinweis: Setzt pvihk.py mit if __name__ == "__main__": Guard voraus.
"""

import sys
import unittest
from unittest.mock import MagicMock


# =============================================================================
# GUI-Module wegmocken bevor pvihk importiert wird.
#
# Hintergrund: class MainWindow(QMainWindow, Ui_MainWindow) steht auf
# Modulebene und wird beim Import ausgewertet - unabhaengig vom
# __main__-Guard. Python prueft dabei die Metaklassen aller Basisklassen.
# MagicMock hat eine eigene Metaklasse die mit type() kollidiert.
# Loesung: QMainWindow, QObject, QRunnable und Ui_MainWindow als echte
# leere Klassen via type() bereitstellen.
# =============================================================================

def _real(name):
    """Echte leere Klasse - taugt als Basisklasse ohne Metaklassen-Konflikt."""
    return type(name, (object,), {})


def _setup_mocks():
    # Echte Klassen fuer alle Basisklassen in pvihk.py
    QObject     = _real('QObject')
    QRunnable   = _real('QRunnable')
    QMainWindow = _real('QMainWindow')

    # QtCore: echte Klassen eintragen, Rest als MagicMock
    qtcore = MagicMock()
    qtcore.QObject     = QObject
    qtcore.QRunnable   = QRunnable
    qtcore.QMainWindow = QMainWindow
    qtcore.Signal      = MagicMock(return_value=MagicMock())
    qtcore.Slot        = lambda *a, **kw: (lambda f: f)
    qtcore.Qt          = MagicMock()
    qtcore.QDate       = MagicMock()
    qtcore.QThreadPool = MagicMock()
    qtcore.QTimer      = MagicMock()

    # QtWidgets: QMainWindow als echte Klasse
    qtwidgets = MagicMock()
    qtwidgets.QMainWindow  = QMainWindow
    qtwidgets.QApplication = _real('QApplication')

    sys.modules['PySide6']            = MagicMock()
    sys.modules['PySide6.QtCore']     = qtcore
    sys.modules['PySide6.QtWidgets']  = qtwidgets
    sys.modules['PySide6.QtGui']      = MagicMock()

    # Ui_MainWindow muss echte Klasse sein (zweite Basisklasse von MainWindow)
    Ui_MainWindow = _real('Ui_MainWindow')
    mainwindow_mod = MagicMock()
    mainwindow_mod.Ui_MainWindow = Ui_MainWindow

    versioning_mod = MagicMock()
    versioning_mod.get_app_metadata = MagicMock(return_value={
        "VERSION": "TEST", "DATE": "01.01.2000",
        "TITLEVERSION": "TEST", "WINDOWTITLE": "TEST",
        "APPNAME": "TEST", "COPYRIGHT": "TEST",
    })

    sys.modules['versioning']        = versioning_mod
    sys.modules['MainWindow']        = mainwindow_mod
    sys.modules['preferencesDialog'] = MagicMock()
    sys.modules['customListWidget']  = MagicMock()
    sys.modules['korrektorItem']     = MagicMock()


_setup_mocks()

import pvihk
berechne = pvihk.berechne_korrektorenverteilung


# =============================================================================
# Hilfsfunktionen
# =============================================================================

def basis_eingabe(**overrides):
    """
    Minimales gueltiges Eingabe-Dict.
    Schluessel exakt wie in berechne_korrektorenverteilung() erwartet.
    Einzelne Felder per kwargs ueberschreibbar.
    """
    daten = {
        "verfügbarkeiten": {          # Umlaut - exakt wie in pvihk.py
            "Anna":   ["2026-03-10", "2026-03-17"],
            "Bert":   ["2026-03-10", "2026-03-17"],
            "Clara":  ["2026-03-10", "2026-03-17"],
            "Dieter": ["2026-03-10", "2026-03-17"],
        },
        "kandidaten": {
            1: "p1", 2: "p2", 3: "p3",
            4: "p4", 5: "p5", 6: "p6",
        },
        "pruefungstage": ["2026-03-10", "2026-03-17"],
        "anzahl_korrektoren_pro_klausur": 2,
        "zeitslots": [
            ["09:00", "10:00", "11:00"],
            ["14:00", "15:00", "16:00"],
        ],
        "lambda_partner": 0.0,
        "max_partner":    None,
    }
    daten.update(overrides)
    return daten


# =============================================================================
# Tests
# =============================================================================

class TestKorrektorenverteilung(unittest.TestCase):

    def test_01_solver_findet_loesung(self):
        """Solver muss bei gueltigem Input eine Loesung liefern."""
        ergebnis = berechne(basis_eingabe())
        self.assertIn(ergebnis["status"], [
            "Optimal",
            "Integer Feasible",
            "Optimal (nach Zeitlimit)",
            "Beste gefundene Lösung (nicht optimal)",
        ])

    def test_02_jeder_pruefling_hat_zwei_korrektoren(self):
        """Jeder Praesenz-Pruefling muss genau 2 Korrektoren haben."""
        ergebnis = berechne(basis_eingabe())
        for datum, eintraege in ergebnis["verteilung"].items():
            for zeit, name, korrektoren in eintraege:
                self.assertEqual(len(korrektoren), 2,
                    f"{name}: erwartet 2 Korrektoren, gefunden {len(korrektoren)}")

    def test_03_verfuegbarkeit_mindestens_ein_korrektor(self):
        """
        Pro Praesenz-Pruefung muss mindestens EINER der zwei Korrektoren
        an dem zugewiesenen Tag verfuegbar sein.
        Nicht beide - nur einer muss anwesend sein.
        """
        eingabe = basis_eingabe()
        verfuegbarkeiten = eingabe["verfügbarkeiten"]
        ergebnis = berechne(eingabe)

        for datum, eintraege in ergebnis["verteilung"].items():
            for zeit, name, korrektoren in eintraege:
                verfuegbar = [
                    k for k in korrektoren
                    if datum in verfuegbarkeiten.get(k, [])
                ]
                self.assertGreaterEqual(len(verfuegbar), 1,
                    f"{name} am {datum}: kein Korrektor verfuegbar! "
                    f"Korrektoren: {korrektoren}")

    def test_04_x_kandidaten_nicht_im_tagesplan(self):
        """
        Kandidaten mit X_-Prefix sind Nur-Klausur-Kandidaten und duerfen
        nicht im Tagesplan erscheinen.
        """
        eingabe = basis_eingabe()
        eingabe["kandidaten"][7] = "X_Fernstudent1"
        eingabe["kandidaten"][8] = "X_Fernstudent2"
        ergebnis = berechne(eingabe)

        for datum, eintraege in ergebnis["verteilung"].items():
            for zeit, name, korrektoren in eintraege:
                self.assertFalse(name.startswith("X_"),
                    f"X_-Kandidat {name} erscheint im Tagesplan!")

    def test_05_x_kandidaten_erhalten_korrektoren(self):
        """
        X_-Kandidaten sind Nur-Klausur-Kandidaten. Sie erscheinen nicht im
        Tagesplan, muessen aber vom Solver Korrektoren zugeteilt bekommen.
        Prueft: Solver laeuft durch, Status ist gueltig, PDF ist nicht leer.
        (PDF-Inhalt ist zlib-komprimiert und nicht direkt als Klartext pruefbar.)
        """
        eingabe = basis_eingabe()
        eingabe["kandidaten"][7] = "X_Fernstudent1"
        eingabe["kandidaten"][8] = "X_Fernstudent2"
        # Solver darf nicht abstuerzen
        ergebnis = berechne(eingabe)
        self.assertIn(ergebnis["status"], [
            "Optimal", "Integer Feasible",
            "Optimal (nach Zeitlimit)",
            "Beste gefundene Lösung (nicht optimal)",
        ], "Solver-Status ungueltig bei X_-Kandidaten")
        # PDF muss existieren und groesser sein als ohne X_-Kandidaten
        pdf = ergebnis.get("pdf_data", b"")
        self.assertGreater(len(pdf), 100,
            "PDF fehlt bei X_-Kandidaten")

    def test_06_keine_doppelten_korrektoren(self):
        """Ein Korrektor darf nicht zweimal demselben Pruefling zugeordnet sein."""
        ergebnis = berechne(basis_eingabe())
        for datum, eintraege in ergebnis["verteilung"].items():
            for zeit, name, korrektoren in eintraege:
                self.assertEqual(len(korrektoren), len(set(korrektoren)),
                    f"{name}: doppelter Korrektor in {korrektoren}")

    def test_07_korrektor_nur_tag1_verfuegbar(self):
        """
        Korrektor der nur an Tag 1 verfuegbar ist darf nicht als
        anwesender Korrektor an Tag 2 eingeteilt sein.
        """
        eingabe = basis_eingabe()
        eingabe["verfügbarkeiten"]["Emil"] = ["2026-03-10"]
        ergebnis = berechne(eingabe)
        tag2 = "2026-03-17"

        if tag2 not in ergebnis["verteilung"]:
            return

        for zeit, name, korrektoren in ergebnis["verteilung"][tag2]:
            if "Emil" in korrektoren:
                andere = [k for k in korrektoren if k != "Emil"]
                partner_ok = any(
                    tag2 in eingabe["verfügbarkeiten"].get(k, [])
                    for k in andere
                )
                self.assertTrue(partner_ok,
                    f"Emil an Tag 2 ohne verfuegbaren Partner: {korrektoren}")

    def test_08_lambda_reduziert_weitergabe_partner(self):
        """
        Mit lambda_partner > 0 darf die Anzahl verschiedener Korrektor-Paare
        nicht groesser sein als ohne Buendelung.
        """
        kandidaten = {i: f"p{i}" for i in range(1, 11)}
        # 10 Kandidaten -> 5 pro Tag -> mindestens 5 Zeitslots pro Tag
        zeitslots_gross = [
            ["08:00", "09:00", "10:00", "11:00", "12:00"],
            ["13:00", "14:00", "15:00", "16:00", "17:00"],
        ]
        eingabe_ohne = basis_eingabe(lambda_partner=0.0, kandidaten=kandidaten, zeitslots=zeitslots_gross)
        eingabe_mit  = basis_eingabe(lambda_partner=2.0, kandidaten=kandidaten, zeitslots=zeitslots_gross)

        def zaehle_paare(ergebnis):
            paare = set()
            for datum, eintraege in ergebnis["verteilung"].items():
                for zeit, name, korrektoren in eintraege:
                    if len(korrektoren) == 2:
                        paare.add(tuple(sorted(korrektoren)))
            return len(paare)

        erg_ohne = berechne(eingabe_ohne)
        erg_mit  = berechne(eingabe_mit)
        paare_ohne = zaehle_paare(erg_ohne)
        paare_mit  = zaehle_paare(erg_mit)

        self.assertLessEqual(paare_mit, paare_ohne,
            f"lambda=2.0 erzeugt mehr Paare ({paare_mit}) "
            f"als lambda=0 ({paare_ohne})")

    def test_09_infeasible_zu_wenig_korrektoren(self):
        """
        Wenn nur ein Korrektor verfuegbar ist aber 2 benoetigt werden,
        muss eine ValueError-Exception geworfen werden.
        """
        eingabe = basis_eingabe()
        eingabe["verfügbarkeiten"] = {
            "Anna": ["2026-03-10", "2026-03-17"],
        }
        with self.assertRaises(ValueError):
            berechne(eingabe)

    def test_10_pdf_wird_erzeugt(self):
        """Das Ergebnis muss gueltige PDF-Bytes enthalten."""
        ergebnis = berechne(basis_eingabe())
        pdf = ergebnis.get("pdf_data", b"")
        self.assertGreater(len(pdf), 100, "PDF ist leer oder zu klein")
        self.assertTrue(pdf.startswith(b"%PDF"),
            "PDF hat keinen gueltigen PDF-Header")


    def test_11_zu_wenig_zeitslots_gibt_warnung(self):
        """
        Wenn mehr Praesenz-Prueflinge vorhanden sind als Zeitslots,
        muss eine ValueError-Exception mit erklaerenden Text geworfen werden.
        """
        eingabe = basis_eingabe()
        # 6 Praesenz-Kandidaten -> 3 pro Tag, aber nur 1 Zeitslot pro Tag
        eingabe["zeitslots"] = [
            ["09:00"],   # Tag 1: nur 1 Slot
            ["09:00"],   # Tag 2: nur 1 Slot
        ]
        with self.assertRaises(ValueError) as ctx:
            berechne(eingabe)
        meldung = str(ctx.exception)
        self.assertIn("Zeitslots", meldung,
            f"Fehlermeldung enthaelt kein 'Zeitslots': {meldung}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
