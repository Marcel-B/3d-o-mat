"""Freistellen für TRELLIS.2: Es hat keinen eigenen Hintergrund-Entferner, bei einem Foto ohne Transparenz würde das
ganze Bild zum Objekt. Deshalb bekommt es ein PNG mit echtem Alphakanal, auf das Objekt zugeschnitten und quadratisch.

- Hat das Bild schon Transparenz, bleibt sie, wie sie ist.
- Mit rembg (requirements-rembg.txt, auf der CPU des Containers) für Fotos mit beliebigem Hintergrund. Vorgabe ist
  isnet-general-use: auf einem 12-MP-Foto 9 s und 1,5 GB; birefnet-general brauchte 115 s und 9,5 GB, mehr als der
  Container hat (gemessen 2026-10-29, 4 Kerne).
- Sonst ein einfarbiger Hintergrund, vom Rand aus geflutet, wie gen.mjs cutout in MLX-Serve.
"""
import io
import threading

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

MAX_SIDE = 1024
# Größer braucht keiner der Schritte; ein Handyfoto (4000 px) kostet sonst nur Speicher und Zeit.
MAX_INPUT = 2048
_rembg = {}
_rembg_lock = threading.Lock()
# Immer nur ein Freistellen zugleich: jedes hält das Modell und das Bild im Speicher.
_cut_lock = threading.Lock()


def rembg_available() -> bool:
    try:
        import rembg  # noqa: F401
    except ImportError:
        return False
    return True


def _has_alpha(image: Image.Image) -> bool:
    if image.mode not in ("RGBA", "LA", "PA") and "transparency" not in image.info:
        return False
    alpha = np.asarray(image.convert("RGBA"))[:, :, 3]
    return float((alpha < 250).mean()) > 0.01


def _smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def flood(image: Image.Image, tolerance: int = 40) -> Image.Image:
    """Hintergrund in der Farbe des Randes (Median) entfernen, nur was mit dem Rand zusammenhängt."""
    rgb = np.asarray(image.convert("RGB")).astype(np.float32)
    h, w, _ = rgb.shape
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.abs(rgb - bg).max(axis=2)
    labels, _ = ndimage.label(dist < tolerance)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    is_bg = np.isin(labels, edge[edge > 0])
    alpha = np.where(is_bg, 0.0, 1.0)
    # Weiche Kante: Pixel bis 2 px neben dem Hintergrund bekommen Alpha nach ihrem Abstand zur Hintergrundfarbe,
    # und die Hintergrundfarbe wird aus ihnen herausgerechnet.
    near = ndimage.binary_dilation(is_bg, iterations=2) & ~is_bg
    alpha[near] = _smoothstep(tolerance * 0.5, tolerance * 2, dist[near])
    out = rgb.copy()
    soft = (alpha > 0) & (alpha < 1)
    a = alpha[soft][:, None]
    out[soft] = np.clip((rgb[soft] - bg * (1 - a)) / a, 0, 255)
    rgba = np.dstack([out, alpha * 255]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def _session(model: str):
    from rembg import new_session

    with _rembg_lock:
        if model not in _rembg:
            _rembg[model] = new_session(model)
        return _rembg[model]


def warm(model: str, log=print) -> None:
    """Lädt das Modell vorab (beim ersten Mal auch herunter), damit das erste Hochladen nicht darauf wartet."""
    if not rembg_available():
        return
    try:
        _session(model)
        log(f"rembg bereit ({model})")
    except Exception as error:  # noqa: BLE001 – ohne Netz oder mit falschem Namen geht es ohne weiter
        log(f"rembg-Modell {model} nicht ladbar: {error}")


def with_rembg(image: Image.Image, model: str) -> Image.Image:
    from rembg import remove

    return remove(image.convert("RGB"), session=_session(model)).convert("RGBA")


def frame(image: Image.Image, margin: float = 0.08) -> Image.Image:
    """Auf das Objekt zuschneiden, quadratisch mit etwas Rand, höchstens MAX_SIDE."""
    alpha = np.asarray(image)[:, :, 3]
    ys, xs = np.nonzero(alpha > 8)
    if len(xs) == 0:
        return image
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    side = int(max(x1 - x0, y1 - y0) * (1 + 2 * margin))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image.crop((x0, y0, x1, y1)), ((side - (x1 - x0)) // 2, (side - (y1 - y0)) // 2))
    if side > MAX_SIDE:
        canvas = canvas.resize((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return canvas


def cut(data: bytes, method: str = "auto", rembg_model: str = "birefnet-general") -> tuple[bytes, dict]:
    """Gibt das PNG für TRELLIS.2 und eine Auskunft zurück: method, width, height, subject (Anteil des Objekts)."""
    with _cut_lock:
        return _cut(data, method, rembg_model)


def _cut(data: bytes, method: str, rembg_model: str) -> tuple[bytes, dict]:
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    image.load()
    if max(image.size) > MAX_INPUT:
        image.thumbnail((MAX_INPUT, MAX_INPUT), Image.LANCZOS)
    if _has_alpha(image):
        used, result = "alpha", image.convert("RGBA")
    elif method == "none":
        used, result = "none", image.convert("RGBA")
    elif method == "rembg" or (method == "auto" and rembg_available()):
        used, result = "rembg", with_rembg(image, rembg_model)
    else:
        used, result = "flood", flood(image)
    subject = float((np.asarray(result)[:, :, 3] > 8).mean())
    note = None
    # Ein Objekt so hell wie sein Hintergrund flutet mit weg: dann lieber das Bild, wie es ist.
    if used in ("flood", "rembg") and subject < 0.02:
        used, result, note = "none", image.convert("RGBA"), "Freistellen fand kein Objekt; das Bild geht unverändert an TRELLIS.2"
        subject = 1.0
    elif used == "flood" and subject > 0.97:
        note = "Kein einfarbiger Hintergrund gefunden; für Fotos rembg installieren (requirements-rembg.txt)"
    result = frame(result) if used != "none" else result
    buffer = io.BytesIO()
    result.save(buffer, "PNG")
    return buffer.getvalue(), {"method": used, "width": result.width, "height": result.height,
                               "subject": round(subject, 3), "note": note}
