import os
import time
import pandas as pd
import pdfplumber
import re
import streamlit as st
import requests
import json

# Define paths and file names
data_path = "data/pdfs"
output_csv = "data/mercadata.csv"

OUTPUT_COLUMNS = [
    "fecha",
    "identificativo de ticket",
    "ubicación",
    "item",
    "categoría",
    "precio",
    "peso",
    "precio_por_kg",
    "url",
]

NON_PRODUCT_LINE_RE = re.compile(
    r"\b(TARJETA BANCARIA|TOTAL|SUBTOTAL|CREDITO|CR[ÉE]DITO|CAMBIO|ENTREGADO|"
    r"EFECTIVO|DESCRIPCI[ÓO]N|P\. UNIT|IMPORTE|IVA|TEL[ÉE]FONO|FACTURA SIMPLIFICADA)\b",
    re.IGNORECASE,
)
VAT_LINE_RE = re.compile(r"^\d+%\s+\d+,\d{2}\s+\d+,\d{2}$")
ITEM_LINE_RE = re.compile(r"^(?P<cantidad>\d+)\s+(?P<body>.+)$")
TRAILING_TWO_PRICES_RE = re.compile(
    r"^(?P<descripcion>.+?)\s+(?P<precio_unitario>\d+,\d{2})\s+(?P<total>\d+,\d{2})$"
)
TRAILING_PRICE_RE = re.compile(r"^(?P<descripcion>.+?)\s+(?P<total>\d+,\d{2})$")
WEIGHT_LINE_RE = re.compile(
    r"^(?P<peso>\d+,\d{3})\s*kg\s+(?P<precio_por_kg>\d+,\d{2})\s*€/\s*kg\s+(?P<total>\d+,\d{2})$",
    re.IGNORECASE,
)

def categorize_item_old(item):
    """Función para categorizar los ítems"""
    # Normalizamos el nombre del ítem
    item = re.sub(r'[^a-zA-ZÀ-ÿ/\s]', '', item).lower()
    
    # Diccionario de categorías por palabras clave
    categories = {
        "fruta": ["aguacate", "fresón", "nectarina", "paraguayo", "tomate", "pera rocha", "ciruela roja", "banana", "pera conferencia", "mezcla de frutos rojos"],
        "frutos secos": ["almendra", "anacardo", "nuez", "pasas sultanas", "cacahuete"],
        "snacks": ["patatas", "chocolate", "chicles", "cereales rellenos", "patatas lisas", "patatas chili lima", "nachos", "varitas frambuesa"],
        "panadería": ["panecillo", "barra de pan", "barra rústica", "croqueta", "tortillas mexicanas", "chapata cristal", "pan m. 55% centeno", "pan viena redondo"],
        "lácteos": ["leche", "yogur", "mantequilla", "queso", "cremoso", "stracciatella", "griego", "nata"],
        "bebidas y caldos": ["caldo de pollo", "salsa de soja", "agua mineral", "soja calcio brick"],
        "verduras y legumbres": ["garbanzo", "maíz", "ensalada", "cebolla", "pimiento", "champiñón", "calabacín", "zanahoria", "ajo", "brotes tiernos"],
        "carne": ["jamoncitos", "burger", "chuleta", "lomo", "cuarto trasero", "pavo", "albóndigas", "longaniza", "gallina", "tacos", "paleta", "loncha"],
        "condimentos y salsas": ["ketchup", "azúcar", "harina", "sabor", "para freir"],
        "despensa": ["arroz", "macarrón", "mezcla de semillas", "harina", "pasta", "avena crunchy", "arroz largo"],
        "conservas": ["atún", "tomate triturado", "aceitunas", "pepinillo"],
        "pizzas y platos preparados": ["hummus", "preparado andaluz", "ensaladilla rusa", "rosca", "pizza"],
        "otros": ["huevos frescos", "estropajo", "toall.bebe", "dermo", "gamuza atrapapolvo", "rollo hogar doble", "lavavajillas", "colg. triple", "gel crema"]
    }

    for category, keywords in categories.items():
        if any(keyword in item for keyword in keywords):
            return category, "URL no encontrada"
    return "otros", "URL no encontrada"

