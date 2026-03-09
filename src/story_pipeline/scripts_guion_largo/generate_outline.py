import json
import os

from .config import (
    DEFAULT_TOPIC,
    OUTPUT_BASE_DIR_DEFAULT,
    TARGET_MINUTES_DEFAULT,
    SECTIONS_DEFAULT,
    get_outline_path,
    get_run_dir,
)
from .gemini_client import load_api_key
from .outline import generate_outline


def main() -> None:
    # Usar solo constantes de config.py
    topic: str = DEFAULT_TOPIC
    target_minutes: int = TARGET_MINUTES_DEFAULT
    sections_count: int = SECTIONS_DEFAULT

    api_key = load_api_key()

    print(f"Tema: {topic}")
    print(f"Duración objetivo: ~{target_minutes} minutos")
    print(f"Secciones: {sections_count}")

    outline = generate_outline(
        topic=topic,
        target_minutes=target_minutes,
        sections_count=sections_count,
        api_key=api_key,
    )

    run_dir = get_run_dir(topic)
    os.makedirs(run_dir, exist_ok=True)
    outline_path = get_outline_path(topic)

    data = {
        "meta": {
            "topic": topic,
            "target_minutes": target_minutes,
            "sections_count": sections_count,
        },
        "titulo_general": outline.titulo_general,
        "partes": [
            {
                "numero": p.numero,
                "titulo": p.titulo,
                "descripcion": p.descripcion,
            }
            for p in outline.partes
        ],
    }

    with open(outline_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("\n✅ Índice generado y guardado.")
    print(f"Carpeta de trabajo: {run_dir}")
    print(f"Archivo de índice: {outline_path}")
    print("Partes detectadas:")
    for parte in outline.partes:
        print(f"  {parte.numero}. {parte.titulo}")


if __name__ == "__main__":
    main()

