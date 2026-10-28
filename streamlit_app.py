import base64
import hashlib
import io
import json
import re
import ssl
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
from google import genai
from PIL import Image

ZAKLAD = Path(__file__).parent
LOGO = ZAKLAD / "logo.png"
SLOZKA_UZIVATELU = ZAKLAD / "uzivatele"
SLOZKA_UZIVATELU.mkdir(exist_ok=True)
MODELY = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

# SSL kontext pro bezpečné i archivní weby se staršími certifikáty (např. Porta fontium)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

st.set_page_config(
    page_title="Lupa na matriky",
    page_icon=Image.open(LOGO) if LOGO.exists() else ":material/menu_book:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- IDENTIFIKACE A PROSTOR UŽIVATELE (BEZPEČNÉ ODDĚLENÍ HISTORIE) ----------
def ziskej_uzivatelske_info():
    """
    Vrátí (je_prihlasen, zobrazovane_jmeno, email, identifikator_souboru).
    Podporuje Google OAuth přes Streamlit (st.user), nebo režim dočasného hosta.
    """
    prihlasen = False
    jmeno = "Host"
    email = ""
    try:
        user = getattr(st, "user", None)
        if user and getattr(user, "is_logged_in", False):
            prihlasen = True
            email = getattr(user, "email", "") or ""
            jmeno = getattr(user, "name", None) or (email.split("@")[0] if email else "Uživatel")
    except Exception:
        prihlasen = False

    if prihlasen and email:
        h = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
        return True, jmeno, email, f"user_{h}"

    if "host_id" not in st.session_state:
        st.session_state.host_id = uuid.uuid4().hex[:12]
    return False, "Host", "", f"host_{st.session_state.host_id}"


def ziskej_soubor_historie():
    _, _, _, ident = ziskej_uzivatelske_info()
    return SLOZKA_UZIVATELU / f"historie_{ident}.json"


# ---------- TRVALÉ UKLÁDÁNÍ HISTORIE NA DISK (PRO KAŽDÉHO ZVLÁŠŤ) ----------
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
        soubor = ziskej_soubor_historie()
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
        data = data[-25:]  # Uchováme posledních 25 hledání daného uživatele
        temp_soubor = soubor.with_suffix(".tmp")
        temp_soubor.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_soubor.replace(soubor)
    except Exception:
        pass


def nacti_disk_historii():
    soubor = ziskej_soubor_historie()
    if not soubor.exists():
        return []
    try:
        raw = json.loads(soubor.read_text(encoding="utf-8"))
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
    soubor = ziskej_soubor_historie()
    if soubor.exists():
        try:
            soubor.unlink()
        except Exception:
            pass


# ---------- PAMĚŤ APLIKACE A VYHLEDÁVACÍ STAV ----------
VYCHOZI = {
    "zpravy": [], "obrazek": None, "nazev_obrazku": None, "historie": [], "aktivni": None, "ceka": None,
    "tmavy": False, "info_skryto": False, "pismo": "Automaticky", "kniha": "Všechny druhy",
    "rezim_host": False, "auth_chyba": None, "posledni_nacteny_uid": None
}
for k, v in VYCHOZI.items():
    st.session_state.setdefault(k, v)

je_prihlasen, uzivatel_jmeno, uzivatel_email, uid = ziskej_uzivatelske_info()

# PŘIHLAŠOVACÍ BRÁNA (ZABEZPEČENÍ DŮVĚRNÝCH DAT UŽIVATELE)
if not je_prihlasen and not st.session_state.get("rezim_host"):
    LOGO_B64 = base64.b64encode(LOGO.read_bytes()).decode() if LOGO.exists() else ""
    st.markdown('<div style="text-align:center; margin:3vh 0 16px;">', unsafe_allow_html=True)
    if LOGO_B64:
        st.markdown(f'<img src="data:image/png;base64,{LOGO_B64}" style="width:76px; height:76px; border-radius:20px; box-shadow:0 4px 18px rgba(0,0,0,0.2); margin-bottom:12px;">', unsafe_allow_html=True)
    st.markdown("""
        <h1 style="font-size: clamp(2rem, 4vw, 2.8rem); font-weight:700; margin-bottom:6px;">Lupa na matriky</h1>
        <p style="color:var(--muted); font-size:1.05rem; max-width:600px; margin:0 auto 20px;">
            Tvůj soukromý průvodce a čtenář starých rodinných knih a matrik s umělou inteligencí
        </p>
    </div>
    """, unsafe_allow_html=True)

    col_l, col_m, col_r = st.columns([1, 2, 1])
    with col_m:
        with st.container(border=True):
            st.markdown("### 🔐 Zabezpečený rodinný prostor")
            st.markdown(
                "Aby byla tvoje hledání, zápisy i rodinné fotografie v bezpečí a měl/a k nim přístup jen ty, "
                "má každý uživatel svůj oddělený soukromý prostor."
            )

            if st.session_state.get("auth_chyba"):
                st.info(st.session_state.auth_chyba)

            if st.button("🔑 Přihlásit se přes Google", type="primary", use_container_width=True, key="btn_login_gate"):
                try:
                    st.login("google")
                except Exception:
                    st.session_state.auth_chyba = (
                        "ℹ️ Google přihlášení ještě není nastaveno v konfiguraci aplikace (Streamlit Secrets). "
                        "Můžeš zatím vstoupit jedním kliknutím jako host níže."
                    )
                    st.rerun()

            st.markdown("<div style='text-align:center; color:#888; margin:10px 0;'>— nebo —</div>", unsafe_allow_html=True)

            if st.button("👤 Pokračovat jako host (dočasné hledání)", use_container_width=True, key="btn_host_gate"):
                st.session_state.rezim_host = True
                st.rerun()

    st.stop()

# Automatické obnovení historie pro aktuálně aktivního uživatele
aktivni_uid = uid
if st.session_state.get("posledni_nacteny_uid") != aktivni_uid:
    st.session_state.posledni_nacteny_uid = aktivni_uid
    nactena_historie = nacti_disk_historii()
    st.session_state.historie = nactena_historie
    if nactena_historie:
        posledni = nactena_historie[-1]
        st.session_state.zpravy = posledni["zpravy"]
        st.session_state.obrazek = posledni["obrazek"]
        st.session_state.nazev_obrazku = posledni.get("nazev_obrazku")
        st.session_state.aktivni = len(nactena_historie) - 1
    else:
        st.session_state.zpravy = []
        st.session_state.obrazek = None
        st.session_state.nazev_obrazku = None
        st.session_state.aktivni = None

LOGO_B64 = base64.b64encode(LOGO.read_bytes()).decode() if LOGO.exists() else ""
AVATAR_AI = str(LOGO) if LOGO.exists() else ":material/menu_book:"

# ---------- KARTY ÚLOH S OBRÁZKY ----------
IMG_KARTY_DIR = ZAKLAD / "img_karty"
KARTY_INFO = {
    "cela": {
        "titulek": "Přepis celé stránky",
        "podtitul": "Přečíst kurent, švabach i latinu",
        "soubor": "karta_cela.jpg",
        "popis": "Přepiš mi celou stránku",
        "ikona": ":material/description:",
        "prompt": "Přepiš postupně všechny zápisy na stránce. U každého uveď **Přesný přepis** a **Český překlad**."
    },
    "rodina": {
        "titulek": "Rodiče a svědci",
        "podtitul": "Najít předky, kmotry a obce",
        "soubor": "karta_rodina.jpg",
        "popis": "Najdi rodiče, kmotry a svědky",
        "ikona": ":material/family_restroom:",
        "prompt": "Ke každému zápisu přehledně vypiš: hlavní osobu, rodiče (i prarodiče, pokud jsou uvedeni), kmotry nebo svědky, jejich povolání a bydliště."
    },
    "data": {
        "titulek": "Data a události",
        "podtitul": "Přehledná tabulka zápisů",
        "soubor": "karta_data.jpg",
        "popis": "Vypiš všechna data a místa",
        "ikona": ":material/calendar_month:",
        "prompt": "Vypiš přehlednou tabulku všech zápisů: datum, jméno, obec, číslo domu a druh události (narození / sňatek / úmrtí)."
    },
    "preklad": {
        "titulek": "Překlad do češtiny",
        "podtitul": "Srozumitelný převod zápisů",
        "soubor": "karta_preklad.jpg",
        "popis": "Přelož stránku do češtiny",
        "ikona": ":material/translate:",
        "prompt": "Přelož celou stránku do srozumitelné současné češtiny, zápis po zápisu. Původní přepis nepiš."
    },
}
KARTY_B64 = {}
for _k, _v in KARTY_INFO.items():
    _p = IMG_KARTY_DIR / _v["soubor"]
    KARTY_B64[_k] = base64.b64encode(_p.read_bytes()).decode() if _p.exists() else ""

AKCE = {k: (v["popis"], v["ikona"], v["prompt"]) for k, v in KARTY_INFO.items()}

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
1. V políčku dole klikni na **ikonu přílohy** a vyber fotku stránky z matriky nebo zadej odkaz z archivu.
2. Napiš **příjmení** (např. *Vlasák*) a stiskni **Enter**.
3. Další jméno na stejné stránce už jen napiš – fotka se pamatuje.
4. Když odešleš fotku **bez textu**, přepíše se celá stránka.
5. V záložce **„Prolistovat matriku s AI“** můžeš zadat odkaz na knihu z Porta fontium a nechat AI projít vybrané stránky automaticky!
"""

# ---------- BEZPEČNOSTNÍ A SÍŤOVÉ FUNKCE PRO ARCHIVY ----------
def je_bezpecna_url(url: str) -> bool:
    cista = url.strip().lower()
    if not (cista.startswith("http://") or cista.startswith("https://")):
        return False
    parsed = urllib.parse.urlparse(cista)
    hostname = (parsed.hostname or "").lower()
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254"):
        return False
    if hostname.startswith("10.") or hostname.startswith("192.168."):
        return False
    return True


def stahni_obrazek_z_url(url: str):
    """
    Univerzální stažení obrázku – podporuje jak přímé URL obrázků,
    tak prohlížecí odkazy z Porta Fontium (SOA Plzeň).
    """
    cista = url.strip()
    if not je_bezpecna_url(cista):
        return None, "Odkaz musí začínat http:// nebo https:// a nesmí mířit na lokální síť."

    # 1. Podpora pro Porta Fontium (SOA Plzeň)
    if "portafontium.eu/iipimage/" in cista:
        try:
            req = urllib.request.Request(cista, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=12) as r:
                html = r.read().decode("utf-8", errors="ignore")

            casti = cista.rstrip("/").split("/")
            konkretni_nazev = casti[-1] if len(casti) > 4 else None

            scany = list(dict.fromkeys(re.findall(r'register/[\w\-/\.]+\.jp2', html)))
            if not scany:
                return None, "V odkazu Porta Fontium se nepodařilo najít skeny matriky."

            vybrany_scan = scany[0]
            if konkretni_nazev and not konkretni_nazev.isdigit():
                for s in scany:
                    if konkretni_nazev in s:
                        vybrany_scan = s
                        break

            iip_url = f"http://www.portafontium.eu/fcgi-bin/iipsrv.fcgi?FIF={vybrany_scan}&wid=1600&cvt=jpeg"
            iip_req = urllib.request.Request(iip_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(iip_req, context=SSL_CTX, timeout=15) as r2:
                data = r2.read()
            img = Image.open(io.BytesIO(data))
            img.verify()
            return data, None
        except Exception as e:
            return None, f"Chyba při stahování z Porta Fontium: {e}"

    # 2. Běžné stažení přímého obrázku
    try:
        req = urllib.request.Request(cista, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=14) as odpoved:
            data = odpoved.read()
        img = Image.open(io.BytesIO(data))
        img.verify()
        return data, None
    except Exception as e:
        return None, str(e)


def ziskej_skeny_knihy(url: str):
    """
    Z URL matriky získá seznam scanů knihy.
    Podporuje Porta fontium, stránkované archivy i jednotlivé URL.
    """
    cista = url.strip()
    if not je_bezpecna_url(cista):
        return "chyba", "Neplatný nebo nepovolený odkaz."

    if "portafontium.eu/iipimage/" in cista:
        try:
            req = urllib.request.Request(cista, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=12) as r:
                html = r.read().decode("utf-8", errors="ignore")
            scany = list(dict.fromkeys(re.findall(r'register/[\w\-/\.]+\.jp2', html)))
            if not scany:
                return "chyba", "V knize na Porta Fontium nebyly nalezeny žádné skeny."
            return "porta", scany
        except Exception as e:
            return "chyba", f"Nepodařilo se načíst obsah knihy: {e}"

    if re.search(r'(?:strana|page|p)=(\d+)', cista, re.I):
        return "strankovane", cista

    return "jine", [cista]


def stahni_konkretni_scan(typ: str, polozka: str, cislo_strany: int = 1):
    try:
        if typ == "porta":
            iip_url = f"http://www.portafontium.eu/fcgi-bin/iipsrv.fcgi?FIF={polozka}&wid=1600&cvt=jpeg"
            req = urllib.request.Request(iip_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
                data = r.read()
        elif typ == "strankovane":
            nova_url = re.sub(r'((?:strana|page|p)=)(\d+)', rf'\g<1>{cislo_strany}', polozka, flags=re.I)
            req = urllib.request.Request(nova_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
                data = r.read()
        else:
            req = urllib.request.Request(polozka, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
                data = r.read()
        img = Image.open(io.BytesIO(data))
        img.verify()
        return data, None
    except Exception as e:
        return None, str(e)


def zkontroluj_stranku_vizualne(client, img_bytes, hledany_text: str) -> str:
    prompt = f"""Jsi zkušený český paleograf a archivář. Rychle prohlédni tuto přiloženou fotografii/sken ze staré matriky.
Hledáme: „{hledany_text}“.

Úkol:
1. Zjisti, zda se na této konkrétní stránce nachází hledané jméno, příjmení, rodina nebo událost. Ber v úvahu i kurent, švabach, latinu a dobové varianty (např. W/V, Y/I, ss/ß, -ová / -in).
2. Stručně shrň, co na stránce je (roky, farnost/obec nebo hlavní zapsaná příjmení a druh zápisů).

Odpověz stručně ve formátu:
STAV: [NALEZENO / NENALEZENO / MOŽNÁ SHODA]
POPIS: [1-3 věty co je na této stránce za zápisy a zda/kde se tam hledaná osoba nachází]
"""
    img = Image.open(io.BytesIO(img_bytes))
    posledni_chyba = None
    for m in MODELY:
        try:
            res = client.models.generate_content(model=m, contents=[img, prompt])
            return res.text
        except Exception as e:
            posledni_chyba = e
            if "503" in str(e) or "UNAVAILABLE" in str(e) or "429" in str(e) or "404" in str(e):
                continue
            raise e
    raise posledni_chyba


# ---------- AKCE TLAČÍTEK ----------
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


# ---------- ARCHIVNÍ WEBY A ROZCESTNÍK ----------
ARCHIVY_INFO = [
    {
        "nazev": "Porta fontium (SOA v Plzni)",
        "oblast": "Plzeňský a Karlovarský kraj (Domažlice, Klatovy, Plzeň, Rokycany, Tachov, Cheb, Sokolov...)",
        "url": "https://www.portafontium.eu/",
        "hledani_url": "https://www.portafontium.eu/searching/all?category=matrika",
        "ikona": "🌲",
        "popis": "Západní Čechy – matriky, kroniky a sčítací operáty online zdarma bez registrace."
    },
    {
        "nazev": "Acta Publica (MZA v Brně)",
        "oblast": "Jihomoravský a Zlínský kraj, Vysočina (Brno, Znojmo, Jihlava, Třebíč, Kroměříž...)",
        "url": "https://www.actapublica.eu/",
        "hledani_url": "https://www.actapublica.eu/matriky/brno/",
        "ikona": "🍇",
        "popis": "Moravské matriky – křty, sňatky a úmrtí pro celou jižní a střední Moravu."
    },
    {
        "nazev": "DigiArchiv SOA v Třeboni",
        "oblast": "Jihočeský kraj a Pelhřimovsko (České Budějovice, Krumlov, Třeboň, Tábor, Písek...)",
        "url": "https://digi.ceskearchivy.cz/",
        "hledani_url": "https://digi.ceskearchivy.cz/",
        "ikona": "🐟",
        "popis": "Jižní Čechy – farní matriky, pozemkové knihy a soupisy poddaných podle panství."
    },
    {
        "nazev": "eBadatelna SOA v Praze",
        "oblast": "Středočeský kraj (Kladno, Příbram, Benešov, Kolín, Rakovník, Kutná Hora, Beroun...)",
        "url": "https://ebadatelna.soapraha.cz/",
        "hledani_url": "https://ebadatelna.soapraha.cz/pages/SearchMatrikaPage",
        "ikona": "🏰",
        "popis": "Střední Čechy – digitalizované církevní matriky obcí v okolí Prahy."
    },
    {
        "nazev": "SOA v Zámrsku / Východní Čechy",
        "oblast": "Královéhradecký a Pardubický kraj, Havlíčkův Brod (Krkonoše, Orlické hory...)",
        "url": "https://vychodnicechy.ceskearchivy.cz/",
        "hledani_url": "https://vychodnicechy.ceskearchivy.cz/",
        "ikona": "⛰️",
        "popis": "Východní Čechy – inventář farních matrik a digitalizované matriční knihy."
    },
    {
        "nazev": "Digiteka Zemského archivu v Opavě",
        "oblast": "Moravskoslezský a Olomoucký kraj (Opava, Ostrava, Olomouc, Šumperk, Jeseník...)",
        "url": "https://digi.archives.cz/",
        "hledani_url": "https://digi.archives.cz/",
        "ikona": "⚒️",
        "popis": "Severní Morava a Slezsko – digitalizované matriky, urbáře a gruntovní knihy."
    },
    {
        "nazev": "Archiv hl. města Prahy (Praga Publica)",
        "oblast": "Hlavní město Praha (historické pražské farnosti)",
        "url": "https://katalog.ahmp.cz/pragapublica/",
        "hledani_url": "https://katalog.ahmp.cz/pragapublica/matriky",
        "ikona": "👑",
        "popis": "Praha – veškeré pražské fary, pobytové přihlášky pražského policejního ředitelství."
    },
    {
        "nazev": "Vademecum Národního archivu",
        "oblast": "Celá Česká republika (židovské a vojenské matriky)",
        "url": "https://vademecum.nacr.cz/",
        "hledani_url": "https://vademecum.nacr.cz/",
        "ikona": "📜",
        "popis": "Národní archiv – digitalizované židovské matriky (HBS) a vojenské matriční zápisy."
    },
]


@st.dialog("Archivní rozcestník – Kde hledat matriky online", width="large")
def okno_archivy():
    st.markdown("### 🏛️ Prohledej archivní weby pro svou obec")
    st.caption("Zadej název obce a AI s živým vyhledáváním na internetu ti prohledá archivy, najde farnost i přímý odkaz.")

    with st.form("form_archiv_dialog", clear_on_submit=False):
        c_vstup, c_btn = st.columns([3, 1.2], vertical_alignment="bottom")
        with c_vstup:
            hledana_obec = st.text_input("Obec, město nebo farnost:", placeholder="např. Nepomuk, Tišnov, Blatná, Klenčí pod Čerchovem, Sedlčany...", key="vstup_obec_arch")
        with c_btn:
            spustit_hledani = st.form_submit_button("🔍 Prohledat s AI", type="primary", use_container_width=True)

    if spustit_hledani and hledana_obec.strip():
        dotaz = f"V jakém archivu a pod jakou farnost spadala obec {hledana_obec.strip()}? Prohledej archivní weby a najdi mi online odkaz na matriku."
        st.session_state.zpravy.append({"role": "user", "text": dotaz, "obrazek": None})
        uloz_do_historie(f"Archiv: {hledana_obec.strip()}")
        st.rerun()

    st.markdown("---")
    st.markdown("#### 🗺️ České archivní portály podle krajů:")
    for arch in ARCHIVY_INFO:
        c1, c2 = st.columns([3.6, 1.4], vertical_alignment="center")
        with c1:
            st.markdown(f"**{arch['ikona']} [{arch['nazev']}]({arch['url']})**  \n"
                        f"<small style='color:var(--muted);'>{arch['oblast']}</small>  \n"
                        f"<small>{arch['popis']}</small>", unsafe_allow_html=True)
        with c2:
            st.link_button("Otevřít archiv ↗", arch["hledani_url"], use_container_width=True)
        st.divider()


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
.st-key-historie button, .st-key-pomoc button, .st-key-profil button { width: 100% !important; background: transparent !important; border: none !important;
  border-radius: 999px !important; padding: 6px 12px !important; min-height: 38px !important; transition: background .2s !important; }
.st-key-historie button > div, .st-key-pomoc button > div, .st-key-profil button > div { justify-content: flex-start !important; width: 100% !important; }
.st-key-historie button p, .st-key-pomoc button p, .st-key-profil button p { color: var(--text) !important; font-size: .9rem !important;
  white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important; }
.st-key-historie button [data-testid="stIconMaterial"], .st-key-pomoc button [data-testid="stIconMaterial"], .st-key-profil button [data-testid="stIconMaterial"] { color: var(--muted) !important; }
.st-key-historie button:hover, .st-key-pomoc button:hover, .st-key-profil button:hover { background: var(--hover) !important; }
.st-key-historie [data-testid="stBaseButton-primary"] { background: var(--hover) !important; }
.st-key-historie [data-testid="stBaseButton-primary"] p { font-weight: 600 !important; }
.st-key-historie [data-testid="stVerticalBlock"], .st-key-pomoc [data-testid="stVerticalBlock"] { gap: 2px !important; }
.st-key-rezim { margin-top: 10px; padding: 0 4px 0 12px; }
.st-key-rezim p { font-size: .9rem !important; margin: 0 !important; }
.st-key-rezim [data-testid="stIconMaterial"] { color: var(--muted) !important; }

/* ---------- TLAČÍTKO 1 – Nové hledání / Nový chat ---------- */
.st-key-novy button {
  position: relative !important;
  overflow: hidden !important;
  border: 1px solid rgba(255, 255, 255, 0.25) !important;
  border-radius: 999px !important;
  padding: .75rem 1.3rem !important;
  background: linear-gradient(135deg, #10b981 0%, #059669 50%, #047857 100%) !important;
  box-shadow: 0 4px 16px rgba(16, 185, 129, 0.4) !important;
  transition: transform .2s, box-shadow .2s !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}
.st-key-novy button::before {
  content: "";
  position: absolute;
  top: 0; left: -80%;
  width: 50%;
  height: 100%;
  background: linear-gradient(120deg, transparent, rgba(255, 255, 255, .5), transparent);
  transform: skewX(-20deg);
  transition: left .6s;
}
.st-key-novy button:hover {
  transform: translateY(-2px) scale(1.02) !important;
  box-shadow: 0 8px 24px rgba(16, 185, 129, 0.55) !important;
  background: linear-gradient(135deg, #34d399 0%, #10b981 50%, #059669 100%) !important;
}
.st-key-novy button:hover::before { left: 130%; }
.st-key-novy button:active { transform: scale(.97); }
.st-key-novy button p {
  color: #ffffff !important;
  font-size: 1.02rem !important;
  font-weight: 700 !important;
  margin: 0 !important;
}
.st-key-novy button [data-testid="stIconMaterial"] {
  color: #ffffff !important;
  font-size: 1.5rem !important;
  font-weight: 900 !important;
  background: rgba(255, 255, 255, 0.28) !important;
  border-radius: 50% !important;
  padding: 3px !important;
  margin-right: 8px !important;
  box-shadow: 0 2px 6px rgba(0,0,0,0.2) !important;
  transition: transform .3s ease !important;
}
.st-key-novy button:hover [data-testid="stIconMaterial"] {
  transform: rotate(90deg) scale(1.15) !important;
  background: rgba(255, 255, 255, 0.4) !important;
}

/* ---------- TLAČÍTKO 2 – Stáhnout přepis ---------- */
[class*="st-key-dl_"] button { background: var(--accent) !important; border: none !important; border-radius: 16px !important;
  padding: .55rem 1.1rem !important; overflow: hidden; transition: all .2s !important; }
[class*="st-key-dl_"] button p, [class*="st-key-dl_"] button [data-testid="stIconMaterial"] { color: #fff !important; transition: translate .3s ease-in-out, scale .3s ease-in-out; }
[class*="st-key-dl_"] button:hover [data-testid="stIconMaterial"] { translate: 1.6em 0; scale: 1.2; animation: let .6s ease-in-out infinite alternate; }
[class*="st-key-dl_"] button:hover p { translate: 8em 0; }
[class*="st-key-dl_"] button:active { transform: scale(.95); }
@keyframes let { from { transform: translateY(.1em); } to { transform: translateY(-.1em); } }

/* ---------- TLAČÍTKO PŘIPOJIT STRÁNKU (UIVERSE) ---------- */
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

/* ---------- UVÍTÁNÍ A KARTY S OBRÁZKY ---------- */
.pozdrav { margin: 4vh 0 24px; }
.pozdrav .p1, .pozdrav .p2 { font-size: clamp(2.3rem, 5vw, 3.6rem); font-weight: 500; line-height: 1.15; letter-spacing: -.03em; }
.pozdrav .p1 { background: linear-gradient(74deg, #34a853 0%, #0f9d58 30%, #00acc1 65%, #7cb342 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.pozdrav .p2 { color: var(--muted2); }

.st-key-karty { margin: 1.2rem 0 .8rem; }
.karta-nahled {
  width: 100%;
  height: 120px;
  overflow: hidden;
  border-top-left-radius: 18px;
  border-top-right-radius: 18px;
  border: 1px solid var(--border);
  border-bottom: none;
  background: var(--card);
}
.karta-nahled img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform .35s ease;
}
.st-key-karty [data-testid="stVerticalBlock"]:hover .karta-nahled img {
  transform: scale(1.06);
}
.st-key-karty button {
  height: auto !important;
  min-height: 58px !important;
  background: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-top: none !important;
  border-top-left-radius: 0 !important;
  border-top-right-radius: 0 !important;
  border-bottom-left-radius: 18px !important;
  border-bottom-right-radius: 18px !important;
  padding: 10px 14px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: flex-start !important;
  text-align: left !important;
  box-shadow: 0 4px 14px rgba(0,0,0,.08) !important;
  transition: background .2s, border-color .2s, transform .2s !important;
}
.st-key-karty button:hover {
  background: var(--hover) !important;
  border-color: var(--accent) !important;
  transform: translateY(-2px);
}
.st-key-karty button p {
  text-align: left !important;
  font-size: .95rem !important;
  font-weight: 600 !important;
  line-height: 1.35 !important;
  color: var(--text) !important;
  margin: 0 !important;
}
.st-key-karty button [data-testid="stIconMaterial"] {
  font-size: 1.3rem !important;
  color: var(--accent) !important;
  margin-right: 8px !important;
  flex-shrink: 0 !important;
}
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
def zeptej_se(obrazek_bytes, akce: str, text: str) -> str:
    pismo = st.session_state.get("pismo") or "Automaticky"
    kniha = st.session_state.get("kniha") or "Všechny druhy"

    if obrazek_bytes is None:
        if akce == "text":
            prompt = f"""Jsi zkušený, ochotný a milý český archivář, paleograf a genealog v aplikaci „Lupa na matriky“.
Pomáháš paní (mamince), která bádá ve starých matrikách a rodokmenech své rodiny.
Odpovídej česky, vřele, lidsky, srozumitelně a přehledně i pro laika. Používej odrážky, tučné zvýraznění a klikatelné odkazy ve formátu [Název](URL).

SPECIÁLNÍ SCHOPNOST: VYHLEDÁVÁNÍ V ČESKÝCH ARCHIVECH A MATRIKÁCH
Máš přístup k prohledávání českých archivních webů a online matrik.
Pokud se dotaz týká obcí, farností, archivů nebo kde hledat záznamy předků:
1. Urči současný název i historické (německé) varianty obce.
2. Urči příslušný farní úřad (farnost), pod který obec v dané době spadala.
3. Urči správný státní oblastní archiv a jeho online archivní web:
   - **Porta fontium** (SOA Plzeň) – Plzeňský a Karlovarský kraj: https://www.portafontium.eu/
   - **Acta Publica** (MZA Brno) – Morava a část Vysočiny (Brno, Jihlava, Znojmo, Třebíč...): https://www.actapublica.eu/
   - **DigiArchiv SOA Třeboň** – Jihočeský kraj a Pelhřimov: https://digi.ceskearchivy.cz/
   - **eBadatelna SOA Praha** – Středočeský kraj (Příbram, Kladno, Benešov, Kolín, Rakovník...): https://ebadatelna.soapraha.cz/
   - **SOA v Zámrsku** – Východní Čechy (Hradec Králové, Pardubice, Chrudim, Náchod...): https://vychodnicechy.ceskearchivy.cz/
   - **Digiteka ZA Opava** – Severní Morava a Slezsko (Opava, Ostrava, Olomouc, Šumperk...): https://digi.archives.cz/
   - **Archiv hl. m. Prahy** (Praga Publica) – Farnosti na území Prahy: https://katalog.ahmp.cz/
   - **Vademecum Národního archivu** – Židovské matriky a vojenské záznamy: https://vademecum.nacr.cz/
   - **Kramerius NK ČR** – Dobové noviny, adresáře, seznamy padlých: https://kramerius.nkp.cz/
4. Poskytni přímé klikatelné odkazy na příslušný archivní portál, návod jak tam matriku vyhledat a obvyklé časové rozsahy dochovaných knih (narození, sňatky, úmrtí).
5. Upozorni na případné změny farní příslušnosti (např. před rokem 1784 jiná mateřská farnost než po josefínských reformách).

Pokud se ptá na luštění starého písma (kurent, švabach, latina), překlady latinských a německých slov nebo stará povolání (podruh, chalupník, vejminkář, sedlák...), vysvětli jí vše jednoduše a srozumitelně.
Pokud žádá přepis konkrétního záznamu ze stránky, vysvětli jí, že stačí fotku stránky nahrát tlačítkem 📎 nahoře, nebo zadat odkaz z Porta fontium do záložky Prolistovat matriku s AI.

Dotaz od maminky:
„{text}“"""
        else:
            return ("Pro tento úkol je potřeba přiložit fotografii stránky z matriky. "
                    "📎 Přilož ji prosím tlačítkem nahoře, "
                    "a pak kartu stiskni znovu – hned se do čtení pustím.")
        obsah = [prompt]
    else:
        if akce == "text":
            ukol = f"""Uživatelka napsala: „{text}“.
Pokud jde o jméno nebo příjmení, najdi ho na stránce. Počítej s dobovými variantami pravopisu
(w/v, y/i, ss/ß, latinské a německé tvary, ženské tvary -ová / -in).
Pokud se ptá na obec, archiv nebo původ záznamu, doplň i informace o farnosti a příslušném archivu.
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

        img = Image.open(io.BytesIO(obrazek_bytes))
        obsah = [img, prompt]

    client = genai.Client(api_key=api_key)

    # Konfigurace pro živé vyhledávání na internetu a archivních webech (Google Search Grounding)
    konfigurace = None
    try:
        from google.genai import types
        konfigurace = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    except Exception:
        try:
            konfigurace = {"tools": [{"google_search": {}}]}
        except Exception:
            konfigurace = None

    posledni_chyba = None
    for m in MODELY:
        try:
            # U textových dotazů zapojíme živé vyhledávání v archivech
            if konfigurace and obrazek_bytes is None:
                try:
                    res = client.models.generate_content(model=m, contents=obsah, config=konfigurace)
                    odpoved_text = res.text
                    zdroje = []
                    try:
                        cand = res.candidates[0] if (res and res.candidates) else None
                        meta = cand.grounding_metadata if cand else None
                        if meta and getattr(meta, "grounding_chunks", None):
                            for chunk in meta.grounding_chunks:
                                web = getattr(chunk, "web", None)
                                if web and getattr(web, "uri", None):
                                    titulek = getattr(web, "title", None) or web.uri
                                    zdroje.append(f"- [{titulek}]({web.uri})")
                    except Exception:
                        pass
                    if zdroje:
                        unikatni_zdroje = list(dict.fromkeys(zdroje))[:4]
                        odpoved_text += "\n\n---\n**🔗 Nalezené archivní zdroje a odkazy:**\n" + "\n".join(unikatni_zdroje)
                    return odpoved_text
                except Exception:
                    pass

            return client.models.generate_content(model=m, contents=obsah).text
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

    st.markdown('<div class="sekce">Tvůj profil</div>', unsafe_allow_html=True)
    with st.container(key="profil"):
        if je_prihlasen:
            st.markdown(f"**👤 {uzivatel_jmeno}**  \n<small style='color:var(--muted);'>{uzivatel_email}</small>", unsafe_allow_html=True)
            if st.button("🚪 Odhlásit se", key="btn_sidebar_logout", width="stretch"):
                try:
                    st.logout()
                except Exception:
                    pass
                st.session_state.clear()
                st.rerun()
        else:
            st.markdown(f"**👤 Režim hosta**  \n<small style='color:var(--muted);'>Soukromé dočasné sezení</small>", unsafe_allow_html=True)
            if st.button("🔑 Přihlásit Google", key="btn_sidebar_login", width="stretch", help="Přihlásit se svým Google účtem pro trvalé uchování"):
                st.session_state.rezim_host = False
                st.rerun()

    with st.container(key="novy"):
        st.button("➕ Nové hledání", icon=":material/add:", width="stretch", on_click=nove_hledani)

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
            if st.button("🗑️ Smazat historii", width="stretch", help="Vymazat všechna uložená hledání z tvého prostoru"):
                smaz_celou_historii()
                st.rerun()
    else:
        st.markdown('<div class="prazdno">Zatím tu nic není.</div>', unsafe_allow_html=True)

    st.markdown('<div class="sekce">Pomoc a archivy</div>', unsafe_allow_html=True)
    with st.container(key="pomoc"):
        if st.button("Archivní rozcestník", icon=":material/travel_explore:", width="stretch", help="Kde hledat matriky online podle krajů a obcí"):
            okno_archivy()
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
    osloveni = f"Ahoj, {uzivatel_jmeno}." if je_prihlasen else "Ahoj, mami."
    st.markdown(f'<div class="pozdrav"><div class="p1">{osloveni}</div>'
                f'<div class="p2">Koho dnes v matrikách hledáme?</div></div>', unsafe_allow_html=True)
    with st.container(key="karty"):
        for sloupec, (klic, info) in zip(st.columns(4), KARTY_INFO.items()):
            with sloupec:
                obr_b64 = KARTY_B64.get(klic, "")
                if obr_b64:
                    st.markdown(
                        f'<div class="karta-nahled">'
                        f'<img src="data:image/jpeg;base64,{obr_b64}" alt="{info["titulek"]}">'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                if st.button(info["titulek"], icon=info["ikona"], key=f"karta_{klic}", width="stretch", help=info["popis"]):
                    klik = klic
    if not st.session_state.info_skryto:
        with st.container(key="infobox"):
            st.markdown(":material/info: Můžeš se mě **na cokoliv přímo zeptat** (např. *„Kde najdu matriky pro Nepomuk?“*), vložit níže **fotku stránky z počítače či odkaz**, nebo nechat AI **automaticky prolistovat celou knihu** z archivu.")
            with st.container(horizontal=True, key="info_odkazy"):
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

# ---------- PŘIPOJENÍ STRÁNKY / PROLISTOVÁNÍ MATRIKY S AI ----------
col_pr1, col_pr2 = st.columns([1, 1], vertical_alignment="center")
with col_pr1:
    with st.container(key="pripojit_tlacitko"):
        pop_popis = "📎 Připojit stránku (z PC, odkazem nebo AI)" if not st.session_state.obrazek else "🔄 Změnit stránku / Prozkoumat archiv"
        with st.popover(pop_popis, icon=":material/attachment:"):
            st.markdown("### Připojit stránku matriky nebo prozkoumat knihu")
            st.caption("Nahraj fotku ze svého počítače, zadej odkaz na scan, nebo nech AI automaticky prolistovat knihu v archivu.")
            tab_soubor, tab_odkaz, tab_scanner = st.tabs([
                "💻 Z počítače",
                "🌐 Jedna stránka (URL)",
                "🔍 Prolistovat matriku s AI"
            ])

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
                    "Vlož odkaz na obrázek nebo prohlížeč (např. Porta fontium):",
                    placeholder="https://www.portafontium.eu/iipimage/... nebo přímý odkaz na jpg",
                    key="pop_url_vstup"
                )
                if st.button("Stáhnout a připojit z odkazu", type="primary", key="btn_potvrdit_url", use_container_width=True):
                    if zadany_odkaz:
                        with st.spinner("Stahuji a kontroluji obrázek z odkazu…"):
                            data_url, chyba_url = stahni_obrazek_z_url(zadany_odkaz)
                            if data_url:
                                st.session_state.obrazek = data_url
                                st.session_state.nazev_obrazku = "Obrázek z archivu / odkazu"
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

            with tab_scanner:
                st.markdown("#### 📖 Prolistovat knihu a najít zápis s AI")
                st.caption("Zadej odkaz na knihu (např. z Porta fontium), rozsah stran a koho hledáš. AI postupně projde stránky, přečte písmo a najde hledaného předka.")

                scan_url = st.text_input(
                    "Odkaz na knihu v archivu:",
                    placeholder="https://www.portafontium.eu/iipimage/30067332/...",
                    key="input_scan_url",
                    help="Zadej odkaz na knihu z Porta fontium nebo jiného online archivu."
                )

                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    scan_od = st.number_input("Od strany:", min_value=1, max_value=999, value=1, step=1, key="num_scan_od")
                with col_s2:
                    scan_do = st.number_input("Do strany:", min_value=1, max_value=999, value=5, step=1, key="num_scan_do", help="Doporučeno 3–6 stran naráz pro rychlé zpracování.")

                scan_jmeno = st.text_input(
                    "Hledané příjmení nebo událost:",
                    placeholder="např. Vlasák, Jan Novák, narození 1785...",
                    key="input_scan_jmeno"
                )

                spustit_sken = st.button("🚀 Spustit AI průzkum stránek", type="primary", key="btn_run_scan", use_container_width=True)

                if spustit_sken:
                    if not api_key:
                        st.error("Chybí API klíč k umělé inteligenci v nastavení aplikace (Secrets).")
                    elif not scan_url.strip():
                        st.warning("Vlož prosím odkaz na knihu v archivu.")
                    elif not scan_jmeno.strip():
                        st.warning("Zadej hledané příjmení nebo událost.")
                    elif scan_do < scan_od:
                        st.warning("Číslo cílové strany musí být stejné nebo vyšší než počáteční strana.")
                    else:
                        skutecne_do = min(scan_do, scan_od + 5)
                        if scan_do > skutecne_do:
                            st.info(f"Rozsah byl upraven na {scan_od} až {skutecne_do} (max 6 stran naráz pro rychlý průzkum).")

                        typ_knihy, skeny = ziskej_skeny_knihy(scan_url)
                        if typ_knihy == "chyba":
                            st.error(f"Chyba při čtení knihy: {skeny}")
                        else:
                            vybrane_polozky = []
                            if typ_knihy == "porta":
                                celkem_dostupnych = len(skeny)
                                for cislo in range(scan_od, skutecne_do + 1):
                                    idx = cislo - 1
                                    if 0 <= idx < celkem_dostupnych:
                                        vybrane_polozky.append((cislo, skeny[idx]))
                            elif typ_knihy == "strankovane":
                                for cislo in range(scan_od, skutecne_do + 1):
                                    vybrane_polozky.append((cislo, scan_url))
                            else:
                                vybrane_polozky = [(scan_od, scan_url)]

                            if not vybrane_polozky:
                                st.error("V zadaném rozsahu nebyly nalezeny žádné stránky.")
                            else:
                                vysledky_scanu = []
                                vitezny_scan = None
                                vitezna_strana = None

                                with st.status(f"🤖 AI prochází stránky matriky a hledá „{scan_jmeno.strip()}“…", expanded=True) as status_box:
                                    klient_ai = genai.Client(api_key=api_key)
                                    for str_num, polozka in vybrane_polozky:
                                        status_box.write(f"📄 **Strana {str_num}:** Stahuji archivní sken…")
                                        img_data, ch_stazeni = stahni_konkretni_scan(typ_knihy, polozka, cislo_strany=str_num)
                                        if not img_data:
                                            status_box.write(f"⚠️ **Strana {str_num}:** Nepodařilo se stáhnout ({ch_stazeni})")
                                            vysledky_scanu.append((str_num, "Chyba", f"Nepodařilo se stáhnout: {ch_stazeni}", False))
                                            continue

                                        status_box.write(f"🔍 **Strana {str_num}:** AI luští písmo a porovnává zápisy…")
                                        try:
                                            analyza = zkontroluj_stranku_vizualne(klient_ai, img_data, scan_jmeno.strip())
                                            je_tam = any(x in analyza.upper() for x in ["STAV: NALEZENO", "STAV: [NALEZENO]", "STAV: MOŽNÁ"])
                                            if je_tam:
                                                status_box.write(f"🎉 **Strana {str_num}:** Nalezen záznam!")
                                                vysledky_scanu.append((str_num, "Nalezeno", analyza, True))
                                                vitezny_scan = img_data
                                                vitezna_strana = str_num
                                                status_box.update(label=f"🎉 Úspěch! Záznam nalezen na straně {str_num}!", state="complete")
                                                break
                                            else:
                                                status_box.write(f"ℹ️ **Strana {str_num}:** {analyza.strip()}")
                                                vysledky_scanu.append((str_num, "Nenalezeno", analyza, False))
                                        except Exception as ex:
                                            status_box.write(f"⚠️ **Strana {str_num}:** Chyba čtení: {ex}")
                                            vysledky_scanu.append((str_num, "Chyba", str(ex), False))

                                # Vyhodnocení a odeslání do chatu
                                dotaz_text = f"🔎 Prolistovat matriku a najít „{scan_jmeno.strip()}“ (strany {scan_od} až {skutecne_do})"
                                if vitezny_scan and vitezna_strana:
                                    st.session_state.obrazek = vitezny_scan
                                    st.session_state.nazev_obrazku = f"Nalezená stránka č. {vitezna_strana}"
                                    odpoved_text = (
                                        f"🎉 **Výborná zpráva! Hledaný záznam pro „{scan_jmeno.strip()}“ byl nalezen na straně {vitezna_strana}!**\n\n"
                                        f"Tuto stránku jsem ti rovnou **připojil do Lupy** (můžeš si ji prohlédnout v plné kvalitě nahoře).\n\n"
                                        f"**Zjištěné podrobnosti ze stránky:**\n"
                                        f"{vysledky_scanu[-1][2]}\n\n"
                                        f"💡 *Můžeš mi napsat další otázku k této stránce, nebo kliknout na tlačítka nahoře pro detailní přepis celého zápisu.*"
                                    )
                                else:
                                    polozky_shrnut = []
                                    for s_num, stav, txt, _ in vysledky_scanu:
                                        polozky_shrnut.append(f"- **Strana {s_num}:** {txt.strip()}")
                                    seznam_text = "\n".join(polozky_shrnut) if polozky_shrnut else "Žádné stránky se nepodařilo přečíst."
                                    odpoved_text = (
                                        f"Prozkoumal jsem stránky **{scan_od} až {skutecne_do}** v zadané matrice, ale přímou shodu pro „{scan_jmeno.strip()}“ jsem na nich nenalezl.\n\n"
                                        f"**Přehled zkontrolovaných stránek a co na nich je:**\n"
                                        f"{seznam_text}\n\n"
                                        f"💡 *Tip: Můžeš zkusit prozkoumat další rozsah (např. od strany {skutecne_do + 1}) nebo vyzkoušet jiné pravopisné varianty příjmení.*"
                                    )

                                st.session_state.zpravy.append({
                                    "role": "user",
                                    "text": dotaz_text,
                                    "obrazek": vitezny_scan if len(st.session_state.zpravy) == 0 else None
                                })
                                st.session_state.zpravy.append({
                                    "role": "assistant",
                                    "text": odpoved_text,
                                    "stazeni": None,
                                    "nazev": None
                                })
                                uloz_do_historie(f"Průzkum: {scan_jmeno.strip()}")
                                st.rerun()

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
vstup = st.chat_input("Napiš příjmení nebo otázku…")

pozadavek = None
if vstup:
    text = vstup.strip()
    pozadavek = ("text", text) if text else (st.session_state.ceka or "cela", "")
    st.session_state.ceka = None
elif klik:
    pozadavek = (klik, "")

if pozadavek:
    akce, text = pozadavek
    if not api_key:
        st.error("Chybí API klíč. Ve Streamlitu ho nastav v Settings → Secrets.")
    elif st.session_state.obrazek is None and akce != "text":
        st.session_state.ceka = akce
        st.toast("Pro tento úkol nejdřív přilož fotku stránky tlačítkem nahoře.",
                 icon=":material/add_photo_alternate:")
    else:
        popis = text if akce == "text" else AKCE[akce][0]
        if st.session_state.obrazek is None:
            zobrazeni = popis
            obrazek_pro_bublinu = None
        else:
            zobrazeni = popis
            obrazek_pro_bublinu = st.session_state.obrazek if len(st.session_state.zpravy) == 0 else None

        st.session_state.zpravy.append({"role": "user", "text": zobrazeni, "obrazek": obrazek_pro_bublinu})
        uloz_do_historie(popis)

        with zivy:
            with st.chat_message("user"):
                if obrazek_pro_bublinu:
                    st.image(obrazek_pro_bublinu, width=280)
                st.markdown(zobrazeni)
            with st.chat_message("assistant", avatar=AVATAR_AI):
                hlaska_cekani = "Přemýšlím…" if st.session_state.obrazek is None else "Luštím staré písmo…"
                with st.spinner(hlaska_cekani):
                    stazeni, nazev = None, None
                    try:
                        odpoved = zeptej_se(st.session_state.obrazek, akce, text)
                        cas = datetime.now()
                        stazeni = (f"Lupa na matriky – výsledek ({cas:%d.%m.%Y %H:%M})\n"
                                   f"Dotaz: {popis}\n{'=' * 50}\n\n{odpoved}\n")
                        cisty = "".join(c for c in (text or akce) if c.isalnum()) or "dotaz"
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
