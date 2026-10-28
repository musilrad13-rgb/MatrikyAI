import base64
import io
import json
import urllib.request
from datetime import datetime
from pathlib import Path

import streamlit as st
from google import genai
from PIL import Image

ZAKLAD = Path(__file__).parent
LOGO = ZAKLAD / "logo.png"
SOUBOR_HISTORIE = ZAKLAD / "historie_chatu.json"
MODELY = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash"]

st.set_page_config(
    page_title="Lupa na matriky",
    page_icon=Image.open(LOGO) if LOGO.exists() else ":material/menu_book:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- TRVALÉ UKLÁDÁNÍ HISTORIE NA DISK ----------
def _to_b64(val):
    if not val:
        return None
    if isinstance(val, (bytes, bytearray)):
        return base64.b64encode(val).decode("ascii")
    if isinstance(val, str):
        return val
    return None


def _from_b64(val):
    if not val:
        return None
    if isinstance(val, str):
        try:
            return base64.b64decode(val.encode("ascii"))
        except Exception:
            return None
    if isinstance(val, (bytes, bytearray)):
        return bytes(val)
    return None


def uloz_disk_historii():
    try:
        data = []
        for h in st.session_state.get("historie", []):
            polozka = {
                "nazev": h.get("nazev", "Hledání"),
                "nazev_obrazku": h.get("nazev_obrazku"),
                "obrazek_b64": _to_b64(h.get("obrazek")),
                "zpravy": []
            }
            for z in h.get("zpravy", []):
                polozka["zpravy"].append({
                    "role": z.get("role"),
                    "text": z.get("text"),
                    "stazeni": z.get("stazeni"),
                    "nazev": z.get("nazev"),
                    "obrazek_b64": _to_b64(z.get("obrazek"))
                })
            data.append(polozka)
        data = data[-20:]  # Uchováme posledních 20 hledání
        temp_soubor = SOUBOR_HISTORIE.with_suffix(".tmp")
        temp_soubor.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_soubor.replace(SOUBOR_HISTORIE)
    except Exception:
        pass


def nacti_disk_historii():
    if not SOUBOR_HISTORIE.exists():
        return []
    try:
        raw = json.loads(SOUBOR_HISTORIE.read_text(encoding="utf-8"))
        historie = []
        for h in raw:
            obr = _from_b64(h.get("obrazek_b64"))
            zpravy = []
            for z in h.get("zpravy", []):
                z_obr = _from_b64(z.get("obrazek_b64"))
                zpravy.append({
                    "role": z.get("role"),
                    "text": z.get("text"),
                    "stazeni": z.get("stazeni"),
                    "nazev": z.get("nazev"),
                    "obrazek": z_obr
                })
            historie.append({
                "nazev": h.get("nazev", "Hledání"),
                "nazev_obrazku": h.get("nazev_obrazku"),
                "obrazek": obr,
                "zpravy": zpravy
            })
        return historie
    except Exception:
        return []


def smaz_celou_historii():
    st.session_state.historie = []
    st.session_state.zpravy = []
    st.session_state.obrazek = None
    st.session_state.nazev_obrazku = None
    st.session_state.aktivni = None
    if SOUBOR_HISTORIE.exists():
        try:
            SOUBOR_HISTORIE.unlink()
        except Exception:
            pass


# ---------- PAMĚŤ APLIKACE ----------
VYCHOZI = {"zpravy": [], "obrazek": None, "nazev_obrazku": None, "historie": [], "aktivni": None, "ceka": None,
           "tmavy": False, "info_skryto": False, "pismo": "Automaticky", "kniha": "Všechny druhy"}
for k, v in VYCHOZI.items():
    st.session_state.setdefault(k, v)

# Automatické obnovení historie po restartu / rebootu
if "disk_nacteno" not in st.session_state:
    st.session_state.disk_nacteno = True
    nactena_historie = nacti_disk_historii()
    if nactena_historie:
        st.session_state.historie = nactena_historie
        posledni = nactena_historie[-1]
        st.session_state.zpravy = posledni["zpravy"]
        st.session_state.obrazek = posledni["obrazek"]
        st.session_state.nazev_obrazku = posledni.get("nazev_obrazku")
        st.session_state.aktivni = len(nactena_historie) - 1

LOGO_B64 = base64.b64encode(LOGO.read_bytes()).decode() if LOGO.exists() else ""
AVATAR_AI = str(LOGO) if LOGO.exists() else ":material/menu_book:"

AKCE = {
    "cela": ("Přepiš mi celou stránku", ":material/description:",
             "Přepiš postupně všechny zápisy na stránce. U každého uveď **Přesný přepis** a **Český překlad**."),
    "rodina": ("Najdi rodiče, kmotry a svědky", ":material/family_restroom:",
               "Ke každému zápisu přehledně vypiš: hlavní osobu, rodiče (i prarodiče, pokud jsou uvedeni), "
               "kmotry nebo svědky, jejich povolání a bydliště."),
    "data": ("Vypiš všechna data a místa", ":material/calendar_month:",
             "Vypiš přehlednou tabulku všech zápisů: datum, jméno, obec, číslo domu a druh události "
             "(narození / sňatek / úmrtí)."),
    "preklad": ("Přelož stránku do češtiny", ":material/translate:",
                "Přelož celou stránku do srozumitelné současné češtiny, zápis po zápisu. Původní přepis nepiš."),
}

SLOVNICEK = """
| Latinsky · německy | Česky |
|---|---|
| natus, baptizatus · geboren, getauft | narozen, pokřtěn |
| copulati · getraut | oddáni |
| mortuus, defunctus · gestorben | zemřel |
| filius / filia · Sohn / Tochter | syn / dcera |
| pater / mater · Vater / Mutter | otec / matka |
| legitimus / illegitimus | manželský / nemanželský |
| patrini, levantes · Pathen | kmotři |
| testes · Zeugen | svědci |
| rusticus · Bauer | sedlák |
| casarius · Häusler | chalupník, domkář |
| inquilinus · Inwohner | podruh |
| vidua / viduus · Witwe / Witwer | vdova / vdovec |
| Nro domus · Haus Nr. | číslo domu |
"""

NAPOVEDA = """
1. V políčku dole klikni na **ikonu přílohy** a vyber fotku stránky z matriky.
2. Napiš **příjmení** (např. *Vlasák*) a stiskni **Enter**.
3. Další jméno na stejné stránce už jen napiš – fotka se pamatuje.
4. Když odešleš fotku **bez textu**, přepíše se celá stránka.
5. Karty na úvodu a čipy pod odpovědí spustí další úkoly nad stejnou fotkou.

**Tip:** Je písmo moc malé? Klávesami **Win + Shift + S** vyřízni jen pár řádků.
Výstřižek se uloží do složky **Obrázky → Snímky obrazovky** a odtud ho přiložíš.
"""


# ---------- AKCE TLAČÍTEK ----------
def stahni_obrazek_z_url(url: str):
    try:
        cista = url.strip()
        if not (cista.startswith("http://") or cista.startswith("https://")):
            return None, "Odkaz musí začínat http:// nebo https://"
        req = urllib.request.Request(
            cista,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=14) as odpoved:
            data = odpoved.read()
        # Ověření, že jde o platný obrázek
        img = Image.open(io.BytesIO(data))
        img.verify()
        return data, None
    except Exception as e:
        return None, str(e)


def nove_hledani():
    st.session_state.zpravy = []
    st.session_state.obrazek = None
    st.session_state.nazev_obrazku = None
    st.session_state.aktivni = None
    st.session_state.ceka = None


def otevri_historii(i):
    polozka = st.session_state.historie[i]
    st.session_state.zpravy = polozka["zpravy"]
    st.session_state.obrazek = polozka["obrazek"]
    st.session_state.nazev_obrazku = polozka.get("nazev_obrazku")
    st.session_state.aktivni = i
    st.session_state.ceka = None


def skryj_info():
    st.session_state.info_skryto = True


def uloz_do_historie(nazev):
    if st.session_state.aktivni is None:
        st.session_state.historie.append({
            "nazev": nazev,
            "nazev_obrazku": st.session_state.nazev_obrazku,
            "zpravy": st.session_state.zpravy,
            "obrazek": st.session_state.obrazek
        })
        st.session_state.aktivni = len(st.session_state.historie) - 1
    else:
        st.session_state.historie[st.session_state.aktivni]["obrazek"] = st.session_state.obrazek
        st.session_state.historie[st.session_state.aktivni]["nazev_obrazku"] = st.session_state.nazev_obrazku
        st.session_state.historie[st.session_state.aktivni]["zpravy"] = st.session_state.zpravy
    uloz_disk_historii()


@st.dialog("Slovníček výrazů", width="large")
def okno_slovnicek():
    st.markdown(SLOVNICEK)


@st.dialog("Jak na to")
def okno_napoveda():
    st.markdown(NAPOVEDA)


# ---------- BARVY: SVĚTLÝ / TMAVÝ REŽIM ----------
if st.session_state.tmavy:
    barvy = dict(bg="#131314", side="#1b1c1d", card="#1e1f20", hover="#2a2c2e",
                 text="#e3e3e3", muted="#9aa0a6", muted2="#5f6368", border="#3c4043")
else:
    barvy = dict(bg="#ffffff", side="#f1f6f3", card="#f1f6f3", hover="#e2ede6",
                 text="#1f1f1f", muted="#5f6368", muted2="#c4c7c5", border="#dde3ea")
promenne = ";".join(f"--{k}:{v}" for k, v in barvy.items())

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
.stApp *:not([data-testid="stIconMaterial"]):not(code):not(pre) { font-family: 'Google Sans', 'Inter', system-ui, sans-serif; }
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: var(--bg) !important; color: var(--text) !important; }
header[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stHeader"] svg, [data-testid="stSidebarCollapseButton"] svg, [data-testid="stExpandSidebarButton"] svg { color: var(--muted) !important; }
.stApp p, .stApp li, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp strong, .stApp label, .stApp td, .stApp th { color: var(--text); }
.block-container { max-width: 880px !important; padding-top: 2.6rem !important; padding-bottom: 1.5rem !important; }
hr, table, th, td { border-color: var(--border) !important; }

/* ---------- LEVÝ PANEL ---------- */
[data-testid="stSidebar"], [data-testid="stSidebarContent"] { background: var(--side) !important; border-right: none !important; }
.znacka { display: flex; align-items: center; gap: 12px; padding: 0 4px 18px; }
.znacka img { width: 44px; height: 44px; border-radius: 12px; object-fit: cover; box-shadow: 0 2px 8px rgba(0,0,0,.18); }
.znacka b { display: block; font-size: 1.05rem; color: var(--text); }
.znacka small { color: var(--muted); font-size: .76rem; }
.sekce { font-size: .78rem; font-weight: 600; color: var(--muted); margin: 18px 0 6px 12px; }
.prazdno { color: var(--muted); font-size: .85rem; padding: 2px 12px; }
.st-key-historie button, .st-key-pomoc button { width: 100% !important; background: transparent !important; border: none !important;
  border-radius: 999px !important; padding: 6px 12px !important; min-height: 38px !important; transition: background .2s !important; }
.st-key-historie button > div, .st-key-pomoc button > div { justify-content: flex-start !important; width: 100% !important; }
.st-key-historie button p, .st-key-pomoc button p { color: var(--text) !important; font-size: .9rem !important;
  white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important; }
.st-key-historie button [data-testid="stIconMaterial"], .st-key-pomoc button [data-testid="stIconMaterial"] { color: var(--muted) !important; }
.st-key-historie button:hover, .st-key-pomoc button:hover { background: var(--hover) !important; }
.st-key-historie [data-testid="stBaseButton-primary"] { background: var(--hover) !important; }
.st-key-historie [data-testid="stBaseButton-primary"] p { font-weight: 600 !important; }
.st-key-historie [data-testid="stVerticalBlock"], .st-key-pomoc [data-testid="stVerticalBlock"] { gap: 2px !important; }
.st-key-rezim { margin-top: 10px; padding: 0 4px 0 12px; }
.st-key-rezim p { font-size: .9rem !important; margin: 0 !important; }
.st-key-rezim [data-testid="stIconMaterial"] { color: var(--muted) !important; }

/* ---------- TLAČÍTKO 1 – Nové hledání ---------- */
.st-key-novy button { position: relative; overflow: hidden; border: none !important; border-radius: 999px !important;
  padding: .7rem 1.2rem !important; background: linear-gradient(135deg, var(--accent2), var(--accent)) !important;
  box-shadow: 0 6px 18px rgba(15,157,88,.35) !important; transition: transform .2s, box-shadow .2s !important; }
.st-key-novy button::before { content: ""; position: absolute; top: 0; left: -80%; width: 50%; height: 100%;
  background: linear-gradient(120deg, transparent, rgba(255,255,255,.5), transparent); transform: skewX(-20deg); transition: left .6s; }
.st-key-novy button:hover { transform: translateY(-2px); box-shadow: 0 10px 24px rgba(15,157,88,.45) !important; }
.st-key-novy button:hover::before { left: 130%; }
.st-key-novy button:active { transform: scale(.97); }
.st-key-novy button p, .st-key-novy button [data-testid="stIconMaterial"] { color: #fff !important; font-weight: 600 !important; }
.st-key-novy button [data-testid="stIconMaterial"] { transition: transform .3s; }
.st-key-novy button:hover [data-testid="stIconMaterial"] { transform: rotate(90deg); }

/* ---------- TLAČÍTKO 2 – Stáhnout přepis ---------- */
[class*="st-key-dl_"] button { background: var(--accent) !important; border: none !important; border-radius: 16px !important;
  padding: .55rem 1.1rem !important; overflow: hidden; transition: all .2s !important; }
[class*="st-key-dl_"] button p, [class*="st-key-dl_"] button [data-testid="stIconMaterial"] { color: #fff !important; transition: translate .3s ease-in-out, scale .3s ease-in-out; }
[class*="st-key-dl_"] button:hover [data-testid="stIconMaterial"] { translate: 1.6em 0; scale: 1.2; animation: let .6s ease-in-out infinite alternate; }
[class*="st-key-dl_"] button:hover p { translate: 8em 0; }
[class*="st-key-dl_"] button:active { transform: scale(.95); }
@keyframes let { from { transform: translateY(.1em); } to { transform: translateY(-.1em); } }

/* ---------- TLAČÍTKO uiverse.io/adamgiebl/shaggy-grasshopper-91 (Připojení matriky) ---------- */
.st-key-pripojit_tlacitko button,
.st-key-pripojit_tlacitko [data-testid="stPopover"] button,
.st-key-pripojit_tlacitko .stPopoverTarget button,
.st-key-pripojit_tlacitko div[data-testid="stPopover"] button {
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  font-family: inherit !important;
  cursor: pointer !important;
  font-weight: 500 !important;
  font-size: 15px !important;
  padding: 0.65em 1.4em 0.65em 1.1em !important;
  color: #ffffff !important;
  background: linear-gradient(
    0deg,
    rgba(20, 167, 62, 1) 0%,
    rgba(102, 247, 113, 1) 100%
  ) !important;
  border: none !important;
  box-shadow: 0 0.7em 1.5em -0.5em #14a73e98 !important;
  letter-spacing: 0.05em !important;
  border-radius: 20em !important;
  transition: transform 0.2s, box-shadow 0.2s !important;
}
.st-key-pripojit_tlacitko button:hover,
.st-key-pripojit_tlacitko [data-testid="stPopover"] button:hover,
.st-key-pripojit_tlacitko .stPopoverTarget button:hover,
.st-key-pripojit_tlacitko div[data-testid="stPopover"] button:hover {
  box-shadow: 0 0.5em 1.5em -0.5em #14a73e98 !important;
  transform: translateY(-2px) !important;
  background: linear-gradient(
    0deg,
    rgba(20, 167, 62, 1) 0%,
    rgba(102, 247, 113, 1) 100%
  ) !important;
}
.st-key-pripojit_tlacitko button:active,
.st-key-pripojit_tlacitko [data-testid="stPopover"] button:active,
.st-key-pripojit_tlacitko .stPopoverTarget button:active,
.st-key-pripojit_tlacitko div[data-testid="stPopover"] button:active {
  box-shadow: 0 0.3em 1em -0.5em #14a73e98 !important;
  transform: translateY(1px) !important;
}
.st-key-pripojit_tlacitko button p,
.st-key-pripojit_tlacitko button span,
.st-key-pripojit_tlacitko button [data-testid="stIconMaterial"],
.st-key-pripojit_tlacitko [data-testid="stPopover"] button p,
.st-key-pripojit_tlacitko [data-testid="stPopover"] button span,
.st-key-pripojit_tlacitko [data-testid="stPopover"] button [data-testid="stIconMaterial"] {
  color: #ffffff !important;
  font-weight: 600 !important;
}
button[kind="secondary"] {
  background: var(--card) !important;
  color: var(--text) !important;
  border: 1px solid var(--border) !important;
}
button[kind="secondary"]:hover {
  background: var(--hover) !important;
}
button[kind="secondary"] p {
  color: var(--text) !important;
}
.status-pripojeno {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--card);
  border: 1px solid rgba(20, 167, 62, 0.4);
  color: var(--accent);
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 0.85rem;
  font-weight: 600;
}
.st-key-btn_odebrat_foto button {
  background: transparent !important;
  border: 1px solid var(--border) !important;
  border-radius: 999px !important;
  color: var(--muted) !important;
  padding: 4px 12px !important;
  font-size: 0.82rem !important;
}
.st-key-btn_odebrat_foto button:hover {
  background: rgba(239, 68, 68, 0.15) !important;
  border-color: #ef4444 !important;
  color: #ef4444 !important;
}
.st-key-btn_smazat_hist button {
  background: transparent !important;
  border: 1px dashed var(--border) !important;
  border-radius: 999px !important;
  color: var(--muted) !important;
  padding: 4px 10px !important;
  font-size: 0.8rem !important;
  margin-top: 6px !important;
}
.st-key-btn_smazat_hist button:hover {
  background: rgba(239, 68, 68, 0.12) !important;
  border-color: #ef4444 !important;
  color: #ef4444 !important;
}

/* ---------- HORNÍ VÝBĚR ---------- */
.st-key-horni [data-testid="stPopover"] button { background: transparent !important; border: none !important; border-radius: 10px !important; }
.st-key-horni [data-testid="stPopover"] button p { font-size: 1.1rem !important; color: var(--text) !important; }
.st-key-horni [data-testid="stPopover"] button:hover { background: var(--hover) !important; }

/* ---------- UVÍTÁNÍ ---------- */
.pozdrav { margin: 6vh 0 36px; }
.pozdrav .p1, .pozdrav .p2 { font-size: clamp(2.3rem, 5vw, 3.6rem); font-weight: 500; line-height: 1.15; letter-spacing: -.03em; }
.pozdrav .p1 { background: linear-gradient(74deg, #34a853 0%, #0f9d58 30%, #00acc1 65%, #7cb342 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.pozdrav .p2 { color: var(--muted2); }
.st-key-karty button { height: 168px !important; background: var(--card) !important; border: none !important; border-radius: 16px !important;
  padding: 16px !important; display: flex !important; flex-direction: column-reverse !important; align-items: flex-start !important;
  justify-content: space-between !important; transition: background .2s, transform .2s !important; }
.st-key-karty button > div:not([data-testid="stMarkdownContainer"]) { display: flex !important; flex-direction: column-reverse !important;
  justify-content: space-between !important; align-items: flex-start !important; height: 100% !important; width: 100% !important; }
.st-key-karty button:hover { background: var(--hover) !important; transform: translateY(-2px); }
.st-key-karty button p { text-align: left !important; font-size: .95rem !important; line-height: 1.4 !important; color: var(--text) !important; }
.st-key-karty button [data-testid="stIconMaterial"] { align-self: flex-end !important; background: var(--bg) !important; border-radius: 50% !important;
  padding: 8px !important; font-size: 1.3rem !important; color: var(--text) !important; }
.st-key-infobox { background: var(--card); border-radius: 16px; padding: 16px 20px 10px; margin-top: 16px; }
.st-key-infobox p { font-size: .9rem !important; line-height: 1.55 !important; }
.st-key-infobox [data-testid="stIconMaterial"] { color: var(--muted) !important; }
.st-key-info_odkazy button { background: transparent !important; border: none !important; padding: 2px 8px !important; min-height: 0 !important; }
.st-key-info_odkazy button p { color: var(--accent) !important; font-weight: 600 !important; }
.st-key-info_odkazy button:hover { background: var(--hover) !important; }
.st-key-cipy button { background: transparent !important; border: 1px solid var(--border) !important; border-radius: 999px !important; }
.st-key-cipy button p, .st-key-cipy button [data-testid="stIconMaterial"] { color: var(--text) !important; font-size: .85rem !important; }
.st-key-cipy button:hover { background: var(--hover) !important; }

/* ---------- KONVERZACE ---------- */
[data-testid="stChatMessage"] { background: transparent !important; padding: .4rem 0 !important; gap: 14px; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { flex-direction: row-reverse; }
[data-testid="stChatMessageAvatarUser"] { display: none !important; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
  background: var(--card); border-radius: 22px 22px 6px 22px; padding: 12px 18px; max-width: 75%; flex: 0 1 auto; }
[data-testid="stChatMessageAvatarCustom"] { border-radius: 10px !important; overflow: hidden; }
[data-testid="stChatMessage"] h3 { color: var(--accent) !important; font-size: 1.15rem !important; }

/* ---------- SPODNÍ POLÍČKO ---------- */
[data-testid="stBottom"], [data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] { background: var(--bg) !important; }
[data-testid="stBottomBlockContainer"] { max-width: 880px !important; padding-bottom: 12px !important; }
[data-testid="stBottomBlockContainer"]::after { content: "Lupa může dělat chyby – důležité údaje si vždy ověř v originále matriky.";
  display: block; text-align: center; font-size: .75rem; color: var(--muted); margin-top: 10px; }
[data-testid="stChatInput"] { background: var(--card) !important; border: 1px solid transparent !important; border-radius: 32px !important;
  padding: 6px 8px 6px 14px !important; transition: border-color .2s, box-shadow .2s; }
[data-testid="stChatInput"]:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 4px rgba(15,157,88,.12) !important; }
[data-testid="stChatInput"] > div { background: transparent !important; border: none !important; }
[data-testid="stChatInput"] textarea { background: transparent !important; color: var(--text) !important; font-size: 1rem !important; }
[data-testid="stChatInput"] textarea::placeholder { color: var(--muted) !important; }
[data-testid="stChatInputFileUploadButton"] { border-radius: 50% !important; color: var(--muted) !important; }
[data-testid="stChatInputFileUploadButton"]:hover { background: var(--hover) !important; }
[data-testid="stChatInputSubmitButton"] { background: var(--accent) !important; color: #fff !important; border-radius: 14px !important;
  overflow: hidden; transition: transform .2s, background .2s !important; }
[data-testid="stChatInputSubmitButton"]:disabled { background: var(--hover) !important; color: var(--muted) !important; }
[data-testid="stChatInputSubmitButton"]:not(:disabled):hover svg { animation: let .6s ease-in-out infinite alternate; }
[data-testid="stChatInputSubmitButton"]:active { transform: scale(.92); }

/* ---------- OKNA, VÝBĚRY, OZNÁMENÍ ---------- */
[data-testid="stPopoverBody"], [data-testid="stDialog"] [role="dialog"] { background: var(--side) !important; color: var(--text) !important; border: 1px solid var(--border) !important; box-shadow: 0 12px 36px rgba(0,0,0,.25) !important; }
[data-testid="stPopoverBody"] input, [data-testid="stDialog"] input { background: var(--card) !important; color: var(--text) !important; border: 1px solid var(--border) !important; }
[data-testid="stPopoverBody"] [data-testid="stFileUploaderDropzone"] { background: var(--card) !important; border: 1px dashed var(--border) !important; }
[data-testid="stPopoverBody"] [data-testid="stFileUploaderDropzone"] * { color: var(--text) !important; }
[data-testid="stDialog"] [role="dialog"] svg { color: var(--muted) !important; }
[data-testid^="stBaseButton-pills"] { background: var(--card) !important; border-color: var(--border) !important; }
[data-testid^="stBaseButton-pills"] p { color: var(--text) !important; }
[data-testid="stBaseButton-pillsActive"] { background: rgba(15,157,88,.15) !important; border-color: var(--accent) !important; }
[data-testid="stBaseButton-pillsActive"] p { color: var(--accent) !important; }
[data-testid="stToast"] { background: var(--card) !important; color: var(--text) !important; }
</style>
"""
st.markdown(f"<style>:root{{{promenne};--accent:#0f9d58;--accent2:#34a853}}</style>" + CSS, unsafe_allow_html=True)

# ---------- API KLÍČ (jen ze Secrets!) ----------
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    api_key = ""


# ---------- DOTAZ NA GEMINI ----------
def zeptej_se(obrazek_bytes: bytes, akce: str, text: str) -> str:
    pismo = st.session_state.get("pismo") or "Automaticky"
    kniha = st.session_state.get("kniha") or "Všechny druhy"
    if akce == "text":
        ukol = f"""Uživatelka napsala: „{text}“.
Pokud jde o jméno nebo příjmení, najdi ho na stránce. Počítej s dobovými variantami pravopisu
(w/v, y/i, ss/ß, latinské a německé tvary, ženské tvary -ová / -in).
Odpověz v tomto formátu:

### Nalezeno: ANO / NE / MOŽNÁ

Pro každý zápis:
**Zápis č. X**
- **Přesný přepis:** …
- **Český překlad:** …
- **Co z toho plyne:** datum, rodiče, kmotři / svědci, obec, číslo domu, povolání

Pokud nic nenajdeš, vypiš podobně znějící jména ze stránky.
Pokud jde o otázku, odpověz na ni podle toho, co je na stránce."""
    else:
        ukol = AKCE[akce][2]

    prompt = f"""Jsi zkušený archivář a paleograf, expert na čtení českých matrik
(latina, němčina psaná kurentem / švabachem, stará čeština).
Písmo podle uživatelky: {pismo}. Druh matriky: {kniha}.

{ukol}

Nečitelná místa označ [nečitelné], nejistá čtení napiš takto: [slovo?].
Odpovídej česky, jednoduše a přehledně – čte to laik."""

    client = genai.Client(api_key=api_key)
    img = Image.open(io.BytesIO(obrazek_bytes))
    
    posledni_chyba = None
    for m in MODELY:
        try:
            return client.models.generate_content(model=m, contents=[img, prompt]).text
        except Exception as e:
            posledni_chyba = e
            if "503" in str(e) or "UNAVAILABLE" in str(e) or "429" in str(e) or "404" in str(e):
                continue
            raise e
    raise posledni_chyba


# ---------- LEVÝ PANEL ----------
with st.sidebar:
    if LOGO_B64:
        st.markdown(f'<div class="znacka"><img src="data:image/png;base64,{LOGO_B64}">'
                    f'<div><b>Lupa na matriky</b><small>Čtení starých zápisů s AI</small></div></div>',
                    unsafe_allow_html=True)
    with st.container(key="novy"):
        st.button("Nové hledání", icon=":material/add:", width="stretch", on_click=nove_hledani)

    st.markdown('<div class="sekce">Nedávná hledání</div>', unsafe_allow_html=True)
    if st.session_state.historie:
        with st.container(key="historie"):
            for i in reversed(range(len(st.session_state.historie))):
                nazev = st.session_state.historie[i]["nazev"]
                kratky = nazev if len(nazev) <= 30 else nazev[:29] + "…"
                st.button(kratky, icon=":material/chat_bubble:", key=f"hist_{i}", width="stretch",
                          help=nazev, on_click=otevri_historii, args=(i,),
                          type="primary" if i == st.session_state.aktivni else "secondary")
        with st.container(key="btn_smazat_hist"):
            if st.button("🗑️ Smazat historii", width="stretch", help="Vymazat všechna uložená hledání z paměti"):
                smaz_celou_historii()
                st.rerun()
    else:
        st.markdown('<div class="prazdno">Zatím tu nic není.</div>', unsafe_allow_html=True)

    st.markdown('<div class="sekce">Pomoc</div>', unsafe_allow_html=True)
    with st.container(key="pomoc"):
        if st.button("Slovníček výrazů", icon=":material/menu_book:", width="stretch"):
            okno_slovnicek()
        if st.button("Jak na to", icon=":material/help:", width="stretch"):
            okno_napoveda()

    with st.container(key="rezim"):
        c1, c2 = st.columns([4, 1], vertical_alignment="center")
        with c1:
            if st.session_state.tmavy:
                st.markdown(":material/dark_mode: Tmavý režim")
            else:
                st.markdown(":material/light_mode: Světlý režim")
        with c2:
            st.toggle("Přepnout světlý a tmavý režim", key="tmavy", label_visibility="collapsed")

# ---------- HORNÍ LIŠTA ----------
with st.container(key="horni"):
    with st.popover(f"Lupa na matriky  ·  {st.session_state.get('pismo') or 'Automaticky'}", icon=":material/tune:"):
        st.pills("Písmo / jazyk zápisu", ["Automaticky", "Latina", "Němčina – kurent", "Stará čeština"], key="pismo")
        st.pills("Druh matriky", ["Všechny druhy", "Narození a křty", "Oddaní", "Zemřelí"], key="kniha")

# ---------- UVÍTÁNÍ ----------
klik = None
if not st.session_state.zpravy:
    st.markdown('<div class="pozdrav"><div class="p1">Ahoj, mami.</div>'
                '<div class="p2">Koho dnes v matrikách hledáme?</div></div>', unsafe_allow_html=True)
    with st.container(key="karty"):
        for sloupec, (klic, (popis, ikona, _)) in zip(st.columns(4), AKCE.items()):
            with sloupec:
                if st.button(popis, icon=ikona, key=f"karta_{klic}", width="stretch"):
                    klik = klic
    if not st.session_state.info_skryto:
        with st.container(key="infobox"):
            st.markdown(":material/shield: Nejdřív přilož **fotku stránky z matriky** ikonou přílohy v políčku dole. "
                        "Pak napiš příjmení, nebo klikni na jednu z karet. Fotky se v aplikaci neukládají – "
                        "k přečtení se odešlou do Google Gemini a po zavření stránky zmizí.")
            with st.container(horizontal=True, key="info_odkazy"):
                if st.button("Jak to funguje", key="info_jak"):
                    okno_napoveda()
                st.button("Rozumím", key="info_ok", on_click=skryj_info)

# ---------- KONVERZACE ----------
for i, z in enumerate(st.session_state.zpravy):
    if z["role"] == "user":
        with st.chat_message("user"):
            if z.get("obrazek"):
                st.image(z["obrazek"], width=280)
            st.markdown(z["text"])
    else:
        with st.chat_message("assistant", avatar=AVATAR_AI):
            st.markdown(z["text"])
            if z.get("stazeni"):
                st.download_button("Stáhnout přepis", data=z["stazeni"], file_name=z["nazev"],
                                   mime="text/plain", icon=":material/download:", key=f"dl_{i}")

zivy = st.container()

if st.session_state.zpravy and st.session_state.obrazek:
    with st.container(key="cipy"):
        for sloupec, (klic, (popis, ikona, _)) in zip(st.columns(4), AKCE.items()):
            with sloupec:
                if st.button(popis, icon=ikona, key=f"cip_{klic}", width="stretch"):
                    klik = klic

# ---------- PŘIPOJENÍ STRÁNKY Z MATRIKY (UIVERSE BUTTON shaggy-grasshopper-91) ----------
col_pr1, col_pr2 = st.columns([1, 1], vertical_alignment="center")
with col_pr1:
    with st.container(key="pripojit_tlacitko"):
        pop_popis = "📎 Připojit stránku (z PC nebo odkazem)" if not st.session_state.obrazek else "🔄 Změnit přiloženou stránku"
        with st.popover(pop_popis, icon=":material/attachment:"):
            st.markdown("### Připojit stránku matriky")
            st.caption("Vyber obrázek z počítače, nebo vlož internetový odkaz (např. z Porta fontium).")
            tab_soubor, tab_odkaz = st.tabs(["💻 Z počítače", "🌐 Z internetového odkazu"])
            
            with tab_soubor:
                vybrany_soubor = st.file_uploader(
                    "Vyber obrázek matriky:",
                    type=["jpg", "jpeg", "png", "webp"],
                    key="pop_uploader"
                )
                if vybrany_soubor is not None:
                    st.session_state.obrazek = vybrany_soubor.getvalue()
                    st.session_state.nazev_obrazku = vybrany_soubor.name
                    if st.session_state.aktivni is not None and 0 <= st.session_state.aktivni < len(st.session_state.historie):
                        st.session_state.historie[st.session_state.aktivni]["obrazek"] = st.session_state.obrazek
                        st.session_state.historie[st.session_state.aktivni]["nazev_obrazku"] = st.session_state.nazev_obrazku
                        uloz_disk_historii()
                    st.toast(f"Obrázek „{vybrany_soubor.name}“ byl připojen!", icon=":material/check_circle:")
                    st.rerun()
            
            with tab_odkaz:
                zadany_odkaz = st.text_input(
                    "Vlož odkaz na obrázek matriky:",
                    placeholder="https://.../obrazek.jpg",
                    key="pop_url_vstup"
                )
                if st.button("Stáhnout a připojit z odkazu", type="primary", key="btn_potvrdit_url", use_container_width=True):
                    if zadany_odkaz:
                        with st.spinner("Stahuji a kontroluji obrázek z odkazu…"):
                            data_url, chyba_url = stahni_obrazek_z_url(zadany_odkaz)
                            if data_url:
                                st.session_state.obrazek = data_url
                                st.session_state.nazev_obrazku = "Obrázek z internetového odkazu"
                                if st.session_state.aktivni is not None and 0 <= st.session_state.aktivni < len(st.session_state.historie):
                                    st.session_state.historie[st.session_state.aktivni]["obrazek"] = st.session_state.obrazek
                                    st.session_state.historie[st.session_state.aktivni]["nazev_obrazku"] = st.session_state.nazev_obrazku
                                    uloz_disk_historii()
                                st.toast("Obrázek z odkazu byl úspěšně připojen!", icon=":material/check_circle:")
                                st.rerun()
                            else:
                                st.error(f"Nepodařilo se stáhnout: {chyba_url}")
                    else:
                        st.warning("Vlož prosím platný odkaz.")

with col_pr2:
    if st.session_state.obrazek:
        c_status, c_del = st.columns([3, 1], vertical_alignment="center")
        with c_status:
            nazev = st.session_state.get("nazev_obrazku") or "Obrázek připraven"
            st.markdown(f'<div class="status-pripojeno"><span class="tecka"></span><b>Připojeno:</b> {nazev}</div>', unsafe_allow_html=True)
        with c_del:
            if st.button("✕ Odebrat", key="btn_odebrat_foto", help="Odebrat aktuální fotku matriky"):
                st.session_state.obrazek = None
                st.session_state.nazev_obrazku = None
                st.rerun()

# ---------- SPODNÍ POLÍČKO ----------
vstup = st.chat_input("Napiš příjmení nebo otázku…", accept_file=True,
                      file_type=["jpg", "jpeg", "png", "webp"])

pozadavek, novy_obrazek = None, None
if vstup:
    if vstup.files:
        novy_obrazek = vstup.files[0].getvalue()
        st.session_state.obrazek = novy_obrazek
        st.session_state.nazev_obrazku = vstup.files[0].name
    text = (vstup.text or "").strip()
    pozadavek = ("text", text) if text else (st.session_state.ceka or "cela", "")
    st.session_state.ceka = None
elif klik:
    pozadavek = (klik, "")

if pozadavek:
    akce, text = pozadavek
    if st.session_state.obrazek is None:
        st.session_state.ceka = None if akce == "text" else akce
        st.toast("Nejdřív přilož fotku stránky (tlačítkem výše nebo v políčku).",
                 icon=":material/add_photo_alternate:")
    elif not api_key:
        st.error("Chybí API klíč. Ve Streamlitu ho nastav v Settings → Secrets.")
    else:
        popis = text if akce == "text" else AKCE[akce][0]
        zobrazeni = popis if novy_obrazek else popis + "  \n_(na předchozí stránce)_"
        obrazek_pro_bublinu = novy_obrazek or (st.session_state.obrazek if len(st.session_state.zpravy) == 0 else None)
        st.session_state.zpravy.append({"role": "user", "text": zobrazeni, "obrazek": obrazek_pro_bublinu})
        uloz_do_historie(popis)

        with zivy:
            with st.chat_message("user"):
                if obrazek_pro_bublinu:
                    st.image(obrazek_pro_bublinu, width=280)
                st.markdown(zobrazeni)
            with st.chat_message("assistant", avatar=AVATAR_AI):
                with st.spinner("Luštím staré písmo…"):
                    stazeni, nazev = None, None
                    try:
                        odpoved = zeptej_se(st.session_state.obrazek, akce, text)
                        cas = datetime.now()
                        stazeni = (f"Lupa na matriky – výsledek ({cas:%d.%m.%Y %H:%M})\n"
                                   f"Dotaz: {popis}\n{'=' * 50}\n\n{odpoved}\n")
                        cisty = "".join(c for c in (text or akce) if c.isalnum()) or "stranka"
                        nazev = f"matrika_{cisty}_{cas:%Y%m%d_%H%M}.txt"
                    except Exception as e:
                        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                            odpoved = "Bezplatný limit je na chvíli vyčerpaný. Zkus to prosím za pár minut."
                        else:
                            odpoved = f"Něco se pokazilo: {e}"

        st.session_state.zpravy.append({"role": "assistant", "text": odpoved,
                                        "stazeni": stazeni, "nazev": nazev})
        if st.session_state.aktivni is not None and 0 <= st.session_state.aktivni < len(st.session_state.historie):
            st.session_state.historie[st.session_state.aktivni]["zpravy"] = st.session_state.zpravy
        uloz_disk_historii()
        st.rerun()