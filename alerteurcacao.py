"""
🍫 Alerteur Cacao — Version Marché Européen (Londres)
Surveille le prix du cacao sur ICE Londres (C=GB) et déclenche
une alerte quand la variation dépasse le seuil défini.

Lancement local :  streamlit run alerteurcacao.py
"""

import os
import json
import base64
from datetime import datetime, time, timedelta
import pytz

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

# =============================================================
# ⚙️ CONFIGURATION
# =============================================================
# --- Marché européen (ICE Londres) ---
SYMBOLE_CACAO_LONDRES = "C=GB"     # Contrat ICE Londres (GBP)

# --- Marché US (fallback si Londres indispo) ---
SYMBOLE_CACAO_US = "CC=F"          # Contrat ICE US (USD)

# --- Taux de change ---
SYMBOLE_USDGBP = "USDGBP=X"
SYMBOLE_GBPEUR = "GBPEUR=X"

# --- Paramètres ---
SEUIL_DEFAUT = 100.0               # Seuil d'alerte en £
INTERVALLE_MAJ = 60                # Rafraîchissement auto (secondes)
DELAI_MIN_ALERTE_SEC = 300         # 5 min minimum entre 2 alertes

# --- Fuseau horaire ---
TZ_CAMEROUN = pytz.timezone("Africa/Douala")

# --- Heures d'ouverture ICE Londres, converties en heure Cameroun ---
# Londres : 09:00 → 17:30 (heure UK)
# Cameroun : UTC+1 toute l'année
#   - Hiver UK (GMT = UTC+0) → Cameroun = UK + 1h → 10:00 → 18:30
#   - Été  UK (BST = UTC+1) → Cameroun = UK      → 09:00 → 17:30
# On prend la plage large (hiver) pour être sûr
HEURE_OUVERTURE = time(10, 0)
HEURE_FERMETURE = time(18, 30)

# --- Fichiers de persistance ---
CHEMIN_HISTORIQUE = "alertes_cacao.csv"
CHEMIN_REFERENCE = "reference_cacao.json"


# =============================================================
# 🧰 UTILITAIRES
# =============================================================
def maintenant_cameroun() -> datetime:
    """Retourne l'heure actuelle au Cameroun."""
    return datetime.now(TZ_CAMEROUN)


def marche_ouvert() -> bool:
    """Indique si le marché de Londres est ouvert (approximation)."""
    maintenant = maintenant_cameroun()
    # Pas de marché le week-end
    if maintenant.weekday() >= 5:  # 5 = Samedi, 6 = Dimanche
        return False
    h = maintenant.time()
    return HEURE_OUVERTURE <= h <= HEURE_FERMETURE


def _bip_local():
    """Bip sonore côté serveur (utile en localhost)."""
    try:
        import winsound  # Windows
        winsound.Beep(1000, 500)
        winsound.Beep(1400, 500)
    except ImportError:
        print("\a", end="", flush=True)


