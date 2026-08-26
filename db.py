import sqlite3
import os
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lojas.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()

def seed_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executemany(
        "INSERT INTO departamentos (nome) VALUES (?)",
        [("higiene",), ("bebidas",), ("alimentos",)]
    )

    conn.executemany(
        "INSERT INTO produtos (nome, departamento_id, preco) VALUES (?, ?, ?)",
        [
            ("sabonete", 1, 4.50),
            ("agua", 2, 2.00),
            ("coca", 2, 6.50),
            ("arroz", 3, 22.90),
            ("feijao", 3, 8.50),
        ]
    )

    conn.executemany(
        "INSERT INTO estoque (produto_id, lote, quantidade, data_fabricacao, data_validade) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "L001", 50, "2026-01-10", "2027-01-10"),
            (2, "L002", 100, "2025-06-01", "2026-06-01"),
            (3, "L003", 80, "2026-05-01", "2026-11-01"),
            (4, "L004", 30, "2025-01-01", "2026-01-01"),
            (5, "L005", 40, "2026-07-01", "2027-07-01"),
        ]
    )

    conn.commit()
    conn.close()

def listar_departamentos():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, nome FROM departamentos").fetchall()
    conn.close()
    return [dict(row) for row in rows]

def listar_produtos(departamento=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    query = """
        SELECT produtos.id, produtos.nome, departamentos.nome AS departamento, produtos.preco
        FROM produtos
        JOIN departamentos ON produtos.departamento_id = departamentos.id
    """
    params = ()
    if departamento is not None:
        query += " WHERE departamentos.nome = ?"
        params = (departamento,)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def buscar_produto_por_id(produto_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    query = """
        SELECT produtos.id, produtos.nome, departamentos.nome AS departamento, produtos.preco
        FROM produtos
        JOIN departamentos ON produtos.departamento_id = departamentos.id
        WHERE produtos.id = ?
        """

    row = conn.execute(query, (produto_id,)).fetchone()
    conn.close()
    return dict(row) if row is not None else None

def listar_estoque(produto=None, vence_ate=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    query = """
        SELECT estoque.id, produtos.nome AS produto, departamentos.nome AS departamento,
               estoque.lote, estoque.quantidade, estoque.data_fabricacao, estoque.data_validade
        FROM estoque
        JOIN produtos ON estoque.produto_id = produtos.id
        JOIN departamentos ON produtos.departamento_id = departamentos.id
    """
    condicoes = []
    params = []
    if produto is not None:
        condicoes.append("produtos.nome = ?")
        params.append(produto)
    if vence_ate is not None:
        condicoes.append("estoque.data_validade <= ?")
        params.append(vence_ate)
    if condicoes:
        query += " WHERE " + " AND ".join(condicoes)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    hoje = datetime.date.today()
    resultado = []
    for row in rows:
        item = dict(row)
        data_validade = datetime.date.fromisoformat(item["data_validade"])
        item["dias_para_vencer"] = (data_validade - hoje).days
        resultado.append(item)
    return resultado

def listar_vencidos(data_ref=None):
    if data_ref is None:
        data_ref = datetime.date.today().isoformat()
    return listar_estoque(vence_ate=data_ref)

def get_schema_ddl():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return f.read()

if __name__== "__main__":
    init_db()
    seed_db()