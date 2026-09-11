# Wetland Monitor Chile 🛰️💧

**Wetland Monitor** es una plataforma avanzada de análisis geoespacial para el monitoreo y evaluación de humedales en Chile. Utiliza imágenes satelitales **Sentinel-2 (L2A)** directamente desde **Copernicus Data Space Ecosystem (CDSE)** —el sucesor oficial de *Copernicus Open Access Hub (SciHub)* de la Agencia Espacial Europea (ESA)— para calcular índices espectrales críticos para la conservación y salud de los ecosistemas.

![Screenshot](frontend/public/screenshot.png)

## 🚀 Características Principales

- **Imágenes Satelitales Sentinel-2 en Tiempo Real**: Ingestión y renderizado de teselas RGB y espectrales (resolución 10-20m) a través de las APIs de Sentinel Hub / CDSE Process API.
- **Análisis Multi-Índice**: Cálculo automático de 6 índices espectrales validados:
  - 💧 **MNDWI**: Agua superficial y zonas inundables (`B03 Verde`, `B11 SWIR`).
  - 🌿 **NDRE**: Salud de la vegetación mediante Red Edge (`B08 NIR`, `B05 Red Edge`).
  - 🧪 **NDCI**: Calidad de agua y clorofila-a (`B05 Red Edge`, `B04 Rojo`).
  - 🌾 **SAVI**: Vegetación con factor de ajuste por suelo (`B04 Rojo`, `B08 NIR`).
  - 🦠 **FAI**: Detección de floraciones algales flotantes (`B04 Rojo`, `B08 NIR`, `B11 SWIR`).
  - ⚖️ **WRI**: Ratio Agua/Tierra (`B03 Verde`, `B04 Rojo`, `B08 NIR`, `B11 SWIR`).
- **Series Temporales Robustas**: Estadísticas resistentes a outliers y nubes utilizando la Statistical API de Copernicus.
- **Visualización en Mapa**: MapLibre GL con soporte para capas RGB y mapas térmicos/espectrales con escalas de color graduadas dinámicas.
- **Reportes Automáticos**: Generación de informes DOCX con comparativas visuales satelitales (Inicio vs. Fin de período), estadísticas y gráficos.
- **Áreas Personalizadas**: Soporte para cargar geometrías en formatos SHP (zip), GeoJSON, KML y KMZ.

---

## 🛠️ Tecnologías

### Backend
- **Python 3.11+**
- **FastAPI**: API REST de alto rendimiento.
- **Copernicus Data Space Ecosystem (CDSE) / Sentinel Hub API**: Ingestión y procesamiento de Sentinel-2 L2A.
- **GeoPandas / Shapely**: Análisis y manejo de geometrías vectoriales.
- **Pandas / NumPy**: Análisis estadístico y detección de valores atípicos.
- **python-docx / Matplotlib**: Generación de informes Word y gráficos.

### Frontend
- **Next.js 14 (React)**: Dashboard interactivo.
- **Tailwind CSS**: Diseño moderno y responsivo.
- **MapLibre GL**: Visualización cartográfica y capas raster satelitales.
- **Recharts**: Visualización de series de tiempo históricas.

---

## 📦 Guía de Instalación y Ejecución