def _bip_navigateur():
    """
    Injecte un bip audio HTML5 dans le navigateur du visiteur.
    NOTE : Certains navigateurs bloquent l'autoplay sans interaction.
    Utilise quand tu veux un son côté client.
    """
    # Petit bip WAV en base64 (880 Hz, court)
    audio_b64 = (
        "UklGRlQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YT"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    html = f"""
    <audio autoplay>
        <source src="data:audio/wav;base64,{audio_b64}" type="audio/wav">
    </audio>
    """
    st.markdown(html, unsafe_allow_html=True)


def jouer_son(son_actif: bool, son_navigateur: bool = False):
    """Déclenche le son selon la config."""
    if not son_actif:
        return
    _bip_local()
    if son_navigateur:
        _bip_navigateur()


# =============================================================
# 💾 PERSISTANCE DE LA RÉFÉRENCE
# =============================================================
def sauvegarder_reference(prix: float):
    """Sauvegarde la référence sur disque (survit aux redémarrages)."""
    try:
        with open(CHEMIN_REFERENCE, "w") as f:
            json.dump({
                "prix": prix,
                "horodatage": maintenant_cameroun().isoformat(),
            }, f)
    except Exception:
        pass


def charger_reference() -> float | None:
    """Charge la dernière référence sauvegardée."""
    if not os.path.exists(CHEMIN_REFERENCE):
        return None
    try:
        with open(CHEMIN_REFERENCE) as f:
            data = json.load(f)
        return float(data.get("prix"))
    except Exception:
        return None


# =============================================================
# 📡 RÉCUPÉRATION DES DONNÉES
# =============================================================
@st.cache_data(ttl=300)
def get_taux_usd_gbp() -> float:
    """Taux de change USD → GBP (cache 5 min)."""
    try:
        data = yf.Ticker(SYMBOLE_USDGBP).history(period="1d")
        if data.empty:
            return 0.79
        return float(data["Close"].iloc[-1])
    except Exception:
        return 0.79


@st.cache_data(ttl=300)
def get_taux_gbp_eur() -> float:
    """Taux de change GBP → EUR (cache 5 min)."""
    try:
        data = yf.Ticker(SYMBOLE_GBPEUR).history(period="1d")
        if data.empty:
            return 1.17
        return float(data["Close"].iloc[-1])
    except Exception:
        return 1.17


@st.cache_data(ttl=60)
def get_prix_londres_gbp() -> tuple[float | None, str]:
    """
    Retourne (prix en GBP, symbole utilisé).
    Essaie Londres d'abord, puis fallback sur US × taux USD→GBP.
    """
    # Tentative Londres
    try:
        data = yf.Ticker(SYMBOLE_CACAO_LONDRES).history(
            period="1d", interval="1m"
        )
        if not data.empty:
            prix = float(data["Close"].iloc[-1])
            if prix > 0:
                return prix, SYMBOLE_CACAO_LONDRES
    except Exception:
        pass

    # Fallback US → conversion GBP
    try:
        data = yf.Ticker(SYMBOLE_CACAO_US).history(
            period="1d", interval="1m"
        )
        if not data.empty:
            prix_usd = float(data["Close"].iloc[-1])
            if prix_usd > 0:
                return prix_usd * get_taux_usd_gbp(), SYMBOLE_CACAO_US
    except Exception as e:
        st.warning(f"Erreur récupération prix : {e}")

    return None, "N/A"


@st.cache_data(ttl=60)
def get_historique(periode: str = "1d", intervalle: str = "5m") -> pd.DataFrame:
    """
    Retourne l'historique OHLC en GBP + fuseau Cameroun.
    Essaie Londres, puis fallback US.
    """
    data = None
    source = None

    # Tentative Londres
    try:
        d = yf.Ticker(SYMBOLE_CACAO_LONDRES).history(
            period=periode, interval=intervalle
        )
        if not d.empty:
            data = d
            source = "Londres"
    except Exception:
        pass

    # Fallback US
    if data is None:
        try:
            d = yf.Ticker(SYMBOLE_CACAO_US).history(
                period=periode, interval=intervalle
            )
            if not d.empty:
                data = d
                source = "US"
        except Exception:
            pass

    if data is None or data.empty:
        return pd.DataFrame()

    # Conversion en GBP si US
    if source == "US":
        taux = get_taux_usd_gbp()
        for col in ["Open", "High", "Low", "Close"]:
            data[col] = data[col] * taux

    # ✅ CORRECTION du bug tz_convert (gère les index naïfs)
    try:
        if data.index.tz is None:
            data.index = data.index.tz_localize("UTC")
        data.index = data.index.tz_convert(TZ_CAMEROUN)
    except Exception:
        pass

    return data


# =============================================================
# 🔔 ALERTES
# =============================================================
def sauvegarder_alerte(variation: float, prix_gbp: float, prix_eur: float):
    """Ajoute une alerte à l'historique CSV."""
    ligne = pd.DataFrame([{
        "horodatage": maintenant_cameroun().strftime("%Y-%m-%d %H:%M:%S"),
        "variation_gbp": round(variation, 2),
        "variation_eur": round(variation * (prix_eur / prix_gbp), 2) if prix_gbp else 0,
        "type": "HAUSSE" if variation > 0 else "BAISSE",
        "prix_gbp": round(prix_gbp, 2),
        "prix_eur": round(prix_eur, 2),
    }])
    header = not os.path.exists(CHEMIN_HISTORIQUE)
    ligne.to_csv(CHEMIN_HISTORIQUE, mode="a", header=header, index=False)


def charger_historique() -> pd.DataFrame:
    """Charge les 20 dernières alertes."""
    if not os.path.exists(CHEMIN_HISTORIQUE):
        return pd.DataFrame()
    try:
        df = pd.read_csv(CHEMIN_HISTORIQUE)
        return df.tail(20).iloc[::-1]
    except Exception:
        return pd.DataFrame()


# =============================================================
# 🎨 INTERFACE STREAMLIT
# =============================================================
st.set_page_config(
    page_title="Alerteur Cacao — Marché Européen",
    page_icon="🍫",
    layout="wide",
)

# ---------- État de session ----------
if "prix_reference" not in st.session_state:
    st.session_state.prix_reference = charger_reference()
if "seuil" not in st.session_state:
    st.session_state.seuil = SEUIL_DEFAUT
if "son_actif" not in st.session_state:
    st.session_state.son_actif = True
if "son_navigateur" not in st.session_state:
    st.session_state.son_navigateur = False
if "derniere_alerte" not in st.session_state:
    st.session_state.derniere_alerte = None

# ---------- Header ----------
col1, col2 = st.columns([3, 1])
with col1:
    st.title("🍫 Alerteur Cacao — Marché Européen")
    st.caption(
        f"Heure du Cameroun : **{maintenant_cameroun().strftime('%H:%M:%S')}** "
        f"| Seuil : **{st.session_state.seuil:.0f} £**"
    )
with col2:
    if marche_ouvert():
        st.success("🟢 Londres ouvert")
    else:
        st.error("🔴 Londres fermé")

# Auto-refresh
st_autorefresh(interval=INTERVALLE_MAJ * 1000, key="refresh")

# ---------- Prix actuel ----------
prix_actuel_gbp, symbole_utilise = get_prix_londres_gbp()

if prix_actuel_gbp is None:
    st.error("❌ Impossible de récupérer le prix du cacao. Vérifiez votre connexion.")
    st.stop()

taux_gbp_eur = get_taux_gbp_eur()
prix_actuel_eur = prix_actuel_gbp * taux_gbp_eur

# Initialisation de la référence au 1er lancement
if st.session_state.prix_reference is None:
    st.session_state.prix_reference = prix_actuel_gbp
    sauvegarder_reference(prix_actuel_gbp)

variation_gbp = prix_actuel_gbp - st.session_state.prix_reference
variation_eur = variation_gbp * taux_gbp_eur
variation_pct = (variation_gbp / st.session_state.prix_reference * 100) if st.session_state.prix_reference else 0

# ---------- Métriques ----------
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("💰 Prix (GBP)", f"{prix_actuel_gbp:,.2f} £")
c2.metric("💶 Prix (EUR)", f"{prix_actuel_eur:,.2f} €")
c3.metric(
    "📊 Variation",
    f"{variation_gbp:+,.2f} £",
    delta=f"{variation_pct:+.2f}%",
    delta_color="normal" if variation_gbp >= 0 else "inverse",
)
c4.metric("🎯 Seuil", f"{st.session_state.seuil:.0f} £")
historique = charger_historique()
c5.metric("🔔 Alertes", len(historique))

st.caption(f"📡 Source : `{symbole_utilise}` (ICE Londres) | Taux GBP/EUR : {taux_gbp_eur:.4f}")

# ---------- Vérification alerte (avec anti-spam) ----------
if abs(variation_gbp) >= st.session_state.seuil:
    # Anti-spam : 5 min minimum entre 2 alertes
    maintenant = maintenant_cameroun()
    peut_alerter = (
        st.session_state.derniere_alerte is None
        or (maintenant - st.session_state.derniere_alerte).total_seconds() >= DELAI_MIN_ALERTE_SEC
    )

    direction = "HAUSSE 📈" if variation_gbp > 0 else "BAISSE 📉"

    if peut_alerter:
        st.error(
            f"🚨 **ALERTE {direction}** — Variation de **{variation_gbp:+,.2f} £** "
            f"({variation_eur:+,.2f} €)"
        )
        jouer_son(st.session_state.son_actif, st.session_state.son_navigateur)
        sauvegarder_alerte(variation_gbp, prix_actuel_gbp, prix_actuel_eur)
        st.session_state.prix_reference = prix_actuel_gbp
        sauvegarder_reference(prix_actuel_gbp)
        st.session_state.derniere_alerte = maintenant
    else:
        # Alerte silencieuse (anti-spam)
        dernier = st.session_state.derniere_alerte
        reste = DELAI_MIN_ALERTE_SEC - (maintenant - dernier).total_seconds()
        st.warning(
            f"⚠️ Variation de {variation_gbp:+,.2f} £ détectée, "
            f"mais prochaine alerte dans {int(reste)} s (anti-spam)."
        )

# ---------- Graphiques ----------
tab1, tab2 = st.tabs(["📈 Aujourd'hui", "📉 5 jours"])

with tab1:
    df_jour = get_historique(periode="1d", intervalle="5m")
    if not df_jour.empty:
        fig = go.Figure(data=[go.Candlestick(
            x=df_jour.index,
            open=df_jour["Open"], high=df_jour["High"],
            low=df_jour["Low"], close=df_jour["Close"],
            increasing_line_color="#26A69A",
            decreasing_line_color="#EF5350",
        )])
        fig.update_layout(
            template="plotly_dark",
            height=420,
            xaxis_rangeslider_visible=False,
            margin=dict(l=20, r=20, t=30, b=20),
            yaxis_title="Prix (£)",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Données intraday indisponibles (marché fermé ?)")

with tab2:
    df_semaine = get_historique(periode="5d", intervalle="30m")
    if not df_semaine.empty:
        fig2 = go.Figure(data=[go.Scatter(
            x=df_semaine.index,
            y=df_semaine["Close"],
            mode="lines",
            line=dict(color="#26A69A", width=2),
            fill="tozeroy",
            fillcolor="rgba(38,166,154,0.15)",
        )])
        fig2.update_layout(
            template="plotly_dark",
            height=420,
            margin=dict(l=20, r=20, t=30, b=20),
            yaxis_title="Prix (£)",
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("Données indisponibles")

# ---------- Contrôles + Historique ----------
col_ctrl, col_hist = st.columns([1, 2])

with col_ctrl:
    st.subheader("⚙️ Contrôles")
    st.session_state.seuil = st.number_input(
        "Seuil d'alerte (£)",
        min_value=10.0, max_value=10000.0,
        value=float(st.session_state.seuil), step=10.0,
    )
    st.session_state.son_actif = st.toggle(
        "🔊 Son actif (local)", value=st.session_state.son_actif
    )
    st.session_state.son_navigateur = st.toggle(
        "🌐 Son dans le navigateur",
        value=st.session_state.son_navigateur,
        help="À activer si tu utilises l'app déployée (Streamlit Cloud)."
    )

    st.divider()

    if st.button("🔄 Réinitialiser la référence", use_container_width=True):
        st.session_state.prix_reference = prix_actuel_gbp
        sauvegarder_reference(prix_actuel_gbp)
        st.success("Référence mise à jour !")
        st.rerun()

    st.caption(
        f"Référence actuelle : **{st.session_state.prix_reference:,.2f} £** "
        f"({st.session_state.prix_reference * taux_gbp_eur:,.2f} €)"
    )

with col_hist:
    st.subheader("📜 Historique des alertes")
    if not historique.empty:
        st.dataframe(
            historique.rename(columns={
                "horodatage": "Heure",
                "variation_gbp": "Variation (£)",
                "variation_eur": "Variation (€)",
                "type": "Type",
                "prix_gbp": "Prix (£)",
                "prix_eur": "Prix (€)",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Aucune alerte pour le moment.")

# ---------- Footer ----------
st.divider()
st.caption(
    f"Dernière MAJ : {maintenant_cameroun().strftime('%Y-%m-%d %H:%M:%S')} "
    f"(Africa/Douala) — Source : yfinance `{SYMBOLE_CACAO_LONDRES}` (fallback : `{SYMBOLE_CACAO_US}`)"
)