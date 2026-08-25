# QFw manual pages

This directory contains the installed command and configuration reference for
QFw. Manual sources use the portable `man` macro package and are grouped by
section.

- `man1` documents executable commands.
- `man5` documents configuration file formats.
- `man7` documents QFw concepts and service architecture.

Add each page to the corresponding list in `man/CMakeLists.txt`. Keep command
options synchronized with the command's `--help` output. Workflow procedures
belong in `docs/recipes`; manual pages describe stable interfaces and point to
related pages through `SEE ALSO`.

Preview a page from the source tree:

```bash
man -l man/man1/qfw-setup.1
```

Check roff syntax without formatting output:

```bash
for page in man/man1/*.1 man/man5/*.5 man/man7/*.7; do
  groff -man -z "$page"
done
```

Install only the documentation component:

```bash
cmake -S . -B build
cmake --install build --component documentation
```

`qfw-activate` adds the installed manual directory to `MANPATH`. It also makes
the source-tree pages available when activation uses a CMake build tree.
The application lifecycle is documented by `qfw-activate(1)`,
`qfw-setup(1)`, `qfw-status(1)`, `qfw-srun(1)`, `qfw-teardown(1)`, and
`qfw-deactivate(1)`. Installed tests and their service-mode behavior are
introduced by `qfw-examples(7)`. Each public test wrapper has a section 1 page
whose name includes the script's `.sh` suffix.
