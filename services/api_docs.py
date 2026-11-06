"""API documentation pages that work on a network with no internet access.

FastAPI's defaults load Swagger UI from cdn.jsdelivr.net, which renders a blank
page wherever that is blocked, and would be an outbound call a security review
has to explain anywhere else. The frontend build copies Swagger UI into
frontend/dist/vendor/swagger-ui (see frontend/vite.config.js). A checkout with no
build gets a page that links to the spec rather than a call to a CDN.
"""

from pathlib import Path

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

_ASSETS = Path(__file__).resolve().parent.parent / "frontend" / "dist" / "vendor" / "swagger-ui"
_ASSET_URL = "/vendor/swagger-ui"

# Swagger UI starts from one inline script and styles itself inline.
_DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
)


def docs_page(title: str, openapi_url: str) -> HTMLResponse:
    if (_ASSETS / "swagger-ui-bundle.js").is_file():
        page = get_swagger_ui_html(
            openapi_url=openapi_url,
            title=title,
            swagger_js_url=f"{_ASSET_URL}/swagger-ui-bundle.js",
            swagger_css_url=f"{_ASSET_URL}/swagger-ui.css",
            swagger_favicon_url="/favicon.ico",
        )
    else:
        page = HTMLResponse(
            f"<!doctype html><title>{title}</title><h1>{title}</h1>"
            "<p>The interactive page ships with the frontend build, which this install "
            f"does not have. The API description is at <a href=\"{openapi_url}\">{openapi_url}</a>.</p>"
        )
    page.headers["Content-Security-Policy"] = _DOCS_CSP
    return page
