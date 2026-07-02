# -*- coding: utf-8 -*-
"""
ControELAndo — SuperCapa v5
Control del ordenador por voz con rejilla superpuesta.

Autores:
    Enrique García Prats
    Jorge García Prats
    Alicia Prats Martínez

NOVEDADES V5 - Accesibilidad para personas con movilidad reducida:
  * ALIAS DE VOZ PERSONALIZADOS: cada usuario puede grabar como EL/ELLA
    pronuncia cada comando. El programa aprende su pronunciacion, volumen
    y entonacion propios.
  * Boton "Grabar Voz" en la barra flotante abre el gestor de alias.
  * Grabacion con un click: pulsa "Grabar", di la palabra, el programa
    la transcribe y la vincula al comando elegido.
  * Los alias se guardan en config.json y se aplican automaticamente.
  * Perfiles de microfono (Suave / Normal / Ruidoso) para adaptar el
    reconocimiento a diferentes voces y entornos.
  * Zoom de celda con simbolos visuales (◆●▲★) para seleccion precisa.
  * Coordenada sin clic: al decir la celda solo mueve el cursor.
  * Comando "deshacer" para revertir la ultima accion.
  * HUD muestra en tiempo real lo que el programa esta escuchando.

NOVEDADES V4:
  * Indicador visual "te estoy escuchando" (punto de color en pantalla)
  * Pausar/reanudar la voz: "dormir" / "despierta"
  * Confirmacion visual del ultimo comando reconocido
  * Detecta caida de internet con aviso en pantalla
  * Configuracion persistente (config.json al lado del .exe)
  * Comprobacion de microfono al arrancar (aviso claro si falla)
  * Comando de emergencia: "socorro" parada segura
  * Marcar/seleccionar celda sin clicar: "marcar A1"
  * Cooldown de 250ms entre clics para evitar dobles accidentales
  * Barra flotante: arranca minimizada abajo-centro, "ocultar barra"
  * Arrastrar archivos: "coger A1" -> "soltar T15", "cancelar arrastre"
"""

import ctypes
import json
import os
import queue
import re
import socket
import subprocess
import winsound
import sys
import threading
import time
import tkinter as tk

# DPI awareness - antes de crear ventanas
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pyautogui
import speech_recognition as sr
import keyboard

try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False

try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    HAS_PYCAW = True
except Exception:
    HAS_PYCAW = False

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.02

# ============================================================
#  CONFIGURACION POR DEFECTO (se sobrescribe con config.json)
# ============================================================
COLS = 14
ROWS = 15
LETTERS = "ABCDEFGHJKLMNO"  # sin I (Google la confunde con Y)

GRID_LINE_COLOR = "#888888"
GRID_TEXT_COLOR = "#FFFFFF"
GRID_TEXT_OUTLINE = "#000000"
HIGHLIGHT_COLOR = "#FF2222"    # rojo para celda seleccionada
ZOOM_COLOR      = "#FF8800"    # naranja para subrejilla de zoom
TRANSPARENT_KEY = "#FF00FF"
LANGUAGE = "es-ES"
TOPMOST_REFRESH_MS = 150

MOUSE_STEP_DEFAULT = 30
MOUSE_STEP_MIN = 5
MOUSE_STEP_MAX = 300
MOUSE_STEP_FACTOR = 1.6

CLICK_COOLDOWN_MS = 250  # tiempo minimo entre clics en la misma celda
HUD_TIMEOUT_MS = 2500    # cuanto dura el cartel de "ultimo comando"
AUTO_SLEEP_SECS = 300    # dormir si no hay comandos en 5 min (0 = desactivado)

# ── Perfiles de sensibilidad del microfono ──────────────────
# Cada perfil define:
#   energy_threshold : umbral minimo de energia (nivel de ruido que se ignora)
#                      Valores tipicos: 300-4000. Mas alto = mas estricto.
#   dynamic          : True = el umbral se adapta automaticamente al ruido
#                      False = umbral fijo (mas predecible en entornos ruidosos)
#   dynamic_ratio    : cuanto sube el umbral respecto al ruido de fondo (>1)
#   pause_threshold  : segundos de silencio para considerar que termino la frase
#   phrase_time_limit: segundos maximos de una frase antes de cortar
#   recalib_secs     : cada cuantos segundos recalibrar el ruido de fondo
MIC_PROFILES = {
    "normal": dict(
        energy_threshold=400,
        dynamic=False,
        dynamic_ratio=1.5,
        pause_threshold=0.7,
        phrase_time_limit=5,
        recalib_secs=30,
    ),
    "voz_suave": dict(       # para personas con voz baja o poco volumen
        energy_threshold=200,
        dynamic=False,
        dynamic_ratio=1.2,
        pause_threshold=1.0,
        phrase_time_limit=7,
        recalib_secs=20,
    ),
    "ruidoso": dict(         # entorno con ruido de fondo alto
        energy_threshold=800,
        dynamic=True,
        dynamic_ratio=2.5,
        pause_threshold=0.6,
        phrase_time_limit=4,
        recalib_secs=15,
    ),
}
MIC_PROFILE_DEFAULT = "normal"


