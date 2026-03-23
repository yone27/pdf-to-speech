## Conversor de PDF a Audiolibro con Gemini TTS

Convierte un PDF en un **audiolibro** usando **Gemini 2.5 Pro TTS** en dos pasos:

1. PDF ➜ texto troceado (`partXXX.txt`).
2. Texto ➜ audio (`.wav` por parte).

## Requisitos rápidos

- Python 3.10+.
- Paquetes de Python:

```bash
pip install pdfplumber python-dotenv google-cloud-texttospeech
```

- Google Cloud con:
  - Proyecto creado.
  - API **Cloud Text-to-Speech** habilitada.
  - Credenciales mediante **gcloud** (lo que tú usas):

    ```powershell
    gcloud init
    gcloud auth application-default login
    ```
---

## Uso básico

Suponiendo que estás en la carpeta del proyecto y tienes `mi-libro.pdf`.

### 1. PDF ➜ texto (`pdf_to_text.py`)

```powershell
py pdf_to_text.py mi-libro.pdf
```

Hace esto:

- Crea `mi-libro/text/`.
- Extrae, limpia y trocea el texto del PDF.
- Genera `mi-libro/text/part001.txt`, `part002.txt`, ...

Revisa/edita esos `.txt` antes de generar el audio si quieres corregir algo.

### 2. Texto ➜ audio (`text_to_audiobook.py`)

```powershell
py text_to_audiobook.py mi-libro
```

Hace esto:

- Lee todos los `mi-libro/text/partXXX.txt`.
- Llama a Gemini 2.5 Pro TTS con la voz `Enceladus` (es-419).
- Crea `mi-libro/audio/partXXX.wav`.

---

## Opciones rápidas (por si las necesitas)

### `pdf_to_text.py`

```powershell
py pdf_to_text.py RUTA_PDF [--max-chars N] [--output-dir RUTA]
```

- `--max-chars`: tamaño máximo de cada parte (por defecto `3000`).
- `--output-dir`: base donde se crea la carpeta `nombre-libro/`.

### `text_to_audiobook.py`

```powershell
py text_to_audiobook.py BOOK [--workers N] [--prompt "texto"] [--base-dir RUTA]
```

- `BOOK`: nombre o ruta de la carpeta del libro (la que contiene `text/`).
- `--workers`: peticiones TTS en paralelo (por defecto `1`, puedes subirlo a `2` o `3`).
- `--prompt`: texto de instrucciones de estilo para la voz.
- `--base-dir`: base donde buscar la carpeta del libro si pasas solo el nombre.

DOC: 

// coste por uso
https://console.cloud.google.com/billing/019D42-F50717-3553DA/reports;timeRange=CUSTOM_RANGE;from=2026-01-01;to=2026-03-31;timeGrouping=GROUP_BY_DAY?hl=es-419&project=brave-healer-451403-e6



// habria que hacer un filtro para quitar los 
[VISUAL:
en los guiones

La Guardia Varega: Los Vikingos del Imperio Bizantino
La guardia pretoriana
catafractos
The Epic Story of Perseus Explained
Egyptian Gods Explained In 20 Minutes 
Genghis Khan Explained In 20 Minutes

https://www.youtube.com/watch?v=6I6w9TvWNNo&t=68s roma
https://www.youtube.com/watch?v=2jlExD3OG18 templarios
https://www.youtube.com/watch?v=cWl1gu8_XVI iliada
https://www.youtube.com/watch?v=AM-g4QPNINg trojan war