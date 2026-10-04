-- ============================================================
-- DATA WAREHOUSE - PRODUTIVIDADE OPERACIONAL (PostgreSQL)
-- ============================================================
CREATE SCHEMA IF NOT EXISTS dw;

-- ---------- DIMENSÕES ----------
CREATE TABLE dw.dim_maquina (
    id_maquina      SERIAL PRIMARY KEY,
    nome_maquina    VARCHAR(60)  NOT NULL,
    linha           VARCHAR(30)  NOT NULL,
    setor           VARCHAR(30)  NOT NULL,
    data_instalacao DATE
);

CREATE TABLE dw.dim_turno (
    id_turno    SERIAL PRIMARY KEY,
    nome_turno  VARCHAR(20) NOT NULL,     -- Manhã, Tarde, Noite
    hora_inicio TIME NOT NULL,
    hora_fim    TIME NOT NULL
);

CREATE TABLE dw.dim_produto (
    id_produto     SERIAL PRIMARY KEY,
    nome_produto   VARCHAR(80) NOT NULL,
    familia        VARCHAR(40),
    custo_unitario NUMERIC(10,2) NOT NULL
);

CREATE TABLE dw.dim_motivo_parada (
    id_motivo   SERIAL PRIMARY KEY,
    motivo      VARCHAR(80) NOT NULL,
    categoria   VARCHAR(40) NOT NULL,       -- Mecânica, Elétrica, Logística...
    tipo_parada VARCHAR(15) NOT NULL
        CHECK (tipo_parada IN ('PLANEJADA', 'CORRETIVA'))
);

CREATE TABLE dw.dim_calendario (
    data       DATE PRIMARY KEY,
    ano        SMALLINT NOT NULL,
    mes        SMALLINT NOT NULL,
    semana     SMALLINT NOT NULL,
    dia_semana SMALLINT NOT NULL,
    dia_util   BOOLEAN  NOT NULL
);

-- ---------- FATOS ----------
CREATE TABLE dw.fato_ordem_producao (
    id_ordem       SERIAL PRIMARY KEY,
    id_produto     INT NOT NULL REFERENCES dw.dim_produto(id_produto),
    qtd_planejada  INT NOT NULL CHECK (qtd_planejada > 0),
    data_abertura  DATE NOT NULL REFERENCES dw.dim_calendario(data),
    data_prevista  DATE NOT NULL
);

CREATE TABLE dw.fato_apontamento (
    id_apontamento   BIGSERIAL PRIMARY KEY,
    id_ordem         INT NOT NULL REFERENCES dw.fato_ordem_producao(id_ordem),
    id_maquina       INT NOT NULL REFERENCES dw.dim_maquina(id_maquina),
    id_turno         INT NOT NULL REFERENCES dw.dim_turno(id_turno),
    data             DATE NOT NULL REFERENCES dw.dim_calendario(data),
    horas_produtivas NUMERIC(6,2) NOT NULL CHECK (horas_produtivas >= 0),
    qtd_produzida    INT NOT NULL CHECK (qtd_produzida >= 0),
    qtd_rejeitada    INT NOT NULL DEFAULT 0 CHECK (qtd_rejeitada >= 0)
);

CREATE TABLE dw.fato_parada (
    id_parada     BIGSERIAL PRIMARY KEY,
    id_maquina    INT NOT NULL REFERENCES dw.dim_maquina(id_maquina),
    id_turno      INT NOT NULL REFERENCES dw.dim_turno(id_turno),
    id_motivo     INT NOT NULL REFERENCES dw.dim_motivo_parada(id_motivo),
    inicio_parada TIMESTAMP NOT NULL,
    fim_parada    TIMESTAMP NOT NULL,
    CHECK (fim_parada > inicio_parada)
);

CREATE TABLE dw.fato_meta_operacional (
    id_meta                 SERIAL PRIMARY KEY,
    id_maquina              INT NOT NULL REFERENCES dw.dim_maquina(id_maquina),
    mes_referencia          DATE NOT NULL,   -- primeiro dia do mês
    meta_disponibilidade    NUMERIC(5,4) NOT NULL,   -- ex.: 0.9000
    meta_refugo             NUMERIC(5,4) NOT NULL,   -- ex.: 0.0300
    meta_volume             INT NOT NULL,
    UNIQUE (id_maquina, mes_referencia)
);

-- ---------- ÍNDICES PARA CONSULTAS ANALÍTICAS ----------
CREATE INDEX idx_apont_maquina_data ON dw.fato_apontamento (id_maquina, data);
CREATE INDEX idx_parada_maquina_inicio ON dw.fato_parada (id_maquina, inicio_parada);
