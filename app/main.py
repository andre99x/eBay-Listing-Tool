import csv
import io
import json
import os
import re
from urllib.parse import quote_plus, urlparse
import secrets
import hashlib
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .constants import EBAY_CONDITIONS, normalize_condition
from .db import Base, engine, get_db, ensure_schema
from .models import CategoryCache, ListingHistory, ListingJob, ListingPreview, User, UserSession, EbayAuth
from .schemas import (
    CSVIngestResult,
    CSVPreviewResult,
    DashboardStats,
    JobCreateRequest,
    AuthRequest,
    AuthResponse,
    EbayAuthStart,
    EbayAuthStatus,
    MeResponse,
    PoliciesResponse,
    LpnLookupResponse,
    ListedItemOut,
    ListingJobOut,
    ListingPreviewResponse,
    ListingRequest,
    SuggestionRequest,
    SuggestionResponse,
)
from .services import ai, ebay, keepa

Base.metadata.create_all(bind=engine)
ensure_schema()

app = FastAPI(title="ListGiant", version="0.1.0")


EBAY_CLIENT_ID_PLACEHOLDER = "DEIN_EBAY_CLIENT_ID"
EBAY_REDIRECT_URI_PLACEHOLDER = "https://deine-domain.de/ebay/callback"
EBAY_CLIENT_SECRET_PLACEHOLDER = "DEIN_EBAY_CLIENT_SECRET"


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_hex(32)
    session = UserSession(token=token, user_id=user.id, created_at=datetime.utcnow().isoformat())
    db.add(session)
    db.commit()
    return token


def get_user_from_token(db: Session, token: str | None) -> Optional[User]:
    if not token:
        return None
    session = db.query(UserSession).filter(UserSession.token == token).first()
    if session:
        return db.query(User).filter(User.id == session.user_id).first()
    return None


def current_user_optional(request: Request, db: Session) -> Optional[User]:
    token = extract_token(request)
    if not token:
        return None
    return get_user_from_token(db, token)


def extract_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1]
    return None


