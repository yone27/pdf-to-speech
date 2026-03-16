import argparse
import json
import os
import sys
import time
import shutil
from typing import Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from dotenv import load_dotenv

load_dotenv()

SOURCE_BOOK_DIR = "guion"
TARGET_LANG_CODE = "de"
MODEL_NAME = "gemini-2.5-pro"
SKIP_EXISTING = True  # Si True, no vuelve a traducir archivos ya generados en la carpeta destino
REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 3
DEFAULT_WORKERS = 5


class GeminiTranslationError(Exception):
    pass

def load_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No se encontró la variable de entorno GEMINI_API_KEY. "
            "Configúrala con tu clave de la API de Gemini."
        )
    return api_key


def resolve_book_dirs(source_book_dir: str, target_lang: str) -> Tuple[str, str, str, str]:
    src_book_dir = os.path.abspath(source_book_dir)
    src_text_dir = os.path.join(src_book_dir, "text")

    if not os.path.isdir(src_text_dir):
        raise FileNotFoundError(f"No se encontró la carpeta de texto de origen: {src_text_dir}")

    dst_book_dir = f"{src_book_dir}-{target_lang}"
    dst_text_dir = os.path.join(dst_book_dir, "text")

    os.makedirs(dst_text_dir, exist_ok=True)

    return src_book_dir, src_text_dir, dst_book_dir, dst_text_dir


def copy_images_tree(
    src_book_dir: str,
    dst_book_dir: str,
    *,
    skip_existing: bool,
) -> None:
    """
    Copia la carpeta img/ del libro origen al libro destino,
    preservando la estructura y nombres de archivo.
    """
    src_img_root = os.path.join(src_book_dir, "img")
    if not os.path.isdir(src_img_root):
        # Nada que copiar; es válido si el libro aún no tiene imágenes.
        print(f"No se encontró carpeta de imágenes en origen: {src_img_root}")
        return

    dst_img_root = os.path.join(dst_book_dir, "img")

    copied = 0
    skipped = 0

    for root, _dirs, files in os.walk(src_img_root):
        rel_root = os.path.relpath(root, src_img_root)
        dst_root = os.path.join(dst_img_root, rel_root)
        os.makedirs(dst_root, exist_ok=True)

        for name in files:
            src_path = os.path.join(root, name)
            dst_path = os.path.join(dst_root, name)

            if skip_existing and os.path.isfile(dst_path):
                skipped += 1
                continue

            shutil.copy2(src_path, dst_path)
            copied += 1

    print("\nCopia de imágenes:")
    print(f"  Origen img/:  {src_img_root}")
    print(f"  Destino img/: {dst_img_root}")
    print(f"  Archivos copiados: {copied}")
    print(f"  Archivos saltados: {skipped}")


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


