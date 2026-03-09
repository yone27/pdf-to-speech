from typing import List

from scripts_guion_largo.config import DEFAULT_LANG, DEFAULT_TONE, MODEL_NAME_TEXT
from scripts_guion_largo.gemini_client import call_gemini


def merge_sections(
    sections_text: List[str],
    *,
    target_minutes: int,
    words_per_minute: int,
    tone: str = DEFAULT_TONE,
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
        api_key=api_key,
        model_name=MODEL_NAME_TEXT,
    )
    return texto.strip()

