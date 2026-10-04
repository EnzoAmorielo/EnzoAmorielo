-- ============================================================
-- Query 1 - Disponibilidade mensal por máquina vs. meta
-- ============================================================
-- CTEs separadas para evitar duplicação de linhas ao juntar duas tabelas fato
WITH produtivas AS (
    SELECT
        id_maquina,
        DATE_TRUNC('month', data)::DATE AS mes,
        SUM(horas_produtivas)           AS horas_produtivas
    FROM dw.fato_apontamento
    GROUP BY id_maquina, DATE_TRUNC('month', data)
),
paradas AS (
    SELECT
        id_maquina,
        DATE_TRUNC('month', inicio_parada)::DATE AS mes,
        SUM(EXTRACT(EPOCH FROM (fim_parada - inicio_parada)) / 3600.0) AS horas_paradas
    FROM dw.fato_parada
    GROUP BY id_maquina, DATE_TRUNC('month', inicio_parada)
)
SELECT
    m.nome_maquina,
    m.linha,
    p.mes,
    ROUND(p.horas_produtivas, 1)                         AS horas_produtivas,
    ROUND(COALESCE(pa.horas_paradas, 0), 1)              AS horas_paradas,
    ROUND(p.horas_produtivas
          / NULLIF(p.horas_produtivas + COALESCE(pa.horas_paradas, 0), 0), 4)
                                                         AS disponibilidade,
    meta.meta_disponibilidade,
    ROUND(p.horas_produtivas
          / NULLIF(p.horas_produtivas + COALESCE(pa.horas_paradas, 0), 0)
          - meta.meta_disponibilidade, 4)                AS desvio_vs_meta
FROM produtivas p
JOIN dw.dim_maquina m              ON m.id_maquina = p.id_maquina
LEFT JOIN paradas pa               ON pa.id_maquina = p.id_maquina AND pa.mes = p.mes
JOIN dw.fato_meta_operacional meta ON meta.id_maquina = p.id_maquina
                                  AND meta.mes_referencia = p.mes
ORDER BY p.mes, desvio_vs_meta;