def categorize_item(item):
    """Función para categorizar los ítems usando la API de Mercadona"""

    apiURL = "https://7uzjkl1dj0-dsn.algolia.net/1/indexes/products_prod_4168_es/query"
    headers = {
        "x-algolia-application-id": "7UZJKL1DJ0",
        "x-algolia-api-key": "9d8f2e39e90df472b4f2e559a116fe17",
        "Content-Type": "application/json"
    }

    # Normalizamos el nombre del ítem
    item = re.sub(r'[^a-zA-ZÀ-ÿ/\s]', '', item).lower()
    
    # Cuerpo de la petición
    body = {
        "params": f"query={item}"
    }

    banedwords = [
        "bolsa plastico"
    ]

    if not any(keyword in item for keyword in banedwords):
        try:
            # Hacer la petición POST
            response = requests.post(apiURL, headers=headers, data=json.dumps(body), timeout=10)
            response.raise_for_status()
            response_json = response.json()
            time.sleep(0.5)  # Esperar 0.5 segundos para no exceder el límite de peticiones

            # Verificar si hay hits en la respuesta
            if response_json.get("hits"):
                # Ordenar los hits por score en orden descendente
                hits = sorted(response_json["hits"], key=lambda x: x["score"], reverse=True)

                # Obtener la categoría del hit con mayor score
                top_hit = hits[0]
                if top_hit.get("categories"):
                    top_category = top_hit["categories"][0]["name"]
                    share_url = top_hit.get("share_url", "URL no encontrada")
                    return top_category, share_url
        except requests.RequestException:
            # Si falla la API externa, usamos categorización local.
            pass
    
    # Si no hay hits o no se encuentra la categoría, retornar "otros"
    return categorize_item_old(item)

def extract_location(text):
    """Función para extraer la ubicación de la tienda del ticket."""
    location_match = re.search(r"MERCADONA,\s+S\.A\.\s+[^\n]*\n(.*?)(?=TELÉFONO:)", text, re.DOTALL)
    return location_match.group(1).strip().replace("\n", " ") if location_match else "Ubicación no encontrada"


def _parse_decimal(value: str) -> float:
    return float(value.replace("€", "").replace(" ", "").replace(",", "."))


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _split_description_and_total(body: str, cantidad: int) -> tuple[str, str | None]:
    two_prices_match = TRAILING_TWO_PRICES_RE.match(body)
    if two_prices_match:
        descripcion = _normalize_line(two_prices_match.group("descripcion"))
        precio_unitario = _parse_decimal(two_prices_match.group("precio_unitario"))
        precio_total = _parse_decimal(two_prices_match.group("total"))

        if cantidad <= 1 or abs((precio_unitario * cantidad) - precio_total) < 0.02:
            return descripcion, two_prices_match.group("total")

    trailing_price_match = TRAILING_PRICE_RE.match(body)
    if trailing_price_match:
        descripcion = _normalize_line(trailing_price_match.group("descripcion"))
        return descripcion, trailing_price_match.group("total")

    return body, None


def _looks_like_product_description(description: str) -> bool:
    if not description:
        return False
    if ":" in description:
        return False
    if not re.search(r"[A-ZÀ-Ÿ]", description):
        return False
    if NON_PRODUCT_LINE_RE.search(description):
        return False
    return True


def _append_regular_item_rows(data: list, base_row: dict, cantidad: int, precio_total: float):
    precio_unitario = round(precio_total / cantidad, 2) if cantidad else precio_total
    for _ in range(cantidad):
        data.append([
            base_row["fecha"],
            base_row["identificativo"],
            base_row["location"],
            base_row["item"],
            base_row["categoria"],
            precio_unitario,
            None,
            None,
            base_row["url"],
        ])


def _append_weighted_item_rows(
    data: list,
    base_row: dict,
    cantidad: int,
    peso_total: float,
    precio_por_kg: float,
    precio_total: float,
):
    precio_unitario = round(precio_total / cantidad, 2) if cantidad else precio_total
    peso_unitario = round(peso_total / cantidad, 3) if cantidad else peso_total
    for _ in range(cantidad):
        data.append([
            base_row["fecha"],
            base_row["identificativo"],
            base_row["location"],
            base_row["item"],
            base_row["categoria"],
            precio_unitario,
            peso_unitario,
            precio_por_kg,
            base_row["url"],
        ])


