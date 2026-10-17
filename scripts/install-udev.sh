#!/bin/sh
# Installs the udev rule that grants your user access to the Kraken Z.
# Fully revertible with scripts/uninstall-udev.sh
set -eu

RULE_SRC="$(CDPATH='' cd -- "$(dirname -- "$0")/../udev" && pwd)/99-nzxt-kraken-z.rules"
RULE_DST="/etc/udev/rules.d/99-nzxt-kraken-z.rules"

if [ ! -f "$RULE_SRC" ]; then
    echo "error: $RULE_SRC not found" >&2
    exit 1
fi

if [ -e "$RULE_DST" ]; then
    echo "note: $RULE_DST already exists, backing it up to $RULE_DST.bak"
    sudo cp -a "$RULE_DST" "$RULE_DST.bak"
fi

echo "installing $RULE_DST"
sudo install -m 0644 -o root -g root "$RULE_SRC" "$RULE_DST"

echo "reloading udev rules"
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=1e71
sudo udevadm trigger --subsystem-match=hidraw

sleep 1
echo
echo "resulting permissions:"
for f in /dev/hidraw*; do
    case "$(udevadm info -q property -n "$f" 2>/dev/null | grep -c '1e71\|3008')" in
        0) ;;
        *) getfacl -p "$f" 2>/dev/null | sed 's/^/  /' ;;
    esac
done
echo "done."
