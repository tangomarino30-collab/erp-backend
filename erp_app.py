"""
ERP Server - Railway deployment
"""

import os
from datetime import datetime
from flask import Flask, jsonify, request, session
from flask_cors import CORS
import pymysql
import pymysql.cursors
from dbutils.pooled_db import PooledDB

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "erp_secret_key_2024")
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True
)
# CORS: permite llamadas desde GitHub Pages
CORS(app,
     supports_credentials=True,
     origins=os.environ.get("ALLOWED_ORIGIN", "*"))

PORT = int(os.environ.get("PORT", 5050))

# ──────────────────────────────────────────────
# POOL DE CONEXIONES
# ──────────────────────────────────────────────

DB_CONFIG = {
    "host":        os.environ.get("DB_HOST", "ballast.proxy.rlwy.net"),
    "port":        int(os.environ.get("DB_PORT", 52354)),
    "user":        os.environ.get("DB_USER", "root"),
    # Ponemos tu clave real de Railway como respaldo si la variable de entorno falla:
    "password":    os.environ.get("DB_PASSWORD", "idJOASRTJSKhKqWSFyGlmXNmrshXrsnn"),
    "database":    os.environ.get("DB_NAME", "railway"),
    "charset":     "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit":  False
}

pool = PooledDB(
    creator=pymysql, maxconnections=10,
    mincached=2, maxcached=5,
    blocking=True, ping=1, **DB_CONFIG
)

def get_conn():
    return pool.connection()

# ──────────────────────────────────────────────
# INICIALIZAR TABLAS
# ──────────────────────────────────────────────

def inicializar_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clientes (
                    codigo  VARCHAR(20)   PRIMARY KEY,
                    nombre  VARCHAR(100)  NOT NULL,
                    email   VARCHAR(100)  DEFAULT '',
                    deuda   DECIMAL(12,2) DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS productos (
                    codigo  VARCHAR(20)   PRIMARY KEY,
                    nombre  VARCHAR(100)  NOT NULL,
                    precio  DECIMAL(12,2) DEFAULT 0,
                    stock   INT           DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS comprobantes (
                    id             INT AUTO_INCREMENT PRIMARY KEY,
                    nro            VARCHAR(20),
                    fecha          VARCHAR(20),
                    cod_cliente    VARCHAR(20),
                    nombre_cliente VARCHAR(100),
                    descripcion    VARCHAR(200),
                    cantidad       INT           DEFAULT 1,
                    precio_unit    DECIMAL(12,2) DEFAULT 0,
                    importe        DECIMAL(12,2) DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pagos (
                    id             INT AUTO_INCREMENT PRIMARY KEY,
                    fecha          VARCHAR(20),
                    cod_cliente    VARCHAR(20),
                    nombre_cliente VARCHAR(100),
                    monto_aplicado DECIMAL(12,2) DEFAULT 0,
                    deuda_restante DECIMAL(12,2) DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    nombre      VARCHAR(50)  NOT NULL UNIQUE,
                    clave       VARCHAR(50)  NOT NULL,
                    cod_cliente VARCHAR(20)  NOT NULL
                )
            """)
        conn.commit()
        print("[ERP] Tablas verificadas ✓")
    except Exception as e:
        print(f"[ERROR Inicializar DB]: {e}")
    finally:
        conn.close()

# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def cliente_sesion():
    return session.get("cod_cliente")

def usuario_sesion():
    return session.get("usuario")

# ──────────────────────────────────────────────
# RUTAS
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"status": "ERP API running"})

# ── LOGIN / LOGOUT ──

@app.route("/api/login", methods=["POST"])
def login():
    conn = get_conn()
    try:
        data   = request.json
        nombre = data.get("usuario", "").strip().lower()
        clave  = data.get("clave", "").strip()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM usuarios WHERE nombre=%s AND clave=%s",
                (nombre, clave)
            )
            user = cur.fetchone()
        if not user:
            return jsonify({"error": "Usuario o clave incorrectos."}), 401
        session["usuario"]     = user["nombre"]
        session["cod_cliente"] = user["cod_cliente"]
        return jsonify({"ok": True, "usuario": user["nombre"], "cod_cliente": user["cod_cliente"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/sesion", methods=["GET"])
def sesion():
    if not usuario_sesion():
        return jsonify({"logueado": False})
    return jsonify({"logueado": True, "usuario": usuario_sesion(), "cod_cliente": cliente_sesion()})

# ── CLIENTES ──

@app.route("/api/clientes", methods=["GET"])
def get_clientes():
    cod = cliente_sesion()
    if not cod:
        return jsonify({"error": "No autenticado."}), 401
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clientes WHERE codigo=%s", (cod,))
            row = cur.fetchone()
            if not row:
                return jsonify({})
            cur.execute("SELECT * FROM comprobantes WHERE cod_cliente=%s ORDER BY id ASC", (cod,))
            comps = cur.fetchall()
            cur.execute("SELECT * FROM pagos WHERE cod_cliente=%s ORDER BY id ASC", (cod,))
            pagos = cur.fetchall()

        cliente = {
            "nombre":      row["nombre"],
            "email":       row["email"] or "",
            "deuda":       float(row["deuda"]),
            "movimientos": [],
            "pagos":       [],
        }

        vistos = {}
        for c in comps:
            nro = c["nro"]
            if nro not in vistos:
                vistos[nro] = {"nro": nro, "fecha": c["fecha"],
                               "desc": c["descripcion"], "val": 0.0}
            vistos[nro]["val"] = round(vistos[nro]["val"] + float(c["importe"]), 2)
        cliente["movimientos"] = list(vistos.values())

        for p in pagos:
            cliente["pagos"].append({"fecha": p["fecha"], "val": float(p["monto_aplicado"])})

        return jsonify({cod: cliente})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ── REGISTRO ──

@app.route("/api/registro", methods=["POST"])
def registro():
    conn = get_conn()
    try:
        data  = request.json
        user  = data.get("usuario", "").strip().lower()
        clave = data.get("clave", "").strip()
        if not user or not clave:
            return jsonify({"error": "Completá usuario y clave."}), 400
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM usuarios WHERE nombre=%s", (user,))
            if cur.fetchone():
                return jsonify({"error": "Ese nombre de usuario ya existe."}), 400
            cur.execute("SELECT codigo FROM clientes")
            codigos = set(int(r["codigo"]) for r in cur.fetchall() if str(r["codigo"]).isdigit())
            nuevo_cod = 1
            while nuevo_cod in codigos:
                nuevo_cod += 1
            nuevo_cod = str(nuevo_cod)
            cur.execute(
                "INSERT INTO clientes (codigo, nombre, deuda) VALUES (%s,%s,0)",
                (nuevo_cod, user)
            )
            cur.execute(
                "INSERT INTO usuarios (nombre, clave, cod_cliente) VALUES (%s,%s,%s)",
                (user, clave, nuevo_cod)
            )
        conn.commit()
        return jsonify({"ok": True, "codigo": nuevo_cod})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ── PRODUCTOS ──

@app.route("/api/productos", methods=["GET"])
def get_productos():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM productos ORDER BY nombre ASC")
            rows = cur.fetchall()
        return jsonify([{
            "codigo": r["codigo"],
            "nombre": r["nombre"],
            "precio": float(r["precio"]),
            "stock":  int(r["stock"]),
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ──────────────────────────────────────────────
# ARRANQUE DE LA APLICACIÓN (CORRECCIÓN CRÍTICA)
# ──────────────────────────────────────────────

if __name__ == '__main__':
    inicializar_db()
    # Ejecuta en el puerto asignado dinámicamente por Railway
    app.run(host="0.0.0.0", port=PORT)
