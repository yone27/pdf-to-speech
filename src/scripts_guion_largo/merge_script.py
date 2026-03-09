import os
from typing import List

from scripts_guion_largo.config import (
    DEFAULT_TOPIC,
    DEFAULT_TONE,
    TARGET_MINUTES_DEFAULT,
    WORDS_PER_MINUTE_DEFAULT,
    get_final_script_path,
    get_sections_dir,
    get_run_dir,
)
from scripts_guion_largo.gemini_client import contar_palabras, load_api_key
from scripts_guion_largo.merge import merge_sections


def load_sections(topic: str) -> List[str]:
    sections_dir = get_sections_dir(topic)
    if not os.path.isdir(sections_dir):
        raise FileNotFoundError(
            f"No se encontró la carpeta de secciones: {sections_dir}. "
            "Primero ejecuta generate_sections.py."
        )

    files = [
        f for f in os.listdir(sections_dir) if f.lower().startswith("section_") and f.lower().endswith(".txt")
    ]
    if not files:
        raise RuntimeError(f"No se encontraron archivos de secciones en {sections_dir}.")

    files.sort()
    texts: List[str] = []
    for name in files:
        path = os.path.join(sections_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            texts.append(f.read())
    return texts


def main() -> None:
    # Usar solo constantes de config.py
    topic: str = DEFAULT_TOPIC
    target_minutes: int = TARGET_MINUTES_DEFAULT
    words_per_minute: int = WORDS_PER_MINUTE_DEFAULT
    tone: str = DEFAULT_TONE

    api_key = load_api_key()

    run_dir = get_run_dir(topic)
    sections_dir = get_sections_dir(topic)

    print(f"Tema: {topic}")
    print(f"Carpeta de trabajo: {run_dir}")
    print(f"Carpeta de secciones: {sections_dir}")

    secciones_texto = load_sections(topic)
    print(f"Secciones encontradas: {len(secciones_texto)}")

    print("\n[MERGE] Ensamblando guion final con Gemini...")
    guion_final = merge_sections(
        secciones_texto,
        target_minutes=target_minutes,
        words_per_minute=words_per_minute,
        tone=tone,
        api_key=api_key,
    )

    total_palabras_final = contar_palabras(guion_final)
    final_path = get_final_script_path(topic)
    os.makedirs(os.path.dirname(final_path), exist_ok=True)

    with open(final_path, "w", encoding="utf-8") as f:
        f.write(guion_final)

    print("\n✅ Guion final generado correctamente.")
    print(f"Ruta de salida: {final_path}")
    print(f"Palabras aproximadas del guion final: ~{total_palabras_final}")


if __name__ == "__main__":
    main()