def get_user_or_401(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login erforderlich")
    return user


@app.post("/auth/register", response_model=AuthResponse)
def register_user(payload: AuthRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="E-Mail bereits registriert")

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        created_at=datetime.utcnow().isoformat(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_session(db, user)
    return AuthResponse(token=token, email=user.email)


@app.post("/auth/login", response_model=AuthResponse)
def login_user(payload: AuthRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if not user or user.password_hash != hash_password(payload.password):
        raise HTTPException(status_code=401, detail="Ungültige Zugangsdaten")
    token = create_session(db, user)
    return AuthResponse(token=token, email=user.email)


@app.get("/auth/me", response_model=MeResponse)
def me(user: User = Depends(get_user_or_401), db: Session = Depends(get_db)):
    account_name, linked = get_account_context(db, user_id=user.id)
    return MeResponse(email=user.email, ebay_linked=linked, account_name=account_name if linked else None)


@app.post("/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Kein Token gefunden")
    token = auth_header.split(" ", 1)[1]
    db.query(UserSession).filter(UserSession.token == token).delete()
    db.commit()
    return {"message": "Abgemeldet"}


@app.get("/oauth/ebay/start", response_model=EbayAuthStart)
def start_ebay_oauth(request: Request, user: User = Depends(get_user_or_401), db: Session = Depends(get_db)):
    if EBAY_CLIENT_ID_PLACEHOLDER == "DEIN_EBAY_CLIENT_ID" or EBAY_REDIRECT_URI_PLACEHOLDER == "https://deine-domain.de/ebay/callback":
        raise HTTPException(
            status_code=400,
            detail="Bitte Client-ID und Redirect-URI (RuName) in app/main.py hinterlegen, bevor du eBay verbindest.",
        )
    parsed_redirect = urlparse(EBAY_REDIRECT_URI_PLACEHOLDER)
    # eBay akzeptiert entweder einen RuName ohne Schema oder eine HTTPS-URL. Nur wenn ein Schema vorhanden
    # und nicht https ist, brechen wir ab, damit lokale Tests mit reinen RuName-Strings nicht blockieren.
    if parsed_redirect.scheme and parsed_redirect.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail="eBay akzeptiert nur HTTPS-Redirect-URIs. Verwende eine HTTPS-URL oder gib den reinen RuName ohne http/https an.",
        )
    token = extract_token(request)
    state = f"listgiant-{token}" if token else f"listgiant-user-{user.id}"
    ru_name = quote_plus(EBAY_REDIRECT_URI_PLACEHOLDER)
    auth_url = (
        "https://auth.ebay.com/oauth2/authorize"
        f"?client_id={EBAY_CLIENT_ID_PLACEHOLDER}"
        f"&redirect_uri={ru_name}"
        "&response_type=code&scope=https://api.ebay.com/oauth/api_scope"
        f"&state={state}"
    )
    return EbayAuthStart(auth_url=auth_url)


def _persist_ebay_tokens(db: Session, user_id: int, account_name: str, code: str) -> EbayAuthStatus:
    account_name = account_name or "eBay Konto"
    exchange = ebay.exchange_code_for_token(
        code,
        EBAY_REDIRECT_URI_PLACEHOLDER,
        EBAY_CLIENT_ID_PLACEHOLDER,
        EBAY_CLIENT_SECRET_PLACEHOLDER,
    )

    if exchange and exchange.get("access_token"):
        access_token = exchange["access_token"]
        refresh_token = exchange.get("refresh_token") or f"refresh-from-{code}"
        expires_at = exchange.get("expires_at") or datetime.utcnow().isoformat()
        account_name = exchange.get("account_name") or account_name
    else:
        access_token = f"demo-access-{code}"
        refresh_token = f"demo-refresh-{code}"
        expires_at = datetime.utcnow().isoformat()

    existing = db.query(EbayAuth).filter(EbayAuth.user_id == user_id).first()
    if existing:
        existing.access_token = access_token
        existing.refresh_token = refresh_token
        existing.expires_at = expires_at
        existing.account_name = account_name
    else:
        db.add(
            EbayAuth(
                user_id=user_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                account_name=account_name,
            )
        )

    db.commit()
    return EbayAuthStatus(ebay_linked=True, account_name=account_name, expires_at=expires_at)


def _user_id_from_state(db: Session, state: Optional[str]) -> Optional[int]:
    if not state or not state.startswith("listgiant-"):
        return None
    token = state.replace("listgiant-", "", 1)
    session = db.query(UserSession).filter(UserSession.token == token).first()
    if session:
        return session.user_id
    if token.startswith("user-"):
        try:
            return int(token.replace("user-", "", 1))
        except ValueError:
            return None
    return None


@app.get("/oauth/ebay/callback", response_class=HTMLResponse)
def finish_ebay_oauth_get(code: Optional[str] = None, state: Optional[str] = None, account_name: Optional[str] = None, db: Session = Depends(get_db)):
    user_id = _user_id_from_state(db, state)
    if not user_id:
        return HTMLResponse("<p style='color:#f87171;font-family:sans-serif'>State ungültig – bitte erneut starten.</p>", status_code=400)
    if not code:
        return HTMLResponse("<p style='color:#f87171;font-family:sans-serif'>Code fehlt.</p>", status_code=400)

    status = _persist_ebay_tokens(db, user_id, account_name or "eBay Konto", code)
    html = f"""
    <html><body style='font-family:Inter,system-ui;background:#0b1221;color:#e5e7eb;padding:20px;text-align:center'>
      <h2>ListGiant – eBay verknüpft</h2>
      <p>Account: {status.account_name}</p>
      <p>Du kannst dieses Fenster schließen.</p>
      <script>
        if (window.opener) {{ window.opener.postMessage({{ type: 'ebay-linked', account: '{status.account_name}' }}, '*'); window.close(); }}
      </script>
    </body></html>
    """
    return HTMLResponse(content=html)


@app.post("/oauth/ebay/callback", response_model=EbayAuthStatus)
def finish_ebay_oauth(
    payload: dict = Body(...),
    user: User = Depends(get_user_or_401),
    db: Session = Depends(get_db),
):
    code = payload.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="code fehlt")

    account_name = payload.get("account_name") or "eBay Konto"
    return _persist_ebay_tokens(db, user.id, account_name, code)


@app.get("/oauth/ebay/status", response_model=EbayAuthStatus)
def ebay_status(user: User = Depends(get_user_or_401), db: Session = Depends(get_db)):
    auth = db.query(EbayAuth).filter(EbayAuth.user_id == user.id).first()
    if not auth or not auth.access_token:
        return EbayAuthStatus(ebay_linked=False, account_name=None, expires_at=None)
    return EbayAuthStatus(
        ebay_linked=True,
        account_name=auth.account_name or "eBay Konto",
        expires_at=auth.expires_at,
    )


def get_account_context(db: Session, user_id: int) -> tuple[str, bool]:
    if user_id is None:
        raise HTTPException(status_code=401, detail="Login erforderlich")
    auth = db.query(EbayAuth).filter(EbayAuth.user_id == user_id).first()
    if auth and auth.access_token:
        return auth.account_name or "eBay Verbunden", True
    return "Sandbox Händlerkonto", False


def get_active_ebay_auth(db: Session, user_id: int) -> Optional[EbayAuth]:
    return db.query(EbayAuth).filter(EbayAuth.user_id == user_id).first()


LANDING_PAGE = """
<!doctype html>
<html lang=\"de\">
<head>
  <meta charset=\"utf-8\" />
  <title>ListGiant – eBay Listing Suite</title>
  <style>
    :root {
      --bg: #0c111b;
      --card: #0f1724;
      --muted: #9fb8d1;
      --text: #eef4ff;
      --accent: #7ce7d2;
      --accent-strong: #36c1a0;
      --border: #1c2536;
      --surface: #121a26;
      --danger: #f87171;
      --info: #60a5fa;
    }
    * { box-sizing: border-box; }
    body { font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif; margin: 0; background: radial-gradient(circle at 20% 20%, #131c2c, #0e1522 45%), #0c111b; color: var(--text); }
    .hero { display:flex; align-items:center; justify-content:space-between; padding: 28px 32px; background: linear-gradient(135deg, rgba(124,231,210,0.16), rgba(96,165,250,0.12)); border-bottom: 1px solid var(--border); box-shadow: 0 10px 30px rgba(0,0,0,0.25); position:sticky; top:0; z-index:10; backdrop-filter: blur(6px); }
    .brand { display:flex; align-items:center; gap:14px; }
    .logo { width:52px; height:52px; border-radius:16px; background: radial-gradient(circle at 30% 30%, #7ce7d2, #36c1a0 60%, #4f46e5); display:flex; align-items:center; justify-content:center; color:#0b1220; font-weight:800; letter-spacing:-0.5px; box-shadow: 0 10px 25px rgba(124,231,210,0.3); font-size:18px; }
    h1 { margin: 0; font-size:26px; letter-spacing:-0.4px; }
    .tagline { margin:2px 0 0 0; color: var(--muted); }
    .cta { background: linear-gradient(135deg, #6ee7b7, #22c55e); color:#0b1220; padding:10px 16px; border-radius:12px; font-weight:700; border:none; cursor:pointer; box-shadow: 0 10px 25px rgba(34,197,94,0.35); }
    .cta.secondary { background: transparent; color: var(--text); border:1px solid var(--border); box-shadow:none; }
    .layout { display:flex; gap:18px; padding: 20px 28px 32px 28px; }
    nav { min-width:260px; background: var(--surface); border:1px solid var(--border); border-radius:16px; padding:16px; box-shadow: 0 12px 30px rgba(0,0,0,0.25); position: sticky; top:110px; height: fit-content; }
    .nav-title { font-size:12px; text-transform:uppercase; letter-spacing:0.12em; color: var(--muted); margin-bottom:10px; }
    .nav-buttons { display:flex; flex-direction:column; gap:10px; }
    nav button { background: #111827; color:var(--text); border:1px solid var(--border); padding:12px 14px; border-radius:12px; cursor:pointer; font-weight:700; text-align:left; transition: all 0.2s ease; }
    nav button:hover { transform: translateY(-1px); border-color: var(--accent-strong); box-shadow: 0 10px 20px rgba(124,231,210,0.18); }
    nav button.secondary { background: linear-gradient(135deg, #111827, #0b1220); border-color:#22c55e; color:#e5ecf5; }
    nav button.ghost { background: transparent; border-style: dashed; color: var(--muted); }
    .content { flex:1; display:flex; flex-direction:column; gap:16px; }
    .card { background: var(--card); padding: 20px; border-radius: 16px; border:1px solid var(--border); box-shadow: 0 12px 30px rgba(0,0,0,0.2); }
    .row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
    label { font-weight:700; color: var(--text); }
    input, select, button { padding:10px; font-size:14px; border-radius:10px; border:1px solid var(--border); background: #0b1220; color: var(--text); }
    button { background: linear-gradient(135deg, #22c55e, #16a34a); color:#0b1220; font-weight:800; border:none; cursor:pointer; box-shadow: 0 12px 22px rgba(34,197,94,0.25); }
    button:disabled { opacity:0.45; cursor:not-allowed; }
    button.ghost { background: transparent; color: var(--text); border:1px solid var(--border); box-shadow:none; }
    pre { background:#0f172a; color:#cbd5e1; padding:12px; border-radius:10px; overflow:auto; border:1px solid var(--border); }
    .status { font-weight:800; color:#34d399; }
    .error { color:#fca5a5; font-weight:700; }
    .section { display:none; }
    .section.active { display:block; }
    ul.list { list-style:none; padding:0; margin:0; }
    ul.list li { padding:12px 14px; border:1px solid var(--border); border-radius:12px; margin-bottom:8px; background:#0f172a; }
    .muted { color: var(--muted); }
    .chip { display:inline-flex; align-items:center; gap:6px; background: rgba(124,231,210,0.12); color:#befae6; border:1px solid rgba(124,231,210,0.35); border-radius:999px; padding:6px 12px; font-weight:700; }
    .auth-card { max-width: 1080px; margin: 18px auto; padding: 18px 22px; background: var(--card); border:1px solid var(--border); border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); display: flex; gap:16px; justify-content: space-between; align-items:flex-start; }
    .auth-actions { display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:12px; width:100%; }
    .pill { background: rgba(96,165,250,0.12); color: #bfdbfe; border:1px solid rgba(96,165,250,0.3); padding:6px 12px; border-radius:999px; font-weight:700; }
    .accent-card { border:1px solid rgba(124,231,210,0.4); box-shadow: 0 12px 26px rgba(124,231,210,0.16); }
  </style>
</head>
<body>
  <header class=\"hero\">
    <div class=\"brand\">
      <div class=\"logo\">LG</div>
      <div>
        <h1>ListGiant</h1>
        <p class=\"tagline\">Premium 1-Klick-Listing für eBay – prüfe CSVs, baue Vorschauen, finalisiere Listings.</p>
      </div>
    </div>
    <div class=\"row\" style=\"gap:8px; align-items:center;\">
      <div id=\"authStatus\" class=\"chip\" style=\"display:none;\"></div>
      <button class=\"cta secondary\" onclick=\"window.location='/docs'\">API Docs</button>
      <button class=\"cta\" onclick=\"document.getElementById('nav-import').click()\">CSV hochladen</button>
    </div>
  </header>

  <div class=\"auth-card accent-card\" id=\"authPanel\">
    <div style=\"max-width:340px;\">
      <div class=\"pill\">Account Login</div>
      <h2 style=\"margin:10px 0 6px 0;\">Starte mit ListGiant</h2>
      <p class=\"muted\">Registriere dich oder logge dich ein. Die eBay-Verknüpfung erfolgt danach im Menübereich <strong>Account & eBay</strong> innerhalb der App.</p>
    </div>
    <div class=\"auth-actions\">
      <div class=\"card\" style=\"padding:14px;\">
        <h3 style=\"margin-top:0;\">Registrieren</h3>
        <input id=\"regEmail\" placeholder=\"E-Mail\" />
        <input id=\"regPass\" type=\"password\" placeholder=\"Passwort\" />
        <button id=\"regBtn\">Account erstellen</button>
        <div id=\"regResult\" class=\"muted\"></div>
      </div>
      <div class=\"card\" style=\"padding:14px;\">
        <h3 style=\"margin-top:0;\">Login</h3>
        <input id=\"loginEmail\" placeholder=\"E-Mail\" />
        <input id=\"loginPass\" type=\"password\" placeholder=\"Passwort\" />
        <button id=\"loginBtn\">Anmelden</button>
        <div id=\"loginResult\" class=\"muted\"></div>
      </div>
    </div>
  </div>

  <div id=\"appShell\" style=\"display:none;\">
  <div class=\"layout\">
    <nav>
      <div class=\"nav-title\">Account</div>
      <div class=\"nav-buttons\">
        <button id=\"nav-account\" class=\"secondary\">Account & eBay</button>
      </div>
      <div class=\"nav-title\">CSV</div>
      <div class=\"nav-buttons\">
        <button id=\"nav-db\">Datenbank einsehen</button>
        <button id=\"nav-import\" class=\"secondary\">Retourenliste erweitern</button>
        <button id=\"nav-listed\">Bereits bearbeitet</button>
        <button id=\"nav-reset\" class=\"ghost\">Datenbank löschen</button>
      </div>
      <div class=\"nav-title\" style=\"margin-top:16px;\">Workflow</div>
      <div class=\"nav-buttons\">
        <button id=\"nav-preview\" class=\"ghost\">Vorschau/Finalisieren</button>
        <button id=\"nav-dash\" class=\"ghost\">Dashboard</button>
      </div>
    </nav>

    <div class=\"content\">
      <div id=\"section-account\" class=\"section card active\">
        <h2 style=\"margin-top:0;\">Account & eBay</h2>
        <p class=\"muted\">1) Einloggen/registrieren. 2) <strong>Jetzt anbinden</strong> anklicken – ein eBay-Fenster öffnet sich. 3) Nach Zustimmung schließt sich das Fenster automatisch und dein Konto erscheint hier. Trage deine echten eBay API-Daten in <code>app/main.py</code> bei den Platzhaltern ein.</p>
        <div id=\"accountIdentity\" class=\"chip\"></div>
        <div id=\"accountEbayStatus\" class=\"chip\"></div>
        <div class=\"row\" style=\"margin-top:8px;\">
          <button id=\"oauthStartBtn\" class=\"ghost\">Jetzt anbinden</button>
          <span class=\"muted\" style=\"align-self:center\">1-Klick zu eBay, nach Zustimmung siehst du sofort dein Konto hier.</span>
        </div>
        <div id=\"oauthResult\" class=\"muted\"></div>
      </div>


      <div id=\"section-db\" class=\"section card\">
        <div class=\"row\" style=\"justify-content:space-between; align-items:flex-start;\">
          <div>
            <h2 style=\"margin:0 0 6px 0;\">Datenbank: Artikel unbearbeitet</h2>
            <p class=\"muted\">Alle aktiven LPN/ASIN-Kombinationen, die noch auf ein Listing warten. Nutze die Suche zum Scannen einer LPN.</p>
          </div>
          <div id=\"jobCount\" class=\"chip\"></div>
        </div>
        <div class=\"row\" style=\"margin-top:6px;\">
          <button id=\"refreshJobs\">Aktualisieren</button>
          <input id=\"lookupLpn\" placeholder=\"LPN scannen oder eintippen\" />
          <button id=\"lookupBtn\" class=\"ghost\">LPN suchen</button>
        </div>
        <div id=\"lookupResult\" class=\"muted\"></div>
        <ul id=\"jobs\" class=\"list\"></ul>
      </div>

      <div id=\"section-import\" class=\"section card\">
        <h2 style=\"margin-top:0;\">Retourenliste erweitern (Einlesen)</h2>
        <p class=\"muted\">Einmalig die vollständige Retouren-CSV laden. Später kannst du hier jederzeit neue CSVs hinzufügen; bestehende LPNS werden übersprungen. Vermischte CSVs werden automatisch auf Spalte A=LPN, B=ASIN, C=Artikelname gebracht.</p>
        <input type=\"file\" id=\"csvFile\" accept=\".csv\" />
        <div class=\"row\" style=\"margin-top:10px; gap:8px;\">
          <button id=\"previewUploadBtn\" class=\"ghost\">CSV prüfen</button>
          <button id=\"uploadBtn\" disabled>CSV hinzufügen</button>
        </div>
        <div id=\"mappingControls\" class=\"row\" style=\"gap:12px; margin-top:12px; display:none; flex-wrap:wrap;\">
          <div><label>LPN Spalte</label><br/><select id=\"mapLpn\"></select></div>
          <div><label>ASIN Spalte</label><br/><select id=\"mapAsin\"></select></div>
          <div><label>Artikelname Spalte</label><br/><select id=\"mapItem\"></select></div>
        </div>
        <div id=\"uploadResult\"></div>
        <div id=\"previewTable\"></div>
      </div>

      <div id=\"section-listed\" class=\"section card\">
        <h2 style=\"margin-top:0;\">Bereits bearbeitet</h2>
        <p class=\"muted\">Hier erscheinen LPNs, die finalisiert wurden und deshalb aus der unbearbeiteten Liste entfernt sind.</p>
        <button id=\"refreshListed\">Aktualisieren</button>
        <ul id=\"listed\" class=\"list\"></ul>
      </div>

      <div id=\"section-reset\" class=\"section card\">
        <h2 style=\"margin-top:0;\">Datenbank löschen</h2>
        <p class=\"muted\">Setzt unbearbeitete Artikel, Vorschauen und bereits gelistete Einträge zurück. Erfordert Bestätigung.</p>
        <div class=\"row\">
          <input id=\"confirmReset\" placeholder=\"Schreibe LÖSCHEN zum Bestätigen\" />
          <button id=\"resetBtn\" class=\"ghost\">Datenbank zurücksetzen</button>
        </div>
        <div id=\"resetResult\"></div>
      </div>

      <div id=\"section-preview\" class=\"section card\">
        <h2 style=\"margin-top:0;\">Vorschau erstellen & finalisieren</h2>
        <label for=\"previewLpn\">LPN:</label>
        <input id=\"previewLpn\" placeholder=\"z.B. LPN123\" />
        <button id=\"previewBtn\">Vorschau bauen</button>
        <div id=\"preview\"></div>
      </div>

      <div id=\"section-dash\" class=\"section card\">
        <h2 style=\"margin-top:0;\">Dashboard</h2>
        <button id=\"refreshDash\">Aktualisieren</button>
        <div id=\"dash\"></div>
      </div>
    </div>
  </div>

  </div>

  <script>
    const sections = {
      account: document.getElementById('section-account'),
      db: document.getElementById('section-db'),
      import: document.getElementById('section-import'),
      listed: document.getElementById('section-listed'),
      reset: document.getElementById('section-reset'),
      preview: document.getElementById('section-preview'),
      dash: document.getElementById('section-dash'),
    };

    const buttons = {
      account: document.getElementById('nav-account'),
      db: document.getElementById('nav-db'),
      import: document.getElementById('nav-import'),
      listed: document.getElementById('nav-listed'),
      reset: document.getElementById('nav-reset'),
      preview: document.getElementById('nav-preview'),
      dash: document.getElementById('nav-dash'),
    };

    const tokenKey = 'listgiant_token';
    const authPanel = document.getElementById('authPanel');
    const appShell = document.getElementById('appShell');
    const authStatus = document.getElementById('authStatus');

    const authHeaders = () => {
      const token = localStorage.getItem(tokenKey);
      return token ? { 'Authorization': `Bearer ${token}` } : {};
    };

    Object.entries(buttons).forEach(([key, btn]) => {
      btn.onclick = () => {
        Object.values(sections).forEach(sec => sec.classList.remove('active'));
        sections[key].classList.add('active');
      };
    });

    const uploadBtn = document.getElementById('uploadBtn');
    const previewUploadBtn = document.getElementById('previewUploadBtn');
    const uploadResult = document.getElementById('uploadResult');
    const previewTable = document.getElementById('previewTable');
    const mappingControls = document.getElementById('mappingControls');
    const mapLpn = document.getElementById('mapLpn');
    const mapAsin = document.getElementById('mapAsin');
    const mapItem = document.getElementById('mapItem');
    const jobsDiv = document.getElementById('jobs');
    const listedDiv = document.getElementById('listed');
    const previewBtn = document.getElementById('previewBtn');
    const previewDiv = document.getElementById('preview');
    const dashDiv = document.getElementById('dash');
    const lookupBtn = document.getElementById('lookupBtn');
    const lookupResult = document.getElementById('lookupResult');
    const resetBtn = document.getElementById('resetBtn');
    const resetResult = document.getElementById('resetResult');
    const regEmail = document.getElementById('regEmail');
    const regPass = document.getElementById('regPass');
    const regBtn = document.getElementById('regBtn');
    const regResult = document.getElementById('regResult');
    const loginEmail = document.getElementById('loginEmail');
    const loginPass = document.getElementById('loginPass');
    const loginBtn = document.getElementById('loginBtn');
    const loginResult = document.getElementById('loginResult');
    const accountIdentity = document.getElementById('accountIdentity');
    const accountEbayStatus = document.getElementById('accountEbayStatus');
    const oauthStartBtn = document.getElementById('oauthStartBtn');
    const oauthFinishBtn = document.getElementById('oauthFinishBtn');
    const oauthCode = document.getElementById('oauthCode');
    const oauthAccount = document.getElementById('oauthAccount');
    const oauthResult = document.getElementById('oauthResult');

    window.addEventListener('message', (ev) => {
      if(ev.data && ev.data.type === 'ebay-linked'){
        oauthResult.textContent = `eBay verknüpft als ${ev.data.account || 'eBay Konto'}`;
        checkSession();
      }
    });

    let currentUser = null;

    const api = (path, opts={}) => {
      const headers = { ...(opts.headers || {}), ...authHeaders() };
      return fetch(path, { ...opts, headers }).then(async r => {
        if(!r.ok){
          const t = await r.text();
          throw new Error(t || r.statusText);
        }
        const ct = r.headers.get('content-type') || '';
        if(ct.includes('application/json')) return r.json();
        return r.text();
      });
    };

    let lastFile = null;
    let lastPreview = null;

    function setAuthState(loggedIn, email=null, ebayLinked=false, accountName=null){
      authPanel.style.display = loggedIn ? 'none' : 'flex';
      appShell.style.display = loggedIn ? 'block' : 'none';
      if(loggedIn){
        authStatus.style.display = 'inline-flex';
        const labelParts = [email || 'eingeloggt'];
        labelParts.push(ebayLinked ? 'eBay verknüpft' : 'eBay nicht verknüpft');
        if(accountName) labelParts.push(accountName);
        authStatus.textContent = labelParts.join(' · ');
      } else {
        authStatus.style.display = 'none';
        authStatus.textContent = '';
        renderAccountInfo(null);
      }
    }

    function renderAccountInfo(me){
      if(!me){
        accountIdentity.textContent = 'Nicht angemeldet';
        accountEbayStatus.textContent = 'eBay nicht verknüpft';
        return;
      }
      accountIdentity.textContent = `Eingeloggt als ${me.email}`;
      accountEbayStatus.textContent = me.ebay_linked ? `eBay verknüpft (${me.account_name || 'Konto'})` : 'eBay nicht verknüpft';
    }

    async function checkSession(){
      const token = localStorage.getItem(tokenKey);
      if(!token){ setAuthState(false); return; }
      try {
        const me = await api('/auth/me');
        currentUser = me;
        renderAccountInfo(me);
        setAuthState(true, me.email, me.ebay_linked, me.account_name);
        await Promise.all([loadDashboard(), loadJobs(), loadListed()]);
      } catch (e){
        localStorage.removeItem(tokenKey);
        setAuthState(false);
      }
    }

    regBtn.onclick = async () => {
      regResult.textContent = 'Registriere...';
      try {
        const res = await api('/auth/register', {
          method:'POST',
          headers:{ 'Content-Type':'application/json' },
          body: JSON.stringify({ email: regEmail.value, password: regPass.value })
        });
        localStorage.setItem(tokenKey, res.token);
        regResult.textContent = 'Account erstellt. Eingeloggt.';
        await checkSession();
      } catch (e){
        regResult.textContent = e.message;
      }
    };

    loginBtn.onclick = async () => {
      loginResult.textContent = 'Anmeldung...';
      try {
        const res = await api('/auth/login', {
          method:'POST',
          headers:{ 'Content-Type':'application/json' },
          body: JSON.stringify({ email: loginEmail.value, password: loginPass.value })
        });
        localStorage.setItem(tokenKey, res.token);
        loginResult.textContent = 'Eingeloggt.';
        await checkSession();
      } catch (e){
        loginResult.textContent = e.message;
      }
    };

    oauthStartBtn.onclick = async () => {
      oauthResult.textContent = 'Starte OAuth...';
      try {
        const res = await api('/oauth/ebay/start');
        const popup = window.open(res.auth_url, '_blank');
        oauthResult.innerHTML = `Weiterleitung zu eBay... <a href="${res.auth_url}" target="_blank">falls kein Popup</a>`;
        if(!popup){ oauthResult.innerHTML += ' (Popup geblockt, bitte Link klicken)'; }
      } catch (e){
        oauthResult.textContent = e.message;
      }
    };

    oauthFinishBtn.onclick = async () => {
      oauthResult.textContent = 'Speichere Verknüpfung...';
      try {
        const res = await api('/oauth/ebay/callback', {
          method:'POST',
          headers:{ 'Content-Type':'application/json' },
          body: JSON.stringify({ code: oauthCode.value, account_name: oauthAccount.value })
        });
        oauthResult.textContent = `eBay verknüpft als ${res.account_name}`;
        await checkSession();
      } catch (e){
        oauthResult.textContent = e.message;
      }
    };

    function buildMappingSelect(selectEl, columns, selected){
      selectEl.innerHTML = columns.map((c, idx) => {
        const label = c || `Spalte ${String.fromCharCode(65+idx)}`;
        const val = String.fromCharCode(65+idx);
        const isSel = selected === val ? 'selected' : '';
        return `<option value="${val}" ${isSel}>${label}</option>`;
      }).join('');
    }

    function renderPreviewTable(sampleRows, detected){
      if(!sampleRows.length){ previewTable.innerHTML = '<p class="muted">Keine Daten erkannt.</p>'; return; }
      const rowsHtml = sampleRows.map(r => `<tr><td>${r.lpn || ''}</td><td>${r.asin || ''}</td><td>${r.item_name || ''}</td></tr>`).join('');
      previewTable.innerHTML = `
        <p class="status">Vorschau erstellt – prüfe die Zuordnung und korrigiere sie bei Bedarf.</p>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; margin-top:8px;">
          <thead><tr><th>LPN</th><th>ASIN</th><th>Artikelname</th></tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      `;
      mappingControls.style.display = 'flex';
      uploadBtn.disabled = false;
      mapLpn.value = detected.lpn || mapLpn.value;
      mapAsin.value = detected.asin || mapAsin.value;
      mapItem.value = detected.item_name || mapItem.value;
    }

    previewUploadBtn.onclick = async () => {
      const file = document.getElementById('csvFile').files[0];
      if(!file){ uploadResult.innerHTML = '<p class="error">Bitte CSV auswählen.</p>'; return; }
      uploadResult.textContent = 'Prüfe CSV...';
      previewTable.innerHTML = '';
      uploadBtn.disabled = true;
      const form = new FormData();
      form.append('file', file);
      try {
        const res = await api('/ingest/preview', { method:'POST', body: form });
        lastPreview = res;
        lastFile = file;
        buildMappingSelect(mapLpn, res.columns, res.detected_map.lpn);
        buildMappingSelect(mapAsin, res.columns, res.detected_map.asin);
        buildMappingSelect(mapItem, res.columns, res.detected_map.item_name);
        renderPreviewTable(res.sample_rows, res.detected_map);
        uploadResult.innerHTML = '<p class="status">Vorschau bereit. Bei Bedarf Spalten korrigieren und dann "CSV hinzufügen".</p>';
      } catch (e){
        uploadResult.innerHTML = `<p class="error">${e.message}</p>`;
      }
    };

    uploadBtn.onclick = async () => {
      const file = lastFile || document.getElementById('csvFile').files[0];
      if(!file){ uploadResult.innerHTML = '<p class="error">Bitte zuerst CSV prüfen.</p>'; return; }
      const map = {
        lpn: mapLpn.value,
        asin: mapAsin.value,
        item_name: mapItem.value,
      };
      uploadBtn.disabled = true; uploadResult.textContent = 'Importiere...';
      const form = new FormData();
      form.append('file', file);
      form.append('column_map', JSON.stringify(map));
      form.append('use_header', lastPreview ? String(lastPreview.use_header) : '');
      try {
        const res = await api('/ingest', { method:'POST', body: form });
        const skipped = res.skipped.length ? res.skipped.join(', ') : 'keine';
        uploadResult.innerHTML = `<p class="status">Importiert: ${res.imported}, Übersprungen: ${skipped}</p>`;
        previewTable.innerHTML = '';
        mappingControls.style.display = 'none';
        uploadBtn.disabled = true;
        setTimeout(() => { uploadResult.innerHTML = ''; }, 3000);
        await loadJobs();
      } catch (e){
        uploadResult.innerHTML = `<p class="error">${e.message}</p>`;
      }
    };

    async function loadJobs(){
      jobsDiv.innerHTML = '<li class="muted">Lade...</li>';
      try {
        const jobs = await api('/jobs');
        if(!jobs.length){ jobsDiv.innerHTML = '<li class="muted">Keine Artikel gefunden. Bitte CSV laden.</li>'; document.getElementById('jobCount').textContent = '0 Artikel unbearbeitet'; return; }
        document.getElementById('jobCount').textContent = `Aktuell ${jobs.length} Artikel unbearbeitet`;
        jobsDiv.innerHTML = jobs.map(j => {
          const label = j.item_name ? `${j.lpn} · ${j.item_name}` : j.lpn;
          const asin = j.asin || '—';
          return `<li><div class="row" style="justify-content:space-between; width:100%; align-items:flex-start;"><div><strong>${label}</strong><br/><span class="muted">ASIN: ${asin} · Status: ${j.status}</span></div><button class="ghost deleteJob" data-lpn="${j.lpn}">Löschen</button></div></li>`;
        }).join('');
      } catch(e){ jobsDiv.innerHTML = `<li class="error">${e.message}</li>`; }
    }

    jobsDiv.onclick = async (ev) => {
      const target = ev.target;
      if(target.classList.contains('deleteJob')){
        const lpn = target.getAttribute('data-lpn');
        target.disabled = true;
        try {
          await api(`/jobs/${encodeURIComponent(lpn)}`, { method:'DELETE' });
          await loadJobs();
          await loadDashboard();
        } catch(e){
          alert(e.message);
        }
      }
    };

    async function loadListed(){
      listedDiv.innerHTML = '<li class="muted">Lade...</li>';
      try {
        const listed = await api('/listed');
        if(!listed.length){ listedDiv.innerHTML = '<li class="muted">Noch keine finalisierten Artikel.</li>'; return; }
        listedDiv.innerHTML = listed.map(item => {
          const label = item.item_name ? `${item.lpn} · ${item.item_name}` : item.lpn;
          return `<li><strong>${label}</strong><br/><span class="muted">ASIN: ${item.asin || '—'} · Gelistet am: ${item.listed_at}</span></li>`;
        }).join('');
      } catch(e){ listedDiv.innerHTML = `<li class="error">${e.message}</li>`; }
    }

    document.getElementById('refreshJobs').onclick = loadJobs;
    document.getElementById('refreshListed').onclick = loadListed;
    document.getElementById('refreshDash').onclick = loadDashboard;

    lookupBtn.onclick = async () => {
      const lpn = document.getElementById('lookupLpn').value.trim();
      lookupResult.textContent = '';
      if(!lpn){ lookupResult.innerHTML = '<p class="error">Bitte eine LPN eingeben oder scannen.</p>'; return; }
      lookupBtn.disabled = true;
      try {
        const res = await api(`/lookup/${encodeURIComponent(lpn)}`);
        const asin = res.asin || '—';
        const name = res.item_name ? ` · ${res.item_name}` : '';
        const location = res.location === 'listed' ? `Bereits bearbeitet am ${res.listed_at}` : 'Artikel unbearbeitet';
        lookupResult.innerHTML = `<p><strong>${res.lpn}${name}</strong><br/><span class="muted">ASIN: ${asin} · Status: ${res.status} · ${location}</span></p>`;
      } catch(e){ lookupResult.innerHTML = `<p class="error">${e.message}</p>`; }
      finally { lookupBtn.disabled = false; }
    };

    resetBtn.onclick = async () => {
      resetResult.textContent = '';
      const confirmVal = document.getElementById('confirmReset').value.trim();
      if(confirmVal !== 'LÖSCHEN'){ resetResult.innerHTML = '<p class="error">Bitte LÖSCHEN schreiben um zu bestätigen.</p>'; return; }
      resetBtn.disabled = true;
      try {
        const res = await api('/database/reset', { method:'POST' });
        resetResult.innerHTML = `<p class="status">${res.message}</p>`;
        document.getElementById('confirmReset').value = '';
        await loadJobs(); await loadListed(); await loadDashboard();
      } catch(e){ resetResult.innerHTML = `<p class="error">${e.message}</p>`; }
      finally { resetBtn.disabled = false; }
    };

    previewBtn.onclick = async () => {
      const lpn = document.getElementById('previewLpn').value.trim();
      if(!lpn){ previewDiv.innerHTML = '<p class="error">Bitte LPN eintragen.</p>'; return; }
      previewBtn.disabled = true; previewDiv.textContent = 'Erstelle Vorschau...';
      try {
        const res = await api('/preview', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ lpn }) });
        previewDiv.innerHTML = `
          <p class="status">Vorschau bereit</p>
          <p><strong>Titel:</strong> ${res.title}</p>
          <p><strong>Preis:</strong> ${res.price} | <strong>Zustand:</strong> ${res.condition}</p>
          <p><strong>Marke:</strong> ${res.brand} | <strong>EAN:</strong> ${res.ean} | <strong>Produktart:</strong> ${res.product_type} | <strong>GPSR:</strong> ${res.gpsr}</p>
          <p><strong>Werbung:</strong> ${res.promotion}</p>
          <p><strong>Policies:</strong> Versand ${res.shipping_policy} · Zahlung ${res.payment_policy} · Rücknahme ${res.return_policy} · Konto ${res.account_label}</p>
          <p><strong>Artikelmerkmale:</strong></p>
          <pre>${JSON.stringify(res.article_attributes, null, 2)}</pre>
          <button id="finalizeBtn">Listing finalisieren</button>
        `;
        const finalizeBtn = document.getElementById('finalizeBtn');
        finalizeBtn.onclick = async () => {
          finalizeBtn.disabled = true;
          try {
            const r = await api('/finalize', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ lpn }) });
            previewDiv.innerHTML = `<p class="status">${r.message}</p>`;
            await loadJobs(); await loadListed(); await loadDashboard();
          } catch(e){ previewDiv.innerHTML = `<p class="error">${e.message}</p>`; }
        };
        await loadDashboard();
      } catch(e){ previewDiv.innerHTML = `<p class="error">${e.message}</p>`; }
      finally { previewBtn.disabled = false; }
    };

    async function loadDashboard(){
      dashDiv.textContent = 'Lade...';
      try {
        const d = await api('/dashboard');
        dashDiv.innerHTML = `
          <p><strong>Account:</strong> ${d.active_account}</p>
          <p><strong>User:</strong> ${d.active_user || '—'}</p>
          <p><strong>eBay-Verknüpfung:</strong> ${d.ebay_linked ? 'Aktiv' : 'Nicht verbunden'}</p>
          <p><strong>Listings erstellt:</strong> ${d.listings_created}</p>
          <p><strong>Artikel unbearbeitet:</strong> ${d.unprocessed_items}</p>
          <p><strong>Gesamt importiert:</strong> ${d.total_jobs}</p>
          <p><strong>Bereits gelistet:</strong> ${d.listed_history}</p>
        `;
      } catch(e){ dashDiv.innerHTML = `<p class="error">${e.message}</p>`; }
    }

    checkSession();
  </script>
</body>
</html>
"""


def _normalize_header_value(value: str) -> str:
    return value.lower().replace(" ", "").replace("_", "")


def _looks_like_lpn(value: str) -> bool:
    cleaned = value.strip()
    return bool(cleaned) and (re.match(r"(?i)^lpn", cleaned) or re.match(r"^[A-Z0-9-]{6,}$", cleaned))


def _looks_like_asin(value: str) -> bool:
    return bool(re.match(r"^[A-Z0-9]{10}$", value.strip(), re.I))


def _detect_mapping(rows: List[List[str]], force_use_header: Optional[bool] = None) -> Tuple[dict, bool, List[str]]:
    """Return column index mapping for lpn/asin/item_name and whether a header row is used."""

    if not rows:
        return {"lpn": 0, "asin": 1, "item_name": 2}, False, []

    header_raw = [c.strip() for c in rows[0]]
    header_norm = [_normalize_header_value(h) for h in header_raw]
    has_header_keys = any(
        key in header_norm for key in ["lpn", "retourennummer", "retourenummer", "asin", "artikelname", "name", "titel"]
    )
    header_looks_like_data = _looks_like_lpn(header_raw[0]) or _looks_like_asin(header_raw[0])
    use_header = force_use_header if force_use_header is not None else (has_header_keys and not header_looks_like_data)

    header_map = {name: idx for idx, name in enumerate(header_norm)} if use_header else {}
    first_data = rows[1] if use_header and len(rows) > 1 else (rows[0] if rows else [])

    def _pick_index(keys: List[str], fallback_idx: int) -> int:
        for key in keys:
            if key in header_map:
                return header_map[key]
        if 0 <= fallback_idx < len(first_data) and first_data[fallback_idx].strip():
            return fallback_idx
        return fallback_idx

    lpn_idx = _pick_index(["lpn", "retourennummer", "retourenummer"], 0)
    asin_idx = _pick_index(["asin"], 1 if len(first_data) > 1 else 0)
    item_idx = _pick_index(["artikelname", "name", "titel"], 2 if len(first_data) > 2 else (len(first_data) - 1))

    return {"lpn": lpn_idx, "asin": asin_idx, "item_name": item_idx}, use_header, header_raw


def _apply_mapping_to_row(cells: List[str], mapping: dict) -> dict:
    lpn = cells[mapping.get("lpn", 0)].strip() if mapping.get("lpn", 0) < len(cells) else ""
    asin = cells[mapping.get("asin", 1)].strip() if mapping.get("asin", 1) < len(cells) else ""
    item = cells[mapping.get("item_name", 2)].strip() if mapping.get("item_name", 2) < len(cells) else ""
    return {"lpn": lpn, "asin": asin or None, "item_name": item or None}


def parse_mixed_csv(
    content: str, column_map: Optional[dict] = None, use_header: Optional[bool] = None
) -> Tuple[List[dict], bool, List[str], dict]:
    """Robustly extract LPN, ASIN und Artikelname auch aus gemischten CSV-Strukturen."""

    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        return [], False, [], {"lpn": 0, "asin": 1, "item_name": 2}

    mapping, detected_header, header_raw = _detect_mapping(rows, force_use_header=use_header)
    if column_map:
        converted = {}
        for key, val in column_map.items():
            if isinstance(val, str) and val:
                converted[key] = ord(val.upper()) - 65
            elif isinstance(val, int):
                converted[key] = val
        mapping.update(converted)

    data_rows = rows[1:] if (use_header if use_header is not None else detected_header) else rows

    parsed: List[dict] = []
    for row in data_rows:
        cells = [c.strip() for c in row]
        if not any(cells):
            continue
        mapped = _apply_mapping_to_row(cells, mapping)
        if not mapped["lpn"]:
            continue
        parsed.append(mapped)

    return parsed, (use_header if use_header is not None else detected_header), header_raw, mapping


def _normalize_condition(condition_id: Optional[str], condition_name: Optional[str]) -> Tuple[str, str]:
    """Validate Zustand anhand der festen eBay-Liste."""

    try:
        return normalize_condition(condition_id, condition_name)
    except ValueError as exc:  # pragma: no cover - runtime validation
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _materialize_lpn(raw_lpn: Optional[str]) -> str:
    if raw_lpn:
        return raw_lpn
    return f"AUTO-{secrets.token_hex(4).upper()}"




@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    """Provide a minimal click-ready UI by default; still offer JSON if requested."""

    if "application/json" in request.headers.get("accept", ""):
        return {
            "message": "eBay Listing Tool API läuft. Verwende /docs für Swagger-UI oder /dashboard für Kennzahlen.",
            "docs_url": "/docs",
            "dashboard_url": "/dashboard",
        }

    return HTMLResponse(LANDING_PAGE)


@app.post("/ingest/preview", response_model=CSVPreviewResult)
def preview_ingest(file: UploadFile = File(...), user: User = Depends(get_user_or_401)):
    try:
        content = file.file.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV konnte nicht gelesen werden") from exc

    raw_rows = list(csv.reader(io.StringIO(content)))
    parsed, use_header, header_raw, mapping = parse_mixed_csv(content)

    column_count = len(header_raw) if header_raw else max((len(r) for r in raw_rows), default=3)
    columns = []
    for idx in range(max(column_count, 3)):
        header_label = header_raw[idx] if idx < len(header_raw) else ""
        columns.append(header_label or f"Spalte {chr(65 + idx)}")

    detected_map = {key: chr(65 + val) for key, val in mapping.items()}
    sample_rows = [row for row in parsed[:10]]

    return CSVPreviewResult(columns=columns, detected_map=detected_map, use_header=use_header, sample_rows=sample_rows)


@app.post("/ingest", response_model=CSVIngestResult)
def ingest_csv(
    file: UploadFile = File(...), column_map: Optional[str] = Form(None), use_header: Optional[str] = Form(None), db: Session = Depends(get_db), user: User = Depends(get_user_or_401)
):
    try:
        content = file.file.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV konnte nicht gelesen werden") from exc

    map_data = None
    if column_map:
        try:
            map_data = json.loads(column_map)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="column_map konnte nicht gelesen werden") from exc

    use_header_flag = None
    if use_header:
        use_header_flag = use_header.lower() == "true"

    rows, _, _, _ = parse_mixed_csv(content, column_map=map_data, use_header=use_header_flag)
    imported = 0
    skipped: List[str] = []

    for row in rows:
        lpn = row.get("lpn", "").strip()
        asin = (row.get("asin") or "").strip()
        item_name = (row.get("item_name") or "").strip()

        if not lpn:
            continue
        if db.query(ListingJob).filter(ListingJob.lpn == lpn, ListingJob.user_id == user.id).first():
            skipped.append(lpn)
            continue

        job = ListingJob(lpn=lpn, asin=asin or None, item_name=item_name or None, user_id=user.id)
        db.add(job)
        imported += 1

    db.commit()
    return CSVIngestResult(imported=imported, skipped=skipped)


