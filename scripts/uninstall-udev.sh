#!/bin/sh
# Removes the udev rule installed by install-udev.sh and restores any backup.
set -eu

RULE_DST="/etc/udev/rules.d/99-nzxt-kraken-z.rules"

if [ -e "$RULE_DST.bak" ]; then
    echo "restoring previous $RULE_DST from backup"
    sudo mv "$RULE_DST.bak" "$RULE_DST"
elif [ -e "$RULE_DST" ]; then
    echo "removing $RULE_DST"
    sudo rm -f "$RULE_DST"
else
    echo "nothing to do, $RULE_DST is not installed"
fi

sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=1e71
sudo udevadm trigger --subsystem-match=hidraw
echo "done. Device permissions are back to kernel defaults (root only)."
