#!/bin/sh
# krakenlcd installer.
#
#   curl -fsSL https://raw.githubusercontent.com/ossini/krakenlcd/main/install.sh | sh
#
# Installs into an isolated virtualenv under ~/.local/share/krakenlcd, links the
# two commands into ~/.local/bin, and installs the udev rule that grants your
# user access to the cooler.  Nothing else on the system is touched, and
# `install.sh --uninstall` takes all of it back out again.
#
# Options (append after `| sh -s --`, e.g. `... | sh -s -- --no-gui`):
#
#   --no-gui        skip PyQt6, install the CLI only
#   --no-udev       skip the udev rule (the tools then need root)
#   --uninstall     remove everything this script installed
#   --ref REF       branch, tag or commit to install (default: main)
#   --repo USER/REPO source repository (default: ossini/krakenlcd)
#   --prefix DIR    base for bin/ and share/ (default: ~/.local)
#   --help
#
# The same knobs exist as environment variables: KRAKENLCD_REF, KRAKENLCD_REPO,
# KRAKENLCD_PREFIX, PYTHON.
set -eu

REPO="${KRAKENLCD_REPO:-ossini/krakenlcd}"
REF="${KRAKENLCD_REF:-main}"
PREFIX="${KRAKENLCD_PREFIX:-$HOME/.local}"
WITH_GUI=1
WITH_UDEV=1
WITH_SERVICE=1
ACTION=install
RULE_DST="/etc/udev/rules.d/99-nzxt-kraken-z.rules"
UNIT_NAME="krakenlcd.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# ---------------------------------------------------------------------------
# output

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B="$(printf '\033[1m')"; DIM="$(printf '\033[2m')"
    RED="$(printf '\033[31m')"; YEL="$(printf '\033[33m')"
    GRN="$(printf '\033[32m')"; R="$(printf '\033[0m')"
else
    B=''; DIM=''; RED=''; YEL=''; GRN=''; R=''
fi

say()  { printf '%s==>%s %s\n' "$B" "$R" "$*"; }
note() { printf '    %s%s%s\n' "$DIM" "$*" "$R"; }
warn() { printf '%swarning:%s %s\n' "$YEL" "$R" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$RED" "$R" "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
krakenlcd installer.

  curl -fsSL https://raw.githubusercontent.com/ossini/krakenlcd/main/install.sh | sh

Installs into an isolated virtualenv under ~/.local/share/krakenlcd, links the
two commands into ~/.local/bin, and installs the udev rule that grants your
user access to the cooler.

Options (append after `| sh -s --`, e.g. `... | sh -s -- --no-gui`):

  --no-gui         skip PyQt6, install the CLI only
  --no-udev        skip the udev rule (the tools then need root)
  --no-service     skip the systemd user service that restores the display
  --uninstall      remove everything this script installed
  --ref REF        branch, tag or commit to install (default: main)
  --repo USER/REPO  source repository (default: ossini/krakenlcd)
  --prefix DIR     base for bin/ and share/ (default: ~/.local)
  -h, --help       this text

Environment: KRAKENLCD_REF, KRAKENLCD_REPO, KRAKENLCD_PREFIX, PYTHON, NO_COLOR.
EOF
    exit 0
}

# ---------------------------------------------------------------------------
# arguments

while [ $# -gt 0 ]; do
    case "$1" in
        --no-gui)     WITH_GUI=0 ;;
        --no-udev)    WITH_UDEV=0 ;;
        --no-service) WITH_SERVICE=0 ;;
        --uninstall) ACTION=uninstall ;;
        --ref)       REF="${2:?--ref needs a value}"; shift ;;
        --repo)      REPO="${2:?--repo needs a value}"; shift ;;
        --prefix)    PREFIX="${2:?--prefix needs a value}"; shift ;;
        --ref=*)     REF="${1#*=}" ;;
        --repo=*)    REPO="${1#*=}" ;;
        --prefix=*)  PREFIX="${1#*=}" ;;
        -h|--help)   usage ;;
        *)           die "unknown option: $1  (try --help)" ;;
    esac
    shift
done

BINDIR="$PREFIX/bin"
DATADIR="$PREFIX/share/krakenlcd"
VENV="$DATADIR/venv"
SRCDIR="$DATADIR/src"

# ---------------------------------------------------------------------------
# privilege escalation, used only for the udev rule