# ============================================================
#  CONFIG PERSISTENTE
# ============================================================
def get_config_path():
    """config.json al lado del .exe / del .py."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "config.json")


def load_config():
    defaults = {
        "mouse_step": MOUSE_STEP_DEFAULT,
        "bar_visible": True,
        "bar_minimized": True,
        "bar_x": None,        # None = centrar abajo
        "bar_y": None,
        "grid_visible_on_start": True,
        "mic_profile": MIC_PROFILE_DEFAULT,
        "beep_enabled": True,
        "auto_sleep_secs": AUTO_SLEEP_SECS,
        "terms_accepted": False,   # aceptación de términos
        "terms_version": "",       # versión de términos aceptada
        "tutorial_seen": False,    # si ya vio el tutorial
    }
    try:
        with open(get_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        defaults.update(data)
    except Exception:
        pass
    return defaults


def save_config(cfg):
    try:
        with open(get_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[aviso] no se pudo guardar config: {e}")


# ============================================================
#  ALIAS DE VOZ PERSONALIZADOS
#  Permiten que cada usuario configure como EL pronuncia cada
#  comando. Se guardan en config.json bajo la clave "aliases".
#  Formato: {"lo que yo digo": "trigger_estandar_del_programa"}
# ============================================================

# Catalogo de comandos disponibles para vincular alias.
# Agrupados por categoria para la UI del gestor.
ALIAS_CATALOG = {
    "Raton - movimiento": [
        ("arriba",         "arriba"),
        ("abajo",          "abajo"),
        ("izquierda",      "izquierda"),
        ("derecha",        "derecha"),
        ("mas rapido",     "mas rapido"),
        ("mas despacio",   "mas despacio"),
    ],
    "Raton - clics": [
        ("clic",           "clic aqui"),
        ("pulsar",         "pulsar aqui"),
        ("doble clic",     "doble clic"),
        ("clic derecho",   "boton derecho"),
        ("soltar aqui",    "soltar aqui"),
        ("cancelar",       "cancelar arrastre"),
    ],
    "Edicion": [
        ("copiar",         "copiar"),
        ("pegar",          "pegar"),
        ("cortar",         "cortar"),
        ("borrar",         "borrar"),
        ("seleccionar todo", "seleccionar todo"),
        ("intro",          "intro"),
    ],
    "Ventanas": [
        ("cerrar ventana", "cerrar ventana"),
        ("minimizar",      "minimizar"),
        ("maximizar",      "maximizar"),
        ("otra ventana",   "cambiar ventana"),
        ("escritorio",     "ver escritorio"),
        ("explorador",     "abrir explorador"),
    ],
    "Programa": [
        ("dormir",         "dormir"),
        ("despierta",      "despierta"),
        ("rejilla",        "rejilla"),
        ("ocultar rejilla","ocultar rejilla"),
        ("empezar texto",  "empezar dictado"),
        ("fin texto",      "fin dictado"),
        ("socorro",        "socorro"),
        ("salir",          "salir del programa"),
        ("pitido on",      "pitido on"),
        ("pitido off",     "pitido off"),
        ("ayuda",          "ayuda"),
        ("historial",      "historial"),
        ("tutorial",       "tutorial"),
        ("centrar barra",  "centrar barra"),
    ],
    "Zoom": [
        ("zoom",           "zoom"),
        ("diamante",       "diamante"),
        ("circulo",        "circulo"),
        ("triangulo",      "triangulo"),
        ("estrella",       "estrella"),
        ("salir zoom",     "salir"),
    ],
    "Deshacer / Rehacer": [
        ("deshacer",       "deshacer"),
        ("volver atras",   "volver atras"),
        ("revertir",       "revertir"),
        ("repetir",        "repetir"),
        ("otra vez",       "otra vez"),
    ],
    "Scroll y Pagina": [
        ("subir pagina",      "subir pagina"),
        ("bajar pagina",      "bajar pagina"),
        ("pagina anterior",   "pagina anterior"),
        ("pagina siguiente",  "pagina siguiente"),
        ("ir al inicio",      "ir al inicio"),
        ("ir al final",       "ir al final"),
    ],
    "Volumen": [
        ("subir volumen",  "subir volumen"),
        ("bajar volumen",  "bajar volumen"),
        ("silencio",       "silencio"),
        ("volumen maximo", "volumen maximo"),
    ],
    "Puntuacion (dictado)": [
        ("punto",           "punto"),
        ("coma",            "coma"),
        ("interrogacion",   "interrogacion"),
        ("exclamacion",     "exclamacion"),
        ("dos puntos",      "dos puntos"),
        ("punto y coma",    "punto y coma"),
        ("arroba",          "arroba"),
        ("nueva linea",     "nueva linea"),
        ("parrafo",         "parrafo"),
        ("punto aparte",    "punto aparte"),
        ("abrir parentesis","abre parentesis"),
        ("cerrar parentesis","cierra parentesis"),
        ("comillas",        "comillas"),
        ("guion",           "guion"),
        ("tabulador",       "tabulador"),
    ],
}

# Lista plana para busqueda rapida: alias_lower -> trigger
def load_aliases(cfg):
    """Devuelve dict {texto_dicho_lower: trigger_estandar}."""
    raw = cfg.get("aliases", {})
    return {k.lower().strip(): v.lower().strip() for k, v in raw.items()}


def apply_aliases(text, aliases):
    """
    Sustituye en 'text' cualquier alias conocido por su trigger estandar.
    Compara sin acento y en minusculas. Devuelve el texto (posiblemente
    sustituido) listo para classify_intent / parse_coordinate.
    """
    if not aliases:
        return text
    t = text.lower().strip()
    # Busqueda exacta primero
    if t in aliases:
        result = aliases[t]
        print(f"[alias] '{t}' -> '{result}'")
        return result
    # Busqueda por contenido (el alias aparece dentro de la frase)
    for alias, trigger in aliases.items():
        if alias in t:
            result = t.replace(alias, trigger, 1)
            print(f"[alias] '{alias}' en '{t}' -> '{result}'")
            return result
    return text




# ============================================================
#  DICCIONARIO DE INTENCIONES (NLP por palabras clave)
# ============================================================
INTENTS = {
    # --- emergencia ---
    "EMERGENCY": [
        "socorro", "emergencia", "para todo", "para todo el programa",
        "apaga ya", "ayuda urgente",
    ],
    # --- ciclo de vida ---
    "QUIT": [
        "salir del programa", "cerrar programa", "cerrar supercapa",
        "apagar programa", "apagar supercapa", "adios supercapa",
    ],
    "SLEEP": [
        "dormir", "duermete", "duérmete", "ponte a dormir",
        "deja de escuchar", "para de escuchar", "callate", "cállate",
        "modo dormir",
    ],
    "WAKE": [
        "despierta", "despiertate", "despiértate", "vuelve a escuchar",
        "vuelve a la escucha", "modo despierto", "ya estoy",
    ],
    # --- rejilla ---
    "HIDE_GRID": [
        "ocultar rejilla", "esconder rejilla", "quitar rejilla",
        "quita la rejilla", "quita la cuadricula", "quita la cuadrícula",
        "oculta la rejilla", "ocultar cuadricula", "ocultar cuadrícula",
    ],
    "SHOW_GRID": [
        "mostrar rejilla", "muestra la rejilla", "activar rejilla",
        "activa la rejilla", "rejilla", "cuadricula", "cuadrícula",
        "mostrar capa", "activar capa", "muestra la capa",
    ],
    "ZOOM_EXIT": [
        "salir", "salir zoom", "quitar zoom", "zoom fuera", "zoom normal",
        "rejilla normal", "volver rejilla", "fuera", "cancelar zoom",
    ],
    "CLEAR_HIGHLIGHT": [
        "desmarcar", "desmarca", "quita la marca", "limpia la marca",
        "quitar marca", "limpiar seleccion", "limpiar selección",
    ],
    # --- utilidades ---
    "BEEP_TOGGLE": [
        "activar pitido", "desactivar pitido", "silenciar pitido",
        "pitido si", "pitido no", "pitido on", "pitido off",
        "sonido si", "sonido no",
    ],
    "HELP": [
        "ayuda", "que puedo decir", "qué puedo decir",
        "comandos", "lista de comandos", "muestra ayuda",
    ],
    "TUTORIAL": [
        "tutorial", "ver tutorial", "muestra tutorial",
        "cómo funciona", "como funciona", "instrucciones",
    ],
    "SHOW_HISTORY": [
        "historial", "ultimos comandos", "últimos comandos",
        "ver historial", "muestra historial",
    ],
    "CENTER_BAR": [
        "centrar barra", "centra la barra", "recolocar barra",
        "barra al centro", "mover barra",
    ],
    # --- deshacer ---
    "UNDO": [
        "deshacer", "deshacer accion", "deshacer acción",
        "volver atras", "volver atrás", "accion anterior", "acción anterior",
        "revertir", "revertir accion", "revertir acción",
        "control z", "ctrl z", "undo", "deshaz", "deshaz eso",
    ],
    # --- repetir ultimo comando ---
    "REPEAT": [
        "repetir", "repite", "repetir accion", "repetir acción",
        "otra vez", "de nuevo", "hazlo otra vez",
    ],
    # --- scroll pagina ---
    "SCROLL_UP": [
        "subir pagina", "subir página", "pagina arriba", "página arriba",
        "scroll arriba", "rueda arriba",
    ],
    "SCROLL_DOWN": [
        "bajar pagina", "bajar página", "pagina abajo", "página abajo",
        "scroll abajo", "rueda abajo",
    ],
    "PAGE_UP": [
        "pagina anterior", "página anterior", "retroceder pagina",
        "retroceder página", "re pag", "repag",
    ],
    "PAGE_DOWN": [
        "pagina siguiente", "página siguiente", "avanzar pagina",
        "avanzar página", "av pag", "avpag",
    ],
    "GO_HOME": [
        "ir al inicio", "inicio del documento", "inicio del texto",
        "principio", "control inicio", "ctrl inicio",
    ],
    "GO_END": [
        "ir al final", "final del documento", "final del texto",
        "final", "control fin", "ctrl fin",
    ],
    # --- barra ---
    "HIDE_BAR": [
        "ocultar barra", "oculta la barra", "quita la barra",
        "esconder barra", "esconde la barra",
    ],
    "SHOW_BAR": [
        "mostrar barra", "muestra la barra", "ensena la barra",
        "enseña la barra", "saca la barra", "abrir barra",
    ],
    # --- arrastre ---
    "DROP_HERE": [
        "soltar aqui", "soltar aquí", "suelta aqui", "suelta aquí",
        "soltar ya", "suelta ya", "drop",
    ],
    "CANCEL_DRAG": [
        "cancelar arrastre", "cancela el arrastre", "cancelar arrastrar",
        "olvidalo", "olvidalo", "cancela",
    ],
    # --- clic en posicion actual ---
    "CLICK_HERE": [
        "clic aqui", "clic aquí", "pulsar aqui", "pulsar aquí",
        "pulsar", "pulsa aqui", "pulsa aquí", "click aqui", "click aquí",
    ],
    # --- ventanas ---
    "CLOSE_WINDOW": [
        "cerrar ventana", "cierra ventana", "cierra la ventana",
        "cierra esa ventana", "cierra esta ventana", "cierra esto",
        "cierralo", "ciérralo", "cerrar esto", "quita esto", "quitalo",
        "quítalo",
    ],
    "MAXIMIZE": [
        "maximizar", "maximiza", "maximízalo", "maximizalo",
        "ponlo en grande", "ponlo grande", "pantalla completa",
        "hazlo grande", "agrandalo", "agrándalo",
    ],
    "MINIMIZE": [
        "minimizar", "minimiza", "minimizalo", "minimízalo",
        "ponlo pequeño", "ponlo pequeno", "hazlo pequeño",
        "esconde la ventana", "ocultar ventana", "esconder ventana",
    ],
    "SWITCH_WINDOW": [
        "cambiar ventana", "cambiar de ventana", "siguiente ventana",
        "otra ventana", "cambia de ventana", "pasa a la siguiente ventana",
    ],
    # --- sistema ---
    "OPEN_EXPLORER": [
        "abrir explorador", "abre el explorador", "abre explorador",
        "explorador de archivos", "abrir archivos", "abre los archivos",
        "abre una carpeta", "abrir una carpeta", "mis archivos",
        "mis documentos",
    ],
    "SHOW_DESKTOP": [
        "mostrar escritorio", "muestra el escritorio", "ver escritorio",
        "ir al escritorio", "escritorio", "enseñame el escritorio",
    ],
    "PROJECT": [
        "duplicar pantalla", "extender pantalla", "proyectar pantalla",
        "segunda pantalla", "proyección", "proyeccion",
    ],
    # --- edicion ---
    "SELECT_ALL": [
        "seleccionar todo", "selecciona todo", "selecciónalo todo",
        "seleccionalo todo",
    ],
    "ENTER": [
        "intro", "enter", "buscar", "aceptar", "confirma", "confirmar",
    ],
    "COPY": [
        "copiar", "copia eso", "copia esto", "copialo", "cópialo", "copia",
    ],
    "PASTE": [
        "pegar", "pega eso", "pega esto", "pegalo", "pégalo", "pega",
    ],
    "CUT": [
        "cortar", "corta eso", "corta esto", "cortalo", "córtalo", "corta",
    ],
    "DELETE": [
        "eliminar", "borrar", "suprimir", "elimina eso", "borra eso",
        "elimínalo", "eliminalo", "bórralo", "borralo", "borra", "elimina",
    ],
    # --- raton: tipos de clic (cuando no hay coordenada) ---
    "DOUBLE_CLICK": [
        "doble clic", "doble click", "doble pica", "doble pulsacion",
        "doble pulsación",
    ],
    "RIGHT_CLICK": [
        "clic derecho", "click derecho", "boton derecho", "botón derecho",
        "menu contextual", "menú contextual",
    ],
    "LEFT_CLICK": [
        "clic izquierdo", "click izquierdo", "clic", "click", "pica",
        "pulsa", "pincha", "toca",
    ],
    # --- raton direccional ---
    "MOVE_UP": ["arriba", "sube", "subir"],
    "MOVE_DOWN": ["abajo", "baja", "bajar"],
    "MOVE_LEFT": ["izquierda", "a la izquierda"],
    "MOVE_RIGHT": ["derecha", "a la derecha"],
    "FASTER": ["mas rapido", "más rápido", "mas rápido", "más rapido", "acelerar"],
    "SLOWER": ["mas despacio", "más despacio", "ralentiza", "frena"],
    # --- volumen ---
    "VOL_UP": ["sube el volumen", "subir volumen", "mas volumen", "más volumen"],
    "VOL_DOWN": ["baja el volumen", "bajar volumen", "menos volumen"],
    "VOL_MUTE": ["silencio", "silenciar", "silencia", "mutear", "mute"],
    "VOL_MAX": ["volumen maximo", "volumen máximo", "máximo volumen"],
    # --- dictado ---
    "DICTATE_START": [
        "texto", "modo texto", "empezar dictado", "empieza a dictar",
        "dictar", "dictame", "díctame",
    ],
    "DICTATE_END": [
        "quitar texto", "salir del texto", "fin del texto", "fin texto",
        "terminar dictado", "termina el dictado", "fin dictado",
    ],
    "DICTATE_DEL_WORD": [
        "borrar palabra", "borra la palabra", "borra palabra",
    ],
    "DICTATE_CLEAR": [
        "borrar todo", "borra todo", "limpia todo", "limpiar todo",
    ],
}

INTENT_PRIORITY = [
    "EMERGENCY",
    "QUIT", "SLEEP", "WAKE",
    "DICTATE_END", "DICTATE_DEL_WORD", "DICTATE_CLEAR", "DICTATE_START",
    "DROP_HERE", "CANCEL_DRAG",
    "CLICK_HERE",
    "ZOOM_EXIT", "CLEAR_HIGHLIGHT",
    "UNDO", "REPEAT",
    "BEEP_TOGGLE", "HELP", "TUTORIAL", "SHOW_HISTORY", "CENTER_BAR",
    "SCROLL_UP", "SCROLL_DOWN", "PAGE_UP", "PAGE_DOWN",
    "GO_HOME", "GO_END",
    "HIDE_GRID", "SHOW_GRID",
    "HIDE_BAR", "SHOW_BAR",
    "CLOSE_WINDOW", "MAXIMIZE", "MINIMIZE", "SWITCH_WINDOW",
    "OPEN_EXPLORER", "SHOW_DESKTOP", "PROJECT",
    "SELECT_ALL", "ENTER",
    "COPY", "PASTE", "CUT", "DELETE",
    "VOL_MUTE", "VOL_MAX", "VOL_UP", "VOL_DOWN",
    "FASTER", "SLOWER",
    "MOVE_UP", "MOVE_DOWN", "MOVE_LEFT", "MOVE_RIGHT",
    "DOUBLE_CLICK", "RIGHT_CLICK", "LEFT_CLICK",
]


def classify_intent(text):
    t = " " + text.lower().strip() + " "
    t = re.sub(r"\s+", " ", t)
    for intent in INTENT_PRIORITY:
        for trigger in INTENTS[intent]:
            if trigger in t:
                return intent
    return None


# ============================================================
#  PARSEO DE COORDENADAS Y NUMEROS
# ============================================================
NUMBERS_ES = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciséis": 16, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20,
}
LETTERS_ES = {
    "a": "A", "alfa": "A", "alpha": "A",
    "b": "B", "be": "B", "bravo": "B",
    "c": "C", "ce": "C", "se": "C", "charlie": "C",
    "d": "D", "de": "D", "delta": "D",
    "e": "E", "echo": "E",
    "f": "F", "efe": "F", "foxtrot": "F",
    "g": "G", "ge": "G", "je": "G", "golf": "G",
    "h": "H", "hache": "H", "ache": "H", "hotel": "H",
    "j": "J", "jota": "J", "juliet": "J",
    "k": "K", "ka": "K", "kilo": "K",
    "l": "L", "ele": "L", "lima": "L",
    "m": "M", "eme": "M", "mike": "M",
    "n": "N", "ene": "N", "november": "N",
    "o": "O", "oscar": "O",
}


def parse_coordinate(text):
    t = text.lower()
    m = re.search(
        r"\b(alfa|alpha|bravo|charlie|delta|echo|foxtrot|golf|hotel|india|"
        r"juliet|kilo|lima|mike|november|oscar|"
        r"hache|ache|jota|efe|erre|ere|eme|ene|ele|ese|"
        r"[a-hj-o]|be|ce|se|de|ge|je|ka|pe|te)\s*(\d{1,2})\b",
        t,
    )
    if m:
        lw = m.group(1)
        letter = LETTERS_ES.get(lw)
        if letter is None and len(lw) == 1:
            letter = lw.upper()
        number = int(m.group(2))
        if letter in LETTERS and 1 <= number <= ROWS:
            return (LETTERS.index(letter), number - 1)
    words = re.findall(r"[a-záéíóúñü]+|\d+", t)
    letter = None
    number = None
    for w in words:
        if letter is None:
            if w in LETTERS_ES:
                letter = LETTERS_ES[w]
            elif len(w) == 1 and w.upper() in LETTERS:
                letter = w.upper()
        if number is None:
            if w in NUMBERS_ES and 1 <= NUMBERS_ES[w] <= ROWS:
                number = NUMBERS_ES[w]
            elif w.isdigit():
                n = int(w)
                if 1 <= n <= ROWS:
                    number = n
    if letter and number:
        return (LETTERS.index(letter), number - 1)
    return None


def extract_number(text, default=1):
    """Saca un numero del texto, en cifra o palabra."""
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        return int(m.group(1))
    for w, n in NUMBERS_ES.items():
        if re.search(r"\b" + re.escape(w) + r"\b", text):
            return n
    return default


# ============================================================
#  ACCIONES BASICAS
# ============================================================
def act_left_click():   pyautogui.click()
def act_double_click(): pyautogui.doubleClick()
def act_right_click():  pyautogui.rightClick()

def act_copy():       pyautogui.hotkey("ctrl", "c")
def act_paste():      pyautogui.hotkey("ctrl", "v")
def act_cut():        pyautogui.hotkey("ctrl", "x")
def act_delete():     pyautogui.press("delete")
def act_select_all(): pyautogui.hotkey("ctrl", "a")
def act_enter():      pyautogui.press("enter")
def act_undo():       pyautogui.hotkey("ctrl", "z")

def act_scroll_up(n=3):    pyautogui.scroll(n)
def act_scroll_down(n=3):  pyautogui.scroll(-n)
def act_page_up(n=1):
    for _ in range(max(1, n)): pyautogui.press("pageup")
def act_page_down(n=1):
    for _ in range(max(1, n)): pyautogui.press("pagedown")
def act_go_home():    pyautogui.hotkey("ctrl", "home")
def act_go_end():     pyautogui.hotkey("ctrl", "end")

BEEP_ENABLED = True   # se puede cambiar en config.json o via boton/voz

def beep_ok():
    if BEEP_ENABLED:
        try:
            winsound.Beep(880, 60)
        except Exception:
            pass

def beep_error():
    if BEEP_ENABLED:
        try:
            winsound.Beep(300, 120)
        except Exception:
            pass

def act_close_window():  pyautogui.hotkey("alt", "f4")
def act_minimize():      pyautogui.hotkey("win", "down")
def act_maximize():      pyautogui.hotkey("win", "up")
def act_switch_window(): pyautogui.hotkey("alt", "tab")

def act_open_explorer(): pyautogui.hotkey("win", "e")
def act_show_desktop():  pyautogui.hotkey("win", "d")
def act_project():       pyautogui.hotkey("win", "p")




# ============================================================
#  VOLUMEN
# ============================================================
class VolumeController:
    def __init__(self):
        self.iface = None
        if HAS_PYCAW:
            try:
                devs = AudioUtilities.GetSpeakers()
                interface = devs.Activate(IAudioEndpointVolume._iid_,
                                          CLSCTX_ALL, None)
                self.iface = cast(interface, POINTER(IAudioEndpointVolume))
            except Exception:
                self.iface = None

    def up(self):
        if self.iface:
            cur = self.iface.GetMasterVolumeLevelScalar()
            self.iface.SetMasterVolumeLevelScalar(min(1.0, cur + 0.1), None)
        else:
            for _ in range(5):
                pyautogui.press("volumeup")

    def down(self):
        if self.iface:
            cur = self.iface.GetMasterVolumeLevelScalar()
            self.iface.SetMasterVolumeLevelScalar(max(0.0, cur - 0.1), None)
        else:
            for _ in range(5):
                pyautogui.press("volumedown")

    def mute(self):
        if self.iface:
            self.iface.SetMute(1, None)
        else:
            pyautogui.press("volumemute")

    def maximum(self):
        if self.iface:
            self.iface.SetMute(0, None)
            self.iface.SetMasterVolumeLevelScalar(1.0, None)


# ============================================================
#  INTERNET CHECK
# ============================================================
def has_internet(timeout=2):
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout)
        return True
    except OSError:
        return False


# ============================================================
#  OVERLAY PRINCIPAL (rejilla + HUDs)
# ============================================================
class GridOverlay:
    """
    Rejilla superpuesta a pantalla completa.

    Modos:
      normal  — rejilla COLS×ROWS con etiquetas A1..O15
      zoom    — subrejilla 3×3 dentro de una celda, etiquetas A5a..A5i
                Se activa diciendo "zoom A5" y se sale con "salir zoom"
    """

    # Simbolos para las 4 subceldas del zoom.
    # Cada entrada: (simbolo_visual, [palabras_de_voz_que_lo_activan])
    ZOOM_SYMBOLS = [
        ("◆", ["diamante", "rombo", "cuadrado"]),
        ("●", ["circulo", "círculo", "bola", "redondo"]),
        ("▲", ["triangulo", "triángulo", "triangulo", "pico", "piramide", "pirámide"]),
        ("★", ["estrella", "astro", "lucero"]),
    ]
    # Lista plana de simbolos para indexar
    ZOOM_LABELS = [s for s, _ in ZOOM_SYMBOLS]


    def __init__(self, master):
        self._master = master
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)

        # Obtener dimensiones desde la ventana raiz (mas fiable que desde Toplevel retirado)
        master.update_idletasks()
        self.screen_w = master.winfo_screenwidth()
        self.screen_h = master.winfo_screenheight()
        # Fallback por si winfo devuelve 0
        if self.screen_w < 100:
            self.screen_w = 1920
        if self.screen_h < 100:
            self.screen_h = 1080

        self.win.geometry(f"{self.screen_w}x{self.screen_h}+0+0")

        self.win.configure(bg=TRANSPARENT_KEY)
        try:
            self.win.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            self.win.attributes("-alpha", 0.25)

        self.canvas = tk.Canvas(
            self.win, width=self.screen_w, height=self.screen_h,
            bg=TRANSPARENT_KEY, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.cell_w = self.screen_w / COLS
        self.cell_h = self.screen_h / ROWS
        # Copias locales para que _resize_grid pueda actualizarlas sin problemas de globals
        self.cols    = COLS
        self.rows    = ROWS
        self.letters = LETTERS
        self.visible = False
        self.highlighted = None
        self.highlight_id = None
        self.last_click_time = 0.0
        self.last_click_cell = None

        self._zoom_col = None
        self._zoom_row = None

        self._draw()
        self.win.after(300, self._apply_click_through)

    # ---- dibujo ----
    def _draw(self):
        self.canvas.delete("all")
        if self._zoom_col is not None:
            self._draw_zoom()
        else:
            self._draw_normal()

    def _draw_normal(self):
        for i in range(self.cols + 1):
            x = i * self.cell_w
            self.canvas.create_line(x, 0, x, self.screen_h,
                                    fill=GRID_LINE_COLOR, width=1)
        for j in range(self.rows + 1):
            y = j * self.cell_h
            self.canvas.create_line(0, y, self.screen_w, y,
                                    fill=GRID_LINE_COLOR, width=1)
        fsize = max(10, int(min(self.cell_w, self.cell_h) / 4.0))
        for i in range(self.cols):
            for j in range(self.rows):
                cx = (i + 0.5) * self.cell_w
                cy = (j + 0.5) * self.cell_h
                label = f"{self.letters[i]}{j + 1}"
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    self.canvas.create_text(
                        cx + dx, cy + dy, text=label,
                        fill=GRID_TEXT_OUTLINE,
                        font=("Arial", fsize, "bold"),
                    )
                self.canvas.create_text(
                    cx, cy, text=label,
                    fill=GRID_TEXT_COLOR,
                    font=("Arial", fsize, "bold"),
                )

    def _draw_zoom(self):
        col, row = self._zoom_col, self._zoom_row
        x0 = col * self.cell_w
        y0 = row * self.cell_h
        x1 = x0 + self.cell_w
        y1 = y0 + self.cell_h

        # Oscurecer el resto de la pantalla
        for rx0, ry0, rx1, ry1 in [
            (0,  0,  self.screen_w, y0),
            (0,  y1, self.screen_w, self.screen_h),
            (0,  y0, x0,  y1),
            (x1, y0, self.screen_w, y1),
        ]:
            if rx1 > rx0 and ry1 > ry0:
                self.canvas.create_rectangle(
                    rx0, ry0, rx1, ry1,
                    fill="#000000", stipple="gray25", outline="")

        # Borde naranja de la celda padre
        self.canvas.create_rectangle(
            x0, y0, x1, y1,
            outline=ZOOM_COLOR, width=4, fill=TRANSPARENT_KEY)

        # Colores de fondo distintos para cada subcelda
        QUAD_COLORS = ["#1a2a4a", "#1a3a1a", "#3a1a1a", "#2a1a3a"]
        SYM_COLORS  = ["#44AAFF", "#44FF88", "#FF6644", "#FF44FF"]

        sw = self.cell_w / 2
        sh = self.cell_h / 2
        parent_label = f"{self.letters[col]}{row + 1}"
        fsize_sym  = max(18, int(min(sw, sh) / 2.2))
        fsize_word = max(9,  int(min(sw, sh) / 6.0))

        for si in range(2):
            for sj in range(2):
                sx0 = x0 + si * sw
                sy0 = y0 + sj * sh
                sx1 = sx0 + sw
                sy1 = sy0 + sh
                scx = (sx0 + sx1) / 2
                scy = (sy0 + sy1) / 2
                idx = sj * 2 + si
                symbol, words = self.ZOOM_SYMBOLS[idx]
                voice_word = words[0]   # palabra principal a mostrar
                color = SYM_COLORS[idx]

                # Fondo de color por cuadrante
                self.canvas.create_rectangle(
                    sx0 + 2, sy0 + 2, sx1 - 2, sy1 - 2,
                    fill=QUAD_COLORS[idx], outline=ZOOM_COLOR, width=2)

                # Simbolo grande centrado
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    self.canvas.create_text(
                        scx + dx, scy - 8 + dy, text=symbol,
                        fill="#000000",
                        font=("Arial", fsize_sym, "bold"))
                self.canvas.create_text(
                    scx, scy - 8, text=symbol,
                    fill=color,
                    font=("Arial", fsize_sym, "bold"))

                # Palabra de voz debajo del simbolo
                self.canvas.create_text(
                    scx, scy + fsize_sym // 2,
                    text=f'"{voice_word}"',
                    fill="#DDDDDD",
                    font=("Arial", fsize_word, "bold"))

        # Titulo ZOOM en esquina
        self.canvas.create_text(
            x0 + 6, y0 + 6,
            text=f"ZOOM {parent_label}  — di el nombre del simbolo",
            anchor="nw",
            fill=ZOOM_COLOR,
            font=("Arial", max(9, fsize_word), "bold"))



    def _apply_click_through(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_LAYERED    = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE  = 0x08000000
            WS_EX_TOOLWINDOW  = 0x00000080
            styles = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            styles |= (WS_EX_LAYERED | WS_EX_TRANSPARENT
                       | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, styles)
        except Exception:
            pass

    def _ensure_win(self):
        """Comprueba que la ventana existe; si no, la recrea."""
        try:
            self.win.state()  # lanza TclError si la ventana no existe
        except Exception:
            print("[grid] ventana destruida, recreando...")
            self.win = tk.Toplevel(self._master)
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            self.win.geometry(f"{self.screen_w}x{self.screen_h}+0+0")
            self.win.configure(bg=TRANSPARENT_KEY)
            try:
                self.win.attributes("-transparentcolor", TRANSPARENT_KEY)
            except tk.TclError:
                self.win.attributes("-alpha", 0.25)
            self.canvas = tk.Canvas(
                self.win, width=self.screen_w, height=self.screen_h,
                bg=TRANSPARENT_KEY, highlightthickness=0, bd=0)
            self.canvas.pack(fill="both", expand=True)
            self.visible = False
            self.highlighted = None
            self.highlight_id = None
            self._zoom_col = None
            self._zoom_row = None
            self._draw()
            self.win.after(100, self._apply_click_through)

    # ---- visibilidad ----
    def show(self):
        self._ensure_win()
        if not self.visible:
            try:
                self.win.deiconify()
                self.win.attributes("-topmost", True)
                self._apply_click_through()
                self.visible = True
            except Exception as e:
                print(f"[grid] error al mostrar: {e}")

    def hide(self):
        try:
            if self.visible:
                self.win.withdraw()
                self.visible = False
        except Exception as e:
            print(f"[grid] error al ocultar: {e}")
            self.visible = False

    def toggle(self):
        self.hide() if self.visible else self.show()

    # ---- zoom ----
    def zoom_cell(self, col, row):
        self._zoom_col = col
        self._zoom_row = row
        self.highlight_id = None
        self.highlighted = None
        self._draw()
        if not self.visible:
            self.show()
        print(f"[zoom] celda {self.letters[col]}{row + 1}")

    def zoom_exit(self):
        self._zoom_col = None
        self._zoom_row = None
        self._draw()
        print("[zoom] salida")

    def zoom_click_sub(self, sub_idx, mode="left"):
        """sub_idx: 0=◆arriba-izq  1=●arriba-dcha  2=▲abajo-izq  3=★abajo-dcha"""
        if self._zoom_col is None or not (0 <= sub_idx <= 3):
            return False
        col, row = self._zoom_col, self._zoom_row
        si = sub_idx % 2
        sj = sub_idx // 2
        x = int(col * self.cell_w + (si + 0.5) * self.cell_w / 2)
        y = int(row * self.cell_h + (sj + 0.5) * self.cell_h / 2)
        time.sleep(0.04)
        if mode == "double":
            pyautogui.doubleClick(x, y)
        elif mode == "right":
            pyautogui.rightClick(x, y)
        else:
            pyautogui.click(x, y)
        sym = self.ZOOM_SYMBOLS[sub_idx][0]
        print(f"[zoom-clic {mode}] {sym} idx={sub_idx} ({x},{y})")
        self.zoom_exit()
        return True


    # ---- marcar/resaltar celda ----
    def highlight_cell(self, col, row):
        self.clear_highlight()
        x1 = col * self.cell_w
        y1 = row * self.cell_h
        x2 = x1 + self.cell_w
        y2 = y1 + self.cell_h
        self.highlight_id = self.canvas.create_rectangle(
            x1, y1, x2, y2,
            outline=HIGHLIGHT_COLOR, width=5,
            fill="#FF0000", stipple="gray12",
        )
        self.highlighted = (col, row)
        if not self.visible:
            self.show()

    def clear_highlight(self):
        if self.highlight_id is not None:
            self.canvas.delete(self.highlight_id)
            self.highlight_id = None
        self.highlighted = None

    # ---- clic en celda ----
    def _cell_center(self, col, row):
        return (int((col + 0.5) * self.cell_w),
                int((row + 0.5) * self.cell_h))

    def click_cell(self, col, row, mode="left"):
        now = time.time() * 1000
        if (self.last_click_cell == (col, row) and
                now - self.last_click_time < CLICK_COOLDOWN_MS):
            print(f"[clic] ignorado por cooldown ({col},{row})")
            return False
        self.last_click_time = now
        self.last_click_cell = (col, row)
        x, y = self._cell_center(col, row)
        self.win.update_idletasks()
        time.sleep(0.04)
        if mode == "double":
            pyautogui.doubleClick(x, y)
        elif mode == "right":
            pyautogui.rightClick(x, y)
        else:
            pyautogui.click(x, y)
        print(f"[clic {mode}] {self.letters[col]}{row + 1} -> ({x}, {y})")
        return True

    # ---- arrastre ----
    def drag_start(self, col, row):
        x, y = self._cell_center(col, row)
        pyautogui.moveTo(x, y, duration=0.05)
        pyautogui.mouseDown()
        print(f"[arrastre] cogido en {self.letters[col]}{row + 1} ({x},{y})")

    def drag_end(self, col, row):
        x, y = self._cell_center(col, row)
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.mouseUp()
        print(f"[arrastre] soltado en {self.letters[col]}{row + 1} ({x},{y})")

    def drag_cancel(self):
        try:
            pyautogui.mouseUp()
        except Exception:
            pass
        print("[arrastre] cancelado")


# ============================================================
#  HUD: indicador de escucha + ultimo comando + dictado
# ============================================================
class HudOverlay:
    """HUD esquina superior izquierda:
       - punto de color = estado del micro
       - línea 1: último comando reconocido (✓/✗)
       - línea 2: lo que acaba de escuchar (texto crudo, siempre visible)
    """
    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.90)
        self.win.configure(bg="#101010")
        self.win.geometry("+12+12")

        frm = tk.Frame(self.win, bg="#101010", padx=10, pady=6)
        frm.pack(fill="both", expand=True)

        # Fila superior: punto + estado/comando
        top = tk.Frame(frm, bg="#101010")
        top.pack(fill="x")
        self.dot_canvas = tk.Canvas(top, width=16, height=16,
                                    bg="#101010", highlightthickness=0)
        self.dot_canvas.pack(side="left", padx=(0, 8))
        self.dot = self.dot_canvas.create_oval(2, 2, 14, 14, fill="#888888",
                                               outline="")
        self.label = tk.Label(top, text="iniciando...",
                              fg="#FFFFFF", bg="#101010",
                              font=("Segoe UI", 10),
                              width=36, anchor="w")
        self.label.pack(side="left")

        # Separador
        tk.Frame(frm, bg="#333333", height=1).pack(fill="x", pady=(4, 2))

        # Fila inferior: lo que acaba de escuchar
        self.lbl_heard = tk.Label(frm, text="",
                                  fg="#777777", bg="#101010",
                                  font=("Segoe UI", 9, "italic"),
                                  width=36, anchor="w")
        self.lbl_heard.pack(fill="x")

        self._last_cmd_until = 0
        self._reset_text = "escuchando"
        self._history_ref = None   # se asigna desde SuperCapa
        self._set_dot_color("#888888")

    def _set_dot_color(self, color):
        self.dot_canvas.itemconfig(self.dot, fill=color)

    def state_listening(self):
        self._set_dot_color("#00FF66")
        self._reset_text = "escuchando"
        if time.time() * 1000 > self._last_cmd_until:
            self.label.config(text=self._reset_text)

    def state_processing(self):
        self._set_dot_color("#FFCC00")

    def state_sleeping(self):
        self._set_dot_color("#888888")
        self._reset_text = "DORMIDO (di 'despierta')"
        self.label.config(text=self._reset_text)

    def state_no_internet(self):
        self._set_dot_color("#FF3333")
        self._reset_text = "sin internet, reintentando..."
        self.label.config(text=self._reset_text)

    def state_no_mic(self):
        self._set_dot_color("#FF3333")
        self._reset_text = "sin microfono"
        self.label.config(text=self._reset_text)

    def show_command(self, cmd, ok=True):
        prefix = "✓ " if ok else "✗ "
        self.label.config(text=f"{prefix}{cmd[:60]}")
        self._last_cmd_until = time.time() * 1000 + HUD_TIMEOUT_MS
        # Log to history if available (set by SuperCapa after init)
        if hasattr(self, '_history_ref') and self._history_ref:
            try:
                self._history_ref.add(cmd, ok)
            except Exception:
                pass

    def show_heard(self, text):
        """Muestra el texto crudo recién reconocido (siempre, sea comando o no)."""
        display = text[:55] + "…" if len(text) > 55 else text
        self.lbl_heard.config(text=f"🎤 {display}", fg="#AAAAAA")

    def tick(self):
        if time.time() * 1000 > self._last_cmd_until:
            self.label.config(text=self._reset_text)


# ============================================================
#  HUD DE DICTADO (centro inferior)
# ============================================================
class DictateHud:
    """Panel de dictado: aparece cuando el modo texto esta activo.
    Muestra '● DICTANDO' en rojo parpadeante y el ultimo texto reconocido.
    Se posiciona arriba-centro para no tapar el trabajo.
    """
    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.92)
        self.win.configure(bg="#101010")

        # Marco con borde de color
        frame = tk.Frame(self.win, bg="#101010", padx=2, pady=2)
        frame.pack(fill="both", expand=True)

        self.lbl_status = tk.Label(frame, text="● DICTANDO",
                                   fg="#FF4444", bg="#101010",
                                   font=("Segoe UI", 12, "bold"),
                                   padx=16, pady=4)
        self.lbl_status.pack(fill="x")

        self.lbl_text = tk.Label(frame, text="(di algo...)",
                                 fg="#AAAAAA", bg="#181818",
                                 font=("Segoe UI", 10),
                                 padx=16, pady=4,
                                 wraplength=400, justify="left",
                                 anchor="w")
        self.lbl_text.pack(fill="x")

        tk.Label(frame, text="Di 'fin texto' para salir",
                 fg="#555555", bg="#101010",
                 font=("Segoe UI", 8),
                 padx=16, pady=2).pack(fill="x")

        self.visible = False
        self._blink_state = True

    def show(self):
        sw = self.win.winfo_screenwidth()
        self.win.update_idletasks()
        w = max(self.win.winfo_reqwidth(), 420)
        h = self.win.winfo_reqheight()
        x = (sw - w) // 2
        y = 10   # arriba-centro, sin tapar el trabajo
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.visible = True
        self.lbl_text.config(text="(di algo...)")

    def hide(self):
        self.win.withdraw()
        self.visible = False

    def update_text(self, text):
        """Actualiza el texto dictado visible en el panel."""
        if self.visible:
            # Mostrar solo las ultimas ~50 letras para que quepa
            display = text[-50:] if len(text) > 50 else text
            self.lbl_text.config(text=display, fg="#FFFFFF")

    def blink(self):
        if not self.visible:
            return
        self._blink_state = not self._blink_state
        self.lbl_status.config(
            fg="#FF4444" if self._blink_state else "#882222")



# ============================================================
#  HISTORIAL DE COMANDOS
# ============================================================
class HistoryHud:
    """Ventana flotante con los ultimos 12 comandos ejecutados."""
    MAX = 12

    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.92)
        self.win.configure(bg="#101010")
        self.visible = False
        self._entries = []  # lista de (texto, ok)

        frm = tk.Frame(self.win, bg="#101010", padx=10, pady=8)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="📋  Historial de comandos",
                 fg="#888888", bg="#101010",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Frame(frm, bg="#333333", height=1).pack(fill="x", pady=(4, 6))

        self.rows_frame = tk.Frame(frm, bg="#101010")
        self.rows_frame.pack(fill="both", expand=True)

        tk.Button(frm, text="Cerrar",
                  command=self.hide,
                  bg="#2a2a2a", fg="#888888",
                  activebackground="#444444",
                  bd=0, padx=8, pady=2,
                  font=("Segoe UI", 8)).pack(pady=(6, 0))

        # Posicionar a la derecha del HUD
        sw = master.winfo_screenwidth()
        self.win.geometry(f"+{sw - 260}+12")

    def add(self, text, ok):
        self._entries.append((text, ok))
        if len(self._entries) > self.MAX:
            self._entries.pop(0)
        if self.visible:
            self._refresh()

    def _refresh(self):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        for txt, ok in reversed(self._entries):
            prefix = "✓" if ok else "✗"
            color  = "#00FF88" if ok else "#FF6644"
            tk.Label(self.rows_frame,
                     text=f"  {prefix}  {txt[:38]}",
                     fg=color, bg="#101010",
                     font=("Segoe UI", 9),
                     anchor="w").pack(fill="x", pady=1)

    def show(self):
        self._refresh()
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.visible = True

    def hide(self):
        self.win.withdraw()
        self.visible = False

    def toggle(self):
        self.hide() if self.visible else self.show()


# ============================================================
#  VENTANA DE AYUDA RAPIDA
# ============================================================
class HelpHud:
    """Ventana con los comandos mas utiles agrupados por categoria."""

    COMMANDS = [
        ("Rejilla",    ["rejilla", "ocultar rejilla", "zoom A5", "zoom", "salir"]),
        ("Navegar",    ["A5  (mover)", "clic A5", "doble A5", "marcar A5"]),
        ("Zoom",       ["diamante / círculo", "triángulo / estrella"]),
        ("Ratón",      ["arriba/abajo/izq/dcha", "clic aquí", "doble clic", "clic derecho"]),
        ("Arrastre",   ["coger A1", "soltar B5", "soltar aquí", "cancelar"]),
        ("Texto",      ["texto", "fin texto", "punto/coma/interrogación", "borrar palabra"]),
        ("Edición",    ["copiar/pegar/cortar", "deshacer", "repetir", "seleccionar todo"]),
        ("Páginas",    ["subir/bajar página N", "página siguiente N", "ir al inicio/final"]),
        ("Ventanas",   ["cerrar ventana", "minimizar", "maximizar", "otra ventana"]),
        ("Sistema",    ["dormir / despierta", "socorro", "pitido on/off", "salir del programa"]),
    ]

    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.95)
        self.win.configure(bg="#101010")
        self.visible = False

        frm = tk.Frame(self.win, bg="#101010", padx=12, pady=10)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="❓  Comandos disponibles — di el nombre",
                 fg="#00AA55", bg="#101010",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Frame(frm, bg="#333333", height=1).pack(fill="x", pady=(4, 6))

        # Dos columnas
        cols = tk.Frame(frm, bg="#101010")
        cols.pack(fill="both", expand=True)
        left  = tk.Frame(cols, bg="#101010")
        right = tk.Frame(cols, bg="#101010")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right.pack(side="left", fill="both", expand=True)

        half = len(self.COMMANDS) // 2
        for idx, (cat, cmds) in enumerate(self.COMMANDS):
            parent = left if idx < half else right
            tk.Label(parent, text=cat,
                     fg="#2196F3", bg="#101010",
                     font=("Segoe UI", 9, "bold"),
                     anchor="w").pack(fill="x", pady=(4, 0))
            for c in cmds:
                tk.Label(parent, text=f"  {c}",
                         fg="#CCCCCC", bg="#101010",
                         font=("Segoe UI", 8),
                         anchor="w").pack(fill="x")

        tk.Frame(frm, bg="#333333", height=1).pack(fill="x", pady=(8, 4))
        tk.Button(frm, text="Cerrar  (di 'ayuda' otra vez)",
                  command=self.hide,
                  bg="#2a2a2a", fg="#888888",
                  activebackground="#444444",
                  bd=0, padx=10, pady=3,
                  font=("Segoe UI", 8)).pack()

        # Centrar en pantalla
        self.win.update_idletasks()
        sw = master.winfo_screenwidth()
        sh = master.winfo_screenheight()
        w = max(self.win.winfo_reqwidth(), 560)
        h = self.win.winfo_reqheight()
        self.win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def show(self):
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.visible = True

    def hide(self):
        self.win.withdraw()
        self.visible = False

    def toggle(self):
        self.hide() if self.visible else self.show()



# ============================================================
#  VERSIÓN DE TÉRMINOS — incrementar si cambia el texto legal
# ============================================================
TERMS_VERSION = "1.2"

# ============================================================
#  PANTALLA DE BIENVENIDA + TÉRMINOS Y CONDICIONES
# ============================================================
class WelcomeDialog:
    """
    Aparece la primera vez que se ejecuta el programa (o si cambian
    los términos). El usuario debe aceptar para continuar.
    """

    TERMS_TEXT = (
        "CONDICIONES DE USO — tapunto-voz\n\n"

        "1. IDENTIFICACIÓN\n"
        "tapunto-voz es un programa gratuito y de código abierto "
        "desarrollado por Enrique García Prats, Jorge García Prats "
        "y Alicia Prats Martínez (en adelante, «los desarrolladores»). "
        "Contacto: tapunto.soporte@gmail.com\n\n"

        "2. RECONOCIMIENTO DE VOZ Y DEPENDENCIA DE GOOGLE\n"
        "El programa utiliza Google Speech Recognition (Google LLC) "
        "para convertir el audio del micrófono en texto. Esto implica:\n"
        "  · El audio de voz se envía a servidores de Google (EEUU/Europa) "
        "mediante conexión cifrada HTTPS.\n"
        "  · Google procesa ese audio según sus propios términos y "
        "política de privacidad (policies.google.com/privacy).\n"
        "  · El funcionamiento del programa DEPENDE de que Google mantenga "
        "activo este servicio. Si Google interrumpe, modifica o deja de "
        "ofrecer el servicio de reconocimiento de voz, tapunto-voz puede "
        "dejar de funcionar total o parcialmente sin previo aviso y sin "
        "que los desarrolladores puedan evitarlo ni sean responsables "
        "de ello.\n\n"

        "3. PRIVACIDAD Y DATOS LOCALES\n"
        "El programa guarda un archivo config.json con tus preferencias "
        "(alias de voz, posición de barra, perfil de micrófono) en la "
        "misma carpeta del ejecutable. Este archivo no sale de tu "
        "ordenador. Más información en tapunto.app/tapunto-voz/legal.html\n\n"

        "4. EXONERACIÓN DE RESPONSABILIDAD — DAÑOS AL EQUIPO\n"
        "El programa se ofrece TAL CUAL, sin garantía de ningún tipo. "
        "Los desarrolladores NO se responsabilizan de:\n"
        "  · Daños, pérdida de datos o mal funcionamiento del ordenador, "
        "el sistema operativo o cualquier otro programa causados por el "
        "uso de tapunto-voz.\n"
        "  · Acciones ejecutadas por error mediante comandos de voz "
        "(borrado de archivos, cierre de aplicaciones, etc.).\n"
        "  · Pérdidas económicas, de productividad o de cualquier otra "
        "naturaleza derivadas del uso o de la imposibilidad de uso.\n"
        "  · Interrupciones del servicio causadas por Google o por "
        "problemas de conectividad a internet.\n\n"

        "5. MENORES DE EDAD\n"
        "El uso de tapunto-voz por parte de menores de 14 años requiere "
        "el consentimiento expreso de sus padres o tutores legales. "
        "Los menores entre 14 y 18 años pueden usar el programa con "
        "conocimiento de sus padres o tutores. Al aceptar estas "
        "condiciones, declaras tener al menos 14 años o contar con "
        "el consentimiento de tu tutor legal.\n\n"

        "6. NO ES UN PRODUCTO SANITARIO\n"
        "tapunto-voz NO es un producto sanitario ni un dispositivo médico "
        "según el Reglamento (UE) 2017/745. No ha sido evaluado ni "
        "certificado como tal. NO sustituye la atención médica, la "
        "terapia ocupacional, la logopedia ni ningún tratamiento "
        "prescrito por un profesional sanitario.\n\n"

        "7. PROPIEDAD INTELECTUAL\n"
        "El nombre TAPUNTO está registrado como marca en la OEPM. "
        "El código se distribuye bajo licencia de código abierto "
        "(ver archivo LICENSE). Queda prohibido redistribuir versiones "
        "modificadas bajo el nombre TAPUNTO sin autorización expresa.\n\n"

        "8. MODIFICACIONES Y CONTINUIDAD DEL SERVICIO\n"
        "Los desarrolladores se reservan el derecho a modificar, "
        "suspender o interrumpir el programa en cualquier momento "
        "sin previo aviso. Las actualizaciones pueden cambiar "
        "funcionalidades existentes.\n\n"

        "Texto legal completo: https://tapunto.app/tapunto-voz/legal.html\n"
        "Soporte: tapunto.soporte@gmail.com"
    )

    def __init__(self, cfg):
        self.cfg      = cfg
        self.accepted = False
        self._build()

    def _build(self):
        self.win = tk.Tk()
        self.win.title("tapunto-voz — Bienvenida")
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        w, h = 680, 640
        self.win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.win.resizable(True, True)
        self.win.configure(bg="#0D47A1")
        self.win.protocol("WM_DELETE_WINDOW", self._reject)

        # Estructura: cabecera azul arriba, cuerpo blanco ocupa el resto
        self.win.rowconfigure(1, weight=1)
        self.win.columnconfigure(0, weight=1)

        # ── Cabecera ──────────────────────────────────────────
        hdr = tk.Frame(self.win, bg="#0D47A1")
        hdr.grid(row=0, column=0, sticky="ew")

        tk.Label(hdr, text="tapunto-voz",
                 font=("Segoe UI", 28, "bold"),
                 fg="white", bg="#0D47A1").pack(pady=(18, 2))
        tk.Label(hdr,
                 text="Control del ordenador por voz · tapunto.app",
                 font=("Segoe UI", 11), fg="#BBDEFB",
                 bg="#0D47A1").pack(pady=(0, 8))

        # Botón X rojo para cerrar
        tk.Button(hdr, text="  ✕  Salir sin aceptar  ",
                  command=self._reject,
                  font=("Segoe UI", 9),
                  bg="#C62828", fg="white",
                  activebackground="#B71C1C",
                  relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(pady=(0, 10))

        # ── Cuerpo blanco usa grid para control preciso ────────
        body = tk.Frame(self.win, bg="white", highlightthickness=0)
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(1, weight=1)   # la caja de texto se expande
        body.columnconfigure(0, weight=1)

        tk.Label(body,
                 text="Antes de empezar, lee y acepta las condiciones de uso:",
                 font=("Segoe UI", 10, "bold"),
                 fg="#1565C0", bg="white").grid(
                     row=0, column=0, sticky="w",
                     padx=24, pady=(14, 4))

        # Caja de texto con scroll — fila 1, se expande
        frm = tk.Frame(body, bg="white")
        frm.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 0))
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        txt = tk.Text(frm, wrap="word",
                      font=("Segoe UI", 9),
                      bg="#F5F5F5", relief="flat",
                      padx=12, pady=10,
                      state="normal")
        sb = tk.Scrollbar(frm, command=txt.yview)
        txt.config(yscrollcommand=sb.set)
        txt.insert("1.0", self.TERMS_TEXT)
        txt.config(state="disabled")
        sb.grid(row=0, column=1, sticky="ns")
        txt.grid(row=0, column=0, sticky="nsew")

        # Zona inferior FIJA — fila 2, altura fija
        bottom = tk.Frame(body, bg="#EEF4FF",
                          highlightthickness=1,
                          highlightbackground="#BBCCE8")
        bottom.grid(row=2, column=0, sticky="ew", padx=0, pady=0)

        # Enlace legal
        lnk = tk.Label(bottom,
                        text="Leer condiciones completas en tapunto.app/tapunto-voz/legal.html",
                        font=("Segoe UI", 8, "underline"),
                        fg="#1565C0", bg="#EEF4FF", cursor="hand2")
        lnk.pack(pady=(8, 2))
        lnk.bind("<Button-1>", lambda e: self._open_legal())

        # Checkbox — fuera del frame de scroll, siempre visible
        self._var = tk.BooleanVar(value=False)
        chk = tk.Checkbutton(
            bottom,
            text="He leído y acepto las condiciones de uso y la política de privacidad.",
            variable=self._var,
            font=("Segoe UI", 10),
            bg="#EEF4FF", activebackground="#EEF4FF",
            fg="#1A1A1A", cursor="hand2",
            anchor="w")
        chk.pack(fill="x", padx=24, pady=(4, 6))

        # Botones — siempre visibles en la parte inferior
        btn_frame = tk.Frame(bottom, bg="#EEF4FF")
        btn_frame.pack(fill="x", padx=24, pady=(0, 14))

        tk.Button(btn_frame, text="No acepto — salir",
                  command=self._reject,
                  font=("Segoe UI", 10),
                  bg="#DDDDDD", fg="#555555",
                  relief="flat", padx=16, pady=8,
                  cursor="hand2").pack(side="left")

        tk.Button(btn_frame, text="  Acepto — continuar  ▶  ",
                  command=self._accept,
                  font=("Segoe UI", 11, "bold"),
                  bg="#1565C0", fg="white",
                  activebackground="#0D47A1",
                  relief="flat", padx=20, pady=10,
                  cursor="hand2").pack(side="right")

    def _open_legal(self):
        import webbrowser
        webbrowser.open("https://tapunto.app/tapunto-voz/legal.html")

    def _accept(self):
        if not self._var.get():
            tk.messagebox.showwarning(
                "Aceptación requerida",
                "Marca la casilla para confirmar que has leído\n"
                "y aceptas las condiciones de uso.",
                parent=self.win)
            return
        self.cfg["terms_accepted"] = True
        self.cfg["terms_version"]  = TERMS_VERSION
        save_config(self.cfg)
        self.accepted = True
        self.win.destroy()

    def _reject(self):
        self.accepted = False
        self.win.destroy()

    def show(self):
        self.win.mainloop()
        return self.accepted


# ============================================================
#  TUTORIAL INTERACTIVO
# ============================================================
class TutorialDialog:
    """Tutorial de 6 pasos que aparece tras aceptar los términos
    o al pulsar '?' / decir 'tutorial'."""

    STEPS = [
        {
            "titulo": "¡Bienvenido a tapunto-voz!",
            "icono":  "🎙",
            "texto": (
                "tapunto-voz te permite controlar el ordenador completamente "
                "con la voz.\n\n"
                "Habla con claridad y en español. El programa escucha "
                "constantemente y ejecuta los comandos que reconoce.\n\n"
                "Este tutorial te guiará por los conceptos básicos "
                "en 6 pasos sencillos."
            ),
        },
        {
            "titulo": "La rejilla",
            "icono":  "📐",
            "texto": (
                "Di  «rejilla»  para mostrar una cuadrícula sobre toda "
                "la pantalla.\n\n"
                "Cada celda tiene una coordenada: letra (A–N) + número (1–15).\n\n"
                "Por ejemplo, la celda del centro podría ser  «G 8».\n\n"
                "Di  «ocultar rejilla»  para quitarla.\n"
                "También puedes usar la tecla  F8."
            ),
        },
        {
            "titulo": "Mover el cursor y hacer clic",
            "icono":  "🖱",
            "texto": (
                "Para mover el cursor a una celda, di su coordenada:\n\n"
                "     «A 5»   →  mueve el cursor a esa celda\n\n"
                "Para hacer clic en una celda:\n\n"
                "     «clic A 5»       →  clic izquierdo\n"
                "     «doble A 5»      →  doble clic\n"
                "     «clic derecho A 5»  →  clic derecho\n\n"
                "Nota: la columna  I  no existe porque Google la confunde "
                "con la  Y.  Usa  J  en su lugar."
            ),
        },
        {
            "titulo": "Zoom — selección precisa",
            "icono":  "🔍",
            "texto": (
                "Si dos elementos están en la misma celda, usa el zoom "
                "para dividirla en 4 subceldas:\n\n"
                "     «zoom A 5»  →  amplía la celda A5\n\n"
                "Luego di el símbolo de la subcelda:\n\n"
                "     ◆  «diamante»   →  arriba izquierda\n"
                "     ●  «círculo»    →  arriba derecha\n"
                "     ▲  «triángulo»  →  abajo izquierda\n"
                "     ★  «estrella»   →  abajo derecha\n\n"
                "El programa hace clic y sale del zoom automáticamente."
            ),
        },
        {
            "titulo": "Modo dictado — escribir con la voz",
            "icono":  "📝",
            "texto": (
                "Para escribir texto en cualquier aplicación:\n\n"
                "     «texto»      →  activa el modo dictado\n\n"
                "Habla y el programa escribe lo que dices. "
                "Para añadir signos de puntuación di su nombre:\n\n"
                "     «punto»  «coma»  «interrogación»  «exclamación»\n"
                "     «dos puntos»  «nueva línea»  «párrafo»\n\n"
                "     «fin texto»  →  desactiva el modo dictado"
            ),
        },
        {
            "titulo": "Personaliza tu voz",
            "icono":  "🗣",
            "texto": (
                "El programa puede aprender cómo  TÚ  pronuncias cada "
                "comando.\n\n"
                "Pulsa el botón  «Grabar Voz»  en la barra inferior, "
                "graba cómo pronuncias un comando y vincúlalo a la "
                "acción correspondiente.\n\n"
                "Comandos útiles para empezar:\n\n"
                "     «ayuda»      →  ver todos los comandos\n"
                "     «historial»  →  últimos 12 comandos\n"
                "     «dormir»  /  «despierta»  →  pausar y reactivar\n"
                "     «socorro»    →  parada de emergencia\n"
                "     «salir del programa»  →  cerrar tapunto-voz"
            ),
        },
    ]

    def __init__(self, parent=None, on_close=None):
        self._step    = 0
        self._on_close = on_close
        if parent:
            self.win = tk.Toplevel(parent)
        else:
            self.win = tk.Tk()
        self._build()

    def _build(self):
        self.win.title("tapunto-voz — Tutorial")
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        w, h = 580, 480
        self.win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.win.resizable(True, True)
        self.win.configure(bg="#0D47A1")
        self.win.protocol("WM_DELETE_WINDOW", self._close)

        # Layout con grid para control preciso de filas
        self.win.rowconfigure(1, weight=1)
        self.win.columnconfigure(0, weight=1)

        # Cabecera fija — fila 0
        hdr = tk.Frame(self.win, bg="#0D47A1")
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(hdr, text="tapunto-voz · Tutorial",
                 font=("Segoe UI", 13, "bold"),
                 fg="white", bg="#0D47A1").pack(pady=(12, 0))
        self._lbl_paso = tk.Label(hdr, text="",
                                   font=("Segoe UI", 9),
                                   fg="#BBDEFB", bg="#0D47A1")
        self._lbl_paso.pack(pady=(2, 4))

        # Botón X rojo para cerrar el tutorial
        tk.Button(hdr, text="  ✕  Cerrar tutorial  ",
                  command=self._close,
                  font=("Segoe UI", 9),
                  bg="#C62828", fg="white",
                  activebackground="#B71C1C",
                  relief="flat", padx=10, pady=4,
                  cursor="hand2").pack(pady=(0, 8))

        # Cuerpo blanco — fila 1, se expande
        body = tk.Frame(self.win, bg="white")
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        # Icono + título — fila 0
        top = tk.Frame(body, bg="white")
        top.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))

        self._lbl_icono = tk.Label(top, text="",
                                    font=("Segoe UI Emoji", 30),
                                    bg="white")
        self._lbl_icono.pack(side="left", padx=(0, 12))

        self._lbl_titulo = tk.Label(top, text="",
                                     font=("Segoe UI", 14, "bold"),
                                     fg="#1565C0", bg="white",
                                     wraplength=420, justify="left")
        self._lbl_titulo.pack(side="left", anchor="w")

        # Texto del paso — fila 1, se expande
        self._lbl_texto = tk.Label(body, text="",
                                    font=("Segoe UI", 10),
                                    fg="#333333", bg="white",
                                    wraplength=520, justify="left")
        self._lbl_texto.grid(row=1, column=0, sticky="nsew",
                              padx=24, pady=(10, 6))

        # Adaptar wraplength al redimensionar la ventana
        def _on_resize(event):
            new_wrap = max(300, event.width - 80)
            self._lbl_texto.config(wraplength=new_wrap)
            self._lbl_titulo.config(wraplength=max(200, new_wrap - 80))
        self.win.bind("<Configure>", _on_resize)

        # Zona inferior FIJA — fila 2
        bottom = tk.Frame(body, bg="#EEF4FF",
                           highlightthickness=1,
                           highlightbackground="#BBCCE8")
        bottom.grid(row=2, column=0, sticky="ew")

        # Puntos de progreso
        dots_frame = tk.Frame(bottom, bg="#EEF4FF")
        dots_frame.pack(pady=(8, 4))
        self._dots = []
        for i in range(len(self.STEPS)):
            d = tk.Label(dots_frame, text="●",
                          font=("Segoe UI", 9),
                          bg="#EEF4FF")
            d.pack(side="left", padx=3)
            self._dots.append(d)

        # Botones navegación
        btn_frame = tk.Frame(bottom, bg="#EEF4FF")
        btn_frame.pack(fill="x", padx=20, pady=(4, 12))

        self._btn_prev = tk.Button(
            btn_frame, text="◀ Anterior",
            command=self._prev,
            font=("Segoe UI", 10),
            bg="#DDDDDD", fg="#555555",
            relief="flat", padx=14, pady=7,
            cursor="hand2")
        self._btn_prev.pack(side="left")

        self._btn_next = tk.Button(
            btn_frame, text="Siguiente ▶",
            command=self._next,
            font=("Segoe UI", 10, "bold"),
            bg="#1565C0", fg="white",
            activebackground="#0D47A1",
            relief="flat", padx=14, pady=7,
            cursor="hand2")
        self._btn_next.pack(side="right")

        self._render()

    def _render(self):
        step = self.STEPS[self._step]
        total = len(self.STEPS)
        self._lbl_paso.config(
            text=f"Paso {self._step + 1} de {total}")
        self._lbl_icono.config(text=step["icono"])
        self._lbl_titulo.config(text=step["titulo"])
        self._lbl_texto.config(text=step["texto"])

        # Puntos de progreso
        for i, d in enumerate(self._dots):
            d.config(fg="#1565C0" if i == self._step else "#CCCCCC")

        # Botones
        self._btn_prev.config(
            state="normal" if self._step > 0 else "disabled")
        if self._step == total - 1:
            self._btn_next.config(text="¡Empezar! ✓",
                                   bg="#1E6B3A")
        else:
            self._btn_next.config(text="Siguiente ▶",
                                   bg="#1565C0")

    def _prev(self):
        if self._step > 0:
            self._step -= 1
            self._render()
            self.win.update()

    def _next(self):
        if self._step < len(self.STEPS) - 1:
            self._step += 1
            self._render()
            self.win.update()
        else:
            self._close()

    def _close(self):
        if self._on_close:
            self._on_close()
        self.win.destroy()

    def show(self):
        """Usar cuando TutorialDialog es la ventana raíz."""
        self.win.mainloop()


# ============================================================
#  GESTOR DE ALIAS DE VOZ
#  Ventana independiente donde el usuario configura sus alias.
# ============================================================
class AliasManager:
    """
    Ventana para gestionar alias de voz personalizados.
    Permite:
      - Ver todos los alias guardados
      - Grabar una nueva palabra (transcripcion via Google)
      - Vincularla a un comando del catalogo
      - Eliminar alias existentes
    """

    MULTI_WORD_INFO = (
        "Comandos de 2+ palabras (los mas dificiles de pronunciar):\n"
        "doble clic · clic derecho · cerrar ventana · cerrar teclado ·\n"
        "abrir teclado · subir volumen · bajar volumen · mas rapido ·\n"
        "mas despacio · otra ventana · ver escritorio · abrir explorador ·\n"
        "empezar dictado · fin dictado · seleccionar todo · ocultar rejilla ·\n"
        "ocultar barra · salir del programa  (configuralos como alias!)"
    )

    def __init__(self, master, cfg, on_close=None):
        self.master = master
        self.cfg = cfg
        self.on_close = on_close
        self._recording = False
        self._rec_thread = None

        self.win = tk.Toplevel(master)
        self.win.title("Mis Palabras - Alias de Voz")
        self.win.configure(bg="#1a1a1a")
        self.win.resizable(True, True)
        self.win.attributes("-topmost", True)
        self.win.geometry("700x560")
        self.win.protocol("WM_DELETE_WINDOW", self._close)

        self._build_ui()
        self._refresh_list()

    # ---- construccion UI ----
    def _build_ui(self):
        DARK  = "#1a1a1a"
        PANEL = "#252525"
        BTN   = "#2e2e2e"
        ACC   = "#00AA55"
        FG    = "#FFFFFF"
        FG2   = "#AAAAAA"
        FONT  = ("Segoe UI", 10)
        FONTS = ("Segoe UI", 9)
        FONTB = ("Segoe UI", 10, "bold")

        # --- titulo ---
        tk.Label(self.win,
                 text="🎙  MIS PALABRAS  –  Alias de Voz Personalizados",
                 bg=DARK, fg=ACC, font=("Segoe UI", 12, "bold"),
                 pady=8).pack(fill="x")

        # ── zona principal: izquierda=crear, derecha=lista acciones ──
        main = tk.Frame(self.win, bg=DARK)
        main.pack(fill="both", expand=True, padx=10, pady=4)

        # ── columna izquierda ──────────────────────────────────────
        left = tk.Frame(main, bg=DARK)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))

        # --- panel nuevo alias ---
        new_frame = tk.LabelFrame(left, text=" Crear nuevo alias ",
                                  bg=PANEL, fg=FG2, font=FONTS,
                                  padx=10, pady=8)
        new_frame.pack(fill="x")

        # Fila 1: lo que YO digo + grabar
        row1 = tk.Frame(new_frame, bg=PANEL)
        row1.pack(fill="x", pady=3)
        tk.Label(row1, text="Lo que YO digo:", bg=PANEL, fg=FG,
                 font=FONT, anchor="w").pack(anchor="w")
        ent_row = tk.Frame(new_frame, bg=PANEL)
        ent_row.pack(fill="x", pady=(0, 4))
        self.var_alias = tk.StringVar()
        self.entry_alias = tk.Entry(
            ent_row, textvariable=self.var_alias,
            font=("Segoe UI", 12), bg="#333333", fg=FG,
            insertbackground=FG, relief="flat")
        self.entry_alias.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_rec = tk.Button(
            ent_row, text="🎙 Grabar",
            command=self._toggle_record,
            bg=ACC, fg=FG, font=FONTS,
            activebackground="#008844",
            relief="flat", padx=10, pady=4)
        self.btn_rec.pack(side="left")

        self.lbl_rec_status = tk.Label(
            new_frame, text="", bg=PANEL, fg="#FFAA00", font=FONTS,
            wraplength=300, justify="left")
        self.lbl_rec_status.pack(anchor="w", pady=(0, 6))

        # Separador
        tk.Label(new_frame, text="Se ejecuta como  (selecciona de la lista →)",
                 bg=PANEL, fg=FG2, font=FONTS).pack(anchor="w", pady=(4, 2))

        self.lbl_selected_cmd = tk.Label(
            new_frame,
            text="(ninguna seleccionada)",
            bg="#1a3a28", fg="#88FF88",
            font=("Segoe UI", 10, "bold"),
            relief="flat", padx=8, pady=4,
            wraplength=300, justify="left")
        self.lbl_selected_cmd.pack(fill="x", pady=(0, 8))

        # Botón guardar
        tk.Button(new_frame, text="✔  Guardar alias",
                  command=self._save_alias,
                  bg="#1a5c36", fg=FG, font=FONTB,
                  activebackground="#2a8c56",
                  relief="flat", padx=14, pady=6).pack(anchor="w")
        self.lbl_save_msg = tk.Label(
            new_frame, text="", bg=PANEL, fg=ACC, font=FONTS,
            wraplength=300, justify="left")
        self.lbl_save_msg.pack(anchor="w", pady=(4, 0))

        # --- lista alias guardados ---
        saved_frame = tk.LabelFrame(left, text=" Mis alias guardados ",
                                    bg=PANEL, fg=FG2, font=FONTS,
                                    padx=8, pady=6)
        saved_frame.pack(fill="both", expand=True, pady=(8, 0))

        hdr = tk.Frame(saved_frame, bg="#333333")
        hdr.pack(fill="x", pady=(0, 2))
        tk.Label(hdr, text="Lo que digo", bg="#333333", fg=FG2,
                 font=FONTS, width=18, anchor="w").pack(side="left", padx=4)
        tk.Label(hdr, text="Accion", bg="#333333", fg=FG2,
                 font=FONTS, anchor="w").pack(side="left", padx=4)

        canvas_s = tk.Canvas(saved_frame, bg=PANEL, highlightthickness=0,
                             height=140)
        sb_s = tk.Scrollbar(saved_frame, orient="vertical",
                            command=canvas_s.yview)
        canvas_s.configure(yscrollcommand=sb_s.set)
        sb_s.pack(side="right", fill="y")
        canvas_s.pack(side="left", fill="both", expand=True)
        self.list_inner = tk.Frame(canvas_s, bg=PANEL)
        self._cw_saved = canvas_s.create_window(
            (0, 0), window=self.list_inner, anchor="nw")
        self.list_inner.bind("<Configure>",
            lambda e: canvas_s.configure(
                scrollregion=canvas_s.bbox("all")))
        canvas_s.bind("<Configure>",
            lambda e: canvas_s.itemconfig(self._cw_saved, width=e.width))
        self._canvas = canvas_s

        # ── columna derecha: todas las acciones disponibles ────────
        right = tk.LabelFrame(
            main,
            text=" Acciones disponibles  (haz clic para seleccionar) ",
            bg=PANEL, fg=FG2, font=FONTS, padx=6, pady=6)
        right.pack(side="left", fill="both", expand=True)

        # Construir lista plana con cabeceras de categoria
        self._action_items = []   # lista de (display_text, trigger|None)
        for cat, entries in ALIAS_CATALOG.items():
            self._action_items.append((f"── {cat} ──", None))
            for label, trigger in entries:
                self._action_items.append((label, trigger))

        # Listbox con scrollbar
        lb_frame = tk.Frame(right, bg=PANEL)
        lb_frame.pack(fill="both", expand=True)
        sb_lb = tk.Scrollbar(lb_frame, orient="vertical")
        self.listbox = tk.Listbox(
            lb_frame,
            yscrollcommand=sb_lb.set,
            bg="#1e1e1e", fg=FG,
            selectbackground="#005533",
            selectforeground="#FFFFFF",
            font=("Segoe UI", 10),
            activestyle="none",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            width=28)
        sb_lb.config(command=self.listbox.yview)
        sb_lb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)

        for display, trigger in self._action_items:
            self.listbox.insert("end", f"  {display}")

        # Colorear cabeceras
        for i, (display, trigger) in enumerate(self._action_items):
            if trigger is None:
                self.listbox.itemconfig(
                    i, fg="#00AA55", selectbackground="#1a1a1a",
                    selectforeground="#00AA55")

        self.listbox.bind("<<ListboxSelect>>", self._on_action_select)

        # Botón cerrar abajo del todo
        tk.Button(self.win, text="Cerrar",
                  command=self._close,
                  bg=BTN, fg=FG2, font=FONTS,
                  activebackground="#444444",
                  relief="flat", padx=16, pady=4).pack(pady=6)

    # ---- helpers ----
    def _on_action_select(self, event):
        """Callback al hacer clic en la lista de acciones."""
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        display, trigger = self._action_items[idx]
        if trigger is None:
            # Es una cabecera de categoria, deseleccionar
            self.listbox.selection_clear(0, "end")
            return
        self._selected_trigger = trigger
        self._selected_label = display
        self.lbl_selected_cmd.config(
            text=f"✔  {display}  →  [{trigger}]")

    def _get_selected_trigger(self):
        return getattr(self, "_selected_trigger", None)

    def _get_trigger_for_cmd_label(self, label):
        """Devuelve el trigger estandar para una etiqueta del catalogo."""
        for entries in ALIAS_CATALOG.values():
            for lbl, trigger in entries:
                if lbl == label:
                    return trigger
        return label  # fallback

    def _refresh_list(self):
        """Repinta la lista de alias guardados."""
        for w in self.list_inner.winfo_children():
            w.destroy()

        aliases = self.cfg.get("aliases", {})
        if not aliases:
            tk.Label(self.list_inner,
                     text="(Todavia no hay alias. Crea el primero arriba!)",
                     bg="#252525", fg="#666666",
                     font=("Segoe UI", 9, "italic"),
                     pady=10).pack(anchor="w", padx=8)
            return

        for alias_text, trigger in sorted(aliases.items()):
            row = tk.Frame(self.list_inner, bg="#2a2a2a",
                           pady=2, padx=4)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"  🎤  {alias_text}",
                     bg="#2a2a2a", fg="#FFFFFF",
                     font=("Segoe UI", 10), width=26,
                     anchor="w").pack(side="left")
            tk.Label(row, text=f"→  {trigger}",
                     bg="#2a2a2a", fg="#88CC88",
                     font=("Segoe UI", 10), width=26,
                     anchor="w").pack(side="left")
            # Boton eliminar (captura de clausura)
            def _del(a=alias_text):
                self._delete_alias(a)
            tk.Button(row, text="✕",
                      command=_del,
                      bg="#3a2020", fg="#FF6666",
                      activebackground="#5a3030",
                      font=("Segoe UI", 8),
                      relief="flat", padx=6).pack(side="right", padx=4)

    # ---- grabacion ----
    def _toggle_record(self):
        if self._recording:
            return  # ya esta grabando, ignorar doble click
        self._recording = True
        self.btn_rec.config(text="⏹ Escuchando...", bg="#AA4400")
        self.lbl_rec_status.config(text="Habla ahora...")
        self._rec_thread = threading.Thread(
            target=self._do_record, daemon=True)
        self._rec_thread.start()

    def _do_record(self):
        """Hilo: graba audio y lo transcribe con Google."""
        r = sr.Recognizer()
        r.pause_threshold = 0.8
        r.dynamic_energy_threshold = True
        try:
            mic = sr.Microphone()
            with mic as src:
                r.adjust_for_ambient_noise(src, duration=0.5)
                audio = r.listen(src, timeout=8, phrase_time_limit=5)
            text = r.recognize_google(audio, language=LANGUAGE)
            text = text.lower().strip()
            self.master.after(0, lambda: self._record_done(text, None))
        except sr.WaitTimeoutError:
            self.master.after(0, lambda: self._record_done(
                None, "No se oyo nada. Intenta de nuevo."))
        except sr.UnknownValueError:
            self.master.after(0, lambda: self._record_done(
                None, "No se entendio. Intenta de nuevo."))
        except sr.RequestError:
            self.master.after(0, lambda: self._record_done(
                None, "Sin internet. Escribe la palabra a mano."))
        except Exception as e:
            self.master.after(0, lambda: self._record_done(
                None, f"Error: {e}"))

    def _record_done(self, text, error):
        self._recording = False
        self.btn_rec.config(text="🎙 Grabar", bg="#00AA55")
        if text:
            self.var_alias.set(text)
            self.lbl_rec_status.config(
                text=f"Escuchado: '{text}'", fg="#00FF88")
        else:
            self.lbl_rec_status.config(text=error or "Error", fg="#FF6644")

    # ---- guardar / borrar ----
    def _save_alias(self):
        alias_text = self.var_alias.get().strip().lower()
        trigger = self._get_selected_trigger()
        if not alias_text:
            self.lbl_save_msg.config(
                text="Escribe o graba la palabra primero.", fg="#FF8844")
            return
        if not trigger:
            self.lbl_save_msg.config(
                text="Selecciona una accion de la lista de la derecha.",
                fg="#FF8844")
            return
        if "aliases" not in self.cfg:
            self.cfg["aliases"] = {}
        self.cfg["aliases"][alias_text] = trigger
        save_config(self.cfg)
        self.lbl_save_msg.config(
            text=f"✔ Guardado: '{alias_text}' → '{trigger}'", fg="#00FF88")
        self.var_alias.set("")
        self.lbl_rec_status.config(text="")
        self._selected_trigger = None
        self._selected_label = None
        self.lbl_selected_cmd.config(text="(ninguna seleccionada)")
        self.listbox.selection_clear(0, "end")
        self._refresh_list()
        if self.on_close:
            self.on_close()

    def _delete_alias(self, alias_text):
        if "aliases" in self.cfg and alias_text in self.cfg["aliases"]:
            del self.cfg["aliases"][alias_text]
            save_config(self.cfg)
            self._refresh_list()
            if self.on_close:
                self.on_close()

    def _close(self):
        if self.on_close:
            self.on_close()
        self.win.destroy()


# ============================================================
#  BARRA DE ACCIONES (ahora abajo-centro y minimizable)
# ============================================================
class ActionBar:
    """Barra flotante minimalista.
    Expandida: Grabar Voz | Texto | Teclado | ▼ | X  (fila 1)
               Mic: Voz suave / Normal / Ruidoso       (fila 2)
    Colapsada: ▲ menu | Grabar Voz | | Mic: S N R
    """
    def __init__(self, master, controller, cfg):
        self.ctrl = controller
        self.cfg = cfg
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.93)
        self.win.configure(bg="#1a1a1a")

        self.expanded_w = 640
        self.expanded_h = 46
        self.collapsed_w = 340
        self.collapsed_h = 28

        # ── Barra expandida — UNA sola fila ───────────────────────
        self.full_frame = tk.Frame(self.win, bg="#1a1a1a")

        def btn(parent, text, cmd, bg="#2e2e2e", fg="#FFFFFF", bold=False):
            f = ("Segoe UI", 10, "bold") if bold else ("Segoe UI", 10)
            return tk.Button(parent, text=text, command=cmd,
                             bg=bg, fg=fg,
                             activebackground="#555555", activeforeground="#FFFFFF",
                             bd=0, padx=10, pady=6, font=f, relief="flat")

        btn(self.full_frame, "Grabar Voz", self.ctrl.open_alias_manager,
            bg="#005533", bold=True).pack(side="left", padx=2, pady=3)

        btn(self.full_frame, "Texto", self.ctrl.start_dictation
            ).pack(side="left", padx=2, pady=3)

        # separador
        tk.Label(self.full_frame, text="|", bg="#1a1a1a", fg="#444444",
                 font=("Segoe UI", 12)).pack(side="left", padx=4)

        # Botones mic
        tk.Label(self.full_frame, text="Mic:", bg="#1a1a1a", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")

        mic_profiles = [
            ("Suave",   "voz_suave",  "#1a3a5c", "#2255AA"),
            ("Normal",  "normal",     "#2a3a2a", "#336633"),
            ("Ruidoso", "ruidoso",    "#3a2a1a", "#885522"),
        ]
        self._mic_btns = {}
        for label, pname, bg_off, bg_on in mic_profiles:
            b = tk.Button(self.full_frame, text=label,
                          command=lambda p=pname: self._set_mic_profile(p),
                          bg=bg_off, fg="#CCCCCC",
                          activebackground="#555555",
                          bd=0, padx=7, pady=6,
                          font=("Segoe UI", 9), relief="flat")
            b.pack(side="left", padx=1, pady=3)
            self._mic_btns[pname] = (b, bg_off, bg_on)

        # separador
        tk.Label(self.full_frame, text="|", bg="#1a1a1a", fg="#444444",
                 font=("Segoe UI", 12)).pack(side="left", padx=4)

        btn(self.full_frame, "▼", self.collapse,
            fg="#AAAAAA").pack(side="left", padx=1, pady=3)

        # Boton pitido (toggle)
        self._beep_btn = tk.Button(
            self.full_frame, text="🔔",
            command=self.ctrl.toggle_beep,
            bg="#2a3a2a", fg="#FFFFFF",
            activebackground="#444444",
            bd=0, padx=8, pady=6,
            font=("Segoe UI", 10), relief="flat")
        self._beep_btn.pack(side="left", padx=1, pady=3)

        btn(self.full_frame, "?", self.ctrl.open_help,
            bg="#2a2a3a", fg="#7B9EFF").pack(side="left", padx=1, pady=3)
        btn(self.full_frame, "📖", self.ctrl.open_tutorial,
            bg="#2a3a2a", fg="#AAFFAA").pack(side="left", padx=1, pady=3)
        btn(self.full_frame, "≡", self.ctrl.open_history,
            bg="#2a2a2a", fg="#AAAAAA").pack(side="left", padx=1, pady=3)

        btn(self.full_frame, "X", self.ctrl.quit,
            bg="#5a1a1a", fg="#FF8888").pack(side="left", padx=2, pady=3)

        self._update_mic_btn_highlight()

        # ── Pestaña colapsada ─────────────────────────────────────
        self.tab_frame = tk.Frame(self.win, bg="#1a1a1a")

        tk.Button(self.tab_frame, text="▲ menu",
                  command=self.expand,
                  bg="#2e2e2e", fg="#CCCCCC",
                  activebackground="#444444",
                  bd=0, padx=10, pady=4,
                  font=("Segoe UI", 9)).pack(side="left")

        tk.Button(self.tab_frame, text="Grabar Voz",
                  command=self.ctrl.open_alias_manager,
                  bg="#005533", fg="#FFFFFF",
                  activebackground="#007744",
                  bd=0, padx=8, pady=4,
                  font=("Segoe UI", 9, "bold")).pack(side="left", padx=3)

        tk.Label(self.tab_frame, text="|", bg="#1a1a1a", fg="#444444",
                 font=("Segoe UI", 9)).pack(side="left", padx=3)

        tk.Label(self.tab_frame, text="Mic:",
                 bg="#1a1a1a", fg="#888888",
                 font=("Segoe UI", 9)).pack(side="left")

        self._tab_mic_btns = {}
        for label, pname, bg_off, bg_on in [
            ("S", "voz_suave", "#1a3a5c", "#2255AA"),
            ("N", "normal",    "#2a3a2a", "#336633"),
            ("R", "ruidoso",   "#3a2a1a", "#885522"),
        ]:
            b = tk.Button(self.tab_frame, text=label,
                          command=lambda p=pname: self._set_mic_profile(p),
                          bg=bg_off, fg="#CCCCCC",
                          activebackground="#555555",
                          bd=0, padx=7, pady=4,
                          font=("Segoe UI", 9))
            b.pack(side="left", padx=1)
            self._tab_mic_btns[pname] = (b, bg_off, bg_on)
        self._update_tab_mic_highlight()

        self.minimized = bool(cfg.get("bar_minimized", True))
        self._apply_state()

        for w in (self.full_frame, self.tab_frame):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._drag)


    def _bottom_center_geom(self, w, h):
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        if self.cfg.get("bar_x") is not None:
            return f"{w}x{h}+{self.cfg['bar_x']}+{self.cfg['bar_y']}"
        x = (sw - w) // 2
        y = sh - h - 50
        return f"{w}x{h}+{x}+{y}"

    def _apply_state(self):
        if self.minimized:
            self.full_frame.pack_forget()
            self.tab_frame.pack(fill="both", expand=True)
            self.win.geometry(self._bottom_center_geom(
                self.collapsed_w, self.collapsed_h))
        else:
            self.tab_frame.pack_forget()
            self.full_frame.pack(fill="both", expand=True)
            self.win.geometry(self._bottom_center_geom(
                self.expanded_w, self.expanded_h))
        self.win.deiconify()
        self.win.attributes("-topmost", True)

    def expand(self):
        self.minimized = False
        self.cfg["bar_minimized"] = False
        save_config(self.cfg)
        self._apply_state()

    def collapse(self):
        self.minimized = True
        self.cfg["bar_minimized"] = True
        save_config(self.cfg)
        self._apply_state()

    def show(self):
        self.win.deiconify()
        self.win.attributes("-topmost", True)

    def hide(self):
        self.win.withdraw()

    def _start_drag(self, e):
        self._dx = e.x_root - self.win.winfo_x()
        self._dy = e.y_root - self.win.winfo_y()

    def _drag(self, e):
        nx = e.x_root - self._dx
        ny = e.y_root - self._dy
        self.win.geometry(f"+{nx}+{ny}")
        self.cfg["bar_x"] = nx
        self.cfg["bar_y"] = ny

    def reassert_topmost(self):
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass

    def _set_mic_profile(self, profile_name):
        self.ctrl.set_mic_profile(profile_name)
        self._update_mic_btn_highlight()
        self._update_tab_mic_highlight()

    def _update_mic_btn_highlight(self):
        current = self.cfg.get("mic_profile", MIC_PROFILE_DEFAULT)
        for pname, (btn, bg_off, bg_on) in self._mic_btns.items():
            if pname == current:
                btn.config(bg=bg_on, fg="#FFFFFF",
                           font=("Segoe UI", 8, "bold"), relief="sunken")
            else:
                btn.config(bg=bg_off, fg="#CCCCCC",
                           font=("Segoe UI", 8), relief="flat")

    def _update_tab_mic_highlight(self):
        current = self.cfg.get("mic_profile", MIC_PROFILE_DEFAULT)
        for pname, (btn, bg_off, bg_on) in self._tab_mic_btns.items():
            if pname == current:
                btn.config(bg=bg_on, fg="#FFFFFF",
                           font=("Segoe UI", 8, "bold"), relief="sunken")
            else:
                btn.config(bg=bg_off, fg="#CCCCCC",
                           font=("Segoe UI", 8), relief="flat")

    def update_beep_btn(self, enabled):
        """Actualiza el color del botón de pitido según su estado."""
        try:
            if enabled:
                self._beep_btn.config(bg="#2a3a2a", fg="#FFFFFF",
                                      text="🔔", relief="flat")
            else:
                self._beep_btn.config(bg="#3a2a2a", fg="#888888",
                                      text="🔕", relief="flat")
        except Exception:
            pass


# ============================================================
#  CONTROLADOR PRINCIPAL
# ============================================================
class SuperCapa:
    def __init__(self):
        self.cfg = load_config()
        self.root = tk.Tk()
        self.root.withdraw()

        self.grid = GridOverlay(self.root)
        self.hud = HudOverlay(self.root)
        self.dictate_hud = DictateHud(self.root)
        self.history_hud = HistoryHud(self.root)
        self.help_hud    = HelpHud(self.root)
        self.bar = ActionBar(self.root, self, self.cfg)
        # Conectar historial al HUD para auto-logging
        self.hud._history_ref = self.history_hud

        self.volume = VolumeController()

        self.command_queue = queue.Queue()
        self.listening  = True
        self.sleeping   = False
        self.dictating  = False
        self.has_net    = True
        self.dragging   = False

        # Pitido de confirmacion (persistido en config)
        global BEEP_ENABLED
        BEEP_ENABLED = self.cfg.get("beep_enabled", True)

        # Reposo automatico
        self._last_command_time = time.time()

        # estado de arrastre
        self.dragging = False

        # ratón direccional
        self.mouse_step = self.cfg.get("mouse_step", MOUSE_STEP_DEFAULT)

        # Aliases de voz personalizados (se recargan al guardar)
        self.aliases = load_aliases(self.cfg)
        self._alias_win = None  # referencia a la ventana del gestor

        # Ultimo intent/texto ejecutado (para comando "repetir")
        self._last_intent = None
        self._last_text   = None
        # Handle de la ventana destino para restaurar foco (pywin32)
        self._last_target_hwnd = None

        # Mapa de puntuacion para el modo dictado
        self.PUNCT_MAP = {
            "punto":              ".",
            "coma":               ",",
            "interrogación":      "?",  "interrogacion":   "?",
            "pregunta":           "?",
            "exclamación":        "!",  "exclamacion":     "!",
            "dos puntos":         ":",
            "punto y coma":       ";",
            "guión":              "-",  "guion":           "-",
            "guión bajo":         "_",  "guion bajo":      "_",
            "arroba":             "@",
            "almohadilla":        "#",  "numeral":         "#",
            "abre paréntesis":    "(",  "abre parentesis":  "(",
            "cierra paréntesis":  ")",  "cierra parentesis": ")",
            "comillas":           '"',
            "barra":              "/",
            "asterisco":          "*",
            "igual":              "=",
            "más":                "+",  "mas":             "+",
            "punto aparte":       ".\n","punto y aparte":  ".\n",
            "punto y seguido":    ". ",
            "nueva línea":        "\n", "nueva linea":     "\n",
            "párrafo":            "\n\n","parrafo":         "\n\n",
            "tabulador":          "\t",
        }

        # Comprobacion de microfono
        self.mic_ok = self._check_microphone()

        # Comprobacion inicial de internet
        self._update_internet_status()

        # Hilo de voz
        self.voice_thread = threading.Thread(
            target=self._voice_loop, daemon=True)
        self.voice_thread.start()

        # Atajos globales
        try:
            keyboard.add_hotkey("f8", lambda: self.command_queue.put("__TOGGLE_GRID__"))
            keyboard.add_hotkey("f9", lambda: self.command_queue.put("__QUIT__"))
            keyboard.add_hotkey("f10", lambda: self.command_queue.put("__TOGGLE_KBD__"))
            keyboard.add_hotkey("f11", lambda: self.command_queue.put("__TOGGLE_SLEEP__"))
        except Exception as e:
            print(f"[aviso] atajos: {e}")

        # Bucles periodicos
        self.root.after(100, self._process_commands)
        self.root.after(TOPMOST_REFRESH_MS, self._refresh_topmost)
        self.root.after(500, self._update_target_hwnd)
        self.root.after(500, self._tick_hud)
        self.root.after(10000, self._check_internet_periodic)
        self.root.after(500, self._blink_dictate_hud)
        self.root.after(15000, self._check_auto_sleep)

        if self.cfg.get("grid_visible_on_start", True):
            self.grid.show()

    # ---- comprobaciones ----
    def _check_microphone(self):
        try:
            mic_list = sr.Microphone.list_microphone_names()
            if not mic_list:
                self.hud.state_no_mic()
                print("[ERROR] No hay micrófonos disponibles.")
                return False
            sr.Microphone()
            return True
        except Exception as e:
            print(f"[ERROR] microfono: {e}")
            self.hud.state_no_mic()
            return False

    def _update_internet_status(self):
        self.has_net = has_internet()
        if not self.has_net:
            self.hud.state_no_internet()

    def _check_internet_periodic(self):
        prev = self.has_net
        self.has_net = has_internet(timeout=2)
        if not self.has_net and prev:
            self.hud.state_no_internet()
        elif self.has_net and not prev and not self.sleeping:
            self.hud.state_listening()
        self.root.after(15000, self._check_internet_periodic)

    # ---- helpers UI ----
    def _tick_hud(self):
        self.hud.tick()
        self.root.after(500, self._tick_hud)

    def _blink_dictate_hud(self):
        self.dictate_hud.blink()
        self.root.after(500, self._blink_dictate_hud)

    def _refresh_topmost(self):
        try:
            self.grid._ensure_win()
            if self.grid.visible:
                self.grid.win.attributes("-topmost", True)
            self.hud.win.attributes("-topmost", True)
            self.bar.reassert_topmost()
            if self.dictate_hud.visible:
                self.dictate_hud.win.attributes("-topmost", True)
            if self.history_hud.visible:
                self.history_hud.win.attributes("-topmost", True)
            if self.help_hud.visible:
                self.help_hud.win.attributes("-topmost", True)
        except Exception:
            pass
        self.root.after(TOPMOST_REFRESH_MS, self._refresh_topmost)

    # ---- acciones de barra ----
    def toggle_grid(self):
        self.grid.toggle()


    def toggle_sleep(self):
        if self.sleeping:
            self.wake()
        else:
            self.sleep()

    def sleep(self):
        self.sleeping = True
        self.hud.state_sleeping()
        print("[modo] DORMIDO")

    def wake(self):
        self.sleeping = False
        self.hud.state_listening()
        print("[modo] DESPIERTO")

    def start_dictation(self):
        if not self.dictating:
            self.dictating = True
            self.dictate_hud.show()
            print("[dictado] activado")

    def stop_dictation(self):
        if self.dictating:
            self.dictating = False
            self.dictate_hud.hide()
            print("[dictado] desactivado")

    def emergency_stop(self):
        """Cierra dictado, suelta arrastre, oculta rejilla.
        No mata el programa, solo deja todo en estado seguro."""
        print("[EMERGENCIA] poniendo todo en estado seguro")
        self.stop_dictation()
        if self.dragging:
            self.grid.drag_cancel()
            self.dragging = False
        self.grid.clear_highlight()
        self.sleep()

    def _check_auto_sleep(self):
        """Duerme el programa si lleva demasiado tiempo sin comandos."""
        if (AUTO_SLEEP_SECS > 0
                and not self.sleeping
                and time.time() - self._last_command_time > AUTO_SLEEP_SECS):
            print(f"[auto-sleep] sin actividad por {AUTO_SLEEP_SECS}s")
            self.sleep()
        self.root.after(15000, self._check_auto_sleep)

    def toggle_beep(self):
        """Activa/desactiva el pitido de confirmacion."""
        global BEEP_ENABLED
        BEEP_ENABLED = not BEEP_ENABLED
        self.cfg["beep_enabled"] = BEEP_ENABLED
        save_config(self.cfg)
        label = "pitido ON" if BEEP_ENABLED else "pitido OFF"
        self.hud.show_command(label, ok=True)
        self.bar.update_beep_btn(BEEP_ENABLED)
        if BEEP_ENABLED:
            beep_ok()

    def center_bar(self):
        """Recoloca la barra en el centro-abajo de la pantalla."""
        self.cfg["bar_x"] = None
        self.cfg["bar_y"] = None
        save_config(self.cfg)
        self.bar._apply_state()
        self.hud.show_command("barra centrada", ok=True)

    def open_help(self):
        self.help_hud.toggle()

    def open_tutorial(self):
        TutorialDialog(parent=self.root,
                       on_close=lambda: None).win.lift()

    def open_history(self):
        self.history_hud.toggle()


    def open_alias_manager(self):
        """Abre (o enfoca) la ventana del gestor de alias de voz."""
        if self._alias_win is not None:
            try:
                self._alias_win.win.lift()
                self._alias_win.win.focus_force()
                return
            except Exception:
                self._alias_win = None
        self._alias_win = AliasManager(
            self.root, self.cfg,
            on_close=self._reload_aliases)

    def _reload_aliases(self):
        """Recarga el diccionario de aliases desde la config en memoria."""
        self.aliases = load_aliases(self.cfg)
        print(f"[alias] {len(self.aliases)} alias cargados: "
              f"{list(self.aliases.keys())}")

    def quit(self):
        print("Cerrando SuperCapa...")
        self.cfg["mouse_step"] = self.mouse_step
        save_config(self.cfg)
        self.listening = False
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    # ---- voz ----
    def _voice_loop(self):
        if not self.mic_ok:
            return
        r = sr.Recognizer()
        self._apply_mic_profile(r)

        try:
            mic = sr.Microphone()
        except Exception as e:
            print(f"[ERROR] microfono: {e}")
            return

        with mic as src:
            print("Calibrando micrófono (3s, silencio por favor)...")
            r.adjust_for_ambient_noise(src, duration=3.0)
            # Fijamos un minimo: nunca bajar del umbral del perfil
            profile = MIC_PROFILES.get(
                self.cfg.get("mic_profile", MIC_PROFILE_DEFAULT),
                MIC_PROFILES[MIC_PROFILE_DEFAULT])
            r.energy_threshold = max(r.energy_threshold,
                                     profile["energy_threshold"])
            print(f"[mic] umbral inicial: {r.energy_threshold:.0f}  "
                  f"perfil: {self.cfg.get('mic_profile', MIC_PROFILE_DEFAULT)}")

        self.hud.state_listening()
        print("Listo. Escuchando.")

        last_recalib = time.time()

        while self.listening:
            # ── recalibrar por tiempo, no por numero de frases ──
            profile = MIC_PROFILES.get(
                self.cfg.get("mic_profile", MIC_PROFILE_DEFAULT),
                MIC_PROFILES[MIC_PROFILE_DEFAULT])
            now = time.time()
            if now - last_recalib >= profile["recalib_secs"]:
                try:
                    with mic as src:
                        prev = r.energy_threshold
                        r.adjust_for_ambient_noise(src, duration=0.8)
                        # Nunca bajar del minimo del perfil
                        r.energy_threshold = max(r.energy_threshold,
                                                 profile["energy_threshold"])
                        if abs(r.energy_threshold - prev) > 20:
                            print(f"[mic] recalibrado: {prev:.0f} → "
                                  f"{r.energy_threshold:.0f}")
                except Exception:
                    pass
                last_recalib = time.time()

            # ── escuchar ──
            try:
                with mic as src:
                    audio = r.listen(
                        src,
                        timeout=3,
                        phrase_time_limit=profile["phrase_time_limit"])
            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                print(f"[aviso] escucha: {e}")
                time.sleep(0.3)
                continue

            # ── si dormido, descartar audio sin enviar a Google ──
            if self.sleeping:
                # Solo comprobar si dice "despierta"
                try:
                    text = r.recognize_google(audio, language=LANGUAGE)
                    text = text.lower().strip()
                    if any(t in text for t in INTENTS["WAKE"]):
                        self.command_queue.put(text)
                except Exception:
                    pass
                continue

            self.hud.state_processing()
            try:
                text = r.recognize_google(audio, language=LANGUAGE)
                text = text.lower().strip()
                print(f"[oido] {text!r}  (energia: {r.energy_threshold:.0f})")
                # Mostrar en HUD lo que se acaba de escuchar (siempre)
                self.root.after(0, lambda t=text: self.hud.show_heard(t))
                self.command_queue.put(text)
            except sr.UnknownValueError:
                pass
            except sr.RequestError as e:
                print(f"[aviso] sin conexion: {e}")
                self.has_net = False
                self.hud.state_no_internet()
                time.sleep(1.5)
                continue

            if not self.sleeping:
                self.hud.state_listening()
            else:
                self.hud.state_sleeping()

    def _apply_mic_profile(self, recognizer):
        """Aplica el perfil de microfono activo al Recognizer dado."""
        profile_name = self.cfg.get("mic_profile", MIC_PROFILE_DEFAULT)
        profile = MIC_PROFILES.get(profile_name, MIC_PROFILES[MIC_PROFILE_DEFAULT])
        recognizer.energy_threshold    = profile["energy_threshold"]
        recognizer.dynamic_energy_threshold = profile["dynamic"]
        recognizer.dynamic_energy_ratio     = profile["dynamic_ratio"]
        recognizer.pause_threshold          = profile["pause_threshold"]
        print(f"[mic] perfil '{profile_name}': "
              f"umbral={profile['energy_threshold']} "
              f"dinamico={profile['dynamic']}")

    def set_mic_profile(self, profile_name):
        """Cambia el perfil de microfono en caliente y guarda en config."""
        if profile_name not in MIC_PROFILES:
            return
        self.cfg["mic_profile"] = profile_name
        save_config(self.cfg)
        self.hud.show_command(f"mic: {profile_name}", ok=True)
        print(f"[mic] perfil cambiado a '{profile_name}'")

    # ---- procesado de cola ----
    def _process_commands(self):
        try:
            while True:
                cmd = self.command_queue.get_nowait()
                if cmd == "__QUIT__":
                    self.quit(); return
                if cmd == "__TOGGLE_GRID__":
                    self.toggle_grid(); continue
                if cmd == "__TOGGLE_KBD__":
                    continue  # teclado eliminado
                if cmd == "__TOGGLE_SLEEP__":
                    self.toggle_sleep(); continue
                self._handle_voice(cmd)
        except queue.Empty:
            pass
        self.root.after(100, self._process_commands)

    def _handle_voice(self, text):
        # 0) aplicar alias personalizados del usuario
        text = apply_aliases(text, self.aliases)
        t = text.strip().lower()

        # 1) emergencia siempre se procesa (incluso dormido)
        if any(t2 in text for t2 in INTENTS["EMERGENCY"]):
            self.hud.show_command("EMERGENCIA", ok=True)
            self.emergency_stop()
            return

        # 2) si esta dormido, solo despertar
        if self.sleeping:
            if any(t2 in text for t2 in INTENTS["WAKE"]):
                self.wake()
                self.hud.show_command("despierto", ok=True)
            return

        # 3) modo dictado: todo se teclea, salvo comandos especiales
        if self.dictating:
            self._handle_dictation(text)
            return

        # ── MODO ZOOM: maxima prioridad cuando la subrejilla esta activa ──
        if self.grid._zoom_col is not None:

            # salir del zoom
            if any(tr in t for tr in INTENTS["ZOOM_EXIT"]):
                self.grid.zoom_exit()
                self.hud.show_command("zoom off", ok=True)
                return

            # simbolo de subcelda → clic y salir zoom automaticamente
            # Cada subcelda se activa por sus palabras de voz
            sub_idx = None
            mode = "left"
            for idx, (symbol, words) in enumerate(self.grid.ZOOM_SYMBOLS):
                for word in words:
                    if word in t:
                        sub_idx = idx
                        break
                if sub_idx is not None:
                    break
            if sub_idx is not None:
                if "doble" in t:
                    mode = "double"
                elif "derecho" in t or "derecha" in t:
                    mode = "right"
                sym = self.grid.ZOOM_SYMBOLS[sub_idx][0]
                ok = self.grid.zoom_click_sub(sub_idx, mode)
                self.hud.show_command(f"zoom {sym}", ok=ok)
                return

            # cualquier otro audio mientras zoom activo → ignorar
            return

        # 4) coordenada con acciones
        coord = parse_coordinate(text)
        if coord:
            tnorm = " " + text + " "

            # zoom A5
            if any(k in tnorm for k in (" zoom ", " ampliar ", " acercar ",
                                         " agrandar ", " lupa ")):
                self.grid.zoom_cell(coord[0], coord[1])
                label = f"{LETTERS[coord[0]]}{coord[1] + 1}"
                self.hud.show_command(f"zoom {label}", ok=True)
                return

            # MARCAR
            if any(k in tnorm for k in (" marcar ", " marca ", " selecciona ",
                                        " seleccionar ", " resalta ",
                                        " resaltar ", " elige ", " elegir ")):
                self.grid.highlight_cell(coord[0], coord[1])
                self.hud.show_command(
                    f"marcado {LETTERS[coord[0]]}{coord[1] + 1}", ok=True)
                return

            # COGER (drag start)
            if any(k in tnorm for k in (" coger ", " coge ", " agarrar ",
                                        " agarra ", " sujeta ", " sujetar ",
                                        " arrastrar desde ")):
                self.grid.drag_start(coord[0], coord[1])
                self.dragging = True
                self.hud.show_command(
                    f"arrastrando desde {LETTERS[coord[0]]}{coord[1] + 1}",
                    ok=True)
                return

            # SOLTAR en coordenada
            if any(k in tnorm for k in (" soltar ", " suelta ", " sueltalo ",
                                        " sueltalo ", " arrastrar hasta ",
                                        " soltar en ")):
                if self.dragging:
                    self.grid.drag_end(coord[0], coord[1])
                    self.dragging = False
                    self.hud.show_command(
                        f"soltado en {LETTERS[coord[0]]}{coord[1] + 1}",
                        ok=True)
                else:
                    x, y = self.grid._cell_center(coord[0], coord[1])
                    pyautogui.moveTo(x, y, duration=0.05)
                    self.hud.show_command(
                        f"movido a {LETTERS[coord[0]]}{coord[1] + 1}", ok=True)
                return

            # CLIC EXPLICITO en coordenada
            has_click_word = any(w in text for w in (
                "clic", "click", "pica", "pulsa", "pincha", "toca",
                "doble", "derecho", "derecha"))
            if has_click_word:
                mode = "left"
                if any(w in text for w in INTENTS["DOUBLE_CLICK"]):
                    mode = "double"
                elif any(w in text for w in INTENTS["RIGHT_CLICK"]):
                    mode = "right"
                ok = self.grid.click_cell(coord[0], coord[1], mode=mode)
                self.grid.clear_highlight()
                label = f"{LETTERS[coord[0]]}{coord[1] + 1}"
                modes = {"left": "clic", "double": "doble clic", "right": "clic dcho"}
                self.hud.show_command(f"{modes[mode]} {label}", ok=ok)
                return

            # SOLO COORDENADA → mover cursor sin clicar
            x, y = self.grid._cell_center(coord[0], coord[1])
            pyautogui.moveTo(x, y, duration=0.05)
            self.grid.highlight_cell(coord[0], coord[1])
            label = f"{LETTERS[coord[0]]}{coord[1] + 1}"
            self.hud.show_command(f"ir a {label}", ok=True)
            return

        # 4b) "zoom" solo → zoom sobre la celda donde esta el cursor
        if t == "zoom" or t in ("ampliar", "acercar", "lupa"):
            mx, my = pyautogui.position()
            col = max(0, min(int(mx / self.grid.cell_w), COLS - 1))
            row = max(0, min(int(my / self.grid.cell_h), ROWS - 1))
            self.grid.zoom_cell(col, row)
            label = f"{LETTERS[col]}{row + 1}"
            self.hud.show_command(f"zoom {label}", ok=True)
            return

        # 5) intencion sin coordenada
        intent = classify_intent(text)
        if intent is None:
            return
        self._dispatch_intent(intent, text)


    def _handle_dictation(self, text):
        # comandos especiales del dictado
        if any(t in text for t in INTENTS["DICTATE_END"]):
            self.stop_dictation()
            self.hud.show_command("dictado off", ok=True)
            beep_ok()
            return
        if any(t in text for t in INTENTS["DICTATE_DEL_WORD"]):
            pyautogui.hotkey("ctrl", "backspace")
            self.dictate_hud.update_text("↩ borra palabra")
            beep_ok()
            return
        if any(t in text for t in INTENTS["DICTATE_CLEAR"]):
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)
            pyautogui.press("delete")
            self.dictate_hud.update_text("✕ texto borrado")
            beep_ok()
            return

        # Comprobar si es un comando de puntuacion
        t = text.strip().lower()
        for phrase, symbol in self.PUNCT_MAP.items():
            if t == phrase or t.endswith(" " + phrase):
                if t == phrase:
                    to_type = symbol
                else:
                    prefix = text[:text.lower().rfind(phrase)].rstrip()
                    to_type = prefix + symbol
                self._type_text(to_type)
                self._release_dictate_bar()
                self.dictate_hud.update_text(f"[{phrase}] → {symbol}")
                beep_ok()
                return

        # Texto normal — escribir
        self.dictate_hud.update_text(text)
        self._type_text(text + " ")
        self._release_dictate_bar()
        beep_ok()

    def _release_dictate_bar(self):
        try:
            self.bar.win.attributes("-disabled", False)
        except Exception:
            pass

    def _update_target_hwnd(self):
        """Llama periodicamente para recordar la ventana activa de destino."""
        if HAS_WIN32:
            try:
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    our = set()
                    for w in (self.bar.win, self.hud.win,
                              self.dictate_hud.win, self.grid.win, self.root):
                        try:
                            our.add(ctypes.windll.user32.GetParent(w.winfo_id()))
                            our.add(w.winfo_id())
                        except Exception:
                            pass
                    if hwnd not in our:
                        self._last_target_hwnd = hwnd
            except Exception:
                pass
        self.root.after(250, self._update_target_hwnd)

    def _restore_focus(self):
        """Restaura el foco a la aplicacion destino. Devuelve True si OK."""
        hwnd = getattr(self, "_last_target_hwnd", None)
        if hwnd and HAS_WIN32:
            try:
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.08)
                return True
            except Exception:
                pass
        # Fallback: deshabilitar barra y esperar
        try:
            self.bar.win.attributes("-disabled", True)
        except Exception:
            pass
        time.sleep(0.12)
        return False

    def _act_with_focus(self, fn):
        """Ejecuta fn() con el foco en la aplicacion destino."""
        restored = self._restore_focus()
        try:
            fn()
        finally:
            if not restored:
                try:
                    self.bar.win.attributes("-disabled", False)
                except Exception:
                    pass

    def _type_text(self, text):
        """Inserta texto via portapapeles preservando unicode y simbolos."""
        if not text:
            return
        self._restore_focus()
        if HAS_PYPERCLIP:
            try:
                old = pyperclip.paste()
            except Exception:
                old = ""
            try:
                pyperclip.copy(text)
                time.sleep(0.06)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.10)
                try:
                    pyperclip.copy(old)
                except Exception:
                    pass
                try:
                    self.bar.win.attributes("-disabled", False)
                except Exception:
                    pass
                return
            except Exception:
                pass
        try:
            pyautogui.write(text, interval=0.02)
        except Exception:
            for ch in text:
                try:
                    pyautogui.write(ch)
                except Exception:
                    pass
        try:
            self.bar.win.attributes("-disabled", False)
        except Exception:
            pass


    def _dispatch_intent(self, intent, text):
        print(f"[intent] {intent}")
        ok = True

        if intent == "QUIT":
            self.quit(); return
        if intent == "SLEEP":
            self.sleep(); self.hud.show_command("dormido", ok=True); return

        if intent == "SHOW_GRID": self.grid.show()
        elif intent == "HIDE_GRID": self.grid.hide()
        elif intent == "ZOOM_EXIT":
            self.grid.zoom_exit()
            self.hud.show_command("zoom off", ok=True)
            return
        elif intent == "CLEAR_HIGHLIGHT": self.grid.clear_highlight()

        elif intent == "SHOW_BAR": self.bar.show()
        elif intent == "HIDE_BAR": self.bar.hide()

        elif intent == "DICTATE_START": self.start_dictation()

        elif intent == "DROP_HERE":
            if self.dragging:
                pyautogui.mouseUp()
                self.dragging = False
                self.hud.show_command("soltado aqui", ok=True)
            else:
                self.hud.show_command("(no hay arrastre)", ok=False)
            return

        elif intent == "CANCEL_DRAG":
            if self.dragging:
                self.grid.drag_cancel(); self.dragging = False

        elif intent == "CLICK_HERE":
            act_left_click()
            self.hud.show_command("clic aqui", ok=True)
            return

        elif intent == "MOVE_UP":
            n = max(1, extract_number(text))
            x, y = pyautogui.position()
            pyautogui.moveTo(x, y - self.mouse_step * n, duration=0.05)
        elif intent == "MOVE_DOWN":
            n = max(1, extract_number(text))
            x, y = pyautogui.position()
            pyautogui.moveTo(x, y + self.mouse_step * n, duration=0.05)
        elif intent == "MOVE_LEFT":
            n = max(1, extract_number(text))
            x, y = pyautogui.position()
            pyautogui.moveTo(x - self.mouse_step * n, y, duration=0.05)
        elif intent == "MOVE_RIGHT":
            n = max(1, extract_number(text))
            x, y = pyautogui.position()
            pyautogui.moveTo(x + self.mouse_step * n, y, duration=0.05)
        elif intent == "FASTER":
            self.mouse_step = min(MOUSE_STEP_MAX,
                                  int(self.mouse_step * MOUSE_STEP_FACTOR))
            self.cfg["mouse_step"] = self.mouse_step
        elif intent == "SLOWER":
            self.mouse_step = max(MOUSE_STEP_MIN,
                                  int(self.mouse_step / MOUSE_STEP_FACTOR))
            self.cfg["mouse_step"] = self.mouse_step

        elif intent == "VOL_UP":   self.volume.up()
        elif intent == "VOL_DOWN": self.volume.down()
        elif intent == "VOL_MUTE": self.volume.mute()
        elif intent == "VOL_MAX":  self.volume.maximum()

        elif intent == "LEFT_CLICK":   self._act_with_focus(act_left_click)
        elif intent == "DOUBLE_CLICK": self._act_with_focus(act_double_click)
        elif intent == "RIGHT_CLICK":  self._act_with_focus(act_right_click)

        elif intent == "COPY":         self._act_with_focus(act_copy)
        elif intent == "UNDO":         self._act_with_focus(act_undo)
        elif intent == "PASTE":        self._act_with_focus(act_paste)
        elif intent == "CUT":          self._act_with_focus(act_cut)
        elif intent == "DELETE":       self._act_with_focus(act_delete)
        elif intent == "SELECT_ALL":   self._act_with_focus(act_select_all)
        elif intent == "ENTER":        self._act_with_focus(act_enter)

        elif intent == "BEEP_TOGGLE":
            self.toggle_beep(); return
        elif intent == "HELP":
            self.open_help(); return
        elif intent == "TUTORIAL":
            self.open_tutorial(); return
        elif intent == "SHOW_HISTORY":
            self.open_history(); return
        elif intent == "CENTER_BAR":
            self.center_bar(); return

        elif intent == "REPEAT":
            if self._last_intent and self._last_intent != "REPEAT":
                self._dispatch_intent(self._last_intent, self._last_text or "")
                self.hud.show_command(f"repetir: {self._last_intent}", ok=True)
            else:
                self.hud.show_command("nada que repetir", ok=False)
            return

        elif intent == "SCROLL_UP":
            n = extract_number(text, default=0)
            amt = n * 5 if n > 0 else 8   # "subir pagina 5" -> 25 ticks, sin numero -> 8
            self._act_with_focus(lambda a=amt: act_scroll_up(a))
            return
        elif intent == "SCROLL_DOWN":
            n = extract_number(text, default=0)
            amt = n * 5 if n > 0 else 8
            self._act_with_focus(lambda a=amt: act_scroll_down(a))
            return
        elif intent == "PAGE_UP":
            n = extract_number(text, default=1)
            self._act_with_focus(lambda p=n: act_page_up(p)); return
        elif intent == "PAGE_DOWN":
            n = extract_number(text, default=1)
            self._act_with_focus(lambda p=n: act_page_down(p)); return
        elif intent == "GO_HOME":
            self._act_with_focus(act_go_home); return
        elif intent == "GO_END":
            self._act_with_focus(act_go_end); return


        elif intent == "CLOSE_WINDOW": self._act_with_focus(act_close_window)
        elif intent == "MINIMIZE":     self._act_with_focus(act_minimize)
        elif intent == "MAXIMIZE":     self._act_with_focus(act_maximize)
        elif intent == "SWITCH_WINDOW":self._act_with_focus(act_switch_window)
        elif intent == "OPEN_EXPLORER":self._act_with_focus(act_open_explorer)
        elif intent == "SHOW_DESKTOP": self._act_with_focus(act_show_desktop)
        elif intent == "PROJECT":      act_project()
        else:
            ok = False

        # Guardar ultimo intent para "repetir"
        if ok and intent not in ("REPEAT", "SLEEP", "WAKE", "EMERGENCY", "QUIT"):
            self._last_intent = intent
            self._last_text   = text

        # Actualizar tiempo de ultimo comando (para auto-sleep)
        self._last_command_time = time.time()

        nice_names = {
            "SHOW_GRID": "rejilla on", "HIDE_GRID": "rejilla off",
            "CLEAR_HIGHLIGHT": "marca limpiada",
            "SHOW_BAR": "barra on", "HIDE_BAR": "barra off",
            "DICTATE_START": "dictado on",
            "CANCEL_DRAG": "arrastre cancelado",
            "FASTER": f"raton x{self.mouse_step}",
            "SLOWER": f"raton x{self.mouse_step}",
            "VOL_UP": "vol +", "VOL_DOWN": "vol -",
            "VOL_MUTE": "silencio", "VOL_MAX": "vol max",
            "LEFT_CLICK": "clic", "DOUBLE_CLICK": "doble clic",
            "RIGHT_CLICK": "clic dcho",
            "COPY": "copiar", "PASTE": "pegar", "CUT": "cortar",
            "UNDO": "deshacer", "REPEAT": "repetir",
            "DELETE": "borrar", "SELECT_ALL": "seleccionar todo",
            "ENTER": "intro",
            "SCROLL_UP": "scroll ↑", "SCROLL_DOWN": "scroll ↓",
            "PAGE_UP": "pág anterior", "PAGE_DOWN": "pág siguiente",
            "GO_HOME": "inicio doc", "GO_END": "final doc",
            "CLOSE_WINDOW": "cerrar ventana", "MINIMIZE": "minimizar",
            "MAXIMIZE": "maximizar", "SWITCH_WINDOW": "alt+tab",
            "OPEN_EXPLORER": "explorador", "SHOW_DESKTOP": "escritorio",
            "PROJECT": "proyectar",
            "BEEP_TOGGLE": "pitido toggle",
            "HELP": "ayuda", "TUTORIAL": "tutorial",
            "SHOW_HISTORY": "historial",
            "CENTER_BAR": "barra centrada",
        }
        label = nice_names.get(intent, intent.lower())
        self.hud.show_command(label, ok=ok)
        self.history_hud.add(label, ok)
        if ok:
            beep_ok()
        else:
            beep_error()

    # ---- main ----
    def run(self):
        self.root.mainloop()


def main():
    import tkinter.messagebox as _mb

    cfg = load_config()

    # ── Pantalla de bienvenida + términos ─────────────────────────
    # Aparece si nunca se han aceptado o si cambió la versión de términos
    needs_terms = (
        not cfg.get("terms_accepted", False)
        or cfg.get("terms_version", "") != TERMS_VERSION
    )
    if needs_terms:
        dlg = WelcomeDialog(cfg)
        accepted = dlg.show()
        if not accepted:
            # El usuario rechazó los términos → salir sin arrancar
            sys.exit(0)
        cfg = load_config()   # recargar con terms_accepted=True

    # ── Tutorial ──────────────────────────────────────────────────
    # Aparece si nunca se ha visto
    if not cfg.get("tutorial_seen", False):
        tut = TutorialDialog()
        tut.show()
        cfg["tutorial_seen"] = True
        save_config(cfg)

    # ── Arrancar programa principal ───────────────────────────────
    SuperCapa().run()



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
