"""
ERP Server - Render + Turso
"""

import os
from datetime import datetime
from flask import Flask, jsonify, request, session
from flask_cors import CORS
import libsql_experimental as libsql

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "erp_secret_key_2024")
CORS(app, supports_credentials=True, origins=os.environ.get("ALLOWED_ORIGIN", "*"))

PORT = int(os.environ.get("PORT", 5050))

TURSO_URL = os.environ.get("TURSO_URL")        # ej: libsql://chofi-tangoch.aws-us-east-1.turso.io
TURSO_TOKEN = os.environ.get("TURSO_TOKEN")

def get_conn():
    conn = libsql.connect("erp-local.db", sync_url=TURSO_URL, auth_token=TURSO_TOKEN)
    conn.sync()
    return conn

# ──────────────────────────────────────────────
# INICIALIZAR TABLAS
# ──────────────────────────────────────────────

def inicializar_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            codigo  TEXT PRIMARY KEY,
            nombre  TEXT NOT NULL,
            email   TEXT DEFAULT '',
            deuda   REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            codigo  TEXT PRIMARY KEY,
            nombre  TEXT NOT NULL,
            precio  REAL DEFAULT 0,
            stock   INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comprobantes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            nro            TEXT,
            fecha          TEXT,
            cod_cliente    TEXT,
            nombre_cliente TEXT,
            descripcion    TEXT,
            cantidad       INTEGER DEFAULT 1,
            precio_unit    REAL DEFAULT 0,
            importe        REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pagos (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha          TEXT,
            cod_cliente    TEXT,
            nombre_cliente TEXT,
            monto_aplicado REAL DEFAULT 0,
            deuda_restante REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT NOT NULL UNIQUE,
            clave       TEXT NOT NULL,
            cod_cliente TEXT NOT NULL
        )
    """)
    conn.commit()
    print("[ERP] Tablas verificadas en Turso ✓")

# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def cliente_sesion():
    return session.get("cod_cliente")

def usuario_sesion():
    return session.get("usuario")

def row_to_dict(cur, row):
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))

# ──────────────────────────────────────────────
# RUTAS
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"status": "ERP API (Turso) running"})

# ── LOGIN / LOGOUT / REGISTRO ──

@app.route("/api/login", methods=["POST"])
def login():
    try:
        data   = request.json
        nombre = data.get("usuario", "").strip().lower()
        clave  = data.get("clave", "").strip()
        conn = get_conn()
        cur = conn.execute("SELECT * FROM usuarios WHERE nombre=? AND clave=?", (nombre, clave))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Usuario o clave incorrectos."}), 401
        user = row_to_dict(cur, row)
        session["usuario"]     = user["nombre"]
        session["cod_cliente"] = user["cod_cliente"]
        return jsonify({"ok": True, "usuario": user["nombre"], "cod_cliente": user["cod_cliente"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/sesion", methods=["GET"])
def sesion():
    if not usuario_sesion():
        return jsonify({"logueado": False})
    return jsonify({"logueado": True, "usuario": usuario_sesion(), "cod_cliente": cliente_sesion()})

@app.route("/api/registro", methods=["POST"])
def registro():
    try:
        data  = request.json
        user  = data.get("usuario", "").strip().lower()
        clave = data.get("clave", "").strip()
        if not user or not clave:
            return jsonify({"error": "Completá usuario y clave."}), 400

        conn = get_conn()
        cur = conn.execute("SELECT id FROM usuarios WHERE nombre=?", (user,))
        if cur.fetchone():
            return jsonify({"error": "Ese nombre de usuario ya existe."}), 400

        cur = conn.execute("SELECT codigo FROM clientes")
        codigos = set()
        for r in cur.fetchall():
            val = r[0]
            if str(val).isdigit():
                codigos.add(int(val))
        nuevo_cod = 1
        while nuevo_cod in codigos:
            nuevo_cod += 1
        nuevo_cod = str(nuevo_cod)

        conn.execute("INSERT INTO clientes (codigo, nombre, deuda) VALUES (?,?,0)", (nuevo_cod, user))
        conn.execute("INSERT INTO usuarios (nombre, clave, cod_cliente) VALUES (?,?,?)", (user, clave, nuevo_cod))
        conn.commit()
        return jsonify({"ok": True, "codigo": nuevo_cod})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── CLIENTES ──

@app.route("/api/clientes", methods=["GET"])
def get_clientes():
    cod = cliente_sesion()
    if not cod:
        return jsonify({"error": "No autenticado."}), 401
    try:
        conn = get_conn()
        cur = conn.execute("SELECT * FROM clientes WHERE codigo=?", (cod,))
        row = cur.fetchone()
        if not row:
            return jsonify({})
        cliente_row = row_to_dict(cur, row)

        cur = conn.execute("SELECT * FROM comprobantes WHERE cod_cliente=? ORDER BY id ASC", (cod,))
        comps = [row_to_dict(cur, r) for r in cur.fetchall()]

        cur = conn.execute("SELECT * FROM pagos WHERE cod_cliente=? ORDER BY id ASC", (cod,))
        pagos = [row_to_dict(cur, r) for r in cur.fetchall()]

        cliente = {
            "nombre":      cliente_row["nombre"],
            "email":       cliente_row["email"] or "",
            "deuda":       float(cliente_row["deuda"]),
            "movimientos": [],
            "pagos":       [],
        }

        vistos = {}
        for c in comps:
            nro = c["nro"]
            if nro not in vistos:
                vistos[nro] = {"nro": nro, "fecha": c["fecha"], "desc": c["descripcion"], "val": 0.0}
            vistos[nro]["val"] = round(vistos[nro]["val"] + float(c["importe"]), 2)
        cliente["movimientos"] = list(vistos.values())

        for p in pagos:
            cliente["pagos"].append({"fecha": p["fecha"], "val": float(p["monto_aplicado"])})

        return jsonify({cod: cliente})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── PRODUCTOS ──

@app.route("/api/productos", methods=["GET"])
def get_productos():
    if not usuario_sesion():
        return jsonify({"error": "No autenticado."}), 401
    try:
        conn = get_conn()
        cur = conn.execute("SELECT * FROM productos ORDER BY nombre ASC")
        rows = [row_to_dict(cur, r) for r in cur.fetchall()]
        return jsonify([{
            "codigo": r["codigo"], "nombre": r["nombre"],
            "precio": float(r["precio"]), "stock": int(r["stock"]),
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── PEDIDO ──

@app.route("/api/pedido", methods=["POST"])
def post_pedido():
    cod = cliente_sesion()
    if not cod:
        return jsonify({"error": "No autenticado."}), 401
    try:
        data  = request.json
        nro   = data["nro"]
        items = data["items"]
        fecha = datetime.now().strftime("%d/%m/%Y")

        conn = get_conn()
        cur = conn.execute("SELECT * FROM clientes WHERE codigo=?", (cod,))
        row = cur.fetchone()
        if not row or not items:
            return jsonify({"error": "Datos inválidos."}), 400
        cliente = row_to_dict(cur, row)

        nombre      = cliente["nombre"]
        total       = round(sum(i["cantidad"] * i["precio"] for i in items), 2)
        nueva_deuda = round(float(cliente["deuda"]) + total, 2)

        for item in items:
            importe = round(item["cantidad"] * item["precio"], 2)
            conn.execute("""
                INSERT INTO comprobantes
                (nro, fecha, cod_cliente, nombre_cliente, descripcion, cantidad, precio_unit, importe)
                VALUES (?,?,?,?,?,?,?,?)
            """, (nro, fecha, cod, nombre, item["nombre"], item["cantidad"], item["precio"], importe))

        conn.execute("UPDATE clientes SET deuda=? WHERE codigo=?", (nueva_deuda, cod))

        for item in items:
            conn.execute("UPDATE productos SET stock = MAX(0, stock - ?) WHERE codigo=?",
                         (item["cantidad"], item["codigo"]))

        conn.commit()
        return jsonify({"ok": True, "fecha": fecha, "nombre": nombre, "total": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── PAGOS ──

@app.route("/api/pagos", methods=["POST"])
def post_pago():
    cod = cliente_sesion()
    if not cod:
        return jsonify({"error": "No autenticado."}), 401
    try:
        data  = request.json
        monto = round(float(data["monto"]), 2)
        fecha = datetime.now().strftime("%d/%m/%Y")

        conn = get_conn()
        cur = conn.execute("SELECT * FROM clientes WHERE codigo=?", (cod,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Cliente no encontrado."}), 404
        cliente = row_to_dict(cur, row)

        deuda_actual = float(cliente["deuda"])
        if deuda_actual <= 0:
            return jsonify({"error": "No tenés deuda pendiente."}), 400

        aplicado       = round(min(monto, deuda_actual), 2)
        deuda_restante = round(deuda_actual - aplicado, 2)
        nombre         = cliente["nombre"]

        conn.execute("""
            INSERT INTO pagos (fecha, cod_cliente, nombre_cliente, monto_aplicado, deuda_restante)
            VALUES (?,?,?,?,?)
        """, (fecha, cod, nombre, aplicado, deuda_restante))

        conn.execute("UPDATE clientes SET deuda=? WHERE codigo=?", (deuda_restante, cod))
        conn.commit()
        return jsonify({"ok": True, "aplicado": aplicado, "deuda_restante": deuda_restante})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────────────
# ARRANQUE
# ──────────────────────────────────────────────

print("\n[ERP] Conectando a Turso...")
inicializar_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
