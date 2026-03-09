import json
import os
import sys

from scripts_guion_largo.config import (
    DEFAULT_TOPIC,
    DEFAULT_TONE,
    SECTIONS_DEFAULT,
    TARGET_MINUTES_DEFAULT,
    WORDS_PER_MINUTE_DEFAULT,
    get_outline_path,
    get_run_dir,
    get_sections_dir,
)
from scripts_guion_largo.gemini_client import contar_palabras, load_api_key
from scripts_guion_largo.meta import generate_meta_md
from scripts_guion_largo.outline import IndiceGuion, ParteIndice
from scripts_guion_largo.sections import generate_section, maybe_expand_section_if_short


def load_outline(topic: str) -> IndiceGuion:
    outline_path = get_outline_path(topic)
    if not os.path.isfile(outline_path):
        raise FileNotFoundError(
            f"No se encontró outline.json para el tema dado: {outline_path}. "
            "Primero ejecuta generate_outline.py."
        )

    with open(outline_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    titulo_general = data.get("titulo_general") or topic
    partes_raw = data.get("partes") or []
    partes = [
        ParteIndice(
            numero=int(p.get("numero", idx + 1)),
            titulo=str(p.get("titulo") or f"Sección {idx + 1}"),
            descripcion=str(p.get("descripcion") or "").strip(),
        )
        for idx, p in enumerate(partes_raw)
    ]

    if not partes:
        raise RuntimeError("outline.json no contiene partes válidas.")

    return IndiceGuion(titulo_general=titulo_general, partes=partes)


def main() -> None:
    # Usar solo constantes de config.py
    topic: str = DEFAULT_TOPIC
    target_minutes: int = TARGET_MINUTES_DEFAULT
    sections_count: int = SECTIONS_DEFAULT
    words_per_minute: int = WORDS_PER_MINUTE_DEFAULT
    tone: str = DEFAULT_TONE
    overwrite: bool = False

    if target_minutes <= 0:
        raise SystemExit("--minutes debe ser mayor que 0.")
    if sections_count <= 0:
        raise SystemExit("--sections debe ser mayor que 0.")
    if words_per_minute <= 0:
        raise SystemExit("--wpm debe ser mayor que 0.")

    api_key = load_api_key()

    outline = load_outline(topic)

    target_total_words = target_minutes * words_per_minute
    words_per_section = max(1, target_total_words // max(1, sections_count))
    min_words_section = int(words_per_section * 0.8)
    max_words_section = int(words_per_section * 1.2)

    run_dir = get_run_dir(topic)
    sections_dir = get_sections_dir(topic)
    os.makedirs(sections_dir, exist_ok=True)

    print(f"Tema: {topic}")
    print(f"Carpeta de trabajo: {run_dir}")
    print(f"Carpeta de secciones: {sections_dir}")
    print(f"Duración objetivo: ~{target_minutes} minutos")
    print(
        f"Palabras por sección: objetivo ~{words_per_section} "
        f"(mín {min_words_section}, máx {max_words_section})"
    )

    resumen_previas = ""
    omitidas = 0
    generadas_ok = 0
    fallidas = 0

    # Archivo combinado con todas las secciones, en orden
    combined_path = os.path.join(run_dir, "secciones_combinadas.md")
    with open(combined_path, "w", encoding="utf-8") as combined_file:
        for parte in outline.partes:
            section_filename = f"section_{parte.numero:02d}.txt"
            section_path = os.path.join(sections_dir, section_filename)

            # Si ya existe y no queremos sobrescribir, reutilizamos el texto guardado
            if os.path.isfile(section_path) and not overwrite:
                print(f"[SKIP] Sección {parte.numero} ya existe ({section_filename}), se omite regeneración.")
                with open(section_path, "r", encoding="utf-8") as f:
                    texto = f.read()
                resumen_previas += f"{parte.numero}. {parte.titulo}: sección ya existente.\n"
                omitidas += 1
            else:
                print(f"[GEN] Sección {parte.numero}: {parte.titulo}")
                try:
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

                    with open(section_path, "w", encoding="utf-8") as f:
                        f.write(texto)

                    resumen_previas += f"{parte.numero}. {parte.titulo}: sección generada.\n"
                    generadas_ok += 1
                except Exception as e:
                    fallidas += 1
                    print(f"    [ERROR] Sección {parte.numero}: {e}", file=sys.stderr)
                    resumen_previas += f"{parte.numero}. {parte.titulo}: error al generar.\n"
                    texto = "(Sección no generada por error.)"

            # Añadir siempre la sección (existente, generada o fallida) al archivo combinado
            combined_file.write(f"# {parte.titulo}\n")
            combined_file.write(texto.strip() + "\n\n")

    total = len(outline.partes)
    peticiones = generadas_ok + fallidas

    # Generar meta.md (resumen SEO, tags, miniaturas, títulos) a partir del outline (menor coste que el guion completo)
    meta_path = os.path.join(run_dir, "meta.md")
    try:
        outline_content = f"Topic: {topic}\nTitle: {outline.titulo_general}\n\n"
        outline_content += "\n\n".join(
            f"{p.numero}. {p.titulo}\n{p.descripcion}" for p in outline.partes
        )
        print("\n[META] Generando resumen SEO, tags, miniaturas y títulos desde outline...")
        meta_text = generate_meta_md(
            outline_content=outline_content,
            topic=topic,
            api_key=api_key,
        )
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(meta_text)
        print(f"[META] Guardado: {meta_path}")
    except Exception as e:
        print(f"[META] No se pudo generar meta.md: {e}", file=sys.stderr)

    print("\n✅ Generación de secciones completada.")
    print(f"Revisa los archivos en: {sections_dir}")
    print("\n--- Resumen ---")
    print(f"Total secciones: {total}")
    print(f"Omitidas (ya existían): {omitidas}")
    print(f"Generadas correctamente: {generadas_ok}")
    print(f"Fallidas: {fallidas}")
    print(f"Peticiones a Gemini (intentos): {peticiones}")
    if peticiones > 0:
        print(f"Tasa de éxito: {generadas_ok}/{peticiones} ({100 * generadas_ok // peticiones}%)")


if __name__ == "__main__":
    main()

