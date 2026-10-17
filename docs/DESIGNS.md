# Making your own design

A design is what the cooler shows while `krakenlcd dash` runs: CPU and GPU
temperatures, load, pump speed, the time, whatever you like, updating live.
Each design is an ordinary **SVG file**, 320 × 320 pixels, with placeholders
like `{{ cpu }}` where the numbers go. You can draw it in Inkscape, Figma or
Illustrator, write it by hand, or ask an AI chat for one.

## In five minutes

```sh
krakenlcd design new mine     # a copy of the "simple" design, yours to change
krakenlcd dash mine           # put it on the cooler
```

Now open `~/.config/krakenlcd/designs/mine/design.svg` in Inkscape or a text
editor. Change a colour, move something, and **save**: the display redraws
within two seconds. Keep `krakenlcd dash mine` running while you work. A
mistake doesn't break anything. You get an error with the line number, and
the last good version stays on screen.

When you're happy, stop it with Ctrl-C. The design comes back on its own
after every reboot, like everything else `krakenlcd` shows.

In the GUI (`krakenlcd-gui`), use **Live design**: pick a design, change its
settings, or press **New design …** and **Edit**.

### No cooler at hand?

```sh
krakenlcd design preview mine --watch     # writes mine-preview.png on every save
krakenlcd design preview mine --demo      # with made-up values, e.g. for a screenshot
```

### Got a picture you like?

```sh
krakenlcd design new mine --image wallpaper.jpg
```

This makes a design with your picture as the background (scaled and cropped to
fit) and the temperatures on top.

## Where designs live

```
~/.config/krakenlcd/designs/
├── mine/
│   ├── design.svg          the design
│   ├── background.png      any picture it uses: <image href="background.png">
│   └── MyFont.ttf          any font it uses, picked up automatically
└── quick.svg               a single .svg works too
```

`krakenlcd design list` shows everything that's there, and
`krakenlcd design folder --open` opens the folder. To share a design, zip its
folder. To install someone else's, unzip it into the folder.

If a design of yours has the same name as a built-in one (`swiss`, `simple`,
`duo`), yours wins.

## Placeholders

Anything between `{{` and `}}` is worked out every time the display
refreshes, and the result is put in its place. It works in text and in
attributes alike:

```xml
<text x="160" y="180">{{ cpu }}°</text>
<path d="{{ arc(160, 160, 140, -135, map(cpu, 20, 100, -135, 135)) }}"/>
<circle fill="{{ '#ff3b30' if cpu >= 80 else '#ffffff' }}" …/>
```

### What you can show

| Name        | What                           | Unit |
|-------------|--------------------------------|------|
| `cpu`       | CPU temperature                | °C   |
| `gpu`       | GPU temperature                | °C   |
| `liquid`    | coolant temperature            | °C   |
| `cpu_load`  | CPU load, all cores            | %    |
| `gpu_load`  | GPU load                       | %    |
| `ram`       | memory in use                  | %    |
| `ram_used`  | memory in use                  | GiB  |
| `ram_total` | memory installed               | GiB  |
| `pump`      | pump speed                     | rpm  |
| `fan`       | radiator fan speed             | rpm  |
| `time`      | the time, `14:05`              |      |
| `date`      | the date, `2026-10-17`         |      |

`krakenlcd sensors` prints them all with this machine's current values.

Temperatures arrive in whole degrees (tenths with the setting
`data-decimals="1"`), percentages in whole percent, and speeds in steps of
10 rpm. They are also smoothed over a few seconds (`--smooth`). That keeps the
display calm, and it means the cooler is only sent a new picture when
something on it actually changes.

A sensor that isn't there, such as no GPU, shows as `--`. Use `cpu or 0` if a
design needs a number either way.

### Functions

