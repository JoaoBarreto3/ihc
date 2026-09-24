import datetime
import os
import sqlite3

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lojas.db")

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS departamentos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE,
    departamento_id INTEGER NOT NULL REFERENCES departamentos(id),
    preco REAL NOT NULL CHECK (preco >= 0)
);

CREATE TABLE IF NOT EXISTS estoque (
    id INTEGER PRIMARY KEY,
    produto_id INTEGER NOT NULL REFERENCES produtos(id),
    lote TEXT NOT NULL,
    quantidade INTEGER NOT NULL CHECK (quantidade >= 0),
    data_fabricacao TEXT NOT NULL,
    data_validade TEXT NOT NULL,
    UNIQUE (produto_id, lote)
);
"""

# -----------------------------------------Banco de dados-----------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()


def seed_db():
    conn = sqlite3.connect(DB_PATH)

    # So insere os dados de exemplo se o banco ainda estiver vazio
    ja_tem_dados = conn.execute("SELECT COUNT(*) FROM departamentos").fetchone()[0] > 0
    if ja_tem_dados:
        conn.close()
        return

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


def get_schema_ddl():
    return SCHEMA_SQL


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


def apenas_select(action_code, arg1, arg2, db_name, trigger_name):
    """Autorizador do sqlite3 que so deixa passar leituras (SELECT)."""
    if action_code in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def executar_consulta(sql):
    """Roda uma consulta SQL contra o banco real, em modo somente leitura."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.set_authorizer(apenas_select)
        cursor = conn.execute(sql)
        colunas = [d[0] for d in cursor.description] if cursor.description else []
        linhas = cursor.fetchall()
        return colunas, linhas
    finally:
        conn.close()


# -----------------------------------------FastAPI-----------------------------------------

app = FastAPI(
    title="Lojas API",
    description="Consulta de produtos e estoque",
)

# -----------------------------------------Respostas-----------------------------------------

class Departamento(BaseModel):
    id: int
    nome: str


class Produto(BaseModel):
    id: int
    nome: str
    departamento: str
    preco: float


class LoteEstoque(BaseModel):
    id: int
    produto: str
    departamento: str
    lote: str
    quantidade: int
    data_fabricacao: str
    data_validade: str
    dias_para_vencer: int


class ConsultaResposta(BaseModel):
    colunas: list[str]
    linhas: list[list]
    erro: str | None = None

# -----------------------------------------Rotas-----------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "tabelas": len(get_schema_ddl().split("CREATE TABLE")) - 1}


@app.get("/schema", response_class=None)
def get_schema():
    return {"ddl": get_schema_ddl()}


@app.get("/departamentos", response_model=list[Departamento])
def get_departamentos():
    return listar_departamentos()


@app.get("/produtos", response_model=list[Produto])
def get_produtos(
    departamento: str | None = Query(None, description="filtra por nome do departamento")
):
    return listar_produtos(departamento)


@app.get("/produtos/{produto_id}", response_model=Produto)
def get_produto(produto_id: int):
    produto = buscar_produto_por_id(produto_id)
    if produto is None:
        raise HTTPException(status_code=404, detail=f"produto {produto_id} não encontrado")
    return produto


@app.get("/estoque", response_model=list[LoteEstoque])
def get_estoque(
    produto: str | None = Query(None, description="filtra por nome do produto"),
    vence_ate: str | None = Query(None, description="data limite no formato YYYY-MM-DD"),
):
    return listar_estoque(produto=produto, vence_ate=vence_ate)


@app.get("/estoque/vencidos", response_model=list[LoteEstoque])
def get_vencidos(
    data_ref: str | None = Query(None, description="data de referência YYYY-MM-DD")
):
    return listar_vencidos(data_ref)


@app.get("/consulta", response_model=ConsultaResposta)
def get_consulta(
    sql: str = Query(..., description="consulta SQL (somente leitura) a ser executada")
):
    """Executa uma string SQL (gerada, por exemplo, pelo bot) contra o banco, em modo somente leitura."""
    try:
        colunas, linhas = executar_consulta(sql)
    except sqlite3.Error as e:
        return ConsultaResposta(colunas=[], linhas=[], erro=str(e))
    return ConsultaResposta(colunas=colunas, linhas=[list(linha) for linha in linhas], erro=None)


if __name__ == "__main__":
    init_db()
    seed_db()