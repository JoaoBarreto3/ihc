import datetime
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "lojas.db"))

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
    UNIQUE (produto_id, lote),
    CHECK (data_validade > data_fabricacao)
);
"""

DEPARTAMENTOS = [
    (1, "higiene"),
    (2, "bebidas"),
    (3, "alimentos"),
    (4, "limpeza"),
    (5, "laticinios"),
]

PRODUTOS = [
    (1, "sabonete", 1, 4.50),
    (2, "agua", 2, 2.00),
    (3, "coca", 2, 6.50),
    (4, "arroz", 3, 22.90),
    (5, "feijao", 3, 8.50),
    (6, "shampoo", 1, 18.90),
    (7, "pasta de dente", 1, 7.20),
    (8, "suco de laranja", 2, 9.80),
    (9, "cerveja", 2, 4.20),
    (10, "macarrao", 3, 5.40),
    (11, "detergente", 4, 2.90),
    (12, "agua sanitaria", 4, 6.00),
    (13, "leite", 5, 5.20),
    (14, "queijo", 5, 39.90),
    (15, "iogurte", 5, 3.80),
]

ESTOQUE = [
    (1, 1, "L001", 50, "2026-01-10", "2027-01-10"),
    (2, 2, "L002", 100, "2025-06-01", "2026-06-01"),
    (3, 3, "L003", 80, "2026-05-01", "2026-11-01"),
    (4, 4, "L004", 30, "2025-01-01", "2026-01-01"),
    (5, 5, "L005", 40, "2026-07-01", "2027-07-01"),
    (6, 2, "L006", 120, "2026-08-01", "2027-08-01"),
    (7, 6, "L007", 25, "2026-03-15", "2028-03-15"),
    (8, 7, "L008", 60, "2026-02-01", "2027-02-01"),
    (9, 8, "L009", 35, "2026-09-20", "2026-10-20"),
    (10, 9, "L010", 200, "2026-06-10", "2026-12-10"),
    (11, 10, "L011", 90, "2026-04-01", "2027-10-01"),
    (12, 11, "L012", 70, "2026-01-05", "2028-01-05"),
    (13, 12, "L013", 45, "2025-09-01", "2026-09-01"),
    (14, 13, "L014", 150, "2026-09-25", "2026-10-25"),
    (15, 13, "L015", 80, "2026-08-20", "2026-09-30"),
    (16, 14, "L016", 12, "2026-07-01", "2026-12-01"),
    (17, 15, "L017", 55, "2026-09-28", "2026-10-28"),
    (18, 4, "L018", 65, "2026-06-01", "2027-06-01"),
]


def autorizador_somente_leitura(action_code, arg1, arg2, db_name, trigger_name):
    if action_code in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                       sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class BancoLojas:
    SQL_PRODUTOS = """
        SELECT produtos.id, produtos.nome, departamentos.nome AS departamento, produtos.preco
        FROM produtos
        JOIN departamentos ON produtos.departamento_id = departamentos.id
    """

    SQL_ESTOQUE = """
        SELECT estoque.id, produtos.nome AS produto, departamentos.nome AS departamento,
               estoque.lote, estoque.quantidade, estoque.data_fabricacao, estoque.data_validade
        FROM estoque
        JOIN produtos ON estoque.produto_id = produtos.id
        JOIN departamentos ON produtos.departamento_id = departamentos.id
    """

    def __init__(self, caminho=DB_PATH):
        self.caminho = caminho

    @contextmanager
    def _conectar(self, somente_leitura=False):
        conn = sqlite3.connect(self.caminho)
        conn.row_factory = sqlite3.Row
        if somente_leitura:
            conn.set_authorizer(autorizador_somente_leitura)
        try:
            yield conn
        finally:
            conn.close()

    @property
    def schema(self):
        return SCHEMA_SQL

    def inicializar(self):
        with self._conectar() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.executemany("INSERT OR IGNORE INTO departamentos (id, nome) VALUES (?, ?)", DEPARTAMENTOS)
            conn.executemany(
                "INSERT OR IGNORE INTO produtos (id, nome, departamento_id, preco) VALUES (?, ?, ?, ?)", PRODUTOS)
            conn.executemany(
                "INSERT OR IGNORE INTO estoque (id, produto_id, lote, quantidade, data_fabricacao, data_validade) "
                "VALUES (?, ?, ?, ?, ?, ?)", ESTOQUE)
            conn.commit()

    def listar_departamentos(self):
        with self._conectar() as conn:
            return [dict(r) for r in conn.execute("SELECT id, nome FROM departamentos ORDER BY id")]

    def listar_produtos(self, departamento=None):
        query, params = self.SQL_PRODUTOS, ()
        if departamento is not None:
            query += " WHERE departamentos.nome = ?"
            params = (departamento,)
        with self._conectar() as conn:
            return [dict(r) for r in conn.execute(query + " ORDER BY produtos.id", params)]

    def buscar_produto(self, produto_id):
        with self._conectar() as conn:
            row = conn.execute(self.SQL_PRODUTOS + " WHERE produtos.id = ?", (produto_id,)).fetchone()
        return dict(row) if row is not None else None

    def listar_estoque(self, produto=None, departamento=None, vence_ate=None):
        condicoes, params = [], []
        if produto is not None:
            condicoes.append("produtos.nome = ?")
            params.append(produto)
        if departamento is not None:
            condicoes.append("departamentos.nome = ?")
            params.append(departamento)
        if vence_ate is not None:
            condicoes.append("estoque.data_validade <= ?")
            params.append(vence_ate)
        query = self.SQL_ESTOQUE
        if condicoes:
            query += " WHERE " + " AND ".join(condicoes)

        with self._conectar() as conn:
            rows = conn.execute(query + " ORDER BY estoque.data_validade", params).fetchall()

        hoje = datetime.date.today()
        resultado = []
        for row in rows:
            item = dict(row)
            item["dias_para_vencer"] = (datetime.date.fromisoformat(item["data_validade"]) - hoje).days
            resultado.append(item)
        return resultado

    def listar_vencidos(self, data_ref=None):
        return self.listar_estoque(vence_ate=data_ref or datetime.date.today().isoformat())

    def executar_consulta(self, sql):
        with self._conectar(somente_leitura=True) as conn:
            cursor = conn.execute(sql)
            colunas = [d[0] for d in cursor.description] if cursor.description else []
            return colunas, [tuple(linha) for linha in cursor.fetchall()]


banco = BancoLojas()


@asynccontextmanager
async def lifespan(app):
    banco.inicializar()
    yield


app = FastAPI(
    title="Lojas API",
    description="Consulta de departamentos, produtos e estoque (lotes com fabricacao e validade)",
    lifespan=lifespan,
)


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


@app.get("/health")
def health():
    return {"status": "ok", "tabelas": banco.schema.count("CREATE TABLE")}


@app.get("/schema")
def get_schema():
    return {"ddl": banco.schema}


@app.get("/departamentos", response_model=list[Departamento])
def get_departamentos():
    return banco.listar_departamentos()


@app.get("/produtos", response_model=list[Produto])
def get_produtos(
    departamento: str | None = Query(None, description="filtra por nome do departamento")
):
    return banco.listar_produtos(departamento)


@app.get("/produtos/{produto_id}", response_model=Produto)
def get_produto(produto_id: int):
    produto = banco.buscar_produto(produto_id)
    if produto is None:
        raise HTTPException(status_code=404, detail=f"produto {produto_id} não encontrado")
    return produto


@app.get("/estoque", response_model=list[LoteEstoque])
def get_estoque(
    produto: str | None = Query(None, description="filtra por nome do produto"),
    departamento: str | None = Query(None, description="filtra por nome do departamento"),
    vence_ate: str | None = Query(None, description="data limite no formato YYYY-MM-DD"),
):
    return banco.listar_estoque(produto=produto, departamento=departamento, vence_ate=vence_ate)


@app.get("/estoque/vencidos", response_model=list[LoteEstoque])
def get_vencidos(
    data_ref: str | None = Query(None, description="data de referência YYYY-MM-DD")
):
    return banco.listar_vencidos(data_ref)


@app.get("/consulta", response_model=ConsultaResposta)
def get_consulta(
    sql: str = Query(..., description="consulta SQL (somente leitura) a ser executada")
):
    try:
        colunas, linhas = banco.executar_consulta(sql)
    except sqlite3.Error as e:
        return ConsultaResposta(colunas=[], linhas=[], erro=str(e))
    return ConsultaResposta(colunas=colunas, linhas=[list(linha) for linha in linhas], erro=None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
