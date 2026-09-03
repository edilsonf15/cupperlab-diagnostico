# Despliegue en producción (Docker + dominio)

La app corre en un contenedor Docker que escucha en el **puerto 8000**. Delante pones un
**reverse proxy** (Traefik/Nginx/Caddy o Plesk) que sirve tu dominio con **HTTPS** y reenvía a `:8000`.

---

## 🟢 Ruta recomendada: DOKPLOY (con subdominio de cupperlab.com)

Dokploy construye la imagen, la corre y le pone **SSL automático** (Traefik + Let's Encrypt).

### Paso 1 · Sube el código a GitHub
Dokploy despliega desde un repo Git. Desde la carpeta del proyecto:
```bash
git init
git add .
git commit -m "Diagnostico SEO+GEO Cupperlab"
git branch -M main
git remote add origin https://github.com/TU-USUARIO/cupperlab-diagnostico.git
git push -u origin main
```
> El `.gitignore` ya excluye `.venv/`, `data/` y `.env` (tus claves NO se suben). ✅

### Paso 2 · DNS del subdominio
En tu proveedor DNS de cupperlab.com crea un registro **A**:
`diagnostico` → **IP de tu servidor Dokploy**. (Espera unos minutos a que propague.)

### Paso 3 · Crea la Aplicación en Dokploy
1. Dokploy → **Create Application**.
2. **Provider: GitHub** → conecta tu cuenta y elige el repo `cupperlab-diagnostico`, rama `main`.
3. **Build Type: Dockerfile** (ruta: `Dockerfile`). Dokploy detecta el resto.

### Paso 4 · Variables de entorno (pestaña *Environment*)
Pega todas (con tus valores reales). **PUBLIC_BASE_URL con tu subdominio**:
```env
PUBLIC_BASE_URL=https://diagnostico.cupperlab.com
CUPPERLAB_PHONE=+34 600 000 000
CUPPERLAB_EMAIL=soporte@cupperlab.com
CUPPERLAB_CALENDLY=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=clientes@cupperlab.com
SMTP_PASS=xxxxxxxxxxxxxxxx
SMTP_FROM=clientes@cupperlab.com
LEAD_INBOX=soporte@cupperlab.com
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AQ...
GEMINI_MODEL=gemini-flash-latest
GOOGLE_PSI_API_KEY=AIza...
RATE_LIMIT_PER_HOUR=30
```

### Paso 5 · Dominio + puerto (pestaña *Domains*)
- **Host:** `diagnostico.cupperlab.com`
- **Container Port:** `8000`
- **HTTPS:** ON (Let's Encrypt) → Dokploy emite el certificado solo.

### Paso 6 · Volumen persistente (pestaña *Advanced → Volumes/Mounts*)
Monta un volumen para que los **leads y los PDFs** sobrevivan a los redeploys:
- **Mount path (contenedor):** `/srv/data`
- Tipo: volumen (o bind mount a una carpeta del host).

### Paso 7 · Deploy
Pulsa **Deploy**. La primera build tarda unos minutos (Chromium). Cuando termine:
- Abre `https://diagnostico.cupperlab.com`
- Prueba un análisis → resultado en pantalla + correo con PDF (adjunto + botón).

> Para actualizar tras un cambio: `git push` y en Dokploy pulsa **Redeploy** (o activa auto-deploy por webhook).

### Requisitos del servidor
- **≥ 2 GB RAM** (Chromium para el PDF). Con 1 GB puede fallar el render.

---

## 1. Prepara el `.env` de producción (en el servidor)

Copia `.env.example` a `.env` y rellena **con tus valores reales**. Lo importante:

```env
# Dominio público (para el enlace "Abrir informe" del correo) — SIN barra final
PUBLIC_BASE_URL=https://diagnostico.cupperlab.com

# Contacto que sale en la landing y el correo
CUPPERLAB_PHONE=+34 600 000 000
CUPPERLAB_EMAIL=soporte@cupperlab.com
CUPPERLAB_CALENDLY=https://calendly.com/tucuenta

# SMTP (envío del informe)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=clientes@cupperlab.com
SMTP_PASS=xxxxxxxxxxxxxxxx
SMTP_FROM=clientes@cupperlab.com
LEAD_INBOX=soporte@cupperlab.com

# IA (ChatGPT navega en vivo) y Gemini (gratis)
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AQ...
GEMINI_MODEL=gemini-flash-latest

# Velocidad
GOOGLE_PSI_API_KEY=AIza...

# (opcional) posición en Google exacta
SEARCH_PROVIDER=
SERPER_API_KEY=
```

> ⚠️ Cambia `PUBLIC_BASE_URL` a tu dominio real. Si lo dejas en `localhost`, el botón
> "Abrir informe" del correo no funcionará para el cliente (el adjunto sí).

---

## 2. Sube el proyecto al servidor

Con git (recomendado) o por SFTP. **No subas** `.venv/`, `data/` ni `.env` con secretos
(ya están en `.dockerignore` / `.gitignore`); el `.env` créalo directamente en el servidor.

---

## 3. Arranca el contenedor

```bash
docker compose up -d --build
```

- La primera vez tarda unos minutos (descarga Chromium + dependencias del sistema).
- Queda escuchando en `http://IP-del-servidor:8000`.
- Los **leads** y los **PDFs generados** se guardan en `./data` (volumen persistente).

Comprobar que está vivo:
```bash
curl http://localhost:8000/salud
docker compose logs -f diagnostico
```

---

## 4. Conecta tu dominio con HTTPS

### Opción A — Plesk (con Docker)
1. Plesk → **Docker** → construye la imagen desde este `Dockerfile` (o usa `docker compose`).
2. En el dominio/subdominio (p. ej. `diagnostico.cupperlab.com`) → **Docker Proxy Rules**:
   reenvía `https://diagnostico.cupperlab.com` → contenedor **:8000**.
3. Activa **SSL (Let's Encrypt)** del subdominio en Plesk.

### Opción B — Nginx + Certbot (VPS propio)
`/etc/nginx/sites-available/diagnostico`:
```nginx
server {
  server_name diagnostico.cupperlab.com;
  client_max_body_size 20m;
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;   # el análisis tarda ~60s
  }
}
```
Luego:
```bash
ln -s /etc/nginx/sites-available/diagnostico /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d diagnostico.cupperlab.com
```

### Opción C — Caddy (SSL automático, lo más simple)
`Caddyfile`:
```
diagnostico.cupperlab.com {
  reverse_proxy 127.0.0.1:8000
}
```

> **DNS**: apunta el (sub)dominio (registro **A**) a la IP del servidor antes de emitir el SSL.

---

## 5. Actualizar tras un cambio
```bash
git pull        # o vuelve a subir los archivos
docker compose up -d --build
```

---

## Notas
- Rendimiento: un análisis tarda ~50-70 s (la velocidad de escritorio de PageSpeed es lenta);
  corre en segundo plano, el cliente ve la barra de progreso.
- Recursos: Chromium necesita algo de RAM. Recomendado **≥ 2 GB** de RAM en el servidor.
- Coste IA: ChatGPT (navegación) ~1-3 céntimos por análisis; Gemini y PageSpeed gratis.
- Rate-limit anti-abuso: `RATE_LIMIT_PER_HOUR` (por IP) en el `.env`.
