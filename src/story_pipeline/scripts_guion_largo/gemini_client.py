import json
import os
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv

from .config import MODEL_NAME_TEXT, REQUEST_TIMEOUT, MAX_RETRIES


load_dotenv()


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
    api_key: str,
    model_name: str = MODEL_NAME_TEXT,
    timeout: float = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> str:
    """Llama al modelo de texto de Gemini y devuelve el texto del primer candidato."""
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

    last_error: Optional[Exception] = None

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

