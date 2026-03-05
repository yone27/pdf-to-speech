import base64
import json
import os
import sys
import time
from typing import Iterable, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

SOURCE_BOOK_DIR = "jardin"
SOURCE_TXT_FALLBACK = None
IMAGE_MODEL_NAME = "gemini-2.5-flash-image"
IMAGES_FORMAT = "png"
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 3
MAX_CONCURRENT_PER_PART = 2 
SKIP_EXISTING = True  # Si True, no re-genera imágenes que ya existen

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


def resolve_book_dir() -> Tuple[str, str]:
  if SOURCE_BOOK_DIR:
    book_dir = os.path.abspath(SOURCE_BOOK_DIR)
    book_name = os.path.basename(book_dir)
    return book_name, book_dir

  txt_path = os.path.abspath(SOURCE_TXT_FALLBACK)
  if not os.path.isfile(txt_path):
    raise FileNotFoundError(
      f"No se encontró el TXT de referencia: {txt_path}. "
      "Configura SOURCE_BOOK_DIR o corrige SOURCE_TXT_FALLBACK."
    )
  book_name = os.path.splitext(os.path.basename(txt_path))[0]
  base_dir = os.path.dirname(txt_path)
  book_dir = os.path.join(base_dir, book_name)
  return book_name, book_dir


def iter_prompt_files(book_dir: str) -> Iterable[Tuple[str, str, str]]:
  img_root = os.path.join(book_dir, "img")
  if not os.path.isdir(img_root):
    raise FileNotFoundError(
      f"No se encontró la carpeta 'img' en el libro: {img_root}. "
      "Primero ejecuta 02_parts_to_image_prompts.py."
    )

  for chapter_slug in sorted(os.listdir(img_root)):
    chapter_img_dir = os.path.join(img_root, chapter_slug)
    if not os.path.isdir(chapter_img_dir):
      continue

    for filename in sorted(os.listdir(chapter_img_dir)):
      if not filename.lower().endswith(".txt"):
        continue
      if not filename.startswith("part"):
        continue

      base_name, _ = os.path.splitext(filename)
      yield chapter_slug, base_name, os.path.join(chapter_img_dir, filename)


def generate_image_for_prompt(
  prompt: str,
  *,
  model_name: str,
  api_key: str,
  timeout: float = REQUEST_TIMEOUT,
  max_retries: int = MAX_RETRIES,
) -> bytes:
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
          f"Respuesta no exitosa de Gemini (status={response.status_code}): {response.text[:500]}"
        )

      data = response.json()
      candidates = data.get("candidates") or []
      if not candidates:
        raise RuntimeError("La respuesta de Gemini no contiene 'candidates'.")

      content = candidates[0].get("content") or {}
      parts = content.get("parts") or []
      for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and "data" in inline:
          b64 = inline["data"]
          return base64.b64decode(b64)

      raise RuntimeError("No se encontró 'inlineData' con imagen en la respuesta de Gemini.")

    except (requests.RequestException, RuntimeError, ValueError) as exc:
      last_error = exc
      if attempt < max_retries:
        wait_time = 2**attempt
        print(
          f"  [WARN] Error al generar imagen (intento {attempt}/{max_retries}): {exc}. "
          f"Reintentando en {wait_time}s...",
          file=sys.stderr,
        )
        time.sleep(wait_time)
      else:
        break

  raise RuntimeError(f"Falló la generación de imagen tras {max_retries} intentos: {last_error}")


def main() -> None:
  book_name, book_dir = resolve_book_dir()
  print(f"Libro: {book_name}")
  print(f"Carpeta base del libro: {book_dir}")

  api_key = load_api_key()

  prompt_files = list(iter_prompt_files(book_dir))
  if not prompt_files:
    print("No se encontraron archivos de prompts en img/<chapter_slug>/partNNN.txt.", file=sys.stderr)
    return

  total_files = len(prompt_files)
  print(f"Archivos de prompts encontrados: {total_files}")

  for file_idx, (chapter_slug, base_name, prompts_path) in enumerate(prompt_files, start=1):
    chapter_img_dir = os.path.join(book_dir, "img", chapter_slug)

    print(f"[{file_idx}/{total_files}] Procesando prompts de {chapter_slug}/{base_name}.txt...")

    with open(prompts_path, "r", encoding="utf-8") as f:
      prompts = [ln.strip() for ln in f if ln.strip()]

    if not prompts:
      print("  No hay prompts en el archivo, se omite.")
      continue

    for idx, prompt in enumerate(prompts, start=1):
      img_filename = f"{base_name}_img{idx:02d}.{IMAGES_FORMAT}"
      img_path = os.path.join(chapter_img_dir, img_filename)

      if SKIP_EXISTING and os.path.isfile(img_path):
        print(f"  - Imagen {img_filename} ya existe, se omite (SKIP_EXISTING=True).")
        continue

      print(f"  - Generando imagen {idx}/{len(prompts)} para {base_name}...")
      image_bytes = generate_image_for_prompt(
        prompt,
        model_name=IMAGE_MODEL_NAME,
        api_key=api_key,
      )

      with open(img_path, "wb") as img_file:
        img_file.write(image_bytes)

  print("✅ Imágenes generadas a partir de los prompts. "
        "Estructura: img/<chapter_slug>/partNNN_img01.png, partNNN_img02.png, ...")


if __name__ == "__main__":
  main()