"""SQLite-backed registry. Agent specs are data, never generated Python code."""
import json
import sqlite3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "agent_factory.db"

def db():
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, objective TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, active_version_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER NOT NULL, version TEXT NOT NULL, specification TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', promotion_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(agent_id, version), FOREIGN KEY(agent_id) REFERENCES agents(id));
        CREATE TABLE IF NOT EXISTS test_cases (id INTEGER PRIMARY KEY AUTOINCREMENT, version_id INTEGER NOT NULL, name TEXT NOT NULL, prompt TEXT NOT NULL, must_include TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(version_id) REFERENCES versions(id));
        CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, version_id INTEGER NOT NULL, task TEXT NOT NULL, output TEXT NOT NULL, status TEXT NOT NULL, duration_ms INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(version_id) REFERENCES versions(id));
        CREATE TABLE IF NOT EXISTS evaluations (id INTEGER PRIMARY KEY AUTOINCREMENT, test_case_id INTEGER NOT NULL, run_id INTEGER NOT NULL, passed INTEGER NOT NULL, score INTEGER NOT NULL, rationale TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(test_case_id) REFERENCES test_cases(id), FOREIGN KEY(run_id) REFERENCES runs(id));""")

def one(query, params=()):
    with db() as con:
        record = con.execute(query, params).fetchone(); return dict(record) if record else None
def many(query, params=()):
    with db() as con: return [dict(item) for item in con.execute(query, params).fetchall()]
def create_agent(name, objective, provider, model, spec):
    with db() as con:
        cur=con.execute("INSERT INTO agents(name, objective, provider, model) VALUES(?,?,?,?)",(name,objective,provider,model)); agent_id=cur.lastrowid
        cur=con.execute("INSERT INTO versions(agent_id,version,specification,status) VALUES(?, '0.1.0', ?, 'draft')",(agent_id,json.dumps(spec))); version_id=cur.lastrowid
    return get_version(version_id)
def get_agent(agent_id):
    agent=one("SELECT a.*,v.version,v.status,v.specification FROM agents a LEFT JOIN versions v ON v.id=COALESCE(a.active_version_id,(SELECT id FROM versions WHERE agent_id=a.id ORDER BY id DESC LIMIT 1)) WHERE a.id=?",(agent_id,))
    if agent and agent["specification"]: agent["specification"]=json.loads(agent["specification"])
    return agent
def get_version(version_id):
    record=one("SELECT v.*,a.name,a.objective,a.provider,a.model FROM versions v JOIN agents a ON a.id=v.agent_id WHERE v.id=?",(version_id,))
    if record: record["specification"]=json.loads(record["specification"])
    return record
def list_agents():
    return many("SELECT a.id,a.name,a.objective,a.provider,a.model,a.created_at,v.id AS version_id,v.version,v.status,(SELECT COUNT(*) FROM test_cases WHERE version_id=v.id) AS test_count FROM agents a JOIN versions v ON v.id=COALESCE(a.active_version_id,(SELECT id FROM versions WHERE agent_id=a.id ORDER BY id DESC LIMIT 1)) ORDER BY a.id DESC")
def list_versions(agent_id):
    items=many("SELECT id,version,status,promotion_note,created_at FROM versions WHERE agent_id=? ORDER BY id DESC",(agent_id,))
    for item in items: item["test_count"]=one("SELECT COUNT(*) AS count FROM test_cases WHERE version_id=?",(item["id"],))["count"]
    return items
def add_test(version_id,name,prompt,must_include):
    with db() as con: cur=con.execute("INSERT INTO test_cases(version_id,name,prompt,must_include) VALUES(?,?,?,?)",(version_id,name,prompt,must_include))
    return one("SELECT * FROM test_cases WHERE id=?",(cur.lastrowid,))
def list_tests(version_id): return many("SELECT * FROM test_cases WHERE version_id=? ORDER BY id DESC",(version_id,))
def save_run(version_id,task,output,status,duration_ms):
    with db() as con: cur=con.execute("INSERT INTO runs(version_id,task,output,status,duration_ms) VALUES(?,?,?,?,?)",(version_id,task,output,status,duration_ms))
    return one("SELECT * FROM runs WHERE id=?",(cur.lastrowid,))
def save_evaluation(test_id,run_id,passed,score,rationale):
    with db() as con: con.execute("INSERT INTO evaluations(test_case_id,run_id,passed,score,rationale) VALUES(?,?,?,?,?)",(test_id,run_id,int(passed),score,rationale))
def recent_runs(): return many("SELECT r.*,a.name,v.version FROM runs r JOIN versions v ON v.id=r.version_id JOIN agents a ON a.id=v.agent_id ORDER BY r.id DESC LIMIT 25")
def promote(version_id,note):
    version=get_version(version_id)
    if not version: return None
    with db() as con:
        con.execute("UPDATE versions SET status='archived' WHERE agent_id=? AND status='approved'",(version["agent_id"],)); con.execute("UPDATE versions SET status='approved',promotion_note=? WHERE id=?",(note or "",version_id)); con.execute("UPDATE agents SET active_version_id=? WHERE id=?",(version_id,version["agent_id"]))
    return get_version(version_id)
