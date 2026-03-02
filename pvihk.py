

import sys
import os

def get_cbc_path():
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, "cbc")



def ensure_cbc_on_path():
    """Ensure bundled 'cbc' is present and executable when running from a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
        cbc = os.path.join(base_path, "cbc")

        # If the binary was bundled, make sure it is executable (macOS can lose exec bits after packaging).
        try:
            if os.path.exists(cbc):
                os.chmod(cbc, 0o755)
        except Exception:
            pass

        # Remove quarantine attribute if present (best-effort).
        try:
            import subprocess
            subprocess.run(["xattr", "-d", "com.apple.quarantine", cbc], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # Put extraction directory first in PATH so PuLP finds 'cbc' without needing a path= argument.
        os.environ["PATH"] = base_path + os.pathsep + os.environ.get("PATH", "")


import sys
import os
import platform
import tempfile
import json

from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, QDate, QObject, QRunnable, QThreadPool, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QAbstractItemView, QListWidgetItem,
    QHeaderView, QTableWidgetItem, QFileDialog, QMessageBox, QListWidget
)

import pulp
from collections import defaultdict
from datetime import datetime
from fpdf import FPDF

from pathlib import Path

from versioning import get_app_metadata
meta = get_app_metadata(increment=True)
VERSION = meta["VERSION"]
DATE = meta["DATE"]
TITLEVERSION = meta["TITLEVERSION"]
WINDOWTITLE = meta["WINDOWTITLE"]
APPNAME = meta["APPNAME"]
COPYRIGHT = meta["COPYRIGHT"]

from  MainWindow import Ui_MainWindow
from preferencesDialog import PreferencesDialog

from customListWidget import CustomListWidget

# Plattformabhängige Lokation der aktuellen Session-Datei
# eingetragene Korrektoren und Prüflinge
SESSION_FILE = Path.home() / ".pvihk_session.json"
PREFERENCES_FILE = Path.home() / ".preferences.json"

# Den aktuellen Pfad für Entwicklung und Produktivbetrieb merken
if getattr(sys, 'frozen', False):
    # Gebündelt als EXE oder APP
    BASIS_DIR = os.path.dirname(sys.executable)
else:
    # Normal als .py Script
    BASIS_DIR = os.path.dirname(os.path.abspath(__file__))

# CBC-Binary auf macOS ausführbar machen (Permissions gehen beim Bundling verloren)
ensure_cbc_on_path()

# Für Multithreading
# Signale aus dem Optimierungsblock
class OptimierungsWorkerSignals(QObject):
    finished = Signal(dict)
    error = Signal(str)

# Routine mit Optimierungsblock wird in diesem Runner aufgerufen
class OptimierungsWorker(QRunnable):
    def __init__(self, eingabedaten):
        super().__init__()
        self.eingabedaten = eingabedaten
        self.signals = OptimierungsWorkerSignals()

    @Slot()
    def run(self):
        try:
            ergebnis = berechne_korrektorenverteilung(self.eingabedaten)
            self.signals.finished.emit(ergebnis)    # Bei Ende: Ergebnis-Dict
        except Exception as e:
            self.signals.error.emit(str(e))         # Bei Fehler String


def berechne_korrektorenverteilung(eingabedaten) -> dict:
    """
    Optimiert die Korrektorenverteilung und erzeugt ein PDF.

    Erwartet:
        eingabedaten["zeitslots"] = [liste_tag1, liste_tag2]

    Kandidaten mit Prefix "X_" sind reine Klausur-Korrekturen:
        - werden gleichmäßig auf Korrektoren verteilt
        - erscheinen NICHT im Tagesplan (keine Anwesenheit nötig)
        - erscheinen in den Versand-/Weitergabelisten
    """

    korrektornamen = list(eingabedaten["verfügbarkeiten"].keys())
    klausurnamen = eingabedaten["kandidaten"]
    termine = eingabedaten["pruefungstage"]
    anzahl_korrektoren = eingabedaten.get("anzahl_korrektoren_pro_klausur", 2)

    # --- Kandidaten trennen: Präsenzprüfung vs. reine Klausurkorrektur (X_-Prefix) ---
    praesenz_klausuren    = [f"K_{i}" for i, name in klausurnamen.items() if not name.startswith("X_")]
    nur_klausur_klausuren = [f"K_{i}" for i, name in klausurnamen.items() if     name.startswith("X_")]
    alle_klausuren = praesenz_klausuren + nur_klausur_klausuren

    # === Zeitslots überprüfen ===
    zeitslots = eingabedaten.get("zeitslots")
    if not isinstance(zeitslots, list) or len(zeitslots) != 2 or not all(isinstance(t, list) for t in zeitslots):
        raise ValueError("zeitslots müssen eine Liste mit zwei Listen sein (je Tag).")

    sortierte_zeiten = []
    for tag_slots in zeitslots:
        zeiten_tag = [datetime.strptime(z, "%H:%M").time() for z in tag_slots]
        sortierte_zeiten.append(zeiten_tag)

    tag_verfuegbarkeit = {datum: [] for datum in termine}
    for korrektor, tage in eingabedaten["verfügbarkeiten"].items():
        for tag in tage:
            tag_verfuegbarkeit[tag].append(korrektor)

    # Tagesaufteilung nur für Präsenz-Kandidaten
    anzahl_tag1 = (len(praesenz_klausuren) + 1) // 2
    anzahl_tag2 = len(praesenz_klausuren) - anzahl_tag1

    prob = pulp.LpProblem("Korrekturverteilung", pulp.LpMinimize)

    # x für ALLE Kandidaten (Präsenz + nur Klausur)
    x = pulp.LpVariable.dicts("x",
        ((k, p) for k in alle_klausuren for p in korrektornamen), 0, 1, pulp.LpBinary)

    # klausur_tag und anwesenheit nur für Präsenz-Kandidaten
    klausur_tag = pulp.LpVariable.dicts("klausur_tag",
        ((k, t) for k in praesenz_klausuren for t in [0, 1]), 0, 1, pulp.LpBinary)
    anwesenheit = pulp.LpVariable.dicts("anwesenheit",
        ((p, t) for p in korrektornamen for t in [0, 1]), 0, 1, pulp.LpBinary)

    # Belastung über ALLE Kandidaten → faire Gesamtverteilung
    belastung = {p: pulp.lpSum(x[k, p] for k in alle_klausuren) for p in korrektornamen}
    mittlere_belastung = anzahl_korrektoren * len(alle_klausuren) / len(korrektornamen)
    abweichung = pulp.LpVariable.dicts("abweichung", korrektornamen, 0)

    for p in korrektornamen:
        prob += belastung[p] - mittlere_belastung <= abweichung[p]
        prob += mittlere_belastung - belastung[p] <= abweichung[p]

    # Hier erfolgt die Gewichtung: Gleichverteilung / Anwesenheit
    prob += (
        1.0 * pulp.lpSum(abweichung[p] for p in korrektornamen) +
        0.1 * pulp.lpSum(anwesenheit[p, t] for p in korrektornamen for t in [0, 1])
    )

    # Jede Klausur (Präsenz + nur-Klausur) bekommt genau anzahl_korrektoren Korrektoren
    for k in alle_klausuren:
        prob += pulp.lpSum(x[k, p] for p in korrektornamen) == anzahl_korrektoren

    # Nur für Präsenz-Kandidaten: Tageszuordnung + Verfügbarkeitseinschränkung
    for k in praesenz_klausuren:
        for t in [0, 1]:
            gruppe = tag_verfuegbarkeit[termine[t]]
            prob += klausur_tag[k, t] <= pulp.lpSum(x[k, p] for p in gruppe)
        prob += klausur_tag[k, 0] + klausur_tag[k, 1] == 1

    if praesenz_klausuren:
        prob += pulp.lpSum(klausur_tag[k, 0] for k in praesenz_klausuren) == anzahl_tag1
        prob += pulp.lpSum(klausur_tag[k, 1] for k in praesenz_klausuren) == anzahl_tag2

    # Anwesenheits-Trigger: nur durch Präsenz-Zuordnungen (nicht durch nur-Klausur)
    for p in korrektornamen:
        for t in [0, 1]:
            if p in tag_verfuegbarkeit[termine[t]]:
                for k in praesenz_klausuren:
                    prob += x[k, p] <= anwesenheit[p, t]

    if praesenz_klausuren:
        for t in [0, 1]:
            prob += pulp.lpSum(anwesenheit[p, t] for p in tag_verfuegbarkeit[termine[t]]) >= 3

    import time
    start_time = time.time()
    prob.solve(pulp.PULP_CBC_CMD(timeLimit=10, msg=True))
    end_time = time.time()

    solver_status = pulp.LpStatus[prob.status]
    duration = end_time - start_time

    if solver_status in ["Infeasible", "Unbounded", "Undefined", "Not Solved"]:
        raise ValueError(f"Optimierung nicht erfolgreich! Status: {solver_status}")
    if solver_status == "Optimal" and duration >= 9:
        final_status = "Optimal (nach Zeitlimit)"
    elif solver_status == "Optimal":
        final_status = "Optimal"
    elif solver_status == "Integer Feasible":
        final_status = "Beste gefundene Lösung (nicht optimal)"
    else:
        final_status = solver_status

    zuordnung = defaultdict(list)
    klausur_tage = {}
    for (k, p), var in x.items():
        if var.varValue == 1:
            zuordnung[k].append(p)
    for (k, t), var in klausur_tag.items():
        if var.varValue == 1:
            klausur_tage[k] = t

    klausurverteilung = defaultdict(list)   # nur Präsenz → Tagesplan
    versand_start     = defaultdict(list)   # alle Kandidaten → Versandliste
    weitergaben       = defaultdict(list)   # alle Kandidaten → Weitergabeliste

    # --- Tagesplan: nur Präsenz-Kandidaten ---
    for t in [0, 1]:
        datum = termine[t]
        klausuren_fuer_tag = sorted(k for k, tag in klausur_tage.items() if tag == t)
        zeiten = sortierte_zeiten[t][:len(klausuren_fuer_tag)]
        for k, zeit in zip(klausuren_fuer_tag, zeiten):
            zeit_str = zeit.strftime("%H:%M")
            pruefer = zuordnung[k]
            klausurname = klausurnamen[int(k.split("_")[1])]
            klausurverteilung[datum].append((zeit_str, klausurname, pruefer))

    # --- Versand/Weitergabe: ALLE Kandidaten (Präsenz + nur-Klausur) ---
    paarweise = defaultdict(list)
    for k, pruefer in zuordnung.items():
        if len(pruefer) == 2:
            p1, p2 = sorted(pruefer)
            paarweise[(p1, p2)].append(k)
    for (p1, p2), klist in paarweise.items():
        versand_start[p1].extend(klist)
        weitergaben[(p1, p2)].extend(klist)

    # ================================================================
    # PDF erstellen
    # ================================================================
    class FooterPDF(FPDF):
        def footer(self):
            self.set_y(-10)
            self.set_font("Arial", size=8)
            self.cell(0, 5, f"Erstellt von PVIHK({VERSION}) am {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} - created by fz@zenmeister.de", align="C")

    pdf = FooterPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 8, "Prüfungsverteilung nach Tagen und aktiven Korrektoren", ln=True)
    pdf.ln(3)

    # --- Abschnitt 1: Tagesplan (nur Präsenzprüfungen) ---
    for datum, eintraege in klausurverteilung.items():
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 6, f"Zeitplan: {datum}", ln=True)
        pdf.set_fill_color(200, 200, 220)
        pdf.cell(22, 6, "Zeit",        border=1, fill=True)
        pdf.cell(78, 6, "Prüfung",     border=1, fill=True)
        pdf.cell(85, 6, "Korrektoren", border=1, ln=True, fill=True)
        pdf.set_font("Arial", size=9)
        for zeit, klausurname, pruefer in eintraege:
            pdf.cell(22, 6, zeit,               border=1)
            pdf.cell(78, 6, klausurname,         border=1)
            pdf.cell(85, 6, ", ".join(pruefer),  border=1, ln=True)
        pdf.ln(3)

    # --- Abschnitt 2: Nur-Klausur-Korrekturen (X_-Kandidaten, kein Zeitplan) ---
    if nur_klausur_klausuren:
        pdf.ln(4)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 6, "Klausur-Korrekturen (ohne Präsenzprüfung)", ln=True)
        pdf.set_font("Arial", "B", 9)
        pdf.set_fill_color(220, 220, 200)
        pdf.cell(100, 6, "Prüfling",    border=1, fill=True)
        pdf.cell(85,  6, "Korrektoren", border=1, ln=True, fill=True)
        pdf.set_font("Arial", size=9)
        for k in nur_klausur_klausuren:
            klausurname = klausurnamen[int(k.split("_")[1])]
            pruefer     = zuordnung[k]
            pdf.cell(100, 6, klausurname,        border=1)
            pdf.cell(85,  6, ", ".join(pruefer), border=1, ln=True)
        pdf.ln(3)

    # --- Abschnitt 3: Korrektorenübersicht mit Partnern ---
    pdf.ln(4)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 6, "Korrektorenübersicht mit Partnern", ln=True)
    pdf.set_font("Arial", "B", 9)
    pdf.cell(60,  6, "Korrektor (gesamt)",     border=1)
    pdf.cell(130, 6, "Verteilung auf Partner", border=1, ln=True)
    pdf.set_font("Arial", size=9)

    korrektor_partner = defaultdict(lambda: defaultdict(int))
    korrektor_gesamt  = defaultdict(int)

    for k, pruefer in zuordnung.items():
        if len(pruefer) == 2:
            p1, p2 = sorted(pruefer)
            korrektor_partner[p1][p2] += 1
            korrektor_partner[p2][p1] += 1
            korrektor_gesamt[p1] += 1
            korrektor_gesamt[p2] += 1

    for p in sorted(korrektornamen):
        partnertext = ', '.join(f"{q}({n})" for q, n in sorted(korrektor_partner[p].items()))
        pdf.set_text_color(0, 0, 200)
        pdf.cell(60,  6, f"{p} ({korrektor_gesamt[p]})", border=1)
        pdf.set_text_color(0)
        pdf.cell(130, 6, partnertext, border=1, ln=True)

    # --- Abschnitt 4: Versand und Weitergabe (alle Kandidaten) ---
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 6, "Versand und Weitergabe der Klausuren", ln=True)
    pdf.set_font("Arial", size=8)
    pdf.set_text_color(120)
    pdf.cell(0, 5, "* = reine Klausurkorrektur (keine Präsenzprüfung)", ln=True)
    pdf.set_text_color(0)
    pdf.ln(4)
    pdf.set_font("Arial", "B", 10)
    for sender, klist in versand_start.items():
        pdf.cell(0, 6, f"{sender} erhält:", ln=True)
        pdf.set_font("Arial", size=10)
        for k in klist:
            name   = klausurnamen[int(k.split('_')[1])]
            marker = " *" if name.startswith("X_") else ""
            pdf.cell(0, 6, f"   - {name}{marker}", ln=True)
        pdf.set_font("Arial", "B", 10)
        pdf.ln(2)
    for (sender, empfaenger), klist in weitergaben.items():
        pdf.cell(0, 6, f"{sender} -> {empfaenger}:", ln=True)
        pdf.set_font("Arial", size=10)
        for k in klist:
            name   = klausurnamen[int(k.split('_')[1])]
            marker = " *" if name.startswith("X_") else ""
            pdf.cell(0, 6, f"   - {name}{marker}", ln=True)
        pdf.set_font("Arial", "B", 10)
        pdf.ln(2)

    pdf_bytes = pdf.output(dest='S').encode('latin1')

    return {
        "pdf_data":   pdf_bytes,
        "verteilung": klausurverteilung,   # nur Präsenz → GUI-Tabellen
        "status":     final_status
    }



class MainWindow(QMainWindow,Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # Eigenes Widget anstelle des gebauten listWidget setzen
        # Neues Widget erzeugen
        custom_widget = CustomListWidget(self)
        custom_widget.setObjectName("listWidgetList")  # Name zum Suchen

        # Layout und Position finden
        layout = self.verticalLayout_6
        index = layout.indexOf(self.listWidgetList)

        # Altes Widget entfernen und neues einsetzen
        self.listWidgetList.setParent(None) # abkoppeln
        layout.insertWidget(index, custom_widget)

        # Referenz aktualisieren
        self.listWidgetList = custom_widget

        self.preferences_file = PREFERENCES_FILE
        self.zeitslots = [
            [
                "09:00", "10:00", "11:00", "12:00",
                "14:00", "15:00", "16:00", "17:00"
            ],
            [
                "09:00", "10:00", "11:00", "12:00",
                "14:00", "15:00", "16:00", "17:00"
            ]
        ]

        self.lade_preferences()

        self.setWindowTitle(TITLEVERSION)
        icon_path = os.path.join(BASIS_DIR, "assets", "PVIHK.png")
        #        icon_path = resource_path("PVIHK.png")

        self.setWindowIcon(QIcon(icon_path))

        self.pushButtonCancelOptimize.hide()    # Wird z.Z. nicht gebraucht

        self.threadpool = QThreadPool()

        self.pushButtonOptimize.setEnabled(True)
        self.pushButtonCancelOptimize.setEnabled(False)

        self.letztes_pdf_data = None  # Inhalt des aktuell erzeugten PDFs (Bytes)
        self.actionPDF_abspeichern.triggered.connect(self.pdf_abspeichern)

        self.actionSession_save.triggered.connect(self.session_save)
        self.actionSession_read.triggered.connect(self.session_read)

        # About box
        self.actionAbout.triggered.connect(self.about_box)

        self.actionEinstellungen.triggered.connect(self.open_preferences_dialog)

        self.combos1 = []
        self.combos2 = []
        self.listWidgetList.setAcceptDrops(True)    # Prüflingslisten reinziehen
        # noinspection PyUnresolvedReferences
        self.listWidgetList.setDragDropMode(QAbstractItemView.DropOnly)

        # Kleine Schönheitskorrekturen: Selektionsartefakte vermeiden
        # noinspection PyUnresolvedReferences
        self.listWidget1.setSelectionMode(QAbstractItemView.NoSelection)
        # noinspection PyUnresolvedReferences
        self.listWidget2.setSelectionMode(QAbstractItemView.NoSelection)
        # noinspection PyUnresolvedReferences
        self.listWidget1.setFocusPolicy(Qt.NoFocus)
        # noinspection PyUnresolvedReferences
        self.listWidget2.setFocusPolicy(Qt.NoFocus)

        self.listWidget1.currentItemChanged.connect(self.disable_current_item)
        self.listWidget2.currentItemChanged.connect(self.disable_current_item)

        # Signal verbinden
        self.date1Edit.dateChanged.connect(self.sync_date1)
        self.date2Edit.dateChanged.connect(self.sync_date2)

        # PushButton Cancel verbinden
        self.pushButtonCancel.clicked.connect(self.cancel_program)

        # PDF anzeigen über Menüeintrag
        self.actionPDF_anzeigen.triggered.connect(self.pdf_anzeigen)

        self.actionKandidaten_einlesen.triggered.connect(self.kandidaten_einlesen)
        self.actionKorrektoren_einlesen.triggered.connect(self.korrektoren_einlesen)
        self.actionKandidaten_speichern.triggered.connect(self.kandidaten_speichern)
        self.actionKorrektoren_speichern.triggered.connect(self.korrektoren_speichern)

        # Setze aktuelles Datum und eine Woche weiter
        # TODO: später noch Funktion für Datumsgleichheit benötigt!
        self.date1Edit.setDate(QDate.currentDate().addDays(7))
        self.date2Edit.setDate(QDate.currentDate().addDays(14))

        # Korrektoren Listen
        # Feste Auswahlmöglichkeiten
        # TODO: Später über init oder Einstelllungen einlesen lassen...
        options = [" ", "Korrektor1", "Korrektor2", "Korrektor3", "Korrektor4", "Korrektor5", "Korrektor6", "Korrektor7"]
        self.listWidget1.setSpacing(2)  # Kein extra Abstand zwischen Items
        self.listWidget2.setSpacing(2)  # Kein extra Abstand zwischen Items

        from korrektorItem import KorrektorItem  # ganz oben im Modul einfügen, falls noch nicht geschehen

        self.korrektor_items_tag1 = []
        self.korrektor_items_tag2 = []

        korrektoren_default = [f"Korrektor {i + 1}" for i in range(10)]

        for name in korrektoren_default:
            # Tag 1
            item1 = QListWidgetItem(self.listWidget1)
            widget1 = KorrektorItem(name=name, checked=False)
            self.listWidget1.setItemWidget(item1, widget1)
            item1.setSizeHint(widget1.sizeHint())
            self.korrektor_items_tag1.append(widget1)

            # Tag 2
            item2 = QListWidgetItem(self.listWidget2)
            widget2 = KorrektorItem(name=name, checked=False)
            self.listWidget2.setItemWidget(item2, widget2)
            item2.setSizeHint(widget2.sizeHint())
            self.korrektor_items_tag2.append(widget2)

            # Jetzt per StyleSheet nur untere Border simulieren (quasi horizontale Linien)
            self.table1Widget.setStyleSheet("""
            QTableView::item {
                border-bottom: 1px solid gray;
            }
            """)
            self.table2Widget.setStyleSheet("""
            QTableView::item {
                border-bottom: 1px solid gray;
            }
            """)

            self.table1Widget.setColumnWidth(0, 50)
            # noinspection PyUnresolvedReferences
            self.table1Widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            # noinspection PyUnresolvedReferences
            self.table1Widget.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

            self.table2Widget.setColumnWidth(0, 50)
            # noinspection PyUnresolvedReferences
            self.table2Widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            # noinspection PyUnresolvedReferences
            self.table2Widget.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        self.pushButtonOptimize.clicked.connect(self.optimierung_starten)

    # Überprüfen ob Duplikate bei den Korrektoren vorhanden sin
    @staticmethod
    def check_for_duplicates(combos):
        used_texts = set()

        for combo in combos:
            text = combo.currentText().strip()

            if text and text in used_texts:
                # Doppelt! Zurücksetzen
                combo.setCurrentIndex(0)
            else:
                used_texts.add(text)

    def lade_zeitslots(self):
        try:
            if self.preferences_file.exists():
                with open(self.preferences_file, "r", encoding="utf-8") as f:
                    daten = json.load(f)
                zeitslots = daten.get("zeitslots")
                if isinstance(zeitslots, list) and all(isinstance(z, list) for z in zeitslots):
                    return zeitslots
        except Exception as e:
            print(f"Fehler beim Laden der Zeitslots: {e}")

        # Fallback auf Default-Werte
        return [
            ["09:00", "10:00", "11:00", "12:00"],  # Tag 1
            ["14:00", "15:00", "16:00", "17:00"]  # Tag 2
        ]

    def disable_current_item(self, _, __):
        self.listWidget1.setCurrentItem(None)


    # Funktionen: Datum von oben nach unten kopieren
    def sync_date1(self, dat):
        self.labelDate1.setText(dat.toString("dd.MM.yyyy"))

    def sync_date2(self, dat):
        self.labelDate2.setText(dat.toString("dd.MM.yyyy"))

    # Programm mit Cancel beenden
    def cancel_program(self):
        self.close()  # Fenster schließen (sanft)

    # Für den Import über Drag-and-Drop bei der Prüflingliste
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                filepath = url.toLocalFile()
                if filepath.endswith(".txt"):
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            lines = f.readlines()
                        self.listWidgetList.clear()
                        for line in lines:
                            #self.listWidgetList.addItem(line.strip())
                            item = QListWidgetItem(line.strip())
                            item.setFlags(item.flags() | Qt.ItemIsEditable)
                            self.listWidgetList.addItem(item)
                        self.listWidgetList._limit_warning_shown = False

                    except Exception as e:
                        print(f"Fehler beim Lesen der Datei: {e}")

            event.acceptProposedAction()
        else:
            event.ignore()

    def sammle_eingabedaten(self):
        """
        Liest alle relevanten Daten aus der GUI und baut das Eingabedaten-Dictionary für die Optimierung.
        """
        eingabedaten = {}

        # 1. Korrektoren und individuelle Verfügbarkeiten erfassen
        verfuegbarkeiten = {}

        # Verfügbarkeiten von Tag 1 sammeln (listWidget1)
        tag1_datum = self.date1Edit.date().toString("yyyy-MM-dd")
        tag2_datum = self.date2Edit.date().toString("yyyy-MM-dd")

        # Verfügbarkeiten aus den neuen Widgets
        korrektor_zwischenspeicher = {}

        # Tag 1
        for widget in self.korrektor_items_tag1:
            name = widget.get_name().strip()
            if name:
                korrektor_zwischenspeicher.setdefault(name, []).append((tag1_datum, widget.is_checked()))

        # Tag 2
        for widget in self.korrektor_items_tag2:
            name = widget.get_name().strip()
            if name:
                korrektor_zwischenspeicher.setdefault(name, []).append((tag2_datum, widget.is_checked()))

        # Jetzt filtern: nur Korrektoren mit mindestens einer aktiven Checkbox
        for name, tag_infos in korrektor_zwischenspeicher.items():
            tage = [tag for tag, checked in tag_infos if checked]
            if tage:
                verfuegbarkeiten[name] = tage

        eingabedaten["verfügbarkeiten"] = verfuegbarkeiten

        # 2. Kandidatenliste aus listWidgetList lesen
        kandidaten = {}
        for i in range(self.listWidgetList.count()):
            item = self.listWidgetList.item(i)
            text = item.text().strip()
            if text:
                kandidaten[i + 1] = text  # Klausurnummer ab 1

        eingabedaten["kandidaten"] = kandidaten

        # 3. Prüfungstage (Datum 1 und 2)
        pruefungstage = []
        if self.date1Edit.date():
            pruefungstage.append(tag1_datum)
        if self.date2Edit.date():
            pruefungstage.append(tag2_datum)

        eingabedaten["pruefungstage"] = pruefungstage

        # 4. Anzahl Korrektoren pro Klausur
        eingabedaten["anzahl_korrektoren_pro_klausur"] = 2  # aktuell fest

        # 5. Prüfungszeitslots (aus Dialog oder Standard)
        eingabedaten["zeitslots"] = self.zeitslots  # <--- hier ergänzen

        return eingabedaten

    # Optimierung starten (bisher nur Daten sammeln und anzeigen)

    def optimierung_starten(self):
        """
        Startet die Optimierung, verarbeitet das Ergebnis und zeigt Statusmeldungen an.
        """

        self.statusBar().clearMessage()
        self.statusBar().setStyleSheet("")
        self.pushButtonCancelOptimize.setEnabled(True)
        self.pushButtonOptimize.setEnabled(False)

        # Tabellen leeren und GUI sofort aktualisieren
        self.table1Widget.setRowCount(0)
        self.table2Widget.setRowCount(0)
        QApplication.processEvents()

        eingabedaten = self.sammle_eingabedaten()

        # Zeitslots aus Einstellungen übernehmen (wenn vorhanden)
        if self.zeitslots:
            eingabedaten["zeitslots"] = self.zeitslots

        print("Eingabedaten für die Optimierung:")
        import pprint
        pprint.pprint(eingabedaten)

        # Worker erstellen
        worker =  OptimierungsWorker(eingabedaten)
        worker.signals.finished.connect(self.optimierung_abgeschlossen)
        worker.signals.error.connect(self.optimierung_fehler)
        # ergebnis = berechne_korrektorenverteilung(eingabedaten)
        # Worker starten
        self.threadpool.start(worker)


    def optimierung_abgeschlossen(self, ergebnis):
        self.verarbeite_ergebnis(ergebnis)

        self.pushButtonCancelOptimize.setEnabled(False)
        self.pushButtonOptimize.setEnabled(True)

        status = ergebnis.get("status", "Unknown")
        if status == "Optimal":
            self.statusBar().showMessage("Optimierung erfolgreich abgeschlossen (optimale Lösung).")
        elif status == "Optimal (nach Zeitlimit)":
            self.statusBar().showMessage("Optimierung abgeschlossen (optimale Lösung, aber durch Zeitlimit erreicht).")
        elif status == "Beste gefundene Lösung (nicht optimal)":
            self.statusBar().showMessage("Optimierung abgeschlossen (beste gefundene Lösung nach Zeitlimit).")
        else:
            self.statusBar().showMessage(f"Optimierung abgeschlossen (Status: {status})")

    def optimierung_fehler(self, fehlermeldung):
        fehlertext = f"Fehler: {fehlermeldung}"
        print(fehlertext)
        self.statusBar().setStyleSheet("color: red;")
        self.statusBar().showMessage(fehlertext)

        self.pushButtonCancelOptimize.setEnabled(False)
        self.pushButtonOptimize.setEnabled(True)

    def pdf_anzeigen(self):
        """
        Zeigt das aktuell erzeugte PDF an (aus dem Speicher).
        """
        if not self.letztes_pdf_data:
            print("Kein PDF im Speicher vorhanden.")
            return

        try:
            # Temporäre Datei erstellen
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(self.letztes_pdf_data)
                temp_path = tmp.name

            # Datei öffnen (plattformabhängig)
            if sys.platform == "darwin":  # macOS
                os.system(f"open '{temp_path}'")
            elif sys.platform == "win32":  # Windows
                os.startfile(temp_path)
            elif sys.platform.startswith("linux"):
                os.system(f"xdg-open '{temp_path}'")
            else:
                print("Unbekanntes Betriebssystem.")
        except Exception as e:
            print(f"Fehler beim Öffnen der PDF-Datei: {e}")

    def verarbeite_ergebnis(self, ergebnis):
        """
        Verarbeitet das Ergebnis der Optimierung:
        - Speichert das erzeugte PDF (als Bytes)
        - Füllt die Tabellen für Tag 1 und Tag 2 neu
        """

        # 1. PDF-Daten speichern
        self.letztes_pdf_data = ergebnis.get("pdf_data")

        if self.letztes_pdf_data:
            print("Optimierung abgeschlossen. PDF liegt im Speicher.")
        else:
            print("Kein PDF im Ergebnis enthalten.")

        # 2. Tabellen leeren
        self.table1Widget.setRowCount(0)
        self.table2Widget.setRowCount(0)

        # 3. Verteilung holen
        verteilung = ergebnis.get("verteilung", {})

        # 4. Tabellen füllen
        pruefungstage = self.sammle_eingabedaten()["pruefungstage"]

        for tag_index, datum in enumerate(pruefungstage):
            if datum not in verteilung:
                continue

            table = self.table1Widget if tag_index == 0 else self.table2Widget
            eintraege = verteilung[datum]

            for eintrag in eintraege:
                if isinstance(eintrag, (list, tuple)) and len(eintrag) == 3:
                    zeit, pruefling, korr_pair = eintrag
                else:
                    continue  # falls falsches Format, überspringen

                row = table.rowCount()
                table.insertRow(row)

                # Uhrzeit
                table.setItem(row, 0, QTableWidgetItem(zeit))

                # Prüfling
                table.setItem(row, 1, QTableWidgetItem(pruefling))

                # Korrektoren
                korr_name = ", ".join(korr_pair) if isinstance(korr_pair, (list, tuple)) else str(korr_pair)
                table.setItem(row, 2, QTableWidgetItem(korr_name))

    def pdf_abspeichern(self)-> None:
        """
        Speichert das aktuell erzeugte PDF über einen Dateidialog ab.
        """
        if not self.letztes_pdf_data:
            print("Kein PDF im Speicher vorhanden.")
            return

        # Dateidialog öffnen
        dateiname, _ = QFileDialog.getSaveFileName(
            self,
            "PDF speichern unter...",
            filter="PDF Dateien (*.pdf)"
        )

        if not dateiname:
            return

        if not dateiname.lower().endswith(".pdf"):
            dateiname += ".pdf"

        try:
            with open(dateiname, "wb") as f:
                f.write(self.letztes_pdf_data)
            print(f"PDF erfolgreich gespeichert: {dateiname}")
        except Exception as e:
            print(f"Fehler beim Speichern des PDFs: {e}")



    def kandidaten_einlesen(self) -> None:
        """
        Liest eine Textdatei mit Kandidaten ein und füllt die listWidgetList.
        """
        dateiname, _ = QFileDialog.getOpenFileName(
            self,
            "Kandidatenliste laden...",
            filter="Textdateien (*.txt)"
        )

        if not dateiname:
            return  # Abbruch

        try:
            with open(dateiname, "r", encoding="utf-8") as f:
                lines = f.readlines()

            self.listWidgetList.clear()

            for line in lines:
                item = QListWidgetItem(line.strip())
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                self.listWidgetList.addItem(item)

            self.listWidgetList._limit_warning_shown = False

            print(f"{len(lines)} Kandidaten erfolgreich eingelesen.")
        except Exception as e:
            print(f"Fehler beim Einlesen der Kandidatenliste: {e}")

    def korrektoren_einlesen(self):
        dateiname, _ = QFileDialog.getOpenFileName(
            self,
            "Korrektorenliste laden...",
            filter="Textdateien (*.txt)"
        )

        if not dateiname:
            return

        try:
            with open(dateiname, "r", encoding="utf-8") as f:
                zeilen = [line.strip() for line in f if line.strip()]
            version = 1
            if zeilen and zeilen[0].startswith("# version="):
                try:
                    version = int(zeilen[0].split("=")[1])
                except ValueError:
                    version = 0  # ungültige Versionsangabe
                zeilen.pop(0)  # Entferne die Versionszeile

            if version != 2:
                print(f"Korrektorenliste nicht geladen: inkompatible Version {version} (erwartet: 2)")
                return

        except Exception as e:
            print(f"Fehler beim Einlesen der Korrektoren: {e}")
            return

        # Maximal 10 übernehmen
        zeilen = zeilen[:10]

        # Beide Tage aktualisieren (gleichmäßig)
        for i, zeile in enumerate(zeilen):
            parts = zeile.split(";")
            name = parts[0].strip()
            if not name:
                continue  # Zeile ignorieren, wenn Name leer

            checked = parts[1].strip() == "1" if len(parts) > 1 else False

            if i < len(self.korrektor_items_tag1):
                self.korrektor_items_tag1[i].set_name(name)
                self.korrektor_items_tag1[i].set_checked(checked)
            if i < len(self.korrektor_items_tag2):
                self.korrektor_items_tag2[i].set_name(name)
                self.korrektor_items_tag2[i].set_checked(checked)

        print(f"Korrektorenliste erfolgreich geladen mit {len(zeilen)} Einträgen.")

    def kandidaten_speichern(self):
        """
        Speichert die aktuelle Prüflingsliste aus listWidgetList in eine Textdatei.
        """
        dateiname, _ = QFileDialog.getSaveFileName(
            self,
            "Kandidatenliste speichern...",
            filter="Textdateien (*.txt)"
        )

        if not dateiname:
            return

        # Endung automatisch ergänzen, falls der Benutzer sie weglässt
        if not dateiname.lower().endswith(".txt"):
            dateiname += ".txt"

        try:
            with open(dateiname, "w", encoding="utf-8") as f:
                for i in range(self.listWidgetList.count()):
                    item = self.listWidgetList.item(i)
                    f.write(item.text() + "\n")
            print(f"Kandidatenliste erfolgreich gespeichert: {dateiname}")
        except Exception as e:
            print(f"Fehler beim Speichern der Kandidaten: {e}")

    def korrektoren_speichern(self):
        dateiname, _ = QFileDialog.getSaveFileName(
            self,
            "Korrektorenliste speichern...",
            filter="Textdateien (*.txt)"
        )

        if not dateiname:
            return

        if not dateiname.lower().endswith(".txt"):
            dateiname += ".txt"

        try:
            # Nur Namen aus Tag 1 exportieren, zusätzlich mit Anwesenheits-Flag
            with open(dateiname, "w", encoding="utf-8") as f:
                f.write("# version=2\n")
                for w in self.korrektor_items_tag1:
                    name = w.get_name()
                    if name:
                        checked = "1" if w.is_checked() else "0"
                        f.write(f"{name};{checked}\n")

            print(f"Korrektorenliste erfolgreich gespeichert: {dateiname}")
        except Exception as e:
            print(f"Fehler beim Speichern der Korrektoren: {e}")

    def session_save(self):
        """
        Speichert die aktuelle GUI-Sitzung in eine JSON-Datei im Home-Verzeichnis.
        """
        try:
            session_data = {}

            # Fenstergröße und -position
            geom = self.geometry()
            session_data["geometry"] = {
                "x": geom.x(),
                "y": geom.y(),
                "width": geom.width(),
                "height": geom.height()
            }

            # Prüflingsliste
            session_data["prueflinge"] = [self.listWidgetList.item(i).text() for i in
                                          range(self.listWidgetList.count())]

            # Korrektoren Tag 1
            session_data["korrektoren_tag1"] = [
                {"name": w.get_name(), "checked": w.is_checked()}
                for w in self.korrektor_items_tag1
            ]

            # Korrektoren Tag 2
            session_data["korrektoren_tag2"] = [
                {"name": w.get_name(), "checked": w.is_checked()}
                for w in self.korrektor_items_tag2
            ]

            # Prüfungstage
            session_data["datum1"] = self.date1Edit.date().toString(Qt.ISODate)
            session_data["datum2"] = self.date2Edit.date().toString(Qt.ISODate)
            session_data["version"] = 2

            # Datei schreiben
            with open(str(SESSION_FILE), "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2)

            print(f"Sitzung erfolgreich gespeichert unter {SESSION_FILE}")
        except Exception as e:
            print(f"Fehler beim Speichern der Sitzung: {e}")

    def session_read(self):
        """
        Fragt beim Start, ob die letzte Sitzung geladen werden soll und lädt sie ggf.
        """
        try:
            if not SESSION_FILE.exists():
                return  # Keine gespeicherte Sitzung

            # noinspection PyUnresolvedReferences
            reply = QMessageBox.question(
                self,
                "Session laden",
                "Möchten Sie die letzte Sitzung wiederherstellen?",

                QMessageBox.Yes | QMessageBox.No
            )

            # noinspection PyUnresolvedReferences
            if reply != QMessageBox.Yes:
                return

            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                session_data = json.load(f)

            version = session_data.get("version", 1)
            if version != 2:
                print(f"Sitzung wird nicht geladen: inkompatible Version {version} (erwartet: 2)")
                return

            # Fenstergröße und -position
            g = session_data["geometry"]
            self.setGeometry(g["x"], g["y"], g["width"], g["height"])

            # Prüflingsliste
            self.listWidgetList.clear()
            for text in session_data.get("prueflinge", []):
                item = QListWidgetItem(text.strip())
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                self.listWidgetList.addItem(item)

            # Korrektoren Tag 1
            for i, eintrag in enumerate(session_data.get("korrektoren_tag1", [])):
                if i < len(self.korrektor_items_tag1):
                    w = self.korrektor_items_tag1[i]
                    w.set_name(eintrag.get("name", ""))
                    w.set_checked(eintrag.get("checked", False))

            # Korrektoren Tag 2
            for i, eintrag in enumerate(session_data.get("korrektoren_tag2", [])):
                if i < len(self.korrektor_items_tag2):
                    w = self.korrektor_items_tag2[i]
                    w.set_name(eintrag.get("name", ""))
                    w.set_checked(eintrag.get("checked", False))

            # Prüfungstage
            if "datum1" in session_data:
                self.date1Edit.setDate(QDate.fromString(session_data["datum1"], Qt.ISODate))
            if "datum2" in session_data:
                self.date2Edit.setDate(QDate.fromString(session_data["datum2"], Qt.ISODate))

            print(f"Sitzung erfolgreich geladen von {SESSION_FILE}")
        except Exception as e:
            print(f"Fehler beim Laden der Sitzung: {e}")

    def about_box(self):
        QMessageBox.about(
            self,
            WINDOWTITLE,
            f"{APPNAME}\nPrüfungs- und Korrektorenverteilung\n"
            f"{VERSION}\nvom {DATE}\n{COPYRIGHT}"
        )

    def open_preferences_dialog(self):
        dialog = PreferencesDialog(self, self.preferences_file)

        if dialog.exec():
            neue_zeitslots = dialog.get_pruefungszeiten()

            # Zeitslots übernehmen, wenn sie sich geändert haben
            if neue_zeitslots != self.zeitslots:
                self.zeitslots = neue_zeitslots
                self.table1Widget.setRowCount(0)
                self.table2Widget.setRowCount(0)
                self.statusBar().clearMessage()
                self.statusBar().setStyleSheet("")
                self.letztes_pdf_data = None

            # Jetzt auch die 4 Werte dauerhaft speichern
            dialog.save_preferences()
        else:
            print("Abbrechen gedrückt – nichts speichern")

    def lade_preferences(self):
        """
        Liest gespeicherte Einstellungen wie Zeitslots aus ~/.pvihk_preferences.json
        (Eintragen in Widgets erfolgt im Dialog selbst.)
        """
        if not self.preferences_file.exists():
            return

        try:
            with open(self.preferences_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            version = data.get("version", 1)
            if version != 2:
                print(f"Präferenzen werden nicht geladen: inkompatible Version {version} (erwartet: 2)")
                return

            # Nur Zeitslots übernehmen
            if isinstance(data.get("zeitslots"), list) and all(isinstance(z, list) for z in data["zeitslots"]):
                self.zeitslots = data["zeitslots"]

        except Exception as e:
            print(f"Fehler beim Laden der Präferenzen: {e}")

    def speichere_preferences(self):
        """
        Speichert aktuelle Einstellungen (z. B. Zeitslots) in ~/.pvihk_preferences.json.
        Nur die vom Hauptfenster verwalteten Daten, z. B. Zeitslots.
        Weitere Felder wie begin1/dauer1 werden vom PreferencesDialog verwaltet.
        """
        try:
            data = {}

            # Bestehende Datei laden, damit nicht andere Einstellungen verloren gehen
            if self.preferences_file.exists():
                with open(self.preferences_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

            # Nur Zeitslots aus MainWindow aktualisieren
            data["zeitslots"] = self.zeitslots
            data["version"] = 2

            with open(self.preferences_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            print("Präferenzen gespeichert.")
        except Exception as e:
            print(f"Fehler beim Speichern der Präferenzen: {e}")


app = QApplication(sys.argv)

if platform.system() == "Windows":
    app.setStyle("Fusion")

window = MainWindow()
window.show()

app.exec()
