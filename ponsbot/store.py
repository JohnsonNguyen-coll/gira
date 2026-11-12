import json
import sqlite3


class Store:
    def __init__(self, path, legacy_owner=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY);
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
        # Migrate existing single-owner databases in place. Without the old owner
        # ID, leave old rows unassigned and inactive rather than expose them.
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(watches)')}
        with self.db:
            if 'user_id' not in columns:
                self.db.execute('ALTER TABLE watches ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0')
                if legacy_owner > 0:
                    self.db.execute('UPDATE watches SET user_id=?', (legacy_owner,))
            self.db.execute('CREATE INDEX IF NOT EXISTS watches_user_status ON watches(user_id,status)')
            self.db.execute('INSERT OR IGNORE INTO users SELECT DISTINCT user_id FROM watches WHERE user_id>0')

    def register_user(self, user_id):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO users VALUES (?)', (user_id,))

    def users(self):
        return [r[0] for r in self.db.execute('SELECT user_id FROM users')]

    def cursor(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        return int(row[0]) if row else None

    def set_cursor(self, block):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('cursor', ?)", (str(block),))

    def add_watch(self, ticker, minimum, maximum, amount, tax, deployer, wallet, start_block, *, user_id):
        if user_id <= 0:
            raise ValueError('Watch must belong to a Telegram user')
        with self.db:
            cur = self.db.execute("""INSERT INTO watches
                (ticker,min_bps,max_bps,amount,tax_limit,deployer,wallet,start_block,user_id)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (ticker, minimum, maximum, str(amount), tax, deployer, wallet, start_block, user_id))
        return cur.lastrowid

    def watches(self, armed=False, user_id=None):
        sql = "SELECT * FROM watches WHERE user_id>0"
        args = []
        if armed:
            sql += " AND status='armed'"
        if user_id is not None:
            sql += ' AND user_id=?'
            args.append(user_id)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id", args)]

    def watch(self, watch_id, user_id=None):
        sql, args = 'SELECT * FROM watches WHERE id=?', [watch_id]
        if user_id is not None:
            sql += ' AND user_id=?'
            args.append(user_id)
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def cancel(self, watch_id, *, user_id):
        with self.db:
            cur = self.db.execute("UPDATE watches SET status='cancelled' WHERE id=? AND user_id=? AND status='armed'",
                                  (watch_id, user_id))
        return cur.rowcount == 1

    def candidate(self, watch_id, payload):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO candidates(watch_id,token,payload) VALUES (?,?,?)",
                            (watch_id, payload['token'], json.dumps(payload)))

    def candidates(self):
        return [(r['watch_id'], json.loads(r['payload'])) for r in self.db.execute(
            """SELECT c.* FROM candidates c JOIN watches w ON w.id=c.watch_id
               WHERE c.state='waiting' AND w.status='armed' AND w.user_id>0 ORDER BY w.id, c.rowid""")]

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