def _extract_ticket_items(text: str, fecha: str, identificativo: str, location: str) -> list:
    data = []
    pending_item = None

    for raw_line in text.splitlines():
        line = _normalize_line(raw_line)
        if not line:
            continue
        if NON_PRODUCT_LINE_RE.search(line) or VAT_LINE_RE.match(line):
            pending_item = None
            continue

        weight_match = WEIGHT_LINE_RE.match(line)
        if weight_match and pending_item is not None:
            _append_weighted_item_rows(
                data,
                pending_item,
                pending_item["cantidad"],
                _parse_decimal(weight_match.group("peso")),
                _parse_decimal(weight_match.group("precio_por_kg")),
                _parse_decimal(weight_match.group("total")),
            )
            pending_item = None
            continue

        item_match = ITEM_LINE_RE.match(line)
        if not item_match:
            pending_item = None
            continue

        cantidad = int(item_match.group("cantidad"))
        body = _normalize_line(item_match.group("body"))
        descripcion, precio_total = _split_description_and_total(body, cantidad)

        if not _looks_like_product_description(descripcion):
            pending_item = None
            continue

        categoria, url = categorize_item(descripcion)
        pending_item = {
            "fecha": fecha,
            "identificativo": identificativo,
            "location": location,
            "item": descripcion,
            "categoria": categoria,
            "url": url,
            "cantidad": cantidad,
        }

        if precio_total is not None:
            _append_regular_item_rows(data, pending_item, cantidad, _parse_decimal(precio_total))
            pending_item = None

    return data

def process_pdfs(uploaded_files) -> pd.DataFrame:
    """Procesa una lista de UploadedFile de Streamlit y devuelve un DataFrame."""
    import io
    all_rows = []
    for uploaded_file in uploaded_files:
        buf = io.BytesIO(uploaded_file.getbuffer())
        rows = _process_pdf_bytes_to_rows(buf, uploaded_file.name)
        all_rows.extend(rows)
        if not rows:
            st.warning(f"No se pudo extraer texto del archivo: {uploaded_file.name}")

    if all_rows:
        df = pd.DataFrame(all_rows, columns=OUTPUT_COLUMNS)
        return df
    else:
        st.info("No se encontraron datos en los PDFs subidos.")
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

def _process_pdf_bytes_to_rows(pdf_bytes: "io.BytesIO", file_name: str) -> list:
    """Extrae filas de datos de un PDF dado como BytesIO. Devuelve lista de listas."""
    data = []
    with pdfplumber.open(pdf_bytes) as pdf:
        page = pdf.pages[0]
        text = page.extract_text()
        if not text:
            return data

        location = extract_location(text)
        date_match = re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", text)
        fecha = date_match.group(0) if date_match else "Fecha no encontrada"
        ticket_match = re.search(r"FACTURA SIMPLIFICADA:\s+([0-9\-]+)", text)
        identificativo = ticket_match.group(1) if ticket_match else "Identificativo no encontrado"

        data.extend(_extract_ticket_items(text, fecha, identificativo, location))
    return data


def get_google_drive_service():
    """Devuelve un cliente autenticado de Google Drive."""
    import io as _io
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds)


def list_new_pdfs_from_drive(processed_ids: set) -> list:
    """Devuelve lista de (file_id, file_name) de PDFs no procesados aún en la carpeta Drive."""
    service = get_google_drive_service()
    folder_id = st.secrets["google"]["drive_folder_id"]
    results = service.files().list(
        q=f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
        fields="files(id, name)",
        orderBy="createdTime desc"
    ).execute()
    files = results.get("files", [])
    return [(f["id"], f["name"]) for f in files if f["id"] not in processed_ids]


def download_pdf_from_drive(file_id: str) -> "io.BytesIO":
    """Descarga un PDF de Google Drive y lo devuelve como BytesIO."""
    import io
    from googleapiclient.http import MediaIoBaseDownload
    service = get_google_drive_service()
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf


def process_pdfs_from_drive(new_files: list) -> "pd.DataFrame":
    """Procesa una lista de (file_id, file_name) desde Drive y devuelve un DataFrame."""
    all_rows = []
    for file_id, file_name in new_files:
        buf = download_pdf_from_drive(file_id)
        rows = _process_pdf_bytes_to_rows(buf, file_name)
        all_rows.extend(rows)
    if all_rows:
        return pd.DataFrame(all_rows, columns=OUTPUT_COLUMNS)
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def main():
    st.title("Procesador de Tickets PDF")

    # Permitir a los usuarios subir archivos PDF
    uploaded_files = st.file_uploader("Sube tus archivos PDF", accept_multiple_files=True, type="pdf")

    if uploaded_files:
        process_pdfs(uploaded_files)

if __name__ == "__main__":
    main()