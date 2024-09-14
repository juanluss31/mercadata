import os
import pandas as pd
import pdfplumber
import re
import streamlit as st

# Define paths and file names
data_path = "data/pdfs"
output_csv = "data/mercadata.csv"

def categorize_item(item):
    """Función para categorizar los ítems"""
    # Normalizamos el nombre del ítem
    item = re.sub(r'[^a-zA-Z\s]', '', item).lower()
    
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
        "platos preparados": ["hummus", "preparado andaluz", "ensaladilla rusa"],
        "otros": ["huevos frescos", "estropajo", "toall.bebe", "dermo", "gamuza atrapapolvo", "rollo hogar doble", "lavavajillas", "colg. triple", "gel crema"]
    }

    for category, keywords in categories.items():
        if any(keyword in item for keyword in keywords):
            return category
    return "otros"

def extract_location(text):
    """Función para extraer la ubicación de la tienda del ticket."""
    location_match = re.search(r"MERCADONA,\s+S\.A\.\s+[^\n]*\n(.*?)(?=TELÉFONO:)", text, re.DOTALL)
    return location_match.group(1).strip() if location_match else "Ubicación no encontrada"

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
                item_pattern = r"(\d+)\s+([A-ZÀ-ÿ0-9\s/.%-]+?)\s+(\d+,\d{2})\s*(\d+,\d{2})?"

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
                    precio_unitario = match[2]
                    # precio_total = match[3] if match[3] else precio_unitario  # Si no hay precio total, es igual al unitario
                    precio = round(float(precio_unitario.replace(",", ".")), 2)
                    categoria = categorize_item(item)
                    for _ in range(cantidad):
                        data.append([fecha, identificativo, location, item, categoria, precio])
            else:
                print(f"No se pudo extraer texto del archivo: {uploaded_file.name}")

    if data:
        # Crear un DataFrame y guardarlo localmente como CSV
        df = pd.DataFrame(data, columns=["fecha", "identificativo de ticket", "ubicación", "item", "categoría", "precio"])
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