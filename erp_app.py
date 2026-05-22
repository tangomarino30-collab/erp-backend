"""
ERP Server - Railway deployment (JWT Architecture)
"""

import os
import jwt
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, jsonify, request
from flask_cors import CORS
import pymysql
import pymysql.cursors

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "erp_secret_key_2026")
CORS(app, origins="*")

PORT = int(os.environ.get("PORT", 5050))

def get_conn():
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "ballast.proxy.rlwy.net"),
        port=int(os.environ.get("DB_PORT", 52354)),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", "idJOASRTJSKhKqWSFyGlmXNmrshXrsnn"),
        database=os.environ.get("DB_NAME", "railway"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def token_requerido(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token:
            return jsonify({"error": "Token ausente."}), 401
        try:
            if token.startswith("Bearer "):
                token = token.split(" ")[1]
            data = jwt.decode(token, app.secret_key, algorithms=["HS256"])
            current_user = data["cod_cliente"]
        except:
            return jsonify({"error": "Token inválido o expirado."}), 401
        return f(current_user, *args, **kwargs)
    return decorated

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
        print("[ERP] Tablas verificadas ✓")
    except Exception as e:
        print(f"[ERROR Inicializar DB]: {e}")
    finally:
        conn.close()

@app.route("/")
def index():
    return jsonify({"status": "ERP API running"})

@app.route("/api/login", methods=["POST"])
def login():
    conn = get_conn()
    try:
        data   = request.json
        nombre = data.get("usuario", "").strip().lower()
        clave  = data.get("clave", "").strip()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM usuarios WHERE nombre=%s AND clave=%s", (nombre, clave))
            user = cur.fetchone()
        if not user:
            return jsonify({"error": "Usuario o clave incorrectos."}), 401
        
        token = jwt.encode({
            "usuario": user["nombre"],
            "cod_cliente": user["cod_cliente"],
            "exp": datetime.now(timezone.utc) + timedelta(hours=24)
        }, app.secret_key, algorithm=["HS256"])
        
        return jsonify({"ok": True, "token": token, "usuario": user["nombre"], "cod_cliente": user["cod_cliente"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
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
            cur.execute("INSERT INTO clientes (codigo, nombre, deuda) VALUES (%s,%s,0)", (nuevo_cod, user))
            cur.execute("INSERT INTO usuarios (nombre, clave, cod_cliente) VALUES (%s,%s,%s)", (user, clave, nuevo_cod))
        return jsonify({"ok": True, "codigo": nuevo_cod})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/clientes", methods=["GET"])
@token_requerido
def get_clientes(current_user):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clientes WHERE codigo=%s", (current_user,))
            row = cur.fetchone()
            if not row:
                return jsonify({})
            cur.execute("SELECT * FROM comprobantes WHERE cod_cliente=%s ORDER BY id ASC", (current_user,))
            comps = cur.fetchall()
            cur.execute("SELECT * FROM pagos WHERE cod_cliente=%s ORDER BY id ASC", (current_user,))
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
                vistos[nro] = {"nro": nro, "fecha": c["fecha"], "desc": c["descripcion"], "val": 0.0}
            vistos[nro]["val"] = round(vistos[nro]["val"] + float(c["importe"]), 2)
        cliente["movimientos"] = list(vistos.values())

        for p in pagos:
            cliente["pagos"].append({"fecha": p["fecha"], "val": float(p["monto_aplicado"])})

        return jsonify({current_user: cliente})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/productos", methods=["GET"])
def get_productos():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM productos ORDER BY nombre ASC")
            rows = cur.fetchall()
        return jsonify([{
            "codigo": r["codigo"], "nombre": r["nombre"],
            "precio": float(r["precio"]), "stock":  int(r["stock"]),
        } for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/pedido", methods=["POST"])
@token_requerido
def post_pedido(current_user):
    conn = get_conn()
    try:
        data  = request.json
        nro   = data["nro"]
        items = data["items"]
        fecha = datetime.now().strftime("%d/%m/%Y")

        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clientes WHERE codigo=%s", (current_user,))
            cliente = cur.fetchone()
            if not cliente:
                return jsonify({"error": "Cliente no encontrado."}), 404
            if not items:
                return jsonify({"error": "El carrito está vacío."}), 400

            nombre      = cliente["nombre"]
            total       = round(sum(i["cantidad"] * i["precio"] for i in items), 2)
            nueva_deuda = round(float(cliente["deuda"]) + total, 2)

            for item in items:
                importe = round(item["cantidad"] * item["precio"], 2)
                cur.execute("""
                    INSERT INTO comprobantes
                    (nro, fecha, cod_cliente, nombre_cliente, descripcion, cantidad, precio_unit, importe)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (nro, fecha, current_user, nombre, item["nombre"], item["cantidad"], item["precio"], importe))

            cur.execute("UPDATE clientes SET deuda=%s WHERE codigo=%s", (nueva_deuda, current_user))

            for item in items:
                cur.execute("UPDATE productos SET stock = GREATEST(0, stock - %s) WHERE codigo=%s", (item["cantidad"], item["codigo"]))

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    inicializar_db()
    app.run(host="0.0.0.0", port=PORT)