@app.post("/jobs/create", response_model=ListingJobOut)
def create_job(payload: JobCreateRequest, db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    lpn = _materialize_lpn(payload.lpn)
    asin = (payload.asin or "").strip() or None
    ean = (payload.ean or "").strip() or None
    query = (payload.query or "").strip() or None
    item_name = (payload.item_name or "").strip() or query or ean or asin
    cond_id, cond_name = _normalize_condition(payload.condition_id, payload.condition_name)

    if not asin and not (ean or query):
        raise HTTPException(status_code=400, detail="Mindestens EAN oder Artikelsuche erforderlich, wenn keine ASIN vorhanden ist.")

    if db.query(ListingJob).filter(ListingJob.lpn == lpn, ListingJob.user_id == user.id).first():
        raise HTTPException(status_code=400, detail="LPN bereits vorhanden")

    job = ListingJob(
        lpn=lpn,
        asin=asin,
        ean=ean,
        query=query,
        item_name=item_name,
        condition_id=cond_id,
        condition_name=cond_name,
        user_id=user.id,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return ListingJobOut(
        id=job.id,
        lpn=job.lpn,
        asin=job.asin,
        ean=job.ean,
        query=job.query,
        item_name=job.item_name,
        condition_id=job.condition_id,
        condition_name=job.condition_name,
        status=job.status,
    )


@app.post("/suggest", response_model=SuggestionResponse)
def suggest_listing(payload: SuggestionRequest):
    keepa_data = keepa.fetch_by_asin(payload.asin) if payload.asin else None
    ebay_data = ebay.search_listing(
        ean=payload.ean or (keepa_data or {}).get("ean"),
        query=payload.ean or payload.query or payload.asin,
    )

    return SuggestionResponse(
        keepa_used=bool(keepa_data),
        ebay_used=bool(ebay_data),
        keepa_data=keepa_data,
        ebay_data=ebay_data,
    )


@app.post("/preview", response_model=ListingPreviewResponse)
def build_preview(
    listing: ListingRequest,
    db: Session = Depends(get_db),
    request: Request = None,
    user: User = Depends(get_user_or_401),
):
    job = (
        db.query(ListingJob)
        .filter(ListingJob.lpn == listing.lpn, ListingJob.user_id == user.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="LPN nicht gefunden")

    source_data = keepa.fetch_by_asin(job.asin) if job.asin else None
    ebay_guess = ebay.search_listing(
        ean=(source_data or {}).get("ean"),
        query=(source_data or {}).get("title") or job.item_name or job.asin or job.lpn,
    )
    fallback_data = ebay.fetch_listing_data(job.asin or "")

    def pick(*values: Optional[str]) -> str:
        for value in values:
            if value and value != "N/A":
                return value
        return "N/A"

    fallback_title = job.item_name or "N/A"
    title = pick((source_data or {}).get("title"), ebay_guess.get("title"), fallback_data.get("title"), fallback_title)
    description = pick((source_data or {}).get("description"), ebay_guess.get("description"), fallback_data.get("description"))
    if not description or description == "N/A":
        base_desc = job.item_name or job.asin or job.lpn
        description = f"Artikel: {base_desc}" if base_desc else "N/A"
    price = pick((source_data or {}).get("price"), ebay_guess.get("price"), fallback_data.get("price"))
    condition = pick(job.condition_name, (source_data or {}).get("condition"), ebay_guess.get("condition"), fallback_data.get("condition"))
    brand = pick((source_data or {}).get("brand"), ebay_guess.get("brand"), fallback_data.get("brand"))
    ean = pick((source_data or {}).get("ean"), ebay_guess.get("ean"), fallback_data.get("ean"))
    product_type = pick((source_data or {}).get("product_type"), ebay_guess.get("product_type"), fallback_data.get("product_type"))
    gpsr = pick((source_data or {}).get("gpsr"), ebay_guess.get("gpsr"), fallback_data.get("gpsr"))
    images = (source_data or {}).get("images") or ebay_guess.get("images") or fallback_data.get("images") or []
    base_attributes = (source_data or {}).get("article_attributes") or ebay_guess.get("article_attributes") or fallback_data.get("article_attributes") or {}
    if not base_attributes and job.item_name:
        base_attributes = {"Artikelname": job.item_name}
    article_attributes = {key: (value or "N/A") for key, value in base_attributes.items()} or {"Allgemein": "N/A"}

    ai_keywords = [val for val in [job.asin, job.item_name, job.lpn] if val]
    ai_result = ai.build_ai_listing_text(title, description, keywords=ai_keywords)
    title = ai_result["title"]
    description = ai_result["description"]

    account_label, _ = get_account_context(db, user_id=user.id)
    profile = ebay.account_profile(account_label)
    promotion = "Automatische Werbung: 3%" if price != "N/A" else "N/A"

    preview = ListingPreview(
        user_id=user.id,
        lpn=job.lpn,
        title=title,
        description=description,
        price=price,
        condition=condition,
        condition_id=job.condition_id,
        condition_name=job.condition_name,
        brand=brand,
        ean=ean,
        product_type=product_type,
        gpsr=gpsr,
        article_attributes=json.dumps(article_attributes),
        images=",".join(images) if images else "",
        promotion=promotion,
        shipping_policy=profile["shipping_policy"],
        payment_policy=profile["payment_policy"],
        return_policy=profile["return_policy"],
        account_label=profile["account_label"],
        ready=True,
    )
    db.add(preview)
    job.status = "previewed"
    db.commit()
    db.refresh(preview)

    return ListingPreviewResponse(
        lpn=preview.lpn,
        title=preview.title,
        description=preview.description,
        price=preview.price,
        condition=preview.condition,
        condition_id=preview.condition_id,
        condition_name=preview.condition_name,
        brand=preview.brand,
        ean=preview.ean,
        product_type=preview.product_type,
        gpsr=preview.gpsr,
        article_attributes=json.loads(preview.article_attributes),
        images=preview.images.split(",") if preview.images else [],
        promotion=preview.promotion,
        shipping_policy=preview.shipping_policy,
        payment_policy=preview.payment_policy,
        return_policy=preview.return_policy,
        account_label=preview.account_label,
        ready=preview.ready,
    )


@app.post("/jobs/{job_id}/publish")
def publish_job(job_id: int, payload: dict = Body(...), db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    job = db.query(ListingJob).filter(ListingJob.id == job_id, ListingJob.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")

    preview = db.query(ListingPreview).filter(ListingPreview.lpn == job.lpn, ListingPreview.user_id == user.id).first()
    if not preview:
        raise HTTPException(status_code=400, detail="Kein Preview vorhanden")

    auth = get_active_ebay_auth(db, user.id)
    if not auth or not auth.access_token:
        raise HTTPException(status_code=400, detail="eBay nicht verknüpft")

    fulfillment_policy_id = payload.get("fulfillment_policy_id")
    if not (auth.payment_policy_id and auth.return_policy_id and fulfillment_policy_id):
        raise HTTPException(status_code=400, detail="Versand-/Zahlungs-/Rücknahme-Policies fehlen")

    if not preview.category_id:
        raise HTTPException(status_code=400, detail="Kategorie fehlt im Preview")
    if not preview.condition_id:
        raise HTTPException(status_code=400, detail="Zustand fehlt")

    images = preview.images.split(",") if preview.images else []
    if not images:
        raise HTTPException(status_code=400, detail="Mindestens ein Bild erforderlich")

    try:
        price_value = float(preview.price)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Preis ungültig oder fehlt") from exc

    aspects = json.loads(preview.article_attributes or "{}")

    sku = job.lpn
    product_payload = {
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": preview.condition_id,
        "product": {
            "title": preview.title,
            "description": preview.description,
            "aspects": aspects,
            "imageUrls": images,
        },
    }

    offer_payload = {
        "sku": sku,
        "marketplaceId": ebay.DEFAULT_MARKETPLACE,
        "format": "FIXED_PRICE",
        "availableQuantity": 1,
        "pricingSummary": {"price": {"value": price_value, "currency": "EUR"}},
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment_policy_id,
            "paymentPolicyId": auth.payment_policy_id,
            "returnPolicyId": auth.return_policy_id,
        },
        "categoryId": preview.category_id,
    }

    try:
        ebay.create_or_replace_inventory_item(sku, product_payload, auth.access_token)
        offer_id = ebay.create_offer(offer_payload, auth.access_token)
        if not offer_id:
            raise HTTPException(status_code=500, detail="Offer konnte nicht erstellt werden")
        listing_id = ebay.publish_offer(offer_id, auth.access_token)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - runtime failures
        raise HTTPException(status_code=500, detail=f"eBay Publish fehlgeschlagen: {exc}") from exc

    history_entry = ListingHistory(
        user_id=user.id,
        lpn=job.lpn,
        asin=job.asin,
        item_name=job.item_name,
        category_id=preview.category_id,
        condition_id=preview.condition_id,
        condition_name=preview.condition_name,
        ebay_listing_id=listing_id,
        price=str(price_value),
        listed_at=datetime.utcnow().isoformat(timespec="seconds"),
    )
    db.add(history_entry)

    db.query(ListingPreview).filter(ListingPreview.id == preview.id).delete()
    db.delete(job)
    db.commit()

    return {"status": "listed", "offer_id": offer_id, "listing_id": listing_id}


@app.post("/finalize")
def finalize_listing(request: ListingRequest, db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    job = (
        db.query(ListingJob)
        .filter(ListingJob.lpn == request.lpn, ListingJob.user_id == user.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="LPN nicht gefunden")

    db.query(ListingPreview).filter(ListingPreview.lpn == request.lpn, ListingPreview.user_id == user.id).delete()

    history_entry = ListingHistory(
        user_id=user.id,
        lpn=job.lpn,
        asin=job.asin,
        item_name=job.item_name,
        listed_at=datetime.utcnow().isoformat(timespec="seconds"),
    )
    db.add(history_entry)

    db.delete(job)
    db.commit()
    return {"status": "listed", "message": "Listing erstellt und LPN entfernt"}


@app.post("/database/reset")
def reset_database(db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    db.query(ListingPreview).filter(ListingPreview.user_id == user.id).delete()
    db.query(ListingHistory).filter(ListingHistory.user_id == user.id).delete()
    db.query(ListingJob).filter(ListingJob.user_id == user.id).delete()
    db.commit()
    return {"message": "Datenbank geleert und bereit für neuen CSV-Import"}


@app.get("/lookup/{lpn}", response_model=LpnLookupResponse)
def lookup_lpn(lpn: str, db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    job = (
        db.query(ListingJob)
        .filter(ListingJob.lpn == lpn, ListingJob.user_id == user.id)
        .first()
    )
    if job:
        preview = db.query(ListingPreview).filter(ListingPreview.lpn == lpn).first()
        status = "previewed" if preview else job.status
        return LpnLookupResponse(
            lpn=job.lpn,
            asin=job.asin,
            item_name=job.item_name,
            status=status,
            location="jobs",
        )

    listed = (
        db.query(ListingHistory)
        .filter(ListingHistory.lpn == lpn, ListingHistory.user_id == user.id)
        .first()
    )
    if listed:
        return LpnLookupResponse(
            lpn=listed.lpn,
            asin=listed.asin,
            item_name=listed.item_name,
            status="listed",
            location="listed",
            listed_at=listed.listed_at,
        )

    raise HTTPException(status_code=404, detail="LPN nicht gefunden")


@app.get("/jobs", response_model=List[ListingJobOut])
def list_jobs(db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    jobs = (
        db.query(ListingJob)
        .filter(ListingJob.user_id == user.id)
        .order_by(ListingJob.id.asc())
        .all()
    )
    return [
        ListingJobOut(
            id=j.id,
            lpn=j.lpn,
            asin=j.asin,
            ean=j.ean,
            query=j.query,
            item_name=j.item_name,
            condition_id=j.condition_id,
            condition_name=j.condition_name,
            status=j.status,
        )
        for j in jobs
    ]


@app.delete("/jobs/{lpn}")
def delete_job(lpn: str, db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    job = (
        db.query(ListingJob)
        .filter(ListingJob.lpn == lpn, ListingJob.user_id == user.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="LPN nicht gefunden")

    db.query(ListingPreview).filter(ListingPreview.lpn == lpn).delete()
    db.delete(job)
    db.commit()
    return {"message": f"LPN {lpn} entfernt"}


@app.get("/listed", response_model=List[ListedItemOut])
def list_listed(db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    items = (
        db.query(ListingHistory)
        .filter(ListingHistory.user_id == user.id)
        .order_by(ListingHistory.id.asc())
        .all()
    )
    return [
        ListedItemOut(
            id=i.id,
            lpn=i.lpn,
            asin=i.asin,
            item_name=i.item_name,
            listed_at=i.listed_at,
            category_id=i.category_id,
            condition_id=i.condition_id,
            condition_name=i.condition_name,
            ebay_listing_id=i.ebay_listing_id,
            price=i.price,
        )
        for i in items
    ]


@app.get("/previews", response_model=List[ListingPreviewResponse])
def list_previews(db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    previews = (
        db.query(ListingPreview)
        .filter(ListingPreview.user_id == user.id)
        .order_by(ListingPreview.id.desc())
        .all()
    )
    response: List[ListingPreviewResponse] = []
    for preview in previews:
        response.append(
            ListingPreviewResponse(
                lpn=preview.lpn,
                title=preview.title,
                description=preview.description,
                price=preview.price,
                condition=preview.condition,
                condition_id=preview.condition_id,
                condition_name=preview.condition_name,
                brand=preview.brand,
                ean=preview.ean,
                product_type=preview.product_type,
                gpsr=preview.gpsr,
                article_attributes=json.loads(preview.article_attributes),
                images=preview.images.split(",") if preview.images else [],
                promotion=preview.promotion,
                shipping_policy=preview.shipping_policy,
                payment_policy=preview.payment_policy,
                return_policy=preview.return_policy,
                account_label=preview.account_label,
                ready=preview.ready,
            )
        )
    return response


@app.get("/dashboard", response_model=DashboardStats)
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_user_or_401)):
    total_jobs = db.query(ListingJob).filter(ListingJob.user_id == user.id).count()
    pending_jobs = (
        db.query(ListingJob)
        .filter(ListingJob.user_id == user.id)
        .filter(ListingJob.status != "listed")
        .count()
    )
    listings_created = db.query(ListingHistory).filter(ListingHistory.user_id == user.id).count()
    account_label, ebay_linked = get_account_context(db, user_id=user.id)
    profile = ebay.account_profile(account_label)

    return DashboardStats(
        active_account=profile["account_label"],
        active_user=user.email,
        listings_created=listings_created,
        total_jobs=total_jobs,
        last_import_count=total_jobs,
        listed_history=listings_created,
        unprocessed_items=pending_jobs,
        ebay_linked=ebay_linked,
    )
