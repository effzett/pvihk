# versioning.py
import sys
import os
from datetime import date

# Statische Basisinformationen
__version_major_minor__ = "2.4"
###############################
TITLE = "Prüfungs- und Korrektorenverteilung"
APPNAME = "PVIHK"
AUTHOR = "fz@zenmeister.de"

# --- Kontextabhängige Basisverzeichnisse ---

def is_frozen():
    return getattr(sys, 'frozen', False)

def get_base_path():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

# --- Pfade für Versions- und Datumsdateien ---

def get_build_number_path():
    return os.path.join(get_base_path(), "build_number.txt")

def get_build_date_path():
    return os.path.join(get_base_path(), "build_date.txt")

# --- Dateizugriff ---

def read_file(path, default="unbekannt"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return default

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# --- Version + Datum berechnen ---
def get_version_and_date(increment=False):
    number_path = get_build_number_path()
    date_path = get_build_date_path()

    build_str = read_file(number_path, "0")
    try:
        build = int(build_str)
    except ValueError:
        build = 0

    if increment and not is_frozen():
        build += 1
        write_file(number_path, str(build))
        write_file(date_path, date.today().isoformat())

    version = f"{__version_major_minor__}.{build}"
    datum = read_file(date_path, "unbekannt")

    return version, datum

# --- Komplettes Metadatenpaket ---

def get_app_metadata(increment=False):
    version, datum = get_version_and_date(increment=increment)

    try:
        jahr = datum.split("-")[0]
    except Exception:
        jahr = "????"

    copyright_str = f"© {jahr} {AUTHOR}"
    titleversion = f"{TITLE} (V{version})"
    windowtitle = f"{APPNAME}: {TITLE}, {version}, {datum}"

    return {
        "VERSION": version,
        "DATE": datum,
        "TITLEVERSION": titleversion,
        "WINDOWTITLE": windowtitle,
        "APPNAME": APPNAME,
        "COPYRIGHT": copyright_str
    }
