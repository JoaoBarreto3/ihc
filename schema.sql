PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS departamentos (
    id INTEGER PRIMARY KEY,
    nome TEXTO NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY,
    nome TEXTO NOT NULL UNIQUE,
    departamento_id INTEGER NOT NULL REFERENCES departamentos(id),
    preco REAL NOT NULL CHECK (preco>=0)
);

CREATE TABLE IF NOT EXISTS estoque (
    id INTEGER PRIMARY KEY,
    produto_id INTEGER NOT NULL REFERENCES produtos(id),
    lote TEXT NOT NULL,
    quantidade INTEGER NOT NULL CHECK (quantidade >= 0),
    data_fabricacao TEXT NOT NULL,
    data_validade TEXTO NOT NULL,
    UNIQUE (produto_id, lote)
);
