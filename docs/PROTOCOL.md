# NZXT Kraken Z display protocol

Everything below was verified against a Kraken Z53 (`1e71:3008`, firmware
1.5.11) and cross-checked against liquidctl's `KrakenZ3` driver. It is enough
to write an independent implementation.

Nothing here is official. NZXT publishes no specification.

## Transport

The cooler exposes two interfaces that matter:

| Interface | Class | How to reach it | Carries |
|---|---|---|---|
| 1 | HID | `/dev/hidrawN` | control and status, 64-byte reports |
| 0 | vendor-specific | `usbfs`, bulk OUT endpoint `0x02` | image data |

Interface 1 stays bound to `usbhid`; you just `read()`/`write()` the character
device. Interface 0 has no kernel driver, so it is claimed with
`USBDEVFS_CLAIMINTERFACE` and driven with `USBDEVFS_BULK`.

Every HID report is exactly 64 bytes. Byte 0 is the report ID, byte 1 a
sub-command. A reply arrives as report ID + 1 with the same sub-command in
byte 1, and **byte 14 is the result code — `0x01` means accepted**.

The device also emits unsolicited status reports, and other software
(CoolerControl, liquidctl) may be reading the same queue. Match replies on
both the report ID *and* the sub-command; anything else, discard and keep
reading.

## Reports

### `0x10 0x01` — firmware version

Reply `0x11`: bytes 16, 17, 18 are major, minor, patch.

### `0x74 0x01` — status

Reply `0x75`:

| Offset | Meaning |
|---|---|
| 15 | liquid temperature, whole degrees |
| 16 | liquid temperature, tenths |
| 17–18 | pump RPM, 16-bit little-endian |
| 19 | pump duty, percent |
| 23–24 | fan RPM, 16-bit little-endian |
| 25 | fan duty, percent |

If the `nzxt-kraken3` kernel driver is loaded, reading the temperature from
`/sys/class/hwmon/hwmon*/temp1_input` is preferable — it is a plain sysfs read
and never competes for the report queue.

### `0x30 0x01` — query LCD

Reply `0x31`:

| Offset | Meaning |
|---|---|
| 20–21 | width, 16-bit little-endian (320) |
| 22–23 | height, 16-bit little-endian (320) |
| 0x18 | brightness, 0–100 |
| 0x1A | orientation in quarter turns: 0, 1, 2, 3 → 0°, 90°, 180°, 270° |

### `0x30 0x02` — set brightness and orientation

```
30 02 01 <brightness 0..100> 00 00 01 <orientation 0..3>
```

> **Both values live in one report.** You cannot set brightness without also
> writing orientation. Passing a default of `0` for the field you did not mean
> to touch silently rotates the user's display. Always read the current value
> back with `0x30 0x01` first.

### `0x30 0x04 <bucket>` — query bucket

Reply `0x31`, from byte 14: bucket index, asset index, `0x02`, start address
(16-bit LE, KiB), size (16-bit LE, KiB), then two flag bytes. A bucket whose
bytes from 15 onward are all zero is unoccupied.

### `0x32 0x01` — set up a bucket for transfer

```
32 01 <bucket> <bucket+1> <addr_lo> <addr_hi> <size_lo> <size_hi> 01
```

Address and size are in **KiB**, 16-bit little-endian. The fourth byte is the
*end* bucket index, i.e. `bucket + 1` — not a constant.

### `0x32 0x02 <bucket>` — delete a bucket

A bucket that still holds data needs deleting **twice**. The bucket currently
on screen cannot be deleted at all; move the display elsewhere first.

### `0x36` — data transfer

| Report | Meaning |
|---|---|
| `36 03` | purpose unknown; must precede a transfer |
| `36 01 <bucket>` | arm the bucket for writing |
| `36 02` | end of transfer |

### `0x38 0x01 <mode> <bucket>` — set display mode

| Mode | Shows |
|---|---|
| `0x02` | the firmware's own liquid-temperature screen (bucket byte ignored) |
| `0x04` | the image or GIF stored in `<bucket>` |

Reply `0x39`, byte 14 `0x01` on success.

## Uploading an image

The order matters, and four details are easy to get wrong. Get any of them
wrong and **every status byte still reads `0x01`** while the panel keeps
showing the old image — there is no error to go on.

1. `36 03`
2. Delete the target bucket (`32 02 <bucket>`), twice if it held data.
3. `32 01 <bucket> <bucket+1> <addr> <size> 01`
4. `36 01 <bucket>`
5. **Two separate bulk transfers** on endpoint `0x02`:
   * the 20-byte header, on its own,
   * then the payload, in 512-byte chunks.
6. `36 02`
7. `38 01 04 <bucket>`

### Why the header must be its own transfer

512 bytes is the endpoint's max packet size. A 20-byte transfer is therefore a
*short packet*, and that is how the firmware knows where the descriptor ends
and the payload begins. Concatenating header and payload into one buffer
transfers fine at the USB level and displays nothing.

### The header

```
12 FA 01 E8 AB CD EF 98 76 54 32 10   magic, 12 bytes
<kind> 00 00 00                       01 = GIF file, 02 = raw RGBA
<length, 32-bit little-endian>        length of the payload that follows
```

The payload is **not** padded to a KiB boundary. Only the size field in step 3
is rounded up.

### Payload formats

* **GIF** (`kind = 0x01`) — an ordinary GIF file, 320×320. Far the better
  choice: a flat colour compresses to a few hundred bytes where the same frame
  as RGBA is 400 KiB.
* **Raw RGBA** (`kind = 0x02`) — 320 × 320 × 4 bytes, alpha ignored.

### Memory layout

There are 16 buckets sharing 24320 KiB of display memory. Addresses and sizes
are yours to assign; nothing stops two buckets from overlapping, so keep track
yourself. Uploading to the bucket that is currently on screen is refused,
which means a display that updates needs at least two buckets used
alternately.

## Rotation does not apply to uploaded content

The firmware rotates the screens **it** draws. Anything uploaded through a
bucket goes to the panel exactly as sent. A host that wants uploaded content
to appear upright must read the orientation with `0x30 0x01` and bake the
rotation into the image itself.

## What is not possible

The firmware's liquid-temperature screen has no colour, font or layout
parameters. `0x38 0x01 0x02 0x00` takes no arguments, and no other report
appears to influence it. The only way to get a differently coloured
temperature display is to draw one on the host and upload it as an image.
