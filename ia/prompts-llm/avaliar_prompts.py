# ============================================================
# Biblioteca de prompts e avaliação de LLMs para Análise de Dados
# Estudo de caso de portfólio (entradas sintéticas)
#
# Ideia do experimento:
#   Para cada tarefa de análise de dados, comparar dois prompts:
#     v1 = prompt simples (como a maioria das pessoas escreve)
#     v2 = prompt estruturado (papel, contexto, regras e formato)
#   e medir, com checagens automáticas e repetições, qual deles
#   produz saídas mais confiáveis e utilizáveis.
#
# Uso:
#   python avaliar_prompts.py --dry-run     # só mostra os prompts (sem API)
#   python avaliar_prompts.py --selftest    # testa as checagens (sem API)
#   python avaliar_prompts.py --repeticoes 3   # roda o experimento (requer API)
# ============================================================
import argparse
import csv
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

MODELO_PADRAO = os.environ.get("MODELO_LLM", "claude-sonnet-5-5")  # altere via variável de ambiente

# ------------------------------------------------------------
# 1. BIBLIOTECA DE PROMPTS
# ------------------------------------------------------------
SCHEMA_SQL = """dim_maquina(id_maquina, nome_maquina, linha, setor)
dim_motivo_parada(id_motivo, motivo, categoria, tipo_parada)  -- tipo_parada: 'PLANEJADA' | 'CORRETIVA'
fato_parada(id_parada, id_maquina, id_motivo, inicio_parada TIMESTAMP, fim_parada TIMESTAMP)"""

CSV_AMOSTRA = """data,maquina,operador,qtd_aprovada,qtd_rejeitada,horas_produtivas
2021-03-01,CNC-01,Ana,1200,15,7.5
2021-03-01,CNC-01,Ana,1200,15,7.5
2021-03-02,CNC-02,,980,-4,7.0
2021-03-02,cnc 02,Bruno,1010,12,19.0"""

PROMPTS = [
    {
        "id": "dax_explicar",
        "tarefa": "Explicar uma medida DAX para gestores",
        "tecnica": "Papel + contexto + regras + formato",
        "entrada": {
            "medida": "% Turnover = DIVIDE ( [Demissoes], [Total Contratacoes] )",
            "valores": "Demissões = 17; Total de contratações = 234",
        },
        "system_v2": "Você é um analista de BI sênior que explica métricas para gestores sem formação técnica. Responda em português do Brasil.",
        "v1": "Explique esta medida DAX: {medida}",
        "v2": (
            "Contexto: dashboard de RH em Power BI.\n"
            "Medida DAX: {medida}\n"
            "Valores atuais: {valores}\n\n"
            "Tarefa: explique o que a medida calcula.\n"
            "Regras:\n"
            "- Máximo de 90 palavras.\n"
            "- Diga qual é o numerador e qual é o denominador.\n"
            "- Inclua a conta com os valores atuais e o resultado em porcentagem.\n"
            "- Não use termos técnicos de DAX (como \"função\" ou \"contexto de filtro\").\n"
            "Formato: um único parágrafo."
        ),
        "checks": [
            ("max_palavras", 90),
            ("contem", "17"),
            ("contem", "234"),
            ("contem_regex", r"7[,.]2\d?\s*%"),
            ("sem", ["função", "contexto de filtro"]),
        ],
    },
    {
        "id": "sql_do_schema",
        "tarefa": "Gerar SQL (PostgreSQL) a partir de uma pergunta de negócio e do schema",
        "tecnica": "Few-shot + restrições + saída somente em código",
        "entrada": {
            "schema": SCHEMA_SQL,
            "pergunta": "Qual o MTTR (tempo médio de reparo, em horas) por máquina, considerando só paradas corretivas?",
        },
        "system_v2": "Você é especialista em PostgreSQL. Responda somente com SQL válido, sem explicações e sem markdown.",
        "v1": "Escreva uma query SQL para responder: {pergunta}",
        "v2": (
            "Schema (use somente estas tabelas e colunas):\n{schema}\n\n"
            "Exemplo de pergunta: Quantas paradas cada máquina teve?\n"
            "Exemplo de resposta:\n"
            "SELECT m.nome_maquina, COUNT(*) AS qtd_paradas\n"
            "FROM fato_parada fp\n"
            "JOIN dim_maquina m ON m.id_maquina = fp.id_maquina\n"
            "GROUP BY m.nome_maquina;\n\n"
            "Pergunta: {pergunta}\n"
            "Regras: apenas SELECT (pode usar CTE), nomes de colunas legíveis e comentários curtos com --."
        ),
        "checks": [
            ("contem", "fato_parada"),
            ("contem", "dim_motivo_parada"),
            ("contem", "CORRETIVA"),
            ("contem_regex", r"(?i)\bavg\s*\("),
            ("sem", ["```", "insert ", "update ", "delete ", "drop ", "alter "]),
        ],
    },
    {
        "id": "qualidade_dados",
        "tarefa": "Triagem de problemas de qualidade de dados em uma amostra de CSV",
        "tecnica": "Saída estruturada (JSON) + restrição anti-invenção",
        "entrada": {"csv": CSV_AMOSTRA, "colunas": "data,maquina,operador,qtd_aprovada,qtd_rejeitada,horas_produtivas"},
        "system_v2": "Você é um engenheiro de qualidade de dados. Responda somente com JSON válido, sem markdown.",
        "v1": "Veja esse CSV e me diga quais problemas de qualidade existem:\n{csv}",
        "v2": (
            "Analise a amostra de CSV abaixo.\n\n{csv}\n\n"
            "Regras de negócio: um turno tem 8 horas; quantidades não podem ser negativas; "
            "cada combinação data+maquina+turno deve ser única; nomes de máquina seguem o padrão CNC-01.\n"
            "Retorne um JSON com a chave \"problemas\": uma lista de objetos com "
            "\"coluna\" (somente colunas que existem no CSV), \"tipo\" "
            "(nulo | negativo | duplicado | padronizacao | fora_da_faixa) e \"descricao\" (até 15 palavras).\n"
            "Se não tiver certeza de um problema, não o inclua."
        ),
        "checks": [
            ("json_valido", None),
            ("json_chaves", ["problemas"]),
            ("colunas_existentes", "data,maquina,operador,qtd_aprovada,qtd_rejeitada,horas_produtivas"),
            ("sem", ["```"]),
        ],
    },
    {
        "id": "insights_kpi",
        "tarefa": "Resumo executivo a partir de KPIs (sem inventar números)",
        "tecnica": "Fundamentação nos dados fornecidos + limite de tamanho + próxima ação",
        "entrada": {
            "kpis": (
                "Disponibilidade = 77,69%; Qualidade = 99,32%; Horas produtivas = 30.956; "
                "Horas paradas = 8.890; Peças aprovadas = 3.084.251; Peças rejeitadas = 21.076"
            )
        },
        "system_v2": "Você é um analista de operações. Use apenas os números fornecidos. Responda em português do Brasil.",
        "v1": "Faça um resumo executivo destes indicadores de produção: {kpis}",
        "v2": (
            "KPIs do período (dashboard de produção):\n{kpis}\n\n"
            "Escreva um resumo executivo para o gerente da fábrica.\n"
            "Regras:\n"
            "- Máximo de 80 palavras.\n"
            "- Use somente os números acima; não calcule nem invente outros valores.\n"
            "- Aponte qual indicador é o principal ponto de atenção e sugira uma próxima ação.\n"
            "Formato: 2 a 3 frases."
        ),
        "checks": [
            ("max_palavras", 80),
            ("contem", "77,69"),
            ("so_numeros_fornecidos", None),
        ],
    },
]

