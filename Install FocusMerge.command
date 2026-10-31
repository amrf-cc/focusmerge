#!/bin/bash
# Double-click to install FocusMerge. Everything happens in the installer, install.sh.
cd "$(dirname "$0")" || exit 1
echo "FocusMerge installer"
echo
bash ./install.sh
status=$?
echo
if [ $status -eq 0 ]; then
  echo "All done. You can close this window."
else
  echo "The installation did not finish (see the message above). Lightroom was not changed."
fi
echo
read -r -p "Press Return to close this window..." _
exit $status