### Prerrequisitos
1. **Cuenta en Copernicus Data Space Ecosystem (CDSE)**: Registro gratuito en [dataspace.copernicus.eu](https://dataspace.copernicus.eu/).
2. **Node.js 18+** y **Python 3.10+**.

### 1. Configuración del Backend (API)

```bash
cd backend

# Crear y activar entorno virtual
python -m venv venv
.\venv\Scripts\activate  # En Windows
# source venv/bin/activate # En Linux/Mac

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
# Copia .env.example a .env y añade tus credenciales de Copernicus:
cp .env.example .env
```

Contenido de `backend/.env`:
```env
# Credenciales de Copernicus Data Space Ecosystem (CDSE)
CDSE_USERNAME=tu_correo@dominio.cl
CDSE_PASSWORD=tu_contraseña

# Configuración del servidor
BACKEND_URL=http://localhost:8000
PORT=8000
HOST=0.0.0.0
DEBUG=True
```

Iniciar el servidor:
```bash
python main.py
```
El servidor backend iniciará en `http://localhost:8000`.

### 2. Configuración del Frontend (Dashboard)

```bash
cd frontend
npm install

# Configurar variables de entorno
cp .env.local.example .env.local
```

Contenido de `frontend/.env.local`:
```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Iniciar el servidor de desarrollo:
```bash
npm run dev
```
La aplicación estará disponible en `http://localhost:3000`.

---

## 🔐 Autenticación con Copernicus Hub (CDSE)

La plataforma permite autenticación de dos formas:

1. **Vía Archivo `.env` (Recomendado)**:
   Al configurar `CDSE_USERNAME` y `CDSE_PASSWORD` en `backend/.env`, el backend se conecta de forma automática al iniciar y mantiene los tokens en caché con renovación transparente.

2. **Vía Panel Web**:
   En la interfaz (`http://localhost:3000`), el indicador superior muestra el estado de CDSE. Haciendo clic en **Credenciales**, puedes ingresar o actualizar tu correo y contraseña de `dataspace.copernicus.eu` en caliente sin reiniciar el servidor.

---

## 📚 Base Científica de los Índices

El sistema implementa algoritmos validados por la comunidad científica utilizando las bandas de Sentinel-2 L2A:

1. **MNDWI (Modified Normalized Difference Water Index)**
   - *Xu (2006)*. Mejora la delineación de agua abierta suprimiendo ruido de edificaciones y suelo.
   - Bandas: `(B03 - B11) / (B03 + B11)`

2. **NDRE (Normalized Difference Red Edge Index)**
   - *Gitelson & Merzlyak (1994)*. Sensible a la clorofila, satura menos que el NDVI en vegetación densa.
   - Bandas: `(B08 - B05) / (B08 + B05)`

3. **NDCI (Normalized Difference Chlorophyll Index)**
   - *Mishra & Mishra (2012)*. Estimación de clorofila-a en aguas continentales y costeras.
   - Bandas: `(B05 - B04) / (B05 + B04)`

4. **SAVI (Soil Adjusted Vegetation Index)**
   - *Huete (1988)*. Minimiza la reflectancia de fondo del suelo en áreas con vegetación dispersa.
   - Bandas: `((B08 - B04) / (B08 + B04 + 0.5)) * 1.5`

5. **FAI (Floating Algae Index)**
   - *Hu (2009)*. Detección de biomasa algal flotante y floraciones sobre cuerpos de agua.
   - Bandas: `B08 - (B04 + (B11 - B04) * 0.1873)`

6. **WRI (Water Ratio Index)**
   - *Shen & Li (2010)*. Ratio rápido para delimitación de cuerpos de agua.
   - Bandas: `(B03 + B04) / (B08 + B11)`

---

## ☁️ Despliegue en Producción (Vercel + Cloud)

Este proyecto cuenta con una arquitectura desacoplada óptima para despliegue en la nube:

### 1. Despliegue del Frontend en Vercel
1. Sube el repositorio a **GitHub**.
2. Ingresa a [vercel.com](https://vercel.com) y selecciona **Add New... > Project**.
3. Importa el repositorio y configura:
   - **Root Directory**: `frontend` (haz clic en *Edit* y selecciona la carpeta `frontend`).
   - **Framework Preset**: Next.js (detectado automáticamente).
   - **Environment Variables**:
     - `NEXT_PUBLIC_API_URL`: La URL pública de tu backend (ej. `https://tu-backend.onrender.com` o `http://tu-ip:8000`).
4. Haz clic en **Deploy**. ¡Tu frontend estará online con CDN global y SSL automático!

### 2. Despliegue del Backend (FastAPI + GeoPandas)
Debido a que el backend requiere bibliotecas geoespaciales nativas (GDAL, GEOS, Shapely, python-docx), se recomienda desplegarlo en un servicio con soporte para contenedores Docker:

- **Opción A (Render / Railway / Fly.io)**:
  - Usa el archivo `backend/Dockerfile` incluido.
  - Conecta el repositorio en [render.com](https://render.com) o [railway.app](https://railway.app).
  - Configura el Root Directory en `backend`.
  - Configura las variables `PORT=8000` y si lo deseas `CDSE_USERNAME` y `CDSE_PASSWORD`.
- **Opción B (VPS con Docker / Docker Compose)**:
  ```bash
  cd backend
  docker build -t wetland-backend .
  docker run -d -p 8000:8000 --name wetland-backend wetland-backend
  ```

---

## 📄 Licencia

Este proyecto es de código abierto bajo licencia MIT.