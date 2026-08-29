#!/bin/sh
# Backup bazy SQLite. Uruchamiaj z katalogu repo na VPS, np. z crona:
#   0 3 * * * cd /opt/video-generator && ./scripts/backup-db.sh >> /var/log/vg-backup.log 2>&1
#
# Uzywa `sqlite3 .backup`, a nie `cp`: baza jest otwarta przez dzialajacy
# kontener, wiec zwykla kopia moze zlapac plik w polowie transakcji.
# Komenda leci WEWNATRZ kontenera - to tam jest wolumin /data.
set -eu

KEEP_DAYS=7
BACKUP_DIR="./data/backups"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
COMPOSE="docker compose -f docker-compose.prod.yml"

mkdir -p "$BACKUP_DIR"

# python zamiast klienta sqlite3: obraz python:3.12-slim nie ma binarki sqlite3,
# ale modul sqlite3 (i jego .backup()) jest w standardowej bibliotece.
$COMPOSE exec -T app python -c "
import sqlite3
src = sqlite3.connect('/data/video_gen.db')
dst = sqlite3.connect('/data/backups/video_gen-${STAMP}.db')
src.backup(dst)
dst.close(); src.close()
print('backup ok')
"

# Rotacja - bez niej backupy same zapchaja 10 GB dysku.
find "$BACKUP_DIR" -name 'video_gen-*.db' -type f -mtime +$KEEP_DAYS -delete

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup: $BACKUP_DIR/video_gen-${STAMP}.db"
