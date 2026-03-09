import json
from typing import List, Optional

from scripts_guion_largo.config import DEFAULT_LANG, DEFAULT_TONE, MODEL_NAME_TEXT
from scripts_guion_largo.gemini_client import call_gemini, contar_palabras
from scripts_guion_largo.outline import IndiceGuion, ParteIndice


def generate_section(
    topic: str,
    outline: IndiceGuion,
    section: ParteIndice,
    *,
    min_words: int,
    max_words: int,
    tone: str = DEFAULT_TONE,
    resumen_previas: Optional[str],
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
        api_key=api_key,
        model_name=MODEL_NAME_TEXT,
    )
    return texto.strip()


def maybe_expand_section_if_short(
    text: str,
    *,
    min_words: int,
    topic: str,
    outline: IndiceGuion,
    section: ParteIndice,
    tone: str = DEFAULT_TONE,
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
        api_key=api_key,
        model_name=MODEL_NAME_TEXT,
    )
    return texto_expandido.strip()

