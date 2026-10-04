# ============================================================
# Manutenção Preditiva - Estudo de Caso Simulado
# Objetivo: prever falha de maquinário nas próximas 24h
# ============================================================
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.stats import mannwhitneyu

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    classification_report, confusion_matrix,
    roc_auc_score, RocCurveDisplay, PrecisionRecallDisplay
)

sns.set_theme(style="whitegrid")
RNG = np.random.default_rng(42)

# ------------------------------------------------------------
# 1. GERAÇÃO DE DADOS SINTÉTICOS (simulação de sensores)
# ------------------------------------------------------------
n = 20_000
df = pd.DataFrame({
    "temperatura_c":         RNG.normal(70, 8, n),
    "vibracao_mm_s":         RNG.gamma(shape=4, scale=1.1, size=n),
    "pressao_bar":           RNG.normal(6.0, 0.6, n),
    "ruido_db":              RNG.normal(78, 5, n),
    "carga_pct":             RNG.uniform(40, 100, n),
    "horas_desde_manutencao": RNG.uniform(0, 1500, n),
    "idade_maquina_anos":    RNG.integers(1, 21, n),
})

# Probabilidade de falha (relação logística construída para o estudo)
z = (
    -5.0
    + 0.06 * (df["temperatura_c"] - 70)
    + 0.35 * (df["vibracao_mm_s"] - 4.4)
    + 0.0016 * df["horas_desde_manutencao"]
    + 0.05 * df["idade_maquina_anos"]
    + 0.015 * (df["carga_pct"] - 70)
)
prob = 1 / (1 + np.exp(-z))
df["falha_24h"] = (RNG.random(n) < prob).astype(int)

print(f"Registros: {len(df):,} | Taxa de falha: {df['falha_24h'].mean():.2%}")

# ------------------------------------------------------------
# 2. ANÁLISE EXPLORATÓRIA (EDA)
# ------------------------------------------------------------
print(df.describe().T.round(2))

# 2.1 Desbalanceamento da variável alvo
sns.countplot(data=df, x="falha_24h")
plt.title("Distribuição da variável alvo (desbalanceada)")
plt.show()

# 2.2 Correlação entre variáveis
plt.figure(figsize=(8, 6))
sns.heatmap(df.corr(), annot=True, fmt=".2f", cmap="coolwarm", center=0)
plt.title("Matriz de correlação")
plt.show()

# 2.3 H1: temperatura e vibração por grupo
fig, axes = plt.subplots(1, 2, figsize=(11, 4))
sns.boxplot(data=df, x="falha_24h", y="temperatura_c", ax=axes[0])
sns.boxplot(data=df, x="falha_24h", y="vibracao_mm_s", ax=axes[1])
axes[0].set_title("Temperatura vs. falha")
axes[1].set_title("Vibração vs. falha")
plt.tight_layout()
plt.show()

for col in ["temperatura_c", "vibracao_mm_s"]:
    a = df.loc[df.falha_24h == 1, col]
    b = df.loc[df.falha_24h == 0, col]
    stat, p = mannwhitneyu(a, b, alternative="greater")
    print(f"H1 | {col}: média falha={a.mean():.2f} vs normal={b.mean():.2f} | p-valor={p:.2e}")

# 2.4 H2: taxa de falha por faixa de horas desde a manutenção
df["faixa_manut"] = pd.cut(
    df["horas_desde_manutencao"],
    bins=[0, 300, 600, 900, 1200, 1500],
    labels=["0-300", "300-600", "600-900", "900-1200", "1200-1500"],
    include_lowest=True,
)
taxa_manut = df.groupby("faixa_manut", observed=True)["falha_24h"].mean()
taxa_manut.plot(kind="bar", color="#F2C811")
plt.ylabel("Taxa de falha")
plt.title("H2: taxa de falha por horas desde a última manutenção")
plt.show()

# ------------------------------------------------------------
# 3. PREPARAÇÃO E MODELO BASELINE
# ------------------------------------------------------------
features = [
    "temperatura_c", "vibracao_mm_s", "pressao_bar", "ruido_db",
    "carga_pct", "horas_desde_manutencao", "idade_maquina_anos",
]
X = df[features]
y = df["falha_24h"]

# Estratificação mantém a proporção da classe rara em treino e teste
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.25, stratify=y, random_state=42
)

modelos = {
    "Regressão Logística": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(class_weight="balanced", max_iter=1000)),
    ]),
    "Random Forest": RandomForestClassifier(
        n_estimators=300, max_depth=8, min_samples_leaf=20,
        class_weight="balanced", n_jobs=-1, random_state=42
    ),
}

resultados = {}
for nome, modelo in modelos.items():
    modelo.fit(X_train, y_train)
    proba = modelo.predict_proba(X_test)[:, 1]
    resultados[nome] = (modelo, proba)
    print(f"\n=== {nome} | ROC-AUC: {roc_auc_score(y_test, proba):.3f} ===")
    print(classification_report(y_test, modelo.predict(X_test), digits=3))

# ------------------------------------------------------------
# 4. AVALIAÇÃO
# ------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
for nome, (_, proba) in resultados.items():
    RocCurveDisplay.from_predictions(y_test, proba, name=nome, ax=axes[0])
    PrecisionRecallDisplay.from_predictions(y_test, proba, name=nome, ax=axes[1])
axes[0].set_title("Curva ROC")
axes[1].set_title("Curva Precisão-Recall")
plt.tight_layout()
plt.show()

# Matriz de confusão do Random Forest
rf = resultados["Random Forest"][0]
sns.heatmap(confusion_matrix(y_test, rf.predict(X_test)),
            annot=True, fmt="d", cmap="Blues")
plt.xlabel("Previsto"); plt.ylabel("Real")
plt.title("Matriz de confusão - Random Forest")
plt.show()

# Importância das variáveis (H4)
imp = pd.Series(rf.feature_importances_, index=features).sort_values()
imp.plot(kind="barh", color="#0078D4")
plt.title("Importância das variáveis - Random Forest")
plt.show()

# ------------------------------------------------------------
# 5. AJUSTE DE LIMIAR (decisão de negócio)
# ------------------------------------------------------------
# Em manutenção, perder uma falha (falso negativo) costuma custar mais
# que uma inspeção desnecessária (falso positivo). Ajustamos o limiar
# para priorizar recall.
proba_rf = resultados["Random Forest"][1]
for limiar in [0.3, 0.4, 0.5, 0.6]:
    pred = (proba_rf >= limiar).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_test, pred).ravel()
    print(f"Limiar {limiar:.1f} | Recall={tp/(tp+fn):.2%} | "
          f"Precisão={tp/(tp+fp):.2%} | Inspeções geradas={tp+fp}")
