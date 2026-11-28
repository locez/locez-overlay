#!/usr/bin/env bash
set -euo pipefail
herdr --version
test -f /usr/share/bash-completion/completions/herdr
test -f /usr/share/zsh/site-functions/_herdr
test -f /usr/share/fish/vendor_completions.d/herdr.fish
