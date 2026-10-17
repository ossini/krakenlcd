# Contributing to krakenlcd

Thanks for wanting to help! Bug reports, designs and code are all welcome.

## Reporting a bug

Open an issue and include:

- what you ran and what happened, with the full error message
- the output of `krakenlcd status` and `krakenlcd sensors`
- your distribution, kernel version (`uname -r`) and cooler model
- for a design problem: the design file, or a link to it

## Sharing a design

Made a design you like? Two ways to share it:

- **Anywhere.** Zip its folder from `~/.config/krakenlcd/designs/`. Others
  unzip it into theirs, and that's it.
- **As a built-in design**, in a pull request that adds
  `krakenlcd/designs/NAME/design.svg`. It should be 320 × 320, use only the
  fonts that ship with krakenlcd (Chakra Petch, JetBrains Mono) or bring its
  own under a licence that allows redistribution (with the licence text next
  to it), reference no files outside its folder, and draw without warnings.
  Put a preview in the pull request: `krakenlcd design preview NAME --demo`.

[docs/DESIGNS.md](docs/DESIGNS.md) explains how designs work.

## Changing the code

```sh
git clone https://github.com/ossini/krakenlcd
cd krakenlcd
python -m venv .venv && . .venv/bin/activate
pip install -e '.[gui]'
python -m unittest discover -s tests -v
```

The tests need no cooler. **[AGENTS.md](AGENTS.md) has the full checklist
for a pull request**, the same one GitHub Actions runs: tests on Python
3.10–3.14, the GUI offscreen, and the install script. It also lists the few
rules that matter most:

- the pump and fan reports are never written, only display reports
- the protocol layer uses the standard library only
- design placeholders are a sandbox: a shared design may only draw a picture
- the live loop runs for weeks, so whatever it does every tick must be cheap
- saved display commands from older versions must keep working

Keep commits small and logical, and say in the pull request how you tested,
especially whether it was on real hardware.

## Using LLMs

You're welcome to use LLMs (ChatGPT, Claude, Copilot, …) for improvements.
Much of krakenlcd was written that way too. AI coding agents find their
instructions in [AGENTS.md](AGENTS.md); `CLAUDE.md` points there.

What counts is the result: you've read what you submit, understand it, and
have run the checklist. Please mention in the pull request if larger parts
were generated. It helps reviewers know where to look closely.

## Licence

krakenlcd is GPL-3.0-or-later. By contributing, you agree that your
contribution is licensed the same way.
