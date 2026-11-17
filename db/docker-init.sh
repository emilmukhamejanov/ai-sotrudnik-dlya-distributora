#!/usr/bin/env bash
# Выполняется контейнером Postgres один раз, при первой инициализации тома.
set -euo pipefail
SQL_DIR=/opt/db/sql DATA_DIR=/opt/data/demo_1c bash /opt/db/apply.sh
