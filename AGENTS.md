# AGENTS.md

Instructions for AI coding agents working on krakenlcd. Humans are welcome to
read along: the checklist at the end is the same one
[CONTRIBUTING.md](CONTRIBUTING.md) points to.

## What this is

A userspace driver, CLI and Qt GUI for the LCD on NZXT Kraken Z coolers
(Z53/Z63/Z73, USB `1e71:3008`) on Linux. Python ≥ 3.10. No kernel module.
Its main feature is **live designs**: SVG files with `{{ … }}` placeholders,
filled in with sensor readings and drawn on the cooler.

## Layout

| Path | What |
|------|------|
| `krakenlcd/lowlevel.py`, `device.py` | the protocol: HID reports over hidraw, image data over usbfs. **Standard library only.** |
| `krakenlcd/gif.py`, `imaging.py` | GIF codec, scaler and quantiser in pure Python, for the stdlib-only paths (`fill`) |
| `krakenlcd/media.py` | Pillow: re-encoding user images and GIFs, `encode_frame` for live frames |
| `krakenlcd/sensors.py` | everything a design can show (`SENSORS`, `TEXT`) and where it comes from |
| `krakenlcd/template.py` | the `{{ … }}` expression language. **This is a security boundary**, see below. |
| `krakenlcd/design.py` | finding, checking, font resolution, rendering (resvg), `Live` (change detection, hot reload) |
| `krakenlcd/designs/` | the built-in designs, shipped as package data |
| `krakenlcd/cli.py`, `gui.py` | the two front ends |
| `krakenlcd/state.py` | what `krakenlcd restore` replays at login |
| `krakenlcd/tempscreen.py` | the older single-number temperature screen (`krakenlcd temp`) |
| `tests/` | `unittest` tests, no hardware needed |
| `docs/DESIGNS.md` | the user guide for making designs: keep it in sync |
| `docs/PROTOCOL.md` | the wire protocol |

## Rules

**Safety of the hardware.** Only display reports are ever written (IDs
`0x10`, `0x74`, `0x30`, `0x32`, `0x36`, `0x38`). Never write the pump or fan
reports (`0x72`, `0x73`), not even "just to test".

**Never take the cooler away from the user.** The tests don't touch the
device, and you shouldn't either unless asked. A systemd user service
(`krakenlcd.service`) usually owns it. To check something visually, use
`krakenlcd design preview NAME --demo` or `krakenlcd dash --preview out.png`.
If you were asked to test on hardware: `systemctl --user stop
krakenlcd.service` first, use `--no-save` so the user's boot-time display
isn't overwritten, and start the service again afterwards.

**The template language is a sandbox.** Designs get downloaded and shared,
and opening one must never do more than draw a picture.
- Expressions are parsed with `ast` and compiled from an allow-list of nodes.
  Never `eval`/`exec` design content. Never allow attribute access,
  subscripts, comprehensions, lambdas, f-strings or keyword tricks.
- A function added to `FUNCTIONS` must be pure, must not touch files,
  processes or the network, and must bound its own cost (string sizes,
  exponents, loops).
- Anything that changes the evaluator gets a test in `tests/test_template.py`,
  including one in `Refused` for what it must not allow.

**Keep the live loop cheap.** `krakenlcd dash` runs for weeks. Per refresh:
read only the sensors the design uses, round them to their step, compare the
filled-in SVG text with the last one, and stop there if it is the same.
Anything new that runs every tick must cost microseconds, not milliseconds.
Expensive sources (like `nvidia-smi`) are cached.

**Dependencies.** The protocol layer stays standard-library only. Pillow and
resvg-py are for rendering, PyQt6 only for the GUI. A new dependency needs a
very good reason and prebuilt wheels for every Linux architecture.

**Backward compatibility of saved commands.** `~/.config/krakenlcd/state.json`
stores a display command's argv, and the service replays it at every login.
If you change the flags of a display command (`dash`, `temp`, `gif`, `fill`),
old argv must still parse. See `_LEGACY_DASH` in `cli.py`.

**Errors are for users, not programmers.** Say what is wrong, where (file and
line for designs), and how to fix it. `difflib` suggestions for typos are the
house style.

**Built-in designs** are 320 × 320, use only the bundled fonts (Chakra Petch,
JetBrains Mono), reference no external files, and draw without warnings.
`tests/test_design.py` checks the last one.

**Code style.** Match the surrounding code. Comments and docstrings explain
*why*, never restate *what* the next line does. No dead code, no commented-out
code. User-visible text in English.

## Before a pull request

Run all of it. CI runs the same on Python 3.10–3.14 and has to be green.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[gui]'                  # '.' alone if PyQt6 won't install

python -m unittest discover -s tests -v  # GUI tests skip without PyQt6
python -m compileall -q krakenlcd tests

krakenlcd --help >/dev/null
krakenlcd --version
krakenlcd sensors
krakenlcd design list                    # nothing may show as BROKEN
for d in swiss simple duo; do
    krakenlcd design preview "$d" --demo -o "/tmp/$d.png"
done                                     # and look at the PNGs

shellcheck install.sh scripts/*.sh       # if you touched a shell script;
                                         # CI pins shellcheck-py==0.11.0.1
```

Then check whichever of these apply:

- [ ] **Packaging.** New files that have to ship (designs, fonts) are covered
      by `[tool.setuptools.package-data]` in `pyproject.toml`. Verify with a
      normal install (`pip install .`), running the tests from *outside* the
      checkout: `cd /tmp && python -m unittest discover -s <repo>/tests`.
- [ ] **New sensor.** Add it to `sensors.SENSORS` with a sensible `step` and
      `demo` value, and to the table in `docs/DESIGNS.md`.
- [ ] **New template function.** Add it to `FUNCTIONS`, document it in
      `docs/DESIGNS.md`, and test it, refusals included.
- [ ] **A built-in design changed.** Put before/after previews in the pull
      request.
- [ ] **Display command flags changed.** An argv from the previous release
      still replays (`krakenlcd restore`).
- [ ] **Docs.** `CHANGELOG.md` (top section) for anything a user would
      notice, the README for new commands, `docs/DESIGNS.md` for anything
      design-facing.
- [ ] **Version.** For a release, bump `__version__` in
      `krakenlcd/__init__.py`. It is the only place: `pyproject.toml` and
      `krakenlcd --version` read it from there.
- [ ] **Hardware**, if you have it and were asked to: `krakenlcd dash NAME
      --once --no-save` shows the design, and `krakenlcd restore` still puts
      back the saved display.

## Commits and pull requests

Small commits that each make sense on their own, subject in the imperative
("Add …", "Fix …"), at most 72 characters, with a body that says why when it
isn't obvious. The pull request says what changed, why, how it was tested,
and whether there is hardware it was tried on.
