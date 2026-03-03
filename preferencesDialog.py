from PySide6.QtWidgets import (QDialog, QHeaderView, QTableWidgetItem,
                               QGroupBox, QVBoxLayout, QHBoxLayout,
                               QLabel, QSlider, QCheckBox, QSpinBox)
from PySide6.QtCore import Qt, QTime
from datetime import datetime, timedelta
import json
from pathlib import Path
from preferences import Ui_Preferences

from contextlib import contextmanager

@contextmanager
def block_signals(widgets):
    for w in widgets:
        w.blockSignals(True)
    try:
        yield
    finally:
        for w in widgets:
            w.blockSignals(False)

class PreferencesDialog(QDialog, Ui_Preferences):
    def __init__(self, parent=None, preferences_path=None):
        super().__init__(parent)
        self.setupUi(self)

        self.preferences_file = preferences_path or Path.home() / ".pvihk_preferences.json"

        # =====================================================================
        # ERWEITERUNG: Weitergabe-Optimierung (2025-03)
        # Fuegt dem Dialog eine GroupBox mit Schieberegler (lambda) und
        # optionaler harter MAX_PARTNER-Schranke hinzu.
        #
        # Zum vollstaendigen Entfernen:
        #   1. Aufruf self._init_weitergabe_gruppe() loeschen
        #   2. Methode _init_weitergabe_gruppe() loeschen
        #   3. In load_preferences():  Block "ERWEITERUNG" loeschen
        #   4. In save_preferences():  drei daten[...]-Zeilen loeschen
        #   5. Methoden get_lambda_partner() / get_max_partner() loeschen
        #   In pvihk.py ergaenzend:
        #   6. self.lambda_partner / self.max_partner aus __init__ loeschen
        #   7. In lade_preferences() / speichere_preferences() Bloecke loeschen
        #   8. In sammle_eingabedaten() zwei eingabedaten[...]-Zeilen loeschen
        #   9. In berechne_korrektorenverteilung() ERWEITERUNG-Block loeschen
        # =====================================================================
        self._init_weitergabe_gruppe()
        # === ENDE ERWEITERUNG ===

        # Events
        self.timeEditBegin1.timeChanged.connect(self.update_zeittabelle)
        self.timeEditBegin2.timeChanged.connect(self.update_zeittabelle)
        self.spinBoxDuration1.valueChanged.connect(self.update_zeittabelle)
        self.spinBoxDuration2.valueChanged.connect(self.update_zeittabelle)

        # Initial laden
        self.load_preferences()

    # =========================================================================
    # ERWEITERUNG: Weitergabe-Optimierung - UI aufbauen
    # =========================================================================
    def _init_weitergabe_gruppe(self):
        """
        Erstellt GroupBox 'Optimierung der Klausur-Weitergabe' und fuegt
        sie vor dem OK/Abbrechen-Button ins Hauptlayout ein.

        Schieberegler lambda (0.0 bis 3.0, Schrittweite 0.1):
          lambda=0.0  -> keine Buendelung (Verhalten wie bisher, kein Overhead)
          lambda=0.5  -> moderate Buendelung (empfohlener Einstiegswert)
          lambda=3.0  -> maximale Buendelung (Lastungleichheit moeglich)

        Harte Schranke (optional, Checkbox):
          Begrenzt Anzahl verschiedener Weitergabe-Partner pro Korrektor.
          Kann bei engen Verfuegbarkeiten zur Unloesbarkeit fuehren.
        """
        self.groupBox_weitergabe = QGroupBox("Optimierung der Klausur-Weitergabe")
        outer = QVBoxLayout(self.groupBox_weitergabe)

        # --- Zeile 1: lambda-Schieberegler ---
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Buendelungs-Gewichtung (lambda):"))

        self.sliderLambda = QSlider(Qt.Horizontal)
        self.sliderLambda.setRange(0, 30)    # intern 0-30, entspricht 0.0-3.0
        self.sliderLambda.setValue(5)         # Default: lambda = 0.5
        self.sliderLambda.setTickInterval(5)
        self.sliderLambda.setTickPosition(QSlider.TicksBelow)
        self.sliderLambda.setToolTip(
            "0 = keine Buendelung  |  0.5 = moderat  |  3.0 = maximale Buendelung"
        )
        row1.addWidget(self.sliderLambda)

        self.labelLambdaValue = QLabel("0.5")
        self.labelLambdaValue.setMinimumWidth(32)
        row1.addWidget(self.labelLambdaValue)
        outer.addLayout(row1)

        hint1 = QLabel(
            "0 = keine Buendelung (wie bisher)  |  0.5 = moderat  |  "
            "3.0 = maximal  (kann Lastungleichheit erzeugen)"
        )
        hint1.setStyleSheet("color: gray; font-size: 9pt;")
        outer.addWidget(hint1)

        # --- Zeile 2: harte MAX_PARTNER-Schranke ---
        row2 = QHBoxLayout()
        self.checkBoxMaxPartner = QCheckBox(
            "Harte Schranke: max. Weitergabe-Partner pro Korrektor:"
        )
        row2.addWidget(self.checkBoxMaxPartner)

        self.spinBoxMaxPartner = QSpinBox()
        self.spinBoxMaxPartner.setRange(1, 10)
        self.spinBoxMaxPartner.setValue(3)
        self.spinBoxMaxPartner.setSuffix(" Partner")
        self.spinBoxMaxPartner.setEnabled(False)
        self.spinBoxMaxPartner.setToolTip(
            "Erzwingt, dass jeder Korrektor hoechstens N verschiedene Partner hat.\n"
            "Achtung: Kann bei engen Verfuegbarkeiten zur Unloesbarkeit fuehren!"
        )
        row2.addWidget(self.spinBoxMaxPartner)
        outer.addLayout(row2)

        hint2 = QLabel(
            "Warnung: Harte Schranke kann bei unguenstigen Verfuegbarkeiten "
            "unloesbar machen. Dann lambda erhoehen statt harter Schranke nutzen."
        )
        hint2.setStyleSheet("color: darkorange; font-size: 9pt;")
        outer.addWidget(hint2)

        # --- Signals ---
        self.sliderLambda.valueChanged.connect(
            lambda v: self.labelLambdaValue.setText(f"{v / 10:.1f}")
        )
        self.checkBoxMaxPartner.toggled.connect(self.spinBoxMaxPartner.setEnabled)

        # GroupBox vor dem buttonBox einfuegen (letztes Widget im verticalLayout)
        self.verticalLayout.insertWidget(
            self.verticalLayout.count() - 1,
            self.groupBox_weitergabe
        )
    # === ENDE ERWEITERUNG: _init_weitergabe_gruppe ===

    def load_preferences(self):
        if not self.preferences_file.exists():
            # Defaults setzen - alles konsistent
            with block_signals([
                self.timeEditBegin1,
                self.timeEditBegin2,
                self.spinBoxDuration1,
                self.spinBoxDuration2
            ]):
                self.timeEditBegin1.setTime(QTime(9, 0))
                self.timeEditBegin2.setTime(QTime(14, 0))
                self.spinBoxDuration1.setValue(60)
                self.spinBoxDuration2.setValue(60)

            default_slots = [
                ["09:00", "10:00", "11:00", "12:00"],
                ["14:00", "15:00", "16:00", "17:00"]
            ]
            self.set_zeitslots(default_slots)
            self.geladene_zeitslots = default_slots
            return

        try:
            with open(self.preferences_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            version = data.get("version", 1)
            if version != 2:
                print(f"Einstellungen nicht geladen: inkompatible Version {version} (erwartet: 2)")
                return

            with block_signals([
                self.timeEditBegin1,
                self.timeEditBegin2,
                self.spinBoxDuration1,
                self.spinBoxDuration2
            ]):
                if "begin1" in data:
                    h, m = map(int, data["begin1"].split(":"))
                    self.timeEditBegin1.setTime(QTime(h, m))

                if "begin2" in data:
                    h, m = map(int, data["begin2"].split(":"))
                    self.timeEditBegin2.setTime(QTime(h, m))

                if "dauer1" in data:
                    self.spinBoxDuration1.setValue(int(data["dauer1"]))

                if "dauer2" in data:
                    self.spinBoxDuration2.setValue(int(data["dauer2"]))

            if "zeitslots" in data:
                self.set_zeitslots(data["zeitslots"])
                self.geladene_zeitslots = data["zeitslots"]

            # =====================================================================
            # ERWEITERUNG: Weitergabe-Optimierung - Parameter laden
            # =====================================================================
            if "lambda_partner" in data:
                val = int(round(float(data["lambda_partner"]) * 10))
                self.sliderLambda.setValue(max(0, min(30, val)))
            if "max_partner_aktiv" in data:
                self.checkBoxMaxPartner.setChecked(bool(data["max_partner_aktiv"]))
            if "max_partner" in data:
                self.spinBoxMaxPartner.setValue(int(data["max_partner"]))
            # === ENDE ERWEITERUNG ===

        except Exception as e:
            print(f"Fehler beim Laden der Einstellungen: {e}")

    def save_preferences(self):
        daten = {
            "version": 2,
            "begin1": self.timeEditBegin1.time().toString("HH:mm"),
            "begin2": self.timeEditBegin2.time().toString("HH:mm"),
            "dauer1": self.spinBoxDuration1.value(),
            "dauer2": self.spinBoxDuration2.value(),
            "zeitslots": self.get_pruefungszeiten()
        }
        # =====================================================================
        # ERWEITERUNG: Weitergabe-Optimierung - Parameter speichern
        # =====================================================================
        daten["lambda_partner"]    = self.sliderLambda.value() / 10.0
        daten["max_partner_aktiv"] = self.checkBoxMaxPartner.isChecked()
        daten["max_partner"]       = self.spinBoxMaxPartner.value()
        # === ENDE ERWEITERUNG ===

        try:
            with open(self.preferences_file, "w", encoding="utf-8") as f:
                json.dump(daten, f, indent=2)
            print("Einstellungen gespeichert in", self.preferences_file)
        except Exception as e:
            print(f"Fehler beim Speichern der Einstellungen: {e}")

    def update_zeittabelle(self):
        try:
            fmt = "%H:%M"
            dauer1 = self.spinBoxDuration1.value()
            dauer2 = self.spinBoxDuration2.value()

            start1_dt = datetime.combine(datetime.today(), self.timeEditBegin1.time().toPython())
            start2_dt = datetime.combine(datetime.today(), self.timeEditBegin2.time().toPython())

            min_pause = timedelta(minutes=15)
            max_end_time = datetime.combine(datetime.today(), datetime.strptime("18:00", fmt).time())

            zeiten = []

            aktuelle = start1_dt
            while aktuelle + timedelta(minutes=dauer1) <= start2_dt - min_pause:
                zeiten.append(aktuelle.strftime(fmt))
                aktuelle += timedelta(minutes=dauer1)

            pause_start = aktuelle
            pause_dauer = start2_dt - pause_start
            pause_min = int(pause_dauer.total_seconds() // 60)
            if pause_min >= 60:
                pause_str = f"Mittagspause: {pause_min // 60}:{pause_min % 60:02d} Stunden"
            else:
                pause_str = f"Mittagspause: {pause_min} Minuten"
            self.labelLunch.setText(pause_str)

            aktuelle = start2_dt
            while aktuelle + timedelta(minutes=dauer2) <= max_end_time:
                zeiten.append(aktuelle.strftime(fmt))
                aktuelle += timedelta(minutes=dauer2)

            self.tableWidgetTimes.clearContents()
            self.tableWidgetTimes.setRowCount(2)
            self.tableWidgetTimes.setColumnCount(len(zeiten))
            self.tableWidgetTimes.setHorizontalHeaderLabels([f"P{i+1}" for i in range(len(zeiten))])
            self.tableWidgetTimes.setVerticalHeaderLabels(["Tag 1", "Tag 2"])

            for row in range(2):
                for col, zeit in enumerate(zeiten):
                    item = QTableWidgetItem(zeit)
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                    self.tableWidgetTimes.setItem(row, col, item)

            header = self.tableWidgetTimes.horizontalHeader()
            for i in range(self.tableWidgetTimes.columnCount()):
                header.setSectionResizeMode(i, QHeaderView.Stretch)

        except Exception as e:
            print(f"Fehler beim Aktualisieren der Zeittabelle: {e}")

    def set_zeitslots(self, slots: list[list[str]]):
        if not (isinstance(slots, list) and len(slots) == 2):
            print("Ungueltige Zeitslots-Struktur - erwartet 2 Listen")
            return

        max_spalten = max(len(slots[0]), len(slots[1]))
        self.tableWidgetTimes.clearContents()
        self.tableWidgetTimes.setRowCount(2)
        self.tableWidgetTimes.setColumnCount(max_spalten)
        self.tableWidgetTimes.setHorizontalHeaderLabels([f"P{i+1}" for i in range(max_spalten)])
        self.tableWidgetTimes.setVerticalHeaderLabels(["Tag 1", "Tag 2"])

        for row in range(2):
            for col, zeit in enumerate(slots[row]):
                item = QTableWidgetItem(zeit)
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                self.tableWidgetTimes.setItem(row, col, item)

        header = self.tableWidgetTimes.horizontalHeader()
        for i in range(self.tableWidgetTimes.columnCount()):
            header.setSectionResizeMode(i, QHeaderView.Stretch)

    def get_pruefungszeiten(self):
        zeiten = [[], []]
        for row in range(2):
            for col in range(self.tableWidgetTimes.columnCount()):
                item = self.tableWidgetTimes.item(row, col)
                zeiten[row].append(item.text() if item else "")
        return zeiten

    # =========================================================================
    # ERWEITERUNG: Weitergabe-Optimierung - Getter fuer pvihk.py
    # =========================================================================
    def get_lambda_partner(self) -> float:
        """
        Gibt den lambda-Wert zurueck (0.0 bis 3.0).
        lambda=0 -> Buendelung deaktiviert, kein Einfluss auf das LP-Modell.
        """
        return self.sliderLambda.value() / 10.0

    def get_max_partner(self):
        """
        Gibt die harte MAX_PARTNER-Schranke zurueck, oder None wenn deaktiviert.
        Bei None wird keine harte Einschraenkung in das LP-Modell eingebaut.
        """
        if self.checkBoxMaxPartner.isChecked():
            return self.spinBoxMaxPartner.value()
        return None
    # === ENDE ERWEITERUNG: Getter ===
