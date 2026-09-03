"""
Renderiza HTML a PDF con Chromium headless (Playwright). Fidelidad total al CSS
(gradientes, flex, fuentes) = calidad del informe premium de Cupperlab. Funciona
igual en Windows (maqueta) y en Docker/Plesk (produccion). Se llama en segundo
plano, asi que el arranque del navegador (~1 s) no afecta al cliente.
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright


def html_to_pdf(html: str) -> bytes | None:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--no-sandbox", "--disable-dev-shm-usage",
                "--force-device-scale-factor=2", "--high-dpi-support=1",
                "--font-render-hinting=none",
            ])
            # device_scale_factor 2 = raster (gradientes, sombras, logo) nitido, sin pixelado
            ctx = browser.new_context(device_scale_factor=2)
            page = ctx.new_page()
            page.set_content(html, wait_until="networkidle")
            page.emulate_media(media="print")
            pdf = page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                prefer_css_page_size=True,
            )
            browser.close()
            return pdf
    except Exception as exc:  # noqa: BLE001
        print(f"[pdf_render:ERROR] {exc}")
        return None
