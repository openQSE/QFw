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
man -l man/man1/qfw-service-plane.1
```

Check roff syntax without formatting output:

```bash
groff -man -z man/man1/qfw-service-plane.1
```

Install only the documentation component:

```bash
cmake -S . -B build
cmake --install build --component documentation
```

`qfw-activate` adds the installed manual directory to `MANPATH`. It also makes
the source-tree pages available when activation uses a CMake build tree.