# ------------------------------------------------------------
# 2. CHECAGENS AUTOMÁTICAS
# ------------------------------------------------------------
def _limpar_json(texto):
    t = texto.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)  # tolera cercas de markdown
    return t


def _normalizar_numero(s):
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _numeros(texto):
    achados = re.findall(r"\d[\d.]*(?:,\d+)?", texto)
    return {n for n in (_normalizar_numero(a.rstrip(".")) for a in achados) if n is not None}


def aplicar_check(nome, arg, texto, entrada):
    t = texto or ""
    if nome == "max_palavras":
        return len(t.split()) <= int(arg)
    if nome == "contem":
        return str(arg).lower() in t.lower()
    if nome == "contem_regex":
        return re.search(arg, t) is not None
    if nome == "sem":
        return all(p.lower() not in t.lower() for p in arg)
    if nome in ("json_valido", "json_chaves", "colunas_existentes"):
        try:
            obj = json.loads(_limpar_json(t))
        except (json.JSONDecodeError, TypeError):
            return False
        if nome == "json_valido":
            return True
        if nome == "json_chaves":
            return isinstance(obj, dict) and all(k in obj for k in arg)
        colunas = set(arg.split(","))
        lista = obj.get("problemas", []) if isinstance(obj, dict) else []
        return all(isinstance(p, dict) and p.get("coluna") in colunas for p in lista)
    if nome == "so_numeros_fornecidos":
        permitidos = _numeros(entrada["kpis"])
        return all(n in permitidos or n <= 10 for n in _numeros(t))  # ignora contagens pequenas ("2 frases")
    raise ValueError(f"checagem desconhecida: {nome}")


def avaliar(prompt, texto):
    resultados = [aplicar_check(n, a, texto, prompt["entrada"]) for n, a in prompt["checks"]]
    return sum(resultados), len(resultados)


# ------------------------------------------------------------
# 3. EXECUÇÃO
# ------------------------------------------------------------
def montar(prompt, variante):
    user = prompt[variante].format(**prompt["entrada"])
    system = prompt["system_v2"] if variante == "v2" else None
    return system, user


def chamar_llm(client, modelo, system, user):
    kwargs = dict(model=modelo, max_tokens=800, messages=[{"role": "user", "content": user}])
    if system:
        kwargs["system"] = system
    inicio = time.time()
    resp = client.messages.create(**kwargs)
    texto = "".join(b.text for b in resp.content if b.type == "text")
    return texto, time.time() - inicio


