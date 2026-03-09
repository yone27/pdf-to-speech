import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import List

import requests
from dotenv import load_dotenv


load_dotenv()


# ----------------- CONSTANTES ----------------- #

# Modelo de texto a usar
MODEL_NAME_TEXT = "gemini-2.5-pro"

# Tema por defecto del video/guion
DEFAULT_TOPIC = "Proteína GRATIS Durante 10 Años: El Sistema del Estanque Cerrado"

# Parámetros de duración y estructura
TARGET_MINUTES_DEFAULT = 20
SECTIONS_DEFAULT = 3
WORDS_PER_MINUTE_DEFAULT = 140

# Personalización principal del guion
DEFAULT_AUDIENCE = "público general"
DEFAULT_TONE = "entretenido, claro y con ritmo"
DEFAULT_LANG = "español"

# Salida por defecto
OUTPUT_BASE_DIR_DEFAULT = "./scripts_output"
OUTPUT_FILENAME_PREFIX = "guion_"

REQUEST_TIMEOUT = 90.0
MAX_RETRIES = 3


# ----------------- MODELOS DE DATOS ----------------- #


@dataclass
class ParteIndice:
    numero: int
    titulo: str
    descripcion: str


@dataclass
class IndiceGuion:
    titulo_general: str
    partes: List[ParteIndice]


# ----------------- UTILIDADES GEMINI ----------------- #


def load_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No se encontró la variable de entorno GEMINI_API_KEY. "
            "Configúrala con tu clave de la API de Gemini."
        )
    return api_key


