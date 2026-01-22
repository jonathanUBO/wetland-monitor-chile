# Wetland Monitor Chile 🛰️💧

**Wetland Monitor** es una plataforma avanzada de análisis geoespacial para el monitoreo de humedales en Chile. Utiliza **Google Earth Engine (GEE)** para procesar imágenes satelitales (Sentinel-2, Sentinel-1) y calcular índices espectrales críticos para la salud de los ecosistemas.

![Screenshot](frontend/public/screenshot.png)

## 🚀 Características Principales

- **Análisis Multi-Índice**: Cálculo automático de 6 índices clave:
  - 💧 **MNDWI**: Agua superficial y zonas inundables.
  - 🌿 **NDRE**: Salud de la vegetación (Red Edge).
  - 🧪 **NDCI**: Calidad de agua (Clorofila-a).
  - 🌾 **SAVI**: Vegetación densa con ajuste de suelo.
  - 🦠 **FAI**: Floraciones algales flotantes.
  - ⚖️ **WRI**: Ratio Agua/Tierra.
- **Series Temporales Robustas**: Estadísticas resistentes a outliers y nubes.
- **Reportes Automáticos**: Generación de informes DOCX con mapas (Inicio/Fin) y gráficos.
- **Arquitectura Consolidada**: Backend optimizado en un único archivo (`main.py`) para máxima portabilidad.

---

## 🛠️ Tecnologías

### Backend
- **Python 3.11+**
- **FastAPI**: API REST de alto rendimiento.
- **Google Earth Engine API**: Procesamiento satelital.
- **Pandas/Numpy**: Análisis estadístico.

### Frontend
- **Next.js 14 (React)**: Framework web.
- **Tailwind CSS**: Estilizado.
- **Recharts**: Gráficos interactivos.
- **MapLibre GL**: Visualización geoespacial.

---

## 📦 Guía de Instalación y Ejecución

### Prerrequisitos
1. **Cuenta de Google Earth Engine**: [Registro aquí](https://earthengine.google.com/).
2. **Node.js 18+** y **Python 3.10+**.

### 1. Backend (API)

```bash
cd backend
python -m venv venv
.\venv\Scripts\activate  # Windows
# source venv/bin/activate # Linux/Mac

pip install -r requirements.txt
python main.py
```
El servidor iniciará en `http://localhost:8000`.

### 2. Frontend (Dashboard)

```bash
cd frontend
npm install
npm run dev
```
La aplicación estará disponible en `http://localhost:3000`.

---

## 🔐 Configuración de Autenticación (Google Earth Engine)

Esta aplicación utiliza **OAuth 2.0** para que cada usuario se autentique con su propia cuenta de Google, evitando límites de cuota compartidos.

### Pasos para configurar:

1. **Crear Client ID**:
   - Ve a [Google Cloud Console](https://console.cloud.google.com/).
   - **API y servicios > Credenciales > Crear credenciales > ID de cliente de OAuth**.
   - Tipo: **Aplicación web**.
   - Orígenes JS autorizados: `http://localhost:3000`.
   - Copia el **ID de cliente**.

2. **Configurar en la App**:
   - Abre `http://localhost:3000`.
   - Haz clic en **"Login with GEE"** o el botón de configuración (⚙️).
   - Pega tu Client ID.

---

## 📚 Base Científica de los Índices

El sistema implementa algoritmos validados por la comunidad científica:

1. **MNDWI (Modified Normalized Difference Water Index)**
   - *Xu (2006)*. Mejora la delineación de agua abierta suprimiendo ruido de edificaciones y suelo.
   - Fórmula: `(Green - SWIR) / (Green + SWIR)`

2. **NDRE (Normalized Difference Red Edge Index)**
   - *Gitelson & Merzlyak (1994)*. Sensible a la clorofila, satura menos que el NDVI en vegetación densa.
   - Fórmula: `(NIR - RedEdge) / (NIR + RedEdge)`

3. **NDCI (Normalized Difference Chlorophyll Index)**
   - *Mishra & Mishra (2012)*. Estimación de clorofila-a en aguas turbias. Implementación adaptada para bandas de Sentinel-2 (RedEdge 705nm).
   - Fórmula: `(RedEdge - Red) / (RedEdge + Red)`

4. **SAVI (Soil Adjusted Vegetation Index)**
   - *Huete (1988)*. Minimiza la influencia del brillo del suelo en zonas con vegetación dispersa.
   - Fórmula: `((NIR - Red) / (NIR + Red + L)) * (1 + L)` donde L=0.5

*(Todos los índices están normalizados al rango [-1, 1] para consistencia visual).*

---

## 📄 Licencia

Este proyecto es de código abierto bajo licencia MIT.