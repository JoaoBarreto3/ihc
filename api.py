from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

import db

app = FastAPI(
    title="Lojas API",
    description="Consulta de produtos e estoque",
)

#-----------------------------------------Respostas-----------------------------------------

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

#-----------------------------------------Rotas-----------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "tabelas": len(db.get_schema_ddl().split("CREATE TABLE")) - 1}


@app.get("/schema", response_class=None)
def get_schema():
    return {"ddl": db.get_schema_ddl()}


@app.get("/departamentos", response_model=list[Departamento])
def get_departamentos():
    return db.listar_departamentos()


@app.get("/produtos", response_model=list[Produto])
def get_produtos(
    departamento: str | None = Query(None, description="filtra por nome do departamento")
):
    return db.listar_produtos(departamento)


@app.get("/produtos/{produto_id}", response_model=Produto)
def get_produto(produto_id: int):
    produto = db.buscar_produto_por_id(produto_id)
    if produto is None:
        raise HTTPException(status_code=404, detail=f"produto {produto_id} não encontrado")
    return produto


@app.get("/estoque", response_model=list[LoteEstoque])
def get_estoque(
    produto: str | None = Query(None, description="filtra por nome do produto"),
    vence_ate: str | None = Query(None, description="data limite no formato YYYY-MM-DD"),
):
    return db.listar_estoque(produto=produto, vence_ate=vence_ate)


@app.get("/estoque/vencidos", response_model=list[LoteEstoque])
def get_vencidos(
    data_ref: str | None = Query(None, description="data de referência YYYY-MM-DD")
):
    return db.listar_vencidos(data_ref)
