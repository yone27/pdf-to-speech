import json
from dataclasses import dataclass
from typing import List, Tuple

from .config import DEFAULT_AUDIENCE, DEFAULT_LANG, DEFAULT_TONE, MODEL_NAME_TEXT
from .gemini_client import call_gemini


@dataclass
class ParteIndice:
    numero: int
    titulo: str
    descripcion: str


@dataclass
class IndiceGuion:
    titulo_general: str
    partes: List[ParteIndice]


def parse_outline_json(raw: str, fallback_topic: str) -> IndiceGuion:
    """Parsea de forma robusta el JSON devuelto por Gemini a IndiceGuion."""
    try:
        start = raw.index("{")
        json_str = raw[start:]
    except ValueError:
        json_str = raw

    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(json_str)

    titulo_general = data.get("titulo_general") or fallback_topic
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


def generate_outline(
    topic: str,
    target_minutes: int,
    sections_count: int,
    *,
    audience: str = DEFAULT_AUDIENCE,
    tone: str = DEFAULT_TONE,
    api_key: str,
) -> IndiceGuion:
    """Genera un índice estructurado del guion usando Gemini."""
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
        api_key=api_key,
        model_name=MODEL_NAME_TEXT,
    )

    return parse_outline_json(raw, fallback_topic=topic)

