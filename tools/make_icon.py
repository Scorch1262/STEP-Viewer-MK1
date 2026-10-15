"""Erzeugt das Programm-Icon (PNG, ICO für Windows, ICNS für macOS).

Aufruf:  python tools/make_icon.py
Ergebnis: assets/icon.png, assets/icon_256.png, assets/icon.ico, assets/icon.icns
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
S = 1024  # Arbeitsgröße
SS = 4  # Supersampling für glatte Kanten


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def make_icon() -> Image.Image:
    W = S * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # Hintergrund: abgerundetes Quadrat mit Verlauf
    margin = int(W * 0.06)
    radius = int(W * 0.21)
    grad = Image.new("RGBA", (W, W))
    gd = ImageDraw.Draw(grad)
    top, bottom = (52, 60, 76), (20, 22, 28)
    for y in range(W):
        gd.line([(0, y), (W, y)], fill=lerp(top, bottom, y / W) + (255,))
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle([margin, margin, W - margin, W - margin], radius, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    cx, cy = W / 2, W * 0.53

    # Umlaufbahn (Drehen im Raum) – hinterer Teil
    ring_w = int(W * 0.022)
    ring_box = [cx - W * 0.36, cy - W * 0.13, cx + W * 0.36, cy + W * 0.13]
    d.arc(ring_box, 180, 360, fill=(255, 140, 26, 150), width=ring_w)

    # Isometrischer Würfel
    a = W * 0.235  # Kantenlänge
    c30, s30 = math.cos(math.radians(30)), math.sin(math.radians(30))
    # Eckpunkte: ft = vordere obere Ecke
    r = (a * c30, -a * s30)
    l = (-a * c30, -a * s30)
    ft = (cx, cy - a * 0.15)
    lt = (ft[0] + l[0], ft[1] + l[1])
    rt = (ft[0] + r[0], ft[1] + r[1])
    bt = (lt[0] + r[0], lt[1] + r[1])
    fb = (ft[0], ft[1] + a)
    lb = (lt[0], lt[1] + a)
    rb = (rt[0], rt[1] + a)

    top_face = [ft, rt, bt, lt]
    left_face = [ft, lt, lb, fb]
    right_face = [ft, rt, rb, fb]

    # Schatten
    shadow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.ellipse([cx - a * 1.05, fb[1] - a * 0.10, cx + a * 1.05, fb[1] + a * 0.22], fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(W * 0.02))
    img.alpha_composite(shadow)
    d = ImageDraw.Draw(img)

    d.polygon(left_face, fill=(176, 186, 204, 255))
    d.polygon(right_face, fill=(120, 130, 150, 255))
    d.polygon(top_face, fill=(255, 140, 26, 255))

    # Kanten wie in der Ansicht "Schattiert + Kanten"
    edge = (18, 20, 26, 255)
    ew = int(W * 0.012)
    for face in (top_face, left_face, right_face):
        d.line(face + [face[0]], fill=edge, width=ew, joint="curve")

    # Bohrung in der Deckfläche (Ellipse im Iso-Maß)
    hc = ((ft[0] + bt[0]) / 2, (ft[1] + bt[1]) / 2)
    hr = a * 0.30
    d.ellipse([hc[0] - hr * c30 * 1.15, hc[1] - hr * 0.58, hc[0] + hr * c30 * 1.15, hc[1] + hr * 0.58],
              fill=(150, 72, 8, 255), outline=edge, width=ew)

    # Umlaufbahn – vorderer Teil mit Pfeilspitze
    d.arc(ring_box, 0, 180, fill=(255, 168, 70, 255), width=ring_w)
    ang = math.radians(150)
    ax = cx + (ring_box[2] - ring_box[0]) / 2 * math.cos(ang)
    ay = cy + (ring_box[3] - ring_box[1]) / 2 * math.sin(ang)
    hs = W * 0.05
    d.polygon([(ax - hs * 0.9, ay - hs * 0.2), (ax + hs * 0.4, ay - hs * 0.9), (ax + hs * 0.2, ay + hs * 0.6)],
              fill=(255, 168, 70, 255))

    img = img.resize((S, S), Image.LANCZOS)
    return img


def main():
    os.makedirs(ASSETS, exist_ok=True)
    icon = make_icon()
    icon.resize((512, 512), Image.LANCZOS).save(os.path.join(ASSETS, "icon.png"))
    icon.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "icon_256.png"))
    icon.save(os.path.join(ASSETS, "icon.ico"),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(os.path.join(ASSETS, "icon.icns"))
    print("Icons erzeugt in", ASSETS)


if __name__ == "__main__":
    main()
