import os
import re


# Modelo de texto a usar
MODEL_NAME_TEXT = "gemini-2.5-pro"

# Tema por defecto del video/guion
DEFAULT_TOPIC = "¿Por qué los EJÉRCITOS MEDIEVALES eran TAN PEQUEÑOS?"

# Parámetros de duración y estructura
TARGET_MINUTES_DEFAULT = 20
SECTIONS_DEFAULT = 7
WORDS_PER_MINUTE_DEFAULT = 140

# Personalización principal del guion
DEFAULT_AUDIENCE = "público general"
DEFAULT_TONE = "entretenido, claro y con ritmo"
DEFAULT_LANG = "español"

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

