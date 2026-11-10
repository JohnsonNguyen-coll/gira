import json
import sqlite3


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS watches (
          id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, min_bps INTEGER NOT NULL,
          max_bps INTEGER NOT NULL, amount TEXT NOT NULL, tax_limit INTEGER NOT NULL,
          deployer TEXT, wallet TEXT NOT NULL, start_block INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'armed',
          tx_hash TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS candidates (
          watch_id INTEGER NOT NULL, token TEXT NOT NULL, payload TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'waiting',
          PRIMARY KEY(watch_id, token));
        """)

    def cursor(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        return int(row[0]) if row else None

    def set_cursor(self, block):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('cursor', ?)", (str(block),))

    def add_watch(self, ticker, minimum, maximum, amount, tax, deployer, wallet, start_block):
        with self.db:
            cur = self.db.execute("""INSERT INTO watches
                (ticker,min_bps,max_bps,amount,tax_limit,deployer,wallet,start_block)
                VALUES (?,?,?,?,?,?,?,?)""",
                (ticker, minimum, maximum, str(amount), tax, deployer, wallet, start_block))
        return cur.lastrowid

    def watches(self, armed=False):
        sql = "SELECT * FROM watches" + (" WHERE status='armed'" if armed else "")
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id")]

    def watch(self, watch_id):
        row = self.db.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
        return dict(row) if row else None

    def cancel(self, watch_id):
        with self.db:
            cur = self.db.execute("UPDATE watches SET status='cancelled' WHERE id=? AND status='armed'",
                                  (watch_id,))
        return cur.rowcount == 1

    def candidate(self, watch_id, payload):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO candidates(watch_id,token,payload) VALUES (?,?,?)",
                            (watch_id, payload['token'], json.dumps(payload)))

    def candidates(self):
        return [(r['watch_id'], json.loads(r['payload'])) for r in self.db.execute(
            """SELECT c.* FROM candidates c JOIN watches w ON w.id=c.watch_id
               WHERE c.state='waiting' AND w.status='armed' ORDER BY w.id, c.rowid""")]

    def finish_candidate(self, watch_id, token):
        with self.db:
            self.db.execute("UPDATE candidates SET state='done' WHERE watch_id=? AND token=?",
                            (watch_id, token))

    def claim(self, watch_id):
        # Persist before signing: a restart must never silently submit a second buy.
        with self.db:
            cur = self.db.execute("UPDATE watches SET status='executing' WHERE id=? AND status='armed'",
                                  (watch_id,))
        return cur.rowcount == 1

    def status(self, watch_id, status, detail="", tx_hash=None):
        with self.db:
            self.db.execute("UPDATE watches SET status=?,detail=?,tx_hash=COALESCE(?,tx_hash) WHERE id=?",
                            (status, detail, tx_hash, watch_id))
