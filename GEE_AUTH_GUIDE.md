# Guía de Autenticación: Flujo Multi-Usuario (OAuth2)

Esta aplicación está diseñada para que **cada usuario** acceda con sus propias credenciales de Google Earth Engine. Esto garantiza seguridad y evita el agotamiento de cuotas de una sola cuenta.

## 1. Crear un Client ID de OAuth 2.0

1. Ve a la [Google Cloud Console](https://console.cloud.google.com/).
2. Selecciona tu proyecto.
3. Ve a **API y servicios > Pantalla de consentimiento de OAuth**.
   - Configura la pantalla (tipo "Externo" si no tienes Google Workspace).
   - Añade los scopes: `.../auth/earthengine` y `.../auth/userinfo.email`.
4. Ve a **API y servicios > Credenciales**.
5. Haz clic en **Crear credenciales > ID de cliente de OAuth**.
6. Tipo de aplicación: **Aplicación web**.
7. **Orígenes de JavaScript autorizados**: Añade `http://localhost:3000`.
8. Copia el **ID de cliente** generado.


## 2. Configurar el Frontend

1. Abre la aplicación en tu navegador (`http://localhost:3000`).
2. En la barra lateral izquierda, haz clic en **"Configure Client ID"**.
3. Pega tu **ID de cliente** que copiaste en el paso anterior.
4. Haz clic en **Save**.
   *(El ID se guardará en tu navegador, así que solo necesitas hacerlo una vez).*

## 3. Cómo funciona el flujo

1. **Login**: El usuario hace clic en "Login with GEE". Se abre una ventana emergente de Google.
2. **Token**: Tras el login, el frontend recibe un `access_token` temporal.
3. **Backend**: Cuando el usuario hace clic en "Analyze", el frontend envía el token en el header `Authorization: Bearer <token>`.
4. **GEE**: El servidor de FastAPI usa ese token para inicializar la librería de Earth Engine específicamente para esa petición.

## Beneficios
- **Sin Archivos JSON**: No necesitas manejar claves de cuentas de servicio en el servidor.
- **Escalabilidad**: Cientos de usuarios pueden usar la app simultáneamente con sus propias cuotas.
- **Seguridad**: Los tokens son temporales y nunca se almacenan permanentemente.

> [!IMPORTANT]
> Los usuarios deben tener acceso a Google Earth Engine para que el análisis funcione. Pueden registrarse en [earthengine.google.com](https://earthengine.google.com/).
