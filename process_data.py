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

def categorize_item_old(item):
    """Función para categorizar los ítems"""
    # Normalizamos el nombre del ítem
    item = re.sub(r'[^a-zA-ZÀ-ÿ/\s]', '', item).lower()

    print (item)
    
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
            return category
    return "otros"

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
        # Hacer la petición POST
        response = requests.post(apiURL, headers=headers, data=json.dumps(body))
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
                share_url = top_hit.get("share_url","URL no encontrada")
                return top_category, share_url
    
    # Si no hay hits o no se encuentra la categoría, retornar "otros"
    return categorize_item_old(item)

def extract_location(text):
    """Función para extraer la ubicación de la tienda del ticket."""
    location_match = re.search(r"MERCADONA,\s+S\.A\.\s+[^\n]*\n(.*?)(?=TELÉFONO:)", text, re.DOTALL)
    return location_match.group(1).strip().replace("\n", " ") if location_match else "Ubicación no encontrada"

def process_pdfs(uploaded_files):
    data = []

    # Asegurar que el directorio de datos exista
    data_path = "data"
    if not os.path.exists(data_path):
        os.makedirs(data_path)

    for uploaded_file in uploaded_files:
        pdf_path = os.path.join(data_path, uploaded_file.name)
        with open(pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        # Procesar cada archivo PDF
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            text = page.extract_text()

            if text:
                print("Texto extraído del PDF:")
                print(text)

                # Extraer ubicación
                location = extract_location(text)

                # Extraer fecha e identificador del ticket
                date_match = re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", text)
                fecha = date_match.group(0) if date_match else "Fecha no encontrada"

                ticket_match = re.search(r"FACTURA SIMPLIFICADA:\s+([0-9\-]+)", text)
                identificativo = ticket_match.group(1) if ticket_match else "Identificativo no encontrado"

                # Extraer ítems y precios utilizando un patrón más flexible
                # Patrón mejorado para capturar ítems con múltiples palabras y precios
                item_pattern = r"(\d+)\s+([A-ZÀ-ÿ0-9\s/.%-]+?)\s+([0-9\s/,kg€]+?)?\s*(\d+,\d{2})\n"

                # Filtrar líneas no relacionadas con productos
                patron_no_producto = re.compile(r"(TARJETA BANCARIA|TOTAL|SUBTOTAL|CREDITO)", re.IGNORECASE)
                patron_iva = r"([0-9]+%)\s+(\d+,\d{2})\s*(\d+,\d{2})"
                
                # Filtrar líneas no relacionadas con productos
                filtered_lines = [line for line in text.splitlines() if not patron_no_producto.search(line)]
                
                # Extraer ítems de las líneas filtradas
                itemsIVA = re.findall(item_pattern, '\n'.join(filtered_lines))
                items = [match for match in itemsIVA if not re.search(patron_iva, " ".join(match))]

                for match in items:
                    cantidad = int(match[0])
                    item = match[1].strip()
                    precio_unitario = match[3]
                    # precio_total = match[3] if match[3] else precio_unitario  # Si no hay precio total, es igual al unitario
                    precio = round((float(precio_unitario.replace(",", "."))/cantidad), 2)
                    categoria, url = categorize_item(item)
                    for _ in range(cantidad):
                        data.append([fecha, identificativo, location, item, categoria, precio, url])
            else:
                print(f"No se pudo extraer texto del archivo: {uploaded_file.name}")

    if data:
        # Crear un DataFrame y guardarlo localmente como CSV
        df = pd.DataFrame(data, columns=["fecha", "identificativo de ticket", "ubicación", "item", "categoría", "precio", "url"])
        df.to_csv(output_csv, index=False)
        st.success(f"Archivo CSV generado con éxito: {output_csv}")

    else:
        st.info("No se encontraron datos para escribir en el archivo CSV.")

def main():
    st.title("Procesador de Tickets PDF")

    # Permitir a los usuarios subir archivos PDF
    uploaded_files = st.file_uploader("Sube tus archivos PDF", accept_multiple_files=True, type="pdf")

    if uploaded_files:
        process_pdfs(uploaded_files)

if __name__ == "__main__":
    main()