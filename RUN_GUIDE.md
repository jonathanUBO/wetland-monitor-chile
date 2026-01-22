# Guía de Ejecución: Wetland Monitor

Para poner en marcha esta aplicación GEOINT, sigue estos pasos divididos por Backend y Frontend.

## 1. Backend (FastAPI)

El backend procesa los datos de Google Earth Engine y entrega la API.

### Requisitos
- Python 3.9+
- Una cuenta registrada en Google Earth Engine.

### Pasos
1. **Instalar dependencias:**
   ```bash
   cd backend
   pip install -r requirements.txt
   ```

2. **Configurar Autenticación de GEE:**
   - Sigue las instrucciones en [GEE_AUTH_GUIDE.md](./GEE_AUTH_GUIDE.md).
   - Ahora utilizamos un **Client ID de OAuth 2.0** en lugar de una Service Account. Esto permite que cada usuario se autentique con su propia cuenta.
   - Ingresa tu Client ID en la configuración de la interfaz web (botón "Configure Client ID").

3. **Ejecutar el servidor:**
   ```bash
   python main.py
   # O directamente con uvicorn:
   uvicorn main:app --reload --port 8000
   ```
   El backend estará disponible en `http://localhost:8000`. Puedes ver la documentación interactiva en `http://localhost:8000/docs`.

---

## 2. Frontend (Next.js)

El frontend es un dashboard moderno con React y Tailwind CSS.

### Requisitos
- Node.js 18+
- npm o yarn

### Pasos
1. **Crear el proyecto (si aún no lo has hecho):**
   ```bash
   npx create-next-app@latest frontend --typescript --tailwind --eslint
   ```
   *(Selecciona "App Router" cuando te lo pregunte)*.

2. **Instalar librerías necesarias:**
   ```bash
   cd frontend
   npm install lucide-react recharts react-map-gl mapbox-gl axios
   # Opcionalmente para animaciones:
   npm install framer-motion
   ```

3. **Configurar el Dashboard:**
   - Copia el archivo [Dashboard.tsx](./frontend/Dashboard.tsx) a `frontend/src/app/page.tsx` (reemplazando el contenido por defecto) o a tu carpeta de componentes.

4. **Ejecutar en modo desarrollo:**
   ```bash
   npm run dev
   ```
   El frontend estará disponible en `http://localhost:3000`.

---

## 3. Configuración de Mapbox (Opcional pero Recomendado)
Para que el mapa funcione con suavidad, debes:
1. Crear una cuenta en [Mapbox](https://www.mapbox.com/).
2. Obtener un `Public Access Token`.
3. Añadirlo a tu aplicación (normalmente mediante una variable de entorno `.env.local` con `NEXT_PUBLIC_MAPBOX_TOKEN=tu_token`).

## Resumen de Puertos
- **Frontend**: `http://localhost:3000`
- **Backend API**: `http://localhost:8000`