def rodar_experimento(modelo, repeticoes):
    import anthropic  # pip install anthropic  | usa a variável ANTHROPIC_API_KEY

    client = anthropic.Anthropic()
    pasta = Path(__file__).parent / "resultados"
    pasta.mkdir(exist_ok=True)
    arquivo = pasta / f"resultados_{datetime.now():%Y%m%d_%H%M}.csv"
    linhas = []
    for p in PROMPTS:
        for variante in ("v1", "v2"):
            system, user = montar(p, variante)
            for rep in range(1, repeticoes + 1):
                texto, latencia = chamar_llm(client, modelo, system, user)
                ok, total = avaliar(p, texto)
                linhas.append({
                    "prompt": p["id"], "variante": variante, "repeticao": rep, "modelo": modelo,
                    "latencia_s": round(latencia, 2), "checks_ok": ok, "checks_total": total,
                    "aprovado_auto": int(ok == total), "resposta": texto.replace("\n", " ⏎ "),
                    "nota_manual_1a5": "",  # preencher após leitura humana
                })
                print(f"{p['id']:<16} {variante} #{rep}: {ok}/{total} checagens")
    with open(arquivo, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)
    print(f"\nResultados salvos em: {arquivo}\n")
    print(f"{'prompt':<16} {'v1 aprovado':>12} {'v2 aprovado':>12}")
    for p in PROMPTS:
        taxa = {}
        for v in ("v1", "v2"):
            sel = [l for l in linhas if l["prompt"] == p["id"] and l["variante"] == v]
            taxa[v] = sum(l["aprovado_auto"] for l in sel) / len(sel)
        print(f"{p['id']:<16} {taxa['v1']:>11.0%} {taxa['v2']:>12.0%}")


# ------------------------------------------------------------
# 4. TESTES DAS CHECAGENS (sem API)
# ------------------------------------------------------------
def selftest():
    exemplos = {
        "dax_explicar": (
            "A medida divide as demissões (17) pelo total de contratações (234). Ou seja, 17 ÷ 234 = 7,26%, "
            "a parcela das pessoas contratadas que já saiu da empresa.",
            "Essa função DIVIDE cria uma razão entre duas medidas no contexto de filtro atual, "
            "retornando um valor numérico que pode ser formatado de várias maneiras.",
        ),
        "sql_do_schema": (
            "SELECT m.nome_maquina, AVG(EXTRACT(EPOCH FROM (fp.fim_parada - fp.inicio_parada)) / 3600.0) AS mttr_horas\n"
            "FROM fato_parada fp\nJOIN dim_maquina m ON m.id_maquina = fp.id_maquina\n"
            "JOIN dim_motivo_parada mp ON mp.id_motivo = fp.id_motivo\nWHERE mp.tipo_parada = 'CORRETIVA'\nGROUP BY m.nome_maquina;",
            "```sql\nSELECT * FROM paradas;\n```",
        ),
        "qualidade_dados": (
            '{"problemas": [{"coluna": "operador", "tipo": "nulo", "descricao": "operador ausente em uma linha"}]}',
            "Encontrei linhas duplicadas e valores negativos no arquivo.",
        ),
        "insights_kpi": (
            "A disponibilidade de 77,69% é o principal ponto de atenção, com 8.890 horas paradas. "
            "Sugere-se mapear os motivos de parada para priorizar ações.",
            "A disponibilidade ficou em 77,7% e as paradas representam 22,3% do tempo total.",
        ),
    }
    for p in PROMPTS:
        bom, ruim = exemplos[p["id"]]
        ok_b, tot = avaliar(p, bom)
        ok_r, _ = avaliar(p, ruim)
        assert ok_b == tot, f"{p['id']}: resposta boa deveria passar em todas ({ok_b}/{tot})"
        assert ok_r < tot, f"{p['id']}: resposta ruim deveria falhar em alguma checagem"
        print(f"OK  {p['id']:<16} boa={ok_b}/{tot}  ruim={ok_r}/{tot}")
    print("\nTodas as checagens se comportaram como esperado.")


def dry_run():
    for p in PROMPTS:
        print("=" * 70)
        print(f"{p['id']}  |  {p['tarefa']}  |  técnica v2: {p['tecnica']}")
        for v in ("v1", "v2"):
            system, user = montar(p, v)
            print(f"\n--- {v} ---")
            if system:
                print(f"[system] {system}")
            print(user)
    print("=" * 70)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="mostra os prompts sem chamar a API")
    ap.add_argument("--selftest", action="store_true", help="testa as checagens automáticas sem chamar a API")
    ap.add_argument("--repeticoes", type=int, default=3, help="repetições por prompt/variante (padrão: 3)")
    ap.add_argument("--modelo", default=MODELO_PADRAO, help=f"modelo (padrão: {MODELO_PADRAO})")
    a = ap.parse_args()
    if a.dry_run:
        dry_run()
    elif a.selftest:
        selftest()
    else:
        rodar_experimento(a.modelo, a.repeticoes)
