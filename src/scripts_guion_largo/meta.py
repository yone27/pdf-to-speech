"""
Genera meta para YouTube: resumen SEO, tags, ideas de miniatura y títulos
a partir del outline (outline.json), con menor coste que usar el guion completo.
"""

from scripts_guion_largo.config import DEFAULT_LANG
from scripts_guion_largo.gemini_client import call_gemini


def generate_meta_md(
    outline_content: str,
    topic: str,
    *,
    api_key: str,
    lang: str = DEFAULT_LANG,
) -> str:
    """
    Pide a Gemini un documento meta para el video: resumen extenso SEO,
    20 tags, 3 ideas de miniatura y 3 títulos. Usa solo el outline
    (tema + títulos y descripciones de cada parte) para reducir coste.
    Devuelve el texto en el formato del ejemplo (meta.txt).
    """
    lang_instruction = "Generate everything in English." if lang == "ingles" else "Genera todo en español."

    prompt = f"""You are an expert in YouTube SEO and video descriptions.

Based on the following video outline (topic, main title, and section titles with descriptions), produce a single markdown document for the video creator. Use this EXACT structure and labels.

**Instructions:** {lang_instruction}
- The extended summary must be SEO-optimized for the video description (readable, engaging, keyword-rich, 8–15 short paragraphs). Synthesize from the outline; do not invent content that is not suggested by it.
- Tags: exactly 20, comma-separated, lowercase, no hashtags.
- Thumbnails: 3 ideas. Each idea = one short title (e.g. "Big Harvest Transformation"), then a detailed scene description (what to draw: characters, objects, text on thumbnail, mood), then a line "Short text: " with the exact text to put on the thumbnail (e.g. "TRIPLE YOUR HARVEST").
- Titles: 3 alternative video titles, one per line, catchy and SEO-friendly.

Use this exact format (copy the headers and separators):

Resumen extenso para colocar en la descripción del video (YouTube)
=============================================================
[Your extended SEO summary paragraphs here.]

=====================================
tags :
[20 tags separated by commas, no line breaks in the tags line]

=========================================

Y aquí tienes 3 ideas de miniatura pensadas para alto CTR en este nicho:

1️⃣ [Thumbnail 1 title]
[Full scene description. Short text: "TEXT ON THUMB".]

2️⃣ [Thumbnail 2 title]
[Full scene description. Short text: "TEXT ON THUMB".]

3️⃣ [Thumbnail 3 title]
[Full scene description. Short text: "TEXT ON THUMB".]
=================================

[Título 1]
[Título 2]
[Título 3]

---
Outline (topic, title, and sections):
---
{outline_content}
---
"""

    return call_gemini(prompt, api_key=api_key)
