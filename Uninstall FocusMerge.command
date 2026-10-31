#!/bin/bash
# Double-click to remove FocusMerge (the Lightroom plugin and its engine).
cd "$(dirname "$0")" || exit 1
bash ./install.sh --uninstall
status=$?
echo
read -r -p "Press Return to close this window..." _
exit $status
