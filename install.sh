#!/bin/sh
# vitruvio installer
#
#   curl -fsSL https://raw.githubusercontent.com/getsfumato/vitruvio/main/install.sh | sh
#
# Environment:
#   VITRUVIO_VERSION   version to install (default: latest release)
#   VITRUVIO_EXTRAS    comma-separated extras, e.g. "local,api" (default: none)
#   VITRUVIO_BIN_DIR   install directory (default: ~/.local/bin)
#   VITRUVIO_NO_MODIFY_PATH=1   skip the PATH hint
#
# vitruvio is nine pure-Python distributions rather than one binary, so this does not download an
# executable: it downloads the release's wheel bundle and hands it to `uv tool install`, which builds an
# isolated environment for it and links one command into VITRUVIO_BIN_DIR. uv is bootstrapped into a
# directory this script owns if it is not already present, and is asked for a Python by range so that it
# fetches one when the host has nothing new enough -- so nothing here depends on the host's Python.

set -eu

REPO="getsfumato/vitruvio"
BIN="vitruvio"
PKG="vitruvio"
PY_FLOOR="3.11"
TMP=""

# ---- output ----------------------------------------------------------------

if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
  C_DIM=$(printf '\033[2m'); C_GOLD=$(printf '\033[33m')
  C_RED=$(printf '\033[31m'); C_OFF=$(printf '\033[0m')
else
  C_DIM=''; C_GOLD=''; C_RED=''; C_OFF=''
fi

say()  { printf '%s\n' "$*" >&2; }
step() { printf '%s>%s %s\n' "$C_GOLD" "$C_OFF" "$*" >&2; }
dim()  { printf '%s  %s%s\n' "$C_DIM" "$*" "$C_OFF" >&2; }
die()  { printf '%serror%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }

cleanup() { [ -n "$TMP" ] && [ -d "$TMP" ] && rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

need() { command -v "$1" >/dev/null 2>&1; }

# ---- platform -------------------------------------------------------------

case "$(uname -s)" in
  Darwin | Linux) ;;
  *) die "unsupported operating system: $(uname -s) (macOS and Linux only)" ;;
esac

# ---- download -------------------------------------------------------------

fetch() {
  # fetch <url> <dest>
  if need curl; then
    curl -fsSL --retry 3 --connect-timeout 20 -o "$2" "$1"
  elif need wget; then
    wget -qO "$2" "$1"
  else
    die "need curl or wget"
  fi
}

fetch_stdout() {
  if need curl; then
    curl -fsSL --retry 3 --connect-timeout 20 "$1"
  elif need wget; then
    wget -qO- "$1"
  else
    die "need curl or wget"
  fi
}