def translate_text_with_gemini(
    text: str,
    target_lang: str,
    *,
    model_name: str,
    api_key: str,
    timeout: float = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> str:
    url = build_gemini_url(model_name, api_key)

    prompt = (
        f"Traduce el siguiente texto al idioma '{target_lang}'. "
        "Devuelve únicamente la traducción, sin explicaciones ni comentarios adicionales.\n\n"
        f"{text}"
    )

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
                raise GeminiTranslationError(
                    f"Respuesta no exitosa de Gemini (status={response.status_code}): {response.text[:500]}"
                )

            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise GeminiTranslationError("La respuesta de Gemini no contiene 'candidates'.")

            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            if not parts or "text" not in parts[0]:
                raise GeminiTranslationError("No se encontró texto en la respuesta de Gemini.")

            return parts[0]["text"]

        except (requests.RequestException, GeminiTranslationError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                wait_time = 2**attempt
                print(
                    f"  [WARN] Error al traducir (intento {attempt}/{max_retries}): {exc}. "
                    f"Reintentando en {wait_time}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
            else:
                break

    raise GeminiTranslationError(f"Falló la traducción tras {max_retries} intentos: {last_error}")


def process_file(
    index: int,
    total: int,
    filename: str,
    src_path: str,
    dst_path: str,
    *,
    target_lang: str,
    model_name: str,
    api_key: str,
    skip_existing: bool,
) -> dict:
    result: dict = {
        "status": "ok",
        "filename": filename,
        "src_path": src_path,
        "dst_path": dst_path,
        "made_request": False,
        "error": None,
    }

    try:
        if skip_existing and os.path.isfile(dst_path):
            result["status"] = "skipped"
            return result

        with open(src_path, "r", encoding="utf-8") as f:
            original_text = f.read()

        if not original_text.strip():
            translated_text = ""
        else:
            translated_text = translate_text_with_gemini(
                original_text,
                target_lang,
                model_name=model_name,
                api_key=api_key,
            )
            result["made_request"] = True

        with open(dst_path, "w", encoding="utf-8") as f_out:
            f_out.write(translated_text)

        result["status"] = "ok"
        return result
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)
        return result


def process_files(
    src_text_dir: str,
    dst_text_dir: str,
    *,
    target_lang: str,
    model_name: str,
    api_key: str,
    skip_existing: bool,
    workers: int,
) -> None:
    files_to_process = []
    for root, _dirs, files in os.walk(src_text_dir):
        for name in files:
            if not name.lower().endswith(".txt"):
                continue
            src_path = os.path.join(root, name)
            rel_path = os.path.relpath(src_path, src_text_dir)
            dst_path = os.path.join(dst_text_dir, rel_path)
            files_to_process.append((rel_path, src_path, dst_path))

    files_to_process.sort(key=lambda t: t[0])

    if not files_to_process:
        print(f"No se encontraron archivos .txt en {src_text_dir}")
        return

    total = len(files_to_process)
    translated = 0
    skipped = 0
    errors = 0
    total_requests = 0

    print(f"Archivos a procesar: {total}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for index, (rel_path, src_path, dst_path) in enumerate(files_to_process, start=1):
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            futures[
                executor.submit(
                    process_file,
                    index,
                    total,
                    rel_path,
                    src_path,
                    dst_path,
                    target_lang=target_lang,
                    model_name=model_name,
                    api_key=api_key,
                    skip_existing=skip_existing,
                )
            ] = (index, rel_path)

        for future in as_completed(futures):
            index, rel_path = futures[future]
            try:
                result = future.result()
                status = result.get("status")
                made_request = bool(result.get("made_request"))

                if status == "ok":
                    translated += 1
                    if made_request:
                        total_requests += 1
                    print(f"[{index}/{total}] OK '{rel_path}'")
                elif status == "skipped":
                    skipped += 1
                    print(f"[{index}/{total}] Saltando '{rel_path}' (ya existe en destino).")
                else:
                    errors += 1
                    if made_request:
                        total_requests += 1
                    error_msg = result.get("error") or "Error desconocido"
                    print(
                        f"[{index}/{total}] ERROR '{rel_path}': {error_msg}",
                        file=sys.stderr,
                    )
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(
                    f"[{index}/{total}] ERROR inesperado en '{rel_path}': {exc}",
                    file=sys.stderr,
                )

    print("\nResumen de traducción:")
    print(f"  Total archivos:         {total}")
    print(f"  Traducidos ok:          {translated}")
    print(f"  Saltados (existen):     {skipped}")
    print(f"  Con errores:            {errors}")
    print(f"  Peticiones reales a API:{total_requests}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Traduce todos los .txt de la carpeta 'text' de un libro "
            "usando la API de Gemini y guarda los resultados en una "
            "carpeta hermana con sufijo de idioma."
        )
    )
    parser.add_argument(
        "--book-dir",
        type=str,
        default=SOURCE_BOOK_DIR,
        help=(
            "Ruta al directorio del libro origen (el que contiene 'text/'). "
            "Por defecto se usa el valor de SOURCE_BOOK_DIR."
        ),
    )
    parser.add_argument(
        "--lang",
        type=str,
        default=TARGET_LANG_CODE,
        help=(
            "Código de idioma destino (ej: 'en', 'fr', 'de'). "
            "Por defecto se usa TARGET_LANG_CODE."
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        default=MODEL_NAME,
        help="Nombre del modelo de Gemini a usar. Ej: 'models/gemini-1.5-flash'.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Si se indica, sobrescribe archivos ya existentes en la carpeta destino.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Número máximo de peticiones concurrentes a Gemini (por defecto {DEFAULT_WORKERS}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        api_key = load_api_key()
    except RuntimeError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        src_book_dir, src_text_dir, dst_book_dir, dst_text_dir = resolve_book_dirs(
            args.book_dir,
            args.lang,
        )
    except FileNotFoundError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)

    skip_existing = not args.overwrite if SKIP_EXISTING else False

    print(f"Libro origen:   {src_book_dir}")
    print(f"Carpeta origen: {src_text_dir}")
    print(f"Libro destino:  {dst_book_dir}")
    print(f"Carpeta destino:{dst_text_dir}")
    print(f"Idioma destino: {args.lang}")
    print(f"Modelo Gemini:  {args.model}")
    print(f"Sobrescribir existentes: {'sí' if not skip_existing else 'no'}")
    print(f"Workers (hilos): {args.workers}")
    print()

    process_files(
        src_text_dir,
        dst_text_dir,
        target_lang=args.lang,
        model_name=args.model,
        api_key=api_key,
        skip_existing=skip_existing,
        workers=args.workers,
    )

    # Copiar también las imágenes del libro origen al libro traducido.
    # Usa la misma semántica de skip_existing para no sobrescribir por defecto.
    copy_images_tree(
        src_book_dir,
        dst_book_dir,
        skip_existing=skip_existing,
    )


if __name__ == "__main__":
    main()

