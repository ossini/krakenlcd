#!/usr/bin/env python3
"""
Kraken 2a "Swiss / Skala" — 1:1 Nachbau des HTML-Displays mit Pillow.
Ausgabe: 320 x 320 px, rundes Ziffernblatt auf transparentem Hintergrund.

Benoetigte Fonts (kostenlos, Google Fonts) neben das Script legen
oder Pfade unten anpassen:
    ChakraPetch-Bold.ttf
    JetBrainsMono-Regular.ttf
    JetBrainsMono-Bold.ttf

    python kraken_2a.py            -> kraken_2a.png (CPU 67 / GPU 71)
    python kraken_2a.py 84 79      -> eigene Werte
"""

import sys
from PIL import Image, ImageDraw, ImageFont

# ------------------------------------------------------------------ Konstanten
SIZE      = 320          # Displaykante in px (Kraken LCD 2.7" rund)
S         = 4            # Supersampling fuer weiche Kanten
PAPER     = (244, 244, 241, 255)   # #f4f4f1
INK       = (17, 18, 20, 255)      # #111214
TRACK     = (220, 220, 215, 255)   # #dcdcd7
RED       = (216, 52, 42, 255)     # #d8342a
TICK_MIN  = (169, 172, 168, 255)   # #a9aca8
GREY_SUB  = (92, 95, 101, 255)     # #5c5f65
GREY_TOP  = (154, 157, 159, 255)   # #9a9d9f

T_MIN, T_MAX = 30, 100   # Skalenbereich
HOT          = 80        # ab hier rot
SWEEP        = 270.0     # Bogenwinkel gesamt
START        = 225.0     # Kompasswinkel (0 = 12 Uhr, im Uhrzeigersinn) fuer T_MIN

FONT_BIG  = "ChakraPetch-Bold.ttf"
FONT_MONO = "JetBrainsMono-Regular.ttf"
FONT_MONOB= "JetBrainsMono-Bold.ttf"


def font(path, px):
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        print(f"! Font {path} nicht gefunden – Fallback", file=sys.stderr)
        return ImageFont.load_default()


def norm(t):
    return max(0.0, min(1.0, (t - T_MIN) / (T_MAX - T_MIN)))


def compass(t):
    """Temperatur -> Kompasswinkel (0 = 12 Uhr, im Uhrzeigersinn)."""
    return START + norm(t) * SWEEP


def pil_angle(a):
    """Kompasswinkel -> PIL-Winkel (0 = 3 Uhr, im Uhrzeigersinn)."""
    return a - 90.0


def spaced_text(draw, xy, text, fnt, fill, tracking, anchor_center=True, baseline_mid=True):
    """Text mit letter-spacing zeichnen, optional horizontal zentriert."""
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x, y = xy
    if anchor_center:
        x -= total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill, anchor="lm" if baseline_mid else "la")
        x += w + tracking
    return total


def render(cpu=67, gpu=71, state="NORMAL", fill_pct=None):
    W = SIZE * S
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = W / 2

    # --- Ziffernblatt ---------------------------------------------------
    d.ellipse([0, 0, W - 1, W - 1], fill=PAPER)

    # --- Bogen: inset 9px, Ringbreite 6px -------------------------------
    inset, ring = 9 * S, 6 * S
    r_out = c - inset
    box = [c - r_out + ring / 2, c - r_out + ring / 2,
           c + r_out - ring / 2, c + r_out - ring / 2]

    pct = norm(cpu) * 100 if fill_pct is None else max(0.0, min(100.0, fill_pct))
    a_fill = START + pct / 100 * SWEEP
    hot_now = cpu >= HOT

    d.arc(box, pil_angle(a_fill), pil_angle(START + SWEEP), fill=TRACK, width=int(ring))
    if pct > 0:
        d.arc(box, pil_angle(START), pil_angle(a_fill),
              fill=RED if hot_now else INK, width=int(ring))

    # --- Skala 30–100 °C, Zehnerstriche lang ----------------------------
    import math
    for t in range(T_MIN, T_MAX + 1, 5):
        major = t % 10 == 0
        h = (13 if major else 7) * S
        w = (1.5 if major else 1.0) * S
        col = RED if t >= HOT else (INK if major else TICK_MIN)
        r1 = c - 14 * S          # aeusseres Ende
        r0 = r1 - h              # inneres Ende
        rad = math.radians(compass(t) - 90)
        dx, dy = math.cos(rad), math.sin(rad)
        d.line([c + dx * r0, c + dy * r0, c + dx * r1, c + dy * r1],
               fill=col, width=max(1, int(round(w))))

    # --- Typografie -----------------------------------------------------
    f_big   = font(FONT_BIG, 112 * S)
    f_cap   = font(FONT_MONO, 10 * S)
    f_state = font(FONT_MONO, 9 * S)
    f_sub   = font(FONT_MONO, 11 * S)
    f_subb  = font(FONT_MONOB, 11 * S)

    # Mittelblock: Zahl (line-height .78) + 20px Abstand + Caption-Zeile
    num_h = 112 * 0.78 * S
    cap_h = 13 * S
    block = num_h + 20 * S + cap_h
    top = (W - block) / 2

    d.text((c, top + num_h / 2), str(int(round(cpu))), font=f_big, fill=INK, anchor="mm")

    cap_y = top + num_h + 20 * S + cap_h / 2
    cap_w = spaced_text(d, (c, cap_y), "CPU °C", f_cap, INK, 0.28 * 10 * S)
    for sign in (-1, 1):
        x0 = c + sign * (cap_w / 2 + 8 * S)
        x1 = x0 + sign * 26 * S
        d.line([x0, cap_y, x1, cap_y], fill=INK, width=max(1, S))

    # Statuszeile: top 44px
    spaced_text(d, (c, 44 * S + 6.5 * S), state, f_state, GREY_TOP, 0.30 * 9 * S)

    # Fusszeile: bottom 44px  ("GPU " grau + Wert fett schwarz)
    y_sub = W - 44 * S - 6.5 * S
    lbl, val = "GPU ", str(int(round(gpu)))
    w_lbl = sum(d.textlength(ch, font=f_sub) for ch in lbl) + 0.10 * 11 * S * len(lbl)
    w_val = d.textlength(val, font=f_subb)
    x = c - (w_lbl + w_val) / 2
    x += spaced_text(d, (x, y_sub), lbl, f_sub, GREY_SUB, 0.10 * 11 * S, anchor_center=False)
    d.text((x, y_sub), val, font=f_subb, fill=INK, anchor="lm")

    return img.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == "__main__":
    cpu = float(sys.argv[1]) if len(sys.argv) > 1 else 67
    gpu = float(sys.argv[2]) if len(sys.argv) > 2 else 71
    st = "THERMAL LIMIT" if cpu >= HOT else "UNTER LAST" if cpu >= 68 else "NORMAL" if cpu >= 52 else "LEERLAUF"
    render(cpu, gpu, st).save("kraken_2a.png")
    print("kraken_2a.png geschrieben")
