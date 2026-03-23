import os
import re


# Modelo de texto a usar
MODEL_NAME_TEXT = "gemini-2.5-pro"

# Tema por defecto del video/guion
DEFAULT_TOPIC = "The ENTIRE Story of Greek Mythology | Boring History For Sleep"

# Parámetros de duración y estructura
TARGET_MINUTES_DEFAULT = 90
SECTIONS_DEFAULT = 12
WORDS_PER_MINUTE_DEFAULT = 140

# Personalización principal del guion
DEFAULT_AUDIENCE = "público general"
DEFAULT_TONE = "entretenido, claro y con ritmo"
DEFAULT_LANG = "español"

# Presets de estilo de narración:
# - "classic": estilo actual, explicativo y dinámico
# - "immersive_relaxing": estilo inmersivo, calmado y pausado
NARRATIVE_STYLE_PRESET = "immersive_relaxing"


def get_style_block(style_preset: str) -> str:
    preset = (style_preset or "classic").strip().lower()
    if preset == "immersive_relaxing":
        return """
INSTRUCCIONES DE ESTILO:
- Escribe en segunda persona (como si el oyente estuviera dentro de la historia).
- Usa un tono calmado, lento, casi hipnótico.
- Evita lenguaje técnico o académico.
- No suenes como Wikipedia ni como documental tradicional.
- Prioriza sensaciones, atmósfera y emociones sobre datos.
- Usa frases cortas y pausadas.
- Incluye silencios naturales usando saltos de línea.
- Genera una sensación de calma, profundidad y misterio.
- No uses listas ni estructura tipo ensayo.

ESTILO DE NARRACIÓN:
- Debe sentirse como una experiencia, no como una explicación.
- Usa descripciones sensoriales (silencio, oscuridad, presencia, etc.).
- Introduce ideas de forma gradual, no abrupta.
- Mantén ritmo constante, sin picos de intensidad.
- Evita preguntas directas al oyente.

FORMATO:
- Escribe en párrafos cortos.
- Usa saltos de línea frecuentes para crear pausas.
- No uses títulos dentro del texto.
- No expliques lo que haces, solo narra.

OBJETIVO:
- El oyente debe poder relajarse, incluso dormirse, mientras escucha, pero sin perder el interés.
- Empieza de forma suave, sin introducir el tema de golpe. Construye la atmósfera primero.
""".strip()
    return """
INSTRUCCIONES DE ESTILO:
- Mantén un tono entretenido, claro y con ritmo.
- Prioriza claridad, progresión y naturalidad.
- Evita lenguaje excesivamente técnico.
- Usa ejemplos concretos cuando aporten valor.
""".strip()

# Salida por defecto
# Carpeta base = esta misma carpeta de scripts_guion_largo
OUTPUT_BASE_DIR_DEFAULT = os.path.join(os.path.dirname(__file__), "scripts_output")
OUTPUT_FILENAME_PREFIX = "guion_"

# Archivos intermedios
OUTLINE_FILENAME = "outline.json"
SECTIONS_DIRNAME = "sections"

# Red / timeouts
REQUEST_TIMEOUT = 90.0
MAX_RETRIES = 3


def slugify_topic(topic: str) -> str:
    s = topic.strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_\-]+", "", s)
    return s


def get_run_dir(topic: str) -> str:
    base_dir = os.path.abspath(OUTPUT_BASE_DIR_DEFAULT)
    slug = slugify_topic(topic) or "sin_tema"
    return os.path.join(base_dir, slug)


def get_outline_path(topic: str) -> str:
    return os.path.join(get_run_dir(topic), OUTLINE_FILENAME)


def get_sections_dir(topic: str) -> str:
    return os.path.join(get_run_dir(topic), SECTIONS_DIRNAME)


def get_final_script_path(topic: str) -> str:
    slug = slugify_topic(topic) or "sin_tema"
    filename = f"{OUTPUT_FILENAME_PREFIX}{slug}.txt"
    return os.path.join(get_run_dir(topic), filename)