if [ "$(id -u)" = 0 ]; then
    SUDO=''
elif command -v sudo >/dev/null 2>&1; then
    SUDO='sudo'
elif command -v doas >/dev/null 2>&1; then
    SUDO='doas'
else
    SUDO=''
fi

# ---------------------------------------------------------------------------
# uninstall

remove_udev_rule() {
    [ -e "$RULE_DST" ] || [ -e "$RULE_DST.bak" ] || return 0
    if [ -z "$SUDO" ] && [ "$(id -u)" != 0 ]; then
        warn "no sudo/doas found, leaving $RULE_DST in place"
        return 0
    fi
    if [ -e "$RULE_DST.bak" ]; then
        note "restoring $RULE_DST from backup"
        $SUDO mv "$RULE_DST.bak" "$RULE_DST"
    else
        note "removing $RULE_DST"
        $SUDO rm -f "$RULE_DST"
    fi
    $SUDO udevadm control --reload-rules 2>/dev/null || true
    $SUDO udevadm trigger --subsystem-match=usb --attr-match=idVendor=1e71 2>/dev/null || true
    $SUDO udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
}

if [ "$ACTION" = uninstall ]; then
    # The installed copy of this script lives inside the directory we are about
    # to delete, and the shell reads the file as it goes.  Re-run from a copy
    # outside it, unlinked right away so nothing is left behind.
    if [ -z "${KRAKENLCD_REEXEC:-}" ]; then
        case "$0" in
            "$DATADIR"/*)
                SELF="$(mktemp)"
                cp "$0" "$SELF"
                set -- --uninstall --prefix "$PREFIX"
                [ "$WITH_UDEV" = 1 ] || set -- "$@" --no-udev
                KRAKENLCD_REEXEC="$SELF"
                export KRAKENLCD_REEXEC
                exec sh "$SELF" "$@"
                ;;
        esac
    elif [ "$KRAKENLCD_REEXEC" = "$0" ]; then
        rm -f "$0"   # already open; Linux keeps the inode alive until we exit
    fi

    say "uninstalling krakenlcd"
    if [ -f "$UNIT_DIR/$UNIT_NAME" ]; then
        note "disabling $UNIT_NAME"
        systemctl --user disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
        rm -f "$UNIT_DIR/$UNIT_NAME"
        systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
    for cmd in krakenlcd krakenlcd-gui; do
        if [ -L "$BINDIR/$cmd" ] || [ -f "$BINDIR/$cmd" ]; then
            note "removing $BINDIR/$cmd"
            rm -f "$BINDIR/$cmd"
        fi
    done
    if [ -d "$DATADIR" ]; then
        note "removing $DATADIR"
        rm -rf "$DATADIR"
    fi
    if [ "$WITH_UDEV" = 1 ]; then
        remove_udev_rule
    fi
    note "kept: ${XDG_CONFIG_HOME:-$HOME/.config}/krakenlcd (what to show at boot)"
    note "      remove it with 'rm -r' if you want that gone too"
    say "${GRN}done${R} — device permissions are back to kernel defaults"
    exit 0
fi

# ---------------------------------------------------------------------------
# environment checks

[ "$(uname -s)" = Linux ] || die "krakenlcd is Linux-only (found $(uname -s))"

if [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ]; then
    warn "running as root — this installs into /root, not $SUDO_USER's home."
    warn "run it as your normal user instead; it asks for sudo only for the udev rule."
fi

find_python() {
    for p in ${PYTHON:-} python3 python3.13 python3.12 python3.11 python3.10; do
        command -v "$p" >/dev/null 2>&1 || continue
        "$p" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
            2>/dev/null && { echo "$p"; return 0; }
    done
    return 1
}

PY="$(find_python)" || die "no Python 3.10+ found. Install python3 (or set PYTHON=/path/to/python3)."
note "python: $(command -v "$PY") ($("$PY" -c 'import platform; print(platform.python_version())'))"

if ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    die "$PY has no venv/ensurepip module.
    Debian/Ubuntu: sudo apt install python3-venv
    Fedora:        sudo dnf install python3-libs
    openSUSE:      sudo zypper install python3-venv"
fi

if command -v curl >/dev/null 2>&1; then
    FETCH='curl -fsSL'
elif command -v wget >/dev/null 2>&1; then
    FETCH='wget -qO-'
else
    FETCH=''
fi

# ---------------------------------------------------------------------------
# source: a local checkout if we are sitting in one, otherwise a tarball

SELFDIR=''
case "${0:-}" in
    ''|sh|-|bash|dash) ;;
    *) SELFDIR="$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)" ;;
esac

TMPDIR_="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_"; }
trap cleanup EXIT INT TERM

if [ -n "$SELFDIR" ] && [ -f "$SELFDIR/pyproject.toml" ] && [ -d "$SELFDIR/krakenlcd" ]; then
    say "installing from local checkout $SELFDIR"
    FROM="$SELFDIR"
else
    [ -n "$FETCH" ] || die "need curl or wget to download the source"
    say "downloading $REPO@$REF"
    $FETCH "https://codeload.github.com/$REPO/tar.gz/$REF" \
        | tar -xz -C "$TMPDIR_" --strip-components=1 2>/dev/null \
        || die "download failed — check --repo/--ref (repo=$REPO ref=$REF)"
    [ -f "$TMPDIR_/pyproject.toml" ] || die "downloaded archive does not look like krakenlcd"
    FROM="$TMPDIR_"
fi

# ---------------------------------------------------------------------------
# install

say "installing into $VENV"
mkdir -p "$BINDIR" "$DATADIR"

# Keep a copy of the source next to the venv: it is what pip installs from,
# what --uninstall runs from, and where the udev rule comes from later.
rm -rf "$SRCDIR"
mkdir -p "$SRCDIR"
(cd "$FROM" && tar -cf - \
    --exclude=.git --exclude=__pycache__ --exclude='*.egg-info' \
    --exclude=.venv --exclude=venv --exclude=build --exclude=dist .) \
  | (cd "$SRCDIR" && tar -xf -)

if [ -d "$VENV" ]; then
    note "reusing existing virtualenv"
else
    "$PY" -m venv "$VENV" || die "could not create the virtualenv at $VENV"
fi

PIP="$VENV/bin/pip"
"$PIP" install --quiet --upgrade pip >/dev/null 2>&1 || true

if [ "$WITH_GUI" = 1 ]; then
    say "installing krakenlcd with the GUI (this pulls in PyQt6, ~60 MB)"
    if ! "$PIP" install --quiet --upgrade "${SRCDIR}[gui]"; then
        warn "install with the GUI failed — falling back to the CLI only"
        WITH_GUI=0
        "$PIP" install --quiet --upgrade "$SRCDIR" || die "pip install failed"
    fi
else
    say "installing krakenlcd (CLI only)"
    "$PIP" install --quiet --upgrade "$SRCDIR" || die "pip install failed"
fi

# krakenlcd-gui exists in the venv either way; only link it if PyQt6 is actually
# there, so a CLI-only install does not leave a command that dies on import.
if [ "$WITH_GUI" = 1 ] && ! "$VENV/bin/python" -c 'import PyQt6' >/dev/null 2>&1; then
    warn "PyQt6 is not importable in the venv — not linking krakenlcd-gui"
    WITH_GUI=0
fi

rm -f "$BINDIR/krakenlcd-gui"
for cmd in krakenlcd $([ "$WITH_GUI" = 1 ] && echo krakenlcd-gui); do
    [ -x "$VENV/bin/$cmd" ] || continue
    ln -sf "$VENV/bin/$cmd" "$BINDIR/$cmd"
    note "$BINDIR/$cmd -> $VENV/bin/$cmd"
done

# ---------------------------------------------------------------------------
# udev rule

if [ "$WITH_UDEV" = 1 ]; then
    RULE_SRC="$SRCDIR/udev/99-nzxt-kraken-z.rules"
    [ -f "$RULE_SRC" ] || die "$RULE_SRC missing from the source tree"

    if [ -z "$SUDO" ] && [ "$(id -u)" != 0 ]; then
        warn "no sudo/doas found — skipping the udev rule."
        warn "install it later as root: install -m 0644 $RULE_SRC $RULE_DST"
    elif [ -e "$RULE_DST" ] && cmp -s "$RULE_SRC" "$RULE_DST"; then
        say "udev rule already up to date"
    else
        say "installing the udev rule (needs root)"
        note "$RULE_DST — read/write access to device 1e71:3008 for the desktop user"
        # Not allowed to abort the run: if the password prompt is declined or
        # there is no terminal to ask on, the rest of the install is still
        # worth finishing, and the rule can be added afterwards.
        if (
            set -e
            if [ -e "$RULE_DST" ]; then
                $SUDO cp -a "$RULE_DST" "$RULE_DST.bak"
                note "existing file backed up to $RULE_DST.bak"
            fi
            $SUDO install -m 0644 -o root -g root "$RULE_SRC" "$RULE_DST"
            $SUDO udevadm control --reload-rules
            $SUDO udevadm trigger --subsystem-match=usb --attr-match=idVendor=1e71
            $SUDO udevadm trigger --subsystem-match=hidraw
        ); then
            note "replug the cooler once so the rule applies to it"
        else
            warn "could not install the udev rule — carrying on without it"
            note "run this later in a terminal:"
            note "  sudo install -m 0644 $RULE_SRC $RULE_DST"
            note "  sudo udevadm control --reload-rules && sudo udevadm trigger"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# systemd user service: puts the display back after a reboot

SERVICE_STATE=skipped
if [ "$WITH_SERVICE" = 1 ]; then
    UNIT_SRC="$SRCDIR/systemd/$UNIT_NAME"
    if [ ! -f "$UNIT_SRC" ]; then
        warn "$UNIT_SRC missing from the source tree — no service installed"
    elif ! command -v systemctl >/dev/null 2>&1; then
        warn "systemctl not found — skipping the autostart service"
        note "run 'krakenlcd restore' from your desktop's own autostart instead"
    else
        say "installing the user service that restores the display at login"
        mkdir -p "$UNIT_DIR"
        # The unit ships with %h/.local/bin; point it wherever we really put
        # the command, so --prefix keeps working.
        sed "s|^ExecStart=%h/.local/bin/krakenlcd|ExecStart=$BINDIR/krakenlcd|" \
            "$UNIT_SRC" > "$UNIT_DIR/$UNIT_NAME"
        mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/krakenlcd" \
                 "${XDG_CACHE_HOME:-$HOME/.cache}/krakenlcd"

        if systemctl --user daemon-reload >/dev/null 2>&1 &&
           systemctl --user enable "$UNIT_NAME" >/dev/null 2>&1; then
            SERVICE_STATE=enabled
            note "$UNIT_DIR/$UNIT_NAME — enabled, starts with your session"
        else
            SERVICE_STATE=manual
            warn "could not talk to the user systemd instance (no session bus?)"
            note "the unit is in place; enable it from a desktop session with:"
            note "  systemctl --user enable --now $UNIT_NAME"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# report

echo
say "${GRN}krakenlcd installed${R}"
if [ "$WITH_GUI" = 1 ]; then
    note "commands: krakenlcd, krakenlcd-gui"
else
    note "command: krakenlcd (no GUI)"
fi

case ":$PATH:" in
    *":$BINDIR:"*) ;;
    *)
        echo
        warn "$BINDIR is not in your PATH. Add it:"
        case "$(basename "${SHELL:-sh}")" in
            fish) note "fish_add_path $BINDIR" ;;
            zsh)  note "echo 'export PATH=\"$BINDIR:\$PATH\"' >> ~/.zshrc" ;;
            *)    note "echo 'export PATH=\"$BINDIR:\$PATH\"' >> ~/.bashrc" ;;
        esac
        ;;
esac

echo
if command -v lsusb >/dev/null 2>&1 && ! lsusb -d 1e71:3008 >/dev/null 2>&1; then
    warn "no Kraken Z (1e71:3008) found on the USB bus right now."
    note "Only the Z53/Z63/Z73 are supported; the 2023/Elite models use a different panel."
else
    note "Try it:  krakenlcd status"
    note "         krakenlcd dash          # live CPU/GPU dashboard"
    note "         krakenlcd design list   # more designs, and making your own"
    note "         krakenlcd liquid        # back to the firmware screen"
fi

if [ "$SERVICE_STATE" = enabled ]; then
    echo
    note "Whatever you set last comes back at the next login — the service"
    note "replays it. 'krakenlcd restore --show' prints what is remembered,"
    note "'krakenlcd restore --clear' forgets it again."
fi
if [ "$WITH_UDEV" = 1 ]; then
    echo
    note "If you get 'cannot open /dev/hidrawN', replug the cooler once so the"
    note "new udev rule applies to it, or reboot."
fi
echo
note "Uninstall:  $SRCDIR/install.sh --uninstall"