-- ============================================================
-- Query 2 - Pareto de motivos de parada (Window Functions)
-- ============================================================
WITH horas_por_motivo AS (
    SELECT
        mp.motivo,
        mp.categoria,
        SUM(EXTRACT(EPOCH FROM (fp.fim_parada - fp.inicio_parada)) / 3600.0) AS horas_paradas
    FROM dw.fato_parada fp
    JOIN dw.dim_motivo_parada mp ON mp.id_motivo = fp.id_motivo
    WHERE fp.inicio_parada >= DATE '2026-01-01'
    GROUP BY mp.motivo, mp.categoria
),
pareto AS (
    SELECT
        motivo,
        categoria,
        ROUND(horas_paradas, 1) AS horas_paradas,
        ROUND(100.0 * horas_paradas / SUM(horas_paradas) OVER (), 2) AS pct_total,
        ROUND(100.0 * SUM(horas_paradas) OVER (
                  ORDER BY horas_paradas DESC, motivo
                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
              / SUM(horas_paradas) OVER (), 2) AS pct_acumulado
    FROM horas_por_motivo
)
SELECT
    *,
    CASE WHEN pct_acumulado - pct_total < 80 THEN 'Classe A (foco)' ELSE 'Demais' END AS classificacao
FROM pareto
ORDER BY horas_paradas DESC;

-- ============================================================
-- Query 3 - MTBF e MTTR por máquina (LAG)
-- ============================================================
WITH falhas AS (
    SELECT
        fp.id_maquina,
        fp.inicio_parada,
        fp.fim_parada,
        EXTRACT(EPOCH FROM (fp.fim_parada - fp.inicio_parada)) / 3600.0 AS horas_reparo,
        -- fim da falha imediatamente anterior da mesma máquina
        LAG(fp.fim_parada) OVER (
            PARTITION BY fp.id_maquina
            ORDER BY fp.inicio_parada
        ) AS fim_falha_anterior
    FROM dw.fato_parada fp
    JOIN dw.dim_motivo_parada mp ON mp.id_motivo = fp.id_motivo
    WHERE mp.tipo_parada = 'CORRETIVA'
),
intervalos AS (
    SELECT
        id_maquina,
        horas_reparo,
        -- tempo de operação entre o fim da falha anterior e o início desta
        EXTRACT(EPOCH FROM (inicio_parada - fim_falha_anterior)) / 3600.0 AS horas_entre_falhas
    FROM falhas
)
SELECT
    m.nome_maquina,
    m.linha,
    COUNT(*)                          AS qtd_falhas,
    ROUND(AVG(i.horas_entre_falhas), 1) AS mtbf_horas,   -- ignora a 1ª falha (NULL)
    ROUND(AVG(i.horas_reparo), 2)       AS mttr_horas,
    RANK() OVER (ORDER BY AVG(i.horas_entre_falhas) ASC NULLS LAST) AS ranking_criticidade
FROM intervalos i
JOIN dw.dim_maquina m ON m.id_maquina = i.id_maquina
GROUP BY m.nome_maquina, m.linha
HAVING COUNT(*) >= 3
ORDER BY ranking_criticidade;

-- ============================================================
-- Query 4 - Taxa de refugo com média móvel de 7 dias
-- ============================================================
WITH refugo_diario AS (
    SELECT
        m.linha,
        a.data,
        SUM(a.qtd_produzida) AS produzido,
        SUM(a.qtd_rejeitada) AS rejeitado
    FROM dw.fato_apontamento a
    JOIN dw.dim_maquina m ON m.id_maquina = a.id_maquina
    GROUP BY m.linha, a.data
)
SELECT
    linha,
    data,
    produzido,
    rejeitado,
    ROUND(100.0 * rejeitado / NULLIF(produzido, 0), 2) AS pct_refugo_dia,
    -- média móvel ponderada pelo volume (7 dias): soma rejeitos / soma produção
    ROUND(100.0 * SUM(rejeitado) OVER w / NULLIF(SUM(produzido) OVER w, 0), 2) AS pct_refugo_mm7,
    -- variação vs. mesma métrica 7 dias antes
    ROUND(
        100.0 * SUM(rejeitado) OVER w / NULLIF(SUM(produzido) OVER w, 0)
        - LAG(100.0 * SUM(rejeitado) OVER w / NULLIF(SUM(produzido) OVER w, 0), 7)
              OVER (PARTITION BY linha ORDER BY data), 2) AS variacao_pp_7d
FROM refugo_diario
WINDOW w AS (PARTITION BY linha ORDER BY data ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
ORDER BY linha, data;

-- ============================================================
-- Query 5 - Ranking de produtividade por turno e atingimento de meta
-- ============================================================
WITH producao_mensal AS (
    SELECT
        m.linha,
        t.nome_turno,
        a.id_maquina,
        DATE_TRUNC('month', a.data)::DATE AS mes,
        SUM(a.qtd_produzida)              AS volume,
        SUM(a.horas_produtivas)           AS horas_prod
    FROM dw.fato_apontamento a
    JOIN dw.dim_maquina m ON m.id_maquina = a.id_maquina
    JOIN dw.dim_turno   t ON t.id_turno   = a.id_turno
    GROUP BY m.linha, t.nome_turno, a.id_maquina, DATE_TRUNC('month', a.data)
),
metas_linha AS (
    SELECT
        m.linha,
        meta.mes_referencia AS mes,
        SUM(meta.meta_volume) AS meta_volume
    FROM dw.fato_meta_operacional meta
    JOIN dw.dim_maquina m ON m.id_maquina = meta.id_maquina
    GROUP BY m.linha, meta.mes_referencia
),
agregado AS (
    SELECT
        linha,
        mes,
        nome_turno,
        SUM(volume)                              AS volume,
        SUM(volume) / NULLIF(SUM(horas_prod), 0) AS pecas_por_hora
    FROM producao_mensal
    GROUP BY linha, mes, nome_turno
)
SELECT
    ag.linha,
    ag.mes,
    ag.nome_turno,
    ag.volume,
    ROUND(ag.pecas_por_hora, 2) AS pecas_por_hora,
    RANK() OVER (PARTITION BY ag.linha, ag.mes ORDER BY ag.pecas_por_hora DESC) AS rank_turno,
    ROUND(100.0 * SUM(ag.volume) OVER (PARTITION BY ag.linha, ag.mes)
          / NULLIF(ml.meta_volume, 0), 1) AS pct_meta_linha_mes
FROM agregado ag
JOIN metas_linha ml ON ml.linha = ag.linha AND ml.mes = ag.mes
ORDER BY ag.linha, ag.mes, rank_turno;