latest_version() {
  # `releases/latest` never resolves to a draft or a prerelease, which is what makes an unfinished
  # release invisible here: the release workflow uploads and verifies its assets while the release is
  # still a draft, so a version this returns always has a bundle attached.
  fetch_stdout "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"v\{0,1\}\([^"]*\)".*/\1/p' \
    | head -n 1
}

verify() {
  # verify <file> <expected-sha256>
  if need sha256sum; then
    actual=$(sha256sum "$1" | cut -d' ' -f1)
  elif need shasum; then
    actual=$(shasum -a 256 "$1" | cut -d' ' -f1)
  else
    dim "no sha256 tool available — skipping checksum verification"
    return 0
  fi
  [ "$actual" = "$2" ] || die "checksum mismatch
  expected $2
  actual   $actual"
  dim "checksum ok"
}

# ---- uv -------------------------------------------------------------------

# Installed under the script's own directory rather than by the vendor default, and with
# UV_NO_MODIFY_PATH: someone installing vitruvio did not ask for uv on their PATH or for their shell rc
# to be edited on their behalf. `vitruvio` is what ends up on the PATH; this copy is an implementation
# detail that the next run of this script finds again.
UV_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/vitruvio/uv"

ensure_uv() {
  if need uv; then
    UV=uv
    dim "using uv $(uv --version 2>/dev/null | awk '{print $2}')"
    return 0
  fi
  if [ -x "$UV_HOME/uv" ]; then
    UV="$UV_HOME/uv"
    dim "using uv $("$UV" --version 2>/dev/null | awk '{print $2}') from a previous install"
    return 0
  fi

  step "installing uv"
  mkdir -p "$UV_HOME" || return 1
  # The installer is piped rather than saved: it verifies its own downloads, and UV_INSTALL_DIR is the
  # documented way to place the binary. A failure here is not fatal -- pip_fallback still has a path.
  if ! fetch_stdout "https://astral.sh/uv/install.sh" \
       | env UV_INSTALL_DIR="$UV_HOME" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null 2>&1
  then
    return 1
  fi
  [ -x "$UV_HOME/uv" ] || return 1
  UV="$UV_HOME/uv"
  dim "installed uv $("$UV" --version 2>/dev/null | awk '{print $2}')"
}

# ---- pip fallback ---------------------------------------------------------

# Reached only when uv could not be installed -- an air-gapped host, or a platform its installer does not
# cover. It installs into the user scheme, so the command lands wherever this Python puts user scripts
# rather than in VITRUVIO_BIN_DIR, which is why it says so instead of pretending otherwise.
pip_fallback() {
  wheels="$1"
  need python3 || die "could not install uv, and python3 was not found.

  Install uv, then retry:
    curl -fsSL https://astral.sh/uv/install.sh | sh"

  have=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
  ok=$(python3 - "$have" "$PY_FLOOR" <<'PY'
import sys
have = tuple(int(p) for p in sys.argv[1].split("."))
floor = tuple(int(p) for p in sys.argv[2].split("."))
print("yes" if have >= floor else "no")
PY
)
  [ "$ok" = yes ] || die "python3 is $have; vitruvio needs $PY_FLOOR or newer.

  uv would have fetched a suitable Python, but it could not be installed here. Either install uv:
    curl -fsSL https://astral.sh/uv/install.sh | sh
  or upgrade Python to $PY_FLOOR+."

  step "installing with pip (uv unavailable)"
  python3 -m pip install --user --upgrade --find-links "$wheels" "$SPEC" \
    || die "pip install failed"
  say ''
  say "  installed $BIN $VERSION into this Python's user scripts directory"
  dim "$(python3 -m site --user-base)/bin"
  say ''
  exit 0
}

# ---- main -----------------------------------------------------------------

VERSION="${VITRUVIO_VERSION:-}"
if [ -z "$VERSION" ]; then
  VERSION=$(latest_version || true)
  [ -n "$VERSION" ] || die "could not resolve the latest release of $REPO.

  Pin a version instead:
    VITRUVIO_VERSION=0.1.0 sh install.sh"
fi
VERSION="${VERSION#v}"
step "version $VERSION"

EXTRAS="${VITRUVIO_EXTRAS:-}"
if [ -n "$EXTRAS" ]; then
  SPEC="${PKG}[${EXTRAS}]==${VERSION}"
  step "extras $EXTRAS"
else
  SPEC="${PKG}==${VERSION}"
fi

BIN_DIR="${VITRUVIO_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
ARCHIVE="${PKG}-v${VERSION}-wheels.tar.gz"
BASE="https://github.com/$REPO/releases/download/v${VERSION}"

TMP=$(mktemp -d 2>/dev/null || mktemp -d -t vitruvio)

UV=""
ensure_uv || dim "uv could not be installed"

step "downloading $ARCHIVE"
FROM_PYPI=0
if fetch "$BASE/$ARCHIVE" "$TMP/$ARCHIVE" 2>/dev/null; then
  # checksums file is optional; verify when it is published
  if fetch "$BASE/${ARCHIVE}.sha256" "$TMP/sum" 2>/dev/null; then
    verify "$TMP/$ARCHIVE" "$(cut -d' ' -f1 < "$TMP/sum")"
  fi

  step "extracting"
  mkdir -p "$TMP/wheels"
  tar -xzf "$TMP/$ARCHIVE" -C "$TMP/wheels" || die "could not extract $ARCHIVE"

  # The archive nests its files one directory deep so that extracting it by hand does not scatter
  # eighteen files into the current directory. `find` makes the layout the installer's problem rather
  # than something the archive has to promise forever.
  WHEELS=$(find "$TMP/wheels" -name '*.whl' -exec dirname {} \; 2>/dev/null | head -n 1)
  [ -n "$WHEELS" ] || die "$ARCHIVE contained no wheels"
  count=$(find "$WHEELS" -name '*.whl' | wc -l | tr -d ' ')
  dim "$count wheels"
else
  # No bundle for this version -- an old release, or one cut before the bundle existed. PyPI is the
  # other place the same nine distributions live, and `uv tool install` resolves them there identically.
  dim "no wheel bundle published for v$VERSION — resolving from PyPI"
  FROM_PYPI=1
  WHEELS=""
fi

[ -n "$UV" ] || [ "$FROM_PYPI" = 1 ] || pip_fallback "$WHEELS"
[ -n "$UV" ] || die "could not install uv, and no wheel bundle was available to fall back on.

  Install uv, then retry:
    curl -fsSL https://astral.sh/uv/install.sh | sh"

step "installing $SPEC"
mkdir -p "$BIN_DIR" || die "could not create $BIN_DIR"
[ -w "$BIN_DIR" ] || die "$BIN_DIR is not writable
  retry with:  VITRUVIO_BIN_DIR=\"\$HOME/.local/bin\" sh install.sh"

# --force so that installing over an existing vitruvio upgrades it rather than stopping with "already
# installed". UV_TOOL_BIN_DIR is how the command lands in BIN_DIR; the environment itself lives under uv's
# tool directory and `uv tool uninstall vitruvio` removes both.
#
# --reinstall is not redundant with it, and this is the subtle part. Only `vitruvio` is pinned here; its
# eight siblings are ordinary unpinned dependencies, so an upgrade over an existing environment finds
# vitruvio-kernel 0.1.0 already installed, considers the requirement satisfied, and leaves it there. The
# result is a new CLI on eight old libraries -- which reports the old version, because `--version` reads
# vitruvio.kernel.__version__. --reinstall rebuilds the environment so all nine come from this bundle.
#
# --quiet: uv would otherwise list fifty resolved packages and then warn that BIN_DIR is not on the PATH
# in its own words, immediately before this script says the same thing in its own. Errors still print.
# -p ">=3.11" is what makes uv *fetch* a Python instead of failing on the host's. Left implicit, uv
# discovers whatever `python3` is and resolves against it: on Ubuntu 22.04 that is 3.10, and the install
# dies with "vitruvio requires Python >=3.11 ... your requirements are unsatisfiable" rather than
# downloading anything. Asking for the range explicitly makes the request satisfiable by a managed
# download, and still reuses a host 3.11+ when there is one rather than fetching a second copy.
if [ "$FROM_PYPI" = 1 ]; then
  UV_TOOL_BIN_DIR="$BIN_DIR" "$UV" tool install --quiet --force --reinstall -p ">=$PY_FLOOR" "$SPEC" >&2 \
    || die "could not install $SPEC from PyPI"
else
  # --find-links, not --index: the nine vitruvio distributions come from the release bundle while their
  # third-party dependencies (cyclopts, rich, textual, ...) still resolve from PyPI as normal.
  UV_TOOL_BIN_DIR="$BIN_DIR" "$UV" tool install --quiet --force --reinstall -p ">=$PY_FLOOR" \
    --find-links "$WHEELS" "$SPEC" >&2 \
    || die "could not install $SPEC"
fi

[ -x "$BIN_DIR/$BIN" ] || die "install reported success but $BIN_DIR/$BIN is not there"

# What was asked for and what runs must be the same version. This is one command rather than a comment
# because it is the only place the mixed-versions failure above becomes visible: an environment holding a
# new CLI and old libraries installs cleanly and then answers with the old number.
got=$("$BIN_DIR/$BIN" --version 2>/dev/null | head -n 1 || true)
[ "$got" = "$VERSION" ] || die "installed $VERSION but $BIN reports ${got:-nothing}.

  The environment is holding a mix of versions. Remove it and run this again:
    $UV tool uninstall $PKG"

step "installed $BIN_DIR/$BIN"

# ---- PATH -----------------------------------------------------------------

case ":$PATH:" in
  *":$BIN_DIR:"*) on_path=1 ;;
  *) on_path=0 ;;
esac

say ''
if [ "$on_path" = 1 ]; then
  say "  $BIN $VERSION is ready"
  say ''
  dim "next:  $BIN config show"
elif [ "${VITRUVIO_NO_MODIFY_PATH:-0}" != 1 ]; then
  say "  $BIN_DIR is not on your PATH. Add it:"
  say ''
  # Defaulted before the expansion: `set -u` makes a bare ${SHELL##*/} abort when SHELL is unset, which
  # is common in containers -- so the script would die on its last and most helpful line, after having
  # installed successfully.
  shell_name="${SHELL:-}"
  case "${shell_name##*/}" in
    zsh)  say "    echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
    fish) say "    fish_add_path $BIN_DIR" ;;
    *)    say "    echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc && exec bash" ;;
  esac
  say ''
  dim "then:  $BIN config show"
fi
say ''
