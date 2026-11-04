# ============================================================
# Automação de relatório de produção (Excel -> Python)
# Estudo de caso de portfólio com DADOS SINTÉTICOS
#
# Rotina que costuma ser feita à mão: abrir uma planilha por mês,
# padronizar nomes, conferir erros, consolidar e montar o relatório.
# Aqui ela vira um pipeline repetível: consolidar -> padronizar ->
# validar -> calcular KPIs -> exportar Excel -> medir o tempo.
#
# Uso:
#   python automacao_relatorio.py                  # gera dados fictícios (1ª vez) e roda
#   python automacao_relatorio.py --min-por-planilha 20   # muda a premissa de tempo manual
#   python automacao_relatorio.py --resumo-llm     # resumo executivo via LLM (opcional)
# ============================================================
import argparse
import os
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
ENTRADA = BASE / "dados" / "entrada"
SAIDA = BASE / "saida"
MAQUINAS = ["CNC-01", "CNC-02", "PRENSA-01", "PRENSA-02", "TORNO-01"]
OPERADORES = ["Ana", "Bruno", "Carla", "Diego", "Elisa", "Fabio"]
TURNOS = ["Manhã", "Tarde", "Noite"]
HORAS_TURNO = 8.0


# ------------------------------------------------------------
# 1. DADOS SINTÉTICOS (simulam as planilhas mensais, com erros reais de digitação)
# ------------------------------------------------------------
def gerar_dados(seed=42):
    rng = np.random.default_rng(seed)
    ENTRADA.mkdir(parents=True, exist_ok=True)
    for mes in range(1, 13):
        dias = pd.date_range(f"2021-{mes:02d}-01", periods=28, freq="D")
        linhas = []
        for d in dias:
            for m in MAQUINAS:
                for t in TURNOS:
                    paradas = float(np.clip(rng.gamma(2.0, 0.9), 0, 6))
                    prod = round(HORAS_TURNO - paradas, 2)
                    aprovadas = int(prod * rng.normal(140, 12))
                    rejeitadas = int(aprovadas * rng.uniform(0.002, 0.012))
                    linhas.append([d, m, rng.choice(OPERADORES), t, aprovadas, rejeitadas, prod, round(paradas, 2)])
        df = pd.DataFrame(linhas, columns=["Data", "Máquina", "Operador", "Turno",
                                           "Qtd Aprovada", "Qtd Rejeitada", "Horas Produtivas", "Horas Paradas"])
        # erros típicos de planilha preenchida à mão
        df["Qtd Aprovada"] = df["Qtd Aprovada"].astype(object)  # permite misturar número e texto
        n = len(df)
        idx = rng.choice(n, size=int(n * 0.03), replace=False)
        df.loc[idx[: len(idx) // 5], "Operador"] = None                                   # operador vazio
        df.loc[idx[len(idx) // 5: 2 * len(idx) // 5], "Qtd Rejeitada"] *= -1               # quantidade negativa
        df.loc[idx[2 * len(idx) // 5: 3 * len(idx) // 5], "Máquina"] = "cnc 01"            # nome fora do padrão
        df.loc[idx[3 * len(idx) // 5: 4 * len(idx) // 5], "Horas Produtivas"] = 19.0       # horas acima do turno
        df.loc[idx[4 * len(idx) // 5:], "Qtd Aprovada"] = df.loc[idx[4 * len(idx) // 5:], "Qtd Aprovada"].astype(str) + " un"  # texto
        df = pd.concat([df, df.sample(5, random_state=mes)], ignore_index=True)           # linhas duplicadas
        df.to_excel(ENTRADA / f"producao_2021-{mes:02d}.xlsx", index=False)


# ------------------------------------------------------------
# 2. CONSOLIDAR + PADRONIZAR
# ------------------------------------------------------------
def consolidar():
    partes = []
    for arq in sorted(ENTRADA.glob("producao_*.xlsx")):
        d = pd.read_excel(arq)
        d["Arquivo"] = arq.name
        partes.append(d)
    return pd.concat(partes, ignore_index=True)


def padronizar(df):
    df = df.copy()
    df.columns = ["data", "maquina", "operador", "turno", "qtd_aprovada", "qtd_rejeitada",
                  "horas_produtivas", "horas_paradas", "arquivo"]
    # "cnc 01", "CNC_01 " -> "CNC-01"
    original = df["maquina"].astype(str)
    df["maquina"] = (original.str.strip().str.upper().str.replace(r"[\s_]+", "-", regex=True))
    correcoes = {
        "nomes de máquina padronizados": int((original != df["maquina"]).sum()),
        "valores com texto convertidos": int(df["qtd_aprovada"].astype(str).str.contains(r"[A-Za-z]").sum()),
    }
    for c in ["qtd_aprovada", "qtd_rejeitada", "horas_produtivas", "horas_paradas"]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(r"[^\d.\-]", "", regex=True), errors="coerce")
    df["data"] = pd.to_datetime(df["data"])
    return df, correcoes


# ------------------------------------------------------------
# 3. VALIDAÇÃO (regras de negócio) -> separa linhas boas e linhas com erro
# ------------------------------------------------------------
def validar(df):
    regras = {
        "operador vazio": df["operador"].isna() | (df["operador"].astype(str).str.strip() == ""),
        "quantidade negativa": (df["qtd_aprovada"] < 0) | (df["qtd_rejeitada"] < 0),
        "valor ausente ou inválido": df[["qtd_aprovada", "qtd_rejeitada", "horas_produtivas", "horas_paradas"]].isna().any(axis=1),
        "horas acima do turno": (df["horas_produtivas"] + df["horas_paradas"]) > HORAS_TURNO + 0.5,
        "linha duplicada": df.duplicated(subset=["data", "maquina", "turno"], keep="first"),
    }
    erros = pd.DataFrame(regras)
    df = df.assign(motivo_erro=erros.apply(lambda r: "; ".join(erros.columns[r.values]), axis=1))
    return df[df["motivo_erro"] == ""].drop(columns="motivo_erro"), df[df["motivo_erro"] != ""], erros.sum()


# ------------------------------------------------------------
# 4. KPIs
# ------------------------------------------------------------
def kpis(df):
    def calc(g):
        hp, hpar = g["horas_produtivas"].sum(), g["horas_paradas"].sum()
        ap, rej = g["qtd_aprovada"].sum(), g["qtd_rejeitada"].sum()
        return pd.Series({
            "Horas Produtivas": hp, "Horas Paradas": hpar,
            "Disponibilidade": hp / (hp + hpar), "Qtd Aprovada": ap, "Qtd Rejeitada": rej,
            "Qualidade": ap / (ap + rej), "% Refugo": rej / (ap + rej),
        })
    total = calc(df).to_frame("Total").T
    por_maquina = df.groupby("maquina").apply(calc, include_groups=False).sort_values("Disponibilidade")
    por_mes = df.groupby(df["data"].dt.to_period("M").astype(str)).apply(calc, include_groups=False)
    return total, por_maquina, por_mes


# ------------------------------------------------------------
# 5. EXPORTAÇÃO PARA EXCEL
# ------------------------------------------------------------
def exportar(total, por_maquina, por_mes, erros_df, resumo_texto):
    SAIDA.mkdir(exist_ok=True)
    destino = SAIDA / "relatorio_producao.xlsx"
    with pd.ExcelWriter(destino, engine="openpyxl") as xw:
        pd.DataFrame({"Resumo executivo": [resumo_texto]}).to_excel(xw, sheet_name="Resumo", index=False)
        total.to_excel(xw, sheet_name="KPIs Total")
        por_maquina.to_excel(xw, sheet_name="Por Máquina")
        por_mes.to_excel(xw, sheet_name="Por Mês")
        erros_df.to_excel(xw, sheet_name="Linhas com erro", index=False)
        from openpyxl.styles import Font
        for ws in xw.book.worksheets:
            for cel in ws[1]:
                cel.font = Font(bold=True)
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(60, max(12, max(len(str(c.value or "")) for c in col[:50]) + 2))
            for row in ws.iter_rows(min_row=2):
                for cel in row:
                    if ws.cell(1, cel.column).value in ("Disponibilidade", "Qualidade", "% Refugo"):
                        cel.number_format = "0.00%"
                    elif isinstance(cel.value, float):
                        cel.number_format = "#,##0.00"
    return destino


# ------------------------------------------------------------
# 6. RESUMO EXECUTIVO (regra fixa ou LLM opcional)
# ------------------------------------------------------------
def resumo_regra(total, por_maquina):
    t = total.iloc[0]
    pior = por_maquina.index[0]
    return (f"Disponibilidade de {t['Disponibilidade']:.2%} e qualidade de {t['Qualidade']:.2%} no período. "
            f"A máquina com menor disponibilidade é {pior} ({por_maquina.iloc[0]['Disponibilidade']:.2%}); "
            f"priorizar a análise das paradas dessa máquina.")


def resumo_llm(total, por_maquina):
    import anthropic  # pip install anthropic | usa ANTHROPIC_API_KEY
    client = anthropic.Anthropic()
    dados = f"Total:\n{total.round(4).to_string()}\n\nPor máquina:\n{por_maquina.round(4).to_string()}"
    resp = client.messages.create(
        model=os.environ.get("MODELO_LLM", "claude-sonnet-5-5"), max_tokens=400,
        system="Você é um analista de operações. Use apenas os números fornecidos. Responda em português do Brasil.",
        messages=[{"role": "user", "content": f"{dados}\n\nEscreva um resumo executivo de até 80 palavras, "
                                              "apontando o principal ponto de atenção e uma próxima ação."}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


# ------------------------------------------------------------
# 7. EXECUÇÃO + MEDIÇÃO DE TEMPO
# ------------------------------------------------------------
def main(min_por_planilha, usar_llm):
    if not any(ENTRADA.glob("producao_*.xlsx")):
        print("Gerando planilhas fictícias de entrada...")
        gerar_dados()
    t0 = time.perf_counter()
    bruto = consolidar()
    padronizado, correcoes = padronizar(bruto)
    limpo, erros_df, contagem = validar(padronizado)
    total, por_maquina, por_mes = kpis(limpo)
    texto = resumo_llm(total, por_maquina) if usar_llm else resumo_regra(total, por_maquina)
    destino = exportar(total, por_maquina, por_mes, erros_df, texto)
    segundos = time.perf_counter() - t0

    n_arq = bruto["Arquivo"].nunique()
    manual_min = n_arq * min_por_planilha
    print(f"Planilhas processadas : {n_arq}")
    print(f"Linhas lidas          : {len(bruto):,}")
    print(f"Linhas válidas        : {len(limpo):,}")
    print("Correções automáticas :")
    for nome, qtd in correcoes.items():
        print(f"   - {nome:<30}: {qtd}")
    print(f"Linhas com erro       : {len(erros_df):,} ({len(erros_df) / len(bruto):.1%})")
    for regra, qtd in contagem.items():
        print(f"   - {regra:<22}: {int(qtd)}")
    print(f"\nDisponibilidade       : {total.iloc[0]['Disponibilidade']:.2%}")
    print(f"Qualidade             : {total.iloc[0]['Qualidade']:.2%}")
    print(f"\nTempo da automação    : {segundos:.1f} s")
    print(f"Tempo manual (PREMISSA): {manual_min} min ({min_por_planilha} min x {n_arq} planilhas)")
    print("Obs.: o tempo manual é uma premissa ajustável (--min-por-planilha), não uma medição.")
    print(f"\nRelatório gerado em   : {destino}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-por-planilha", type=float, default=15, help="premissa: minutos manuais por planilha (padrão: 15)")
    ap.add_argument("--resumo-llm", action="store_true", help="gera o resumo executivo com um LLM (requer API)")
    a = ap.parse_args()
    main(a.min_por_planilha, a.resumo_llm)