| Function | What it gives |
|----------|---------------|
| `fmt(value, decimals)` | the number as text with that many decimals, e.g. `fmt(cpu, 1)` → `67.0` |
| `map(value, from_low, from_high, to_low, to_high)` | rescales: `map(cpu, 20, 100, 0, 1)` is 0 at 20 °C and 1 at 100 °C, clamped at both ends |
| `arc(cx, cy, r, from, to)` | path data for a circular arc, for `<path d="…">`; angles in degrees, **0 is the top, clockwise** |
| `ramp(t, colour1, colour2, …)` | a colour along a list, `t` from 0 (first) to 1 (last) |
| `mix(colour1, colour2, t)` | a blend of two colours |
| `clamp(value, low, high)`, `min(…)`, `max(…)`, `abs(…)`, `round(value, decimals)` | the usual |
| `upper(text)`, `lower(text)`, `len(text)` | for text |
| `now('%A %H:%M')` | the time in any [strftime format](https://strftime.org) |
| `sensor(name)` | a sensor whose name is in a setting, e.g. `sensor(big)` |

Expressions work like in Python or JavaScript: `+ - * /`, comparisons,
`and`, `or`, `not`, and `a if condition else b`. Text goes in single quotes
inside attributes: `fill="{{ 'red' if cpu > 80 else 'white' }}"`.

That is all there is. Placeholders can't read files, run programs or reach
the network, so a design from a stranger can only ever draw a picture.

## Settings

Attributes on the `<svg>` element that start with `data-` are settings:

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"
     data-hot="80" data-accent="#2fd4ff">
  …
  <text fill="{{ accent }}">{{ cpu }}</text>
  <circle fill="{{ 'red' if cpu >= hot else accent }}" …/>
```

Each one becomes a name you can use in placeholders (`data-scale-min`
becomes `scale_min`). Settings can be changed without opening the file:

```sh
krakenlcd dash mine --set hot=75 --set accent=#ff00ff
```

The GUI shows a field for each one, with a colour picker for colours. Put
the things people are likely to want to change into settings, and your design
can be adjusted by someone who never opens it.

`data-decimals` is special: it sets how precisely temperatures are passed on
(0 or 1). `data-author` is shown as the author rather than used as a setting.
`<title>` and `<desc>` give the design its name and description in lists.

## Fonts

A font is looked for in this order:

1. `.ttf`/`.otf` files next to `design.svg` (or in a `fonts/` folder there)
2. Chakra Petch and JetBrains Mono, which come with krakenlcd
3. fonts installed on your system

If a font can't be found, you get a warning and the text falls back to your
system's sans-serif. If you share a design, put its fonts in the folder, as
long as their licence allows it.

## Recipes

**A gauge arc** (270°, from 7:30 round to 4:30):

```xml
<path d="{{ arc(160, 160, 140, -135, 135) }}" stroke="#222" stroke-width="12" fill="none"/>
<path d="{{ arc(160, 160, 140, -135, map(cpu, 20, 100, -135, 135)) }}"
      stroke="#2fd4ff" stroke-width="12" stroke-linecap="round" fill="none"/>
```

**A needle**: draw it pointing straight up, then turn it:

```xml
<g transform="rotate({{ map(cpu, 20, 100, -135, 135) }} 160 160)">
  <line x1="160" y1="160" x2="160" y2="40" stroke="#ff3b30" stroke-width="3"/>
</g>
```

**A bar** that fills from the left:

```xml
<rect x="60" y="250" width="200" height="8" rx="4" fill="#222"/>
<rect x="60" y="250" width="{{ map(ram, 0, 100, 0, 200) }}" height="8" rx="4" fill="#a98bff"/>
```

**Colour by temperature**: `fill="{{ ramp(map(cpu, 30, 90, 0, 1), '#2fd4ff', '#ffc83d', '#ff3b30') }}"`

**Show something only when hot**: `visibility="{{ 'visible' if cpu >= 85 else 'hidden' }}"`

**A clock**: `{{ time }}`, or `{{ now('%a %d.%m.') }}` for "Sat 17.10."

The built-in designs are worth reading. `krakenlcd design new copy --from
swiss` makes a copy of one to look at.

## With your tools

**Inkscape**: type `{{ cpu }}` straight into a text object. For anything
else, such as an arc's path or a colour, open the XML editor
(Edit → XML Editor, Ctrl+Shift+X), select the element and set the attribute
to a placeholder. Settings go on the root `svg` node in the same editor. Save
as *Inkscape SVG* or *Plain SVG*; both work.

**Figma / Penpot / Illustrator**: set the frame to 320 × 320, use
`{{ cpu }}` as text, export as SVG. In Figma, turn **off** "Outline text" in
the export settings, otherwise the placeholders become shapes and can't be
filled in. Afterwards, open the file in a text editor to add placeholders
to attributes if you need them.

**An AI chat**: something like this works well:

> Make an SVG for a round 320×320 px display (black outside the circle).
> It's a PC dashboard. Where values go, write placeholders in double braces:
> `{{ fmt(cpu) }}` CPU temperature, `{{ fmt(gpu) }}` GPU temperature,
> `{{ fmt(cpu_load) }}` CPU load in %. For a circular progress arc, use
> `d="{{ arc(160, 160, R, -135, map(cpu, 20, 100, -135, 135)) }}"` (angles in
> degrees, 0 = top, clockwise). Conditional colours:
> `{{ '#ff3b30' if cpu >= 80 else '#ffffff' }}`. Only SVG, no JavaScript, no
> external files. Use the fonts "Chakra Petch" and "JetBrains Mono".

Then save the answer as `~/.config/krakenlcd/designs/NAME.svg` and run
`krakenlcd dash NAME`.

## What it costs

Drawing a design takes about 5–15 ms. It's done by
[resvg](https://github.com/linebender/resvg), a renderer written in Rust.
Encoding the picture for the cooler takes a few more milliseconds. Both only
happen when a value that is actually shown changes. In between, a refresh
reads the sensors, fills in the placeholders and finds nothing new, which
takes well under a millisecond.

What makes a design slow:

- **Big pictures.** A 4000-pixel photo is decoded on every redraw. Scale
  pictures to 320 × 320. `--image` does this for you, and krakenlcd warns
  about oversized ones.
- **Large blurs and filters.** They're fine, but each adds a few
  milliseconds.
- **Seconds on a clock.** `now('%H:%M:%S')` changes every second, so the
  display is redrawn every second too.

## When something's wrong

Errors name the file, the line, and usually the fix:

```
error: ~/.config/krakenlcd/designs/mine/design.svg, line 23: there is no value called 'cpuu' -- did you mean 'cpu'?
```

- `krakenlcd design list` shows designs that fail to load and why.
- `krakenlcd design preview NAME` shows what would be drawn, without the
  cooler.
- If text shows up in the wrong font, look for a warning about the font name.
  Fonts are matched by family name, e.g. "Chakra Petch", not the file name.
- A placeholder showing `--` means the sensor isn't available on this
  machine. `krakenlcd sensors` tells you which ones are.
