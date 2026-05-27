#!/bin/bash
# Copyright (C) 2026  b0a7
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLOTTER="$SCRIPT_DIR/plotProcessingTimes.py"

if [[ ! -f /etc/systemd/system/execution.service ]]; then
  echo "No execution.service found. This plotter requires an installed execution client."
  echo "Press ENTER to continue."
  read -r
  exit 0
fi

missing_packages=()
python3 -c "import rich" >/dev/null 2>&1 || missing_packages+=("python3-rich")

if [[ ${#missing_packages[@]} -gt 0 ]]; then
  echo "Installing execution time plotter dependencies: ${missing_packages[*]}"
  sudo apt-get update
  sudo apt-get install --no-install-recommends --no-install-suggests -y "${missing_packages[@]}"
fi

python3 "$PLOTTER" --source journalctl --unit execution "$@"
