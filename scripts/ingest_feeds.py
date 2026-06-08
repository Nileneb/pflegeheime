"""DEPRECATED: abgelöst durch marktradar/ingest.py (SQLite-Pfad).
Nutze:  python -c "from marktradar import db, ingest; ingest.refresh(db.connect())"
oder den MCP-Server (marktradar/server.py, Tool refresh_news)."""
import sys
sys.exit("ingest_feeds.py ist abgelöst — siehe marktradar/ingest.py")