def build_gemini_url(model_name: str, api_key: str) -> str:
    base_url = os.getenv(
        "GEMINI_API_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    if not model_name.startswith("models/"):
        model_path = f"models/{model_name}"
    else:
        model_path = model_name
    return f"{base_url}/{model_path}:generateContent?key={api_key}"


def call_gemini(
    prompt: str,
    *,
    model_name: str,
    api_key: str,
    timeout: float = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> str:
    url = build_gemini_url(model_name, api_key)

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=timeout,
            )
            if not response.ok:
                raise RuntimeError(
                    f"Respuesta no exitosa de Gemini (status={response.status_code}): "
                    f"{response.text[:500]}"
                )

            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise RuntimeError("La respuesta de Gemini no contiene 'candidates'.")

            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            if not parts or "text" not in parts[0]:
                raise RuntimeError("No se encontró texto en la respuesta de Gemini.")

            return parts[0]["text"].strip()

        except (requests.RequestException, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                wait_time = 2**attempt
                print(
                    f"[WARN] Error al llamar a Gemini (intento {attempt}/{max_retries}): {exc}. "
                    f"Reintentando en {wait_time}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
            else:
                break

    raise RuntimeError(f"Falló la llamada a Gemini tras {max_retries} intentos: {last_error}")


def contar_palabras(texto: str) -> int:
    return len(texto.split())


# ----------------- PLACEHOLDERS DE LÓGICA PRINCIPAL ----------------- #


def generate_outline(
    topic: str,
    target_minutes: int,
    sections_count: int,
    *,
    audience: str,
    tone: str,
    api_key: str,
) -> IndiceGuion:
    """Genera un índice estructurado del guion usando Gemini y lo parsea a IndiceGuion."""
    prompt = f"""
Actúa como guionista para videos de YouTube en {DEFAULT_LANG}.

Tu tarea es crear la estructura de un guion sobre: "{topic}"

Objetivo:
- Duración total estimada: {target_minutes} minutos
- Público: {audience}
- Tono: {tone}

Instrucciones:
- Crea un índice de {sections_count} partes.
- Cada parte debe cubrir un ángulo distinto del tema.
- Evita títulos genéricos.
- Ordena las partes para que haya progresión y curiosidad.
- Para cada parte, añade una breve descripción de 2-3 líneas explicando qué debe desarrollarse.

Devuelve el resultado en formato JSON válido, en {DEFAULT_LANG} y sin texto adicional:
{{
  "titulo_general": "...",
  "partes": [
    {{
      "numero": 1,
      "titulo": "...",
      "descripcion": "..."
    }}
  ]
}}
"""
    raw = call_gemini(
        prompt,
        model_name=MODEL_NAME_TEXT,
        api_key=api_key,
    )

    try:
        start = raw.index("{")
        json_str = raw[start:]
    except ValueError:
        json_str = raw

    # Parsear solo el primer objeto JSON y descartar texto extra
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(json_str)

    titulo_general = data.get("titulo_general") or topic
    partes_raw = data.get("partes") or []
    partes: List[ParteIndice] = []

    for p in partes_raw:
        try:
            numero = int(p.get("numero"))
        except (TypeError, ValueError):
            numero = len(partes) + 1

        titulo = str(p.get("titulo") or f"Sección {numero}")
        descripcion = str(p.get("descripcion") or "").strip()

        partes.append(
            ParteIndice(
                numero=numero,
                titulo=titulo,
                descripcion=descripcion,
            )
        )

    if not partes:
        raise RuntimeError("El índice devuelto por Gemini no contiene partes válidas.")

    return IndiceGuion(titulo_general=titulo_general, partes=partes)


def generate_section(
    topic: str,
    outline: IndiceGuion,
    section: ParteIndice,
    *,
    min_words: int,
    max_words: int,
    tone: str,
    resumen_previas: str | None,
    api_key: str,
) -> str:
    """Genera el texto de una sección concreta del guion."""
    indice_json = json.dumps(
        {
            "titulo_general": outline.titulo_general,
            "partes": [
                {
                    "numero": p.numero,
                    "titulo": p.titulo,
                    "descripcion": p.descripcion,
                }
                for p in outline.partes
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

    resumen_previas_txt = (
        f"Resumen de secciones anteriores:\n{resumen_previas}\n\n"
        if resumen_previas
        else ""
    )

    prompt = f"""
Actúa como guionista para videos de YouTube en {DEFAULT_LANG}.

Necesito desarrollar la siguiente sección de un guion largo.

Tema general: "{topic}"
Título del guion: "{outline.titulo_general}"
Sección actual: {section.numero}
Título de la sección: "{section.titulo}"
Descripción de la sección: "{section.descripcion}"

Contexto de estructura completa (JSON):
{indice_json}

{resumen_previas_txt}Instrucciones:
- Escribe solo esta sección.
- Extensión objetivo: entre {min_words} y {max_words} palabras.
- No repitas ideas ya cubiertas en otras secciones.
- No hagas introducción general ni conclusión final del video.
- Usa un tono {tone}.
- Mantén ritmo narrativo, claridad y naturalidad.
- Incluye explicación, contexto, al menos un ejemplo concreto y una implicación práctica.
- Termina con una transición suave hacia la siguiente sección (si existe), sin cerrarla del todo.

Devuelve solo el texto de la sección, sin encabezados ni notas, escrita completamente en {DEFAULT_LANG}.
"""

    texto = call_gemini(
        prompt,
        model_name=MODEL_NAME_TEXT,
        api_key=api_key,
    )
    return texto.strip()


def maybe_expand_section_if_short(
    text: str,
    *,
    min_words: int,
    topic: str,
    outline: IndiceGuion,
    section: ParteIndice,
    tone: str,
    api_key: str,
) -> str:
    """Si la sección queda por debajo de min_words, la reescribe y expande."""
    if contar_palabras(text) >= min_words:
        return text

    prompt = f"""
Tengo la siguiente sección de un guion de YouTube en {DEFAULT_LANG}, pero ha quedado demasiado corta.

Tema general: "{topic}"
Título del guion: "{outline.titulo_general}"
Sección: {section.numero} - "{section.titulo}"

Texto actual de la sección:
\"\"\"{text}\"\"\"

Instrucciones:
- Expande esta sección manteniendo el mismo estilo y tono ({tone}).
- Añade más desarrollo, ejemplos y matices.
- No repitas frases literalmente.
- No cambies el sentido de lo ya escrito.
- No hagas introducción general ni conclusión global del video.

Devuelve la sección reescrita y expandida, como un único texto coherente en {DEFAULT_LANG}.
"""

    texto_expandido = call_gemini(
        prompt,
        model_name=MODEL_NAME_TEXT,
        api_key=api_key,
    )
    return texto_expandido.strip()


def merge_sections(
    sections_text: List[str],
    *,
    target_minutes: int,
    words_per_minute: int,
    tone: str,
    api_key: str,
) -> str:
    """Une y pule todas las secciones en un guion final listo para narración."""
    joined = "\n\n---\n\n".join(sections_text)
    target_total_words = target_minutes * words_per_minute

    prompt = f"""
Une las siguientes secciones en un único guion fluido para un video de aproximadamente {target_minutes} minutos.

Texto de las secciones (separadas por líneas con ---):
\"\"\"{joined}\"\"\"

Objetivo:
- Mantener la mayor parte del contenido.
- Eliminar repeticiones claras.
- Mejorar transiciones entre bloques.
- Unificar el tono ({tone}).
- Añadir una breve introducción potente al inicio.
- Añadir un cierre satisfactorio al final.
- Intentar que el resultado final esté razonablemente cerca de las {target_total_words} palabras (no hace falta que sea exacto).

Devuelve el guion completo listo para narración, en {DEFAULT_LANG} y sin comentarios adicionales.
"""

    texto = call_gemini(
        prompt,
        model_name=MODEL_NAME_TEXT,
        api_key=api_key,
    )
    return texto.strip()


def save_script(output_path: str, script_text: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(script_text)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Genera un guion largo para narración usando Gemini 2.5 Pro, "
            "siguiendo el flujo índice → secciones → ensamblado final."
        )
    )

    parser.add_argument(
        "--topic",
        required=False,
        default=DEFAULT_TOPIC,
        help=(
            "Tema principal del guion. "
            f"Por defecto: {DEFAULT_TOPIC!r}."
        ),
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=TARGET_MINUTES_DEFAULT,
        help=f"Duración objetivo del guion en minutos (por defecto {TARGET_MINUTES_DEFAULT}).",
    )
    parser.add_argument(
        "--sections",
        type=int,
        default=SECTIONS_DEFAULT,
        help=f"Número de secciones del guion (por defecto {SECTIONS_DEFAULT}).",
    )
    parser.add_argument(
        "--wpm",
        type=int,
        default=WORDS_PER_MINUTE_DEFAULT,
        help=(
            "Palabras por minuto estimadas para la narración "
            f"(por defecto {WORDS_PER_MINUTE_DEFAULT})."
        ),
    )
    parser.add_argument(
        "--tone",
        type=str,
        default=DEFAULT_TONE,
        help=(
            "Descripción breve del tono deseado "
            f"(por defecto: {DEFAULT_TONE})."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Ruta del archivo .txt donde guardar el guion final. "
        "Si se omite, se genera un nombre por defecto en ./scripts_output/.",
    )

    args = parser.parse_args()

    topic: str = args.topic or DEFAULT_TOPIC
    target_minutes: int = args.minutes
    sections_count: int = args.sections
    words_per_minute: int = args.wpm
    tone: str = args.tone or DEFAULT_TONE

    if target_minutes <= 0:
        raise SystemExit("--minutes debe ser mayor que 0.")
    if sections_count <= 0:
        raise SystemExit("--sections debe ser mayor que 0.")
    if words_per_minute <= 0:
        raise SystemExit("--wpm debe ser mayor que 0.")

    api_key = load_api_key()

    target_total_words = target_minutes * words_per_minute
    words_per_section = max(1, target_total_words // max(1, sections_count))
    min_words_section = int(words_per_section * 0.8)
    max_words_section = int(words_per_section * 1.2)

    print(f"Tema: {topic}")
    print(f"Duración objetivo: ~{target_minutes} minutos")
    print(f"Secciones: {sections_count}")
    print(f"Palabras objetivo totales: ~{target_total_words}")
    print(
        f"Palabras por sección: objetivo ~{words_per_section} "
        f"(mín {min_words_section}, máx {max_words_section})"
    )

    print("\n[1/3] Generando índice...")
    outline = generate_outline(
        topic=topic,
        target_minutes=target_minutes,
        sections_count=sections_count,
        audience=DEFAULT_AUDIENCE,
        tone=tone,
        api_key=api_key,
    )
    print(f"Título general: {outline.titulo_general}")
    print("Partes detectadas:")
    for parte in outline.partes:
        print(f"  {parte.numero}. {parte.titulo}")

    print("\n[2/3] Generando secciones...")
    secciones_texto: List[str] = []
    resumen_previas = ""

    for parte in outline.partes:
        print(f"  - Sección {parte.numero}: {parte.titulo}")
        texto = generate_section(
            topic=topic,
            outline=outline,
            section=parte,
            min_words=min_words_section,
            max_words=max_words_section,
            tone=tone,
            resumen_previas=resumen_previas or None,
            api_key=api_key,
        )
        texto = maybe_expand_section_if_short(
            texto,
            min_words=min_words_section,
            topic=topic,
            outline=outline,
            section=parte,
            tone=tone,
            api_key=api_key,
        )
        palabras_sec = contar_palabras(texto)
        print(f"    Longitud sección {parte.numero}: ~{palabras_sec} palabras")

        secciones_texto.append(texto)
        resumen_previas += f"{parte.numero}. {parte.titulo}: sección generada.\n"

    print("\n[3/3] Ensamblando guion final...")
    guion_final = merge_sections(
        secciones_texto,
        target_minutes=target_minutes,
        words_per_minute=words_per_minute,
        tone=tone,
        api_key=api_key,
    )

    total_palabras_final = contar_palabras(guion_final)

    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        base_dir = os.path.abspath(OUTPUT_BASE_DIR_DEFAULT)
        os.makedirs(base_dir, exist_ok=True)
        safe_topic = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in topic)
        filename = f"{OUTPUT_FILENAME_PREFIX}{safe_topic or 'sin_tema'}.txt"
        output_path = os.path.join(base_dir, filename)

    save_script(output_path, guion_final)

    print("\n✅ Guion generado correctamente.")
    print(f"Ruta de salida: {output_path}")
    print(f"Palabras aproximadas del guion final: ~{total_palabras_final}")


if __name__ == "__main__":
    main()

