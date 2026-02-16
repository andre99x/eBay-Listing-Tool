# ListGiant (eBay Listing Tool)

ListGiant ist ein verkaufsfertiges 1-Klick-eBay-Listing-Tool mit poliertem Web-UI. Die API importiert Retourenlisten per CSV (Retourennummer/LPN, ASIN, Artikelname), zieht Daten direkt aus Keepa **nur dann, wenn eine ASIN vorhanden ist** (bei gesetztem `KEEPA_API_KEY`, sonst Mock), ergänzt fehlende Felder mit eBay-Daten (OAuth-/AppID-Unterstützung, sonst Mock), generiert SEO-Texte via integrierter KI-Hilfe und zeigt eine Vorschau an, bevor das Listing finalisiert wird. Login/Registrierung, eBay-OAuth-Platzhalter und ein markentaugliches UI sind integriert.

## Features
- **CSV-Import & Normalisierung**: `/ingest` akzeptiert strukturierte und vermischte CSV-Dateien. Es werden automatisch die Spalten auf **A=LPN**, **B=ASIN** (erkennbare 10-stellige ASINs) und **C=Artikelname** gemappt, falls die Überschriften abweichen oder fehlen. Bereits vorhandene LPNs werden übersprungen; spätere CSVs ergänzen die bestehende Datenbank.
- **Vorschau bauen**: `/preview` kombiniert Keepa-Livedaten (ASIN) oder Keepa-Mock, ergänzt per eBay-Suche (EAN oder Artikelsuche) falls Keepa leer ist, markiert fehlende Artikelmerkmale mit `N/A`, füllt Pflichtfelder (Marke, EAN, Produktart, GPSR) immer aus, fügt 3% Werbung hinzu und erstellt SEO-Titel/Beschreibung.
- **Finalisieren & Historie**: `/finalize` entfernt die LPN nach Listing-Erstellung aus der Datenbank, legt sie in **Bereits gelistet** ab und speichert das Datum in `listing_history`.
- **Dashboard**: `/dashboard` liefert Kennzahlen inkl. bereits gelisteter Artikel, **Artikel unbearbeitet** (offene Datenbank-Einträge) und das aktiv simulierte eBay-Konto.
- **Landing/Health & Menü**: Die Root-Route `/` enthält ein klickbares Menü (CSV-Kategorie: Datenbank einsehen, Retourenliste erweitern, Bereits bearbeitet, Datenbank löschen) sowie Tabs für Vorschau/Finalisieren und Dashboard.
- **Branding & UI**: Poliertes „ListGiant“-Frontend mit Logo, hellem Dark-Theme, Chips und Cards für eine professionelle Vermarktung.
- **LPN-Suche**: `/lookup/{lpn}` erlaubt ein schnelles Scannen/Nachschlagen einer LPN (offene Jobs oder bereits gelistet).
- **Login & Registrierung**: `/auth/register`, `/auth/login`, `/auth/me` liefern schlanke Token-basierte Anmeldung. Das Web-UI blendet den CSV-Workflow erst nach Login ein.
- **eBay OAuth (Platzhalter, Popup-kompatibel)**: `/oauth/ebay/start`, `/oauth/ebay/callback`, `/oauth/ebay/status` kapseln die Konto-Verknüpfung. Das Frontend öffnet die eBay-Seite direkt und schließt das Popup nach erfolgreicher Zustimmung automatisch. In `app/main.py` die Konstanten `EBAY_CLIENT_ID_PLACEHOLDER`, `EBAY_CLIENT_SECRET_PLACEHOLDER` und `EBAY_REDIRECT_URI_PLACEHOLDER` (dein eBay-**RuName**) mit echten Werten befüllen – danach reicht ein Klick auf **Jetzt anbinden**, und der Kontoname wird aus der eBay-Identity-API gezogen, sofern die Tokens von eBay stammen.

## API-Keys (Keepa & eBay)
- **Keepa**: Setze `KEEPA_API_KEY` als Umgebungsvariable, dann nutzt das Tool echte Keepa-Produktdaten (nur mit ASIN). Ohne Key wird der Mock verwendet.
- **eBay**: Hinterlege `EBAY_OAUTH_TOKEN` (Bearer-Token aus eBay OAuth) für die Browse-API oder alternativ `EBAY_APP_ID` für die Finding-API. Ohne echte Werte wird der eBay-Mock genutzt.
- **Listing-Vorschläge testen**: Über `/suggest` kannst du per `asin` prüfen, welche Daten von Keepa zurückkommen; eBay wird zusätzlich per EAN oder Artikelsuche befragt, wenn keine Keepa-Daten vorliegen.

## Projektstruktur
```
app/
  main.py           # FastAPI-Einstieg, Routen
  db.py             # SQLite/SQLAlchemy Setup
  models.py         # Datenbanktabellen
  schemas.py        # Pydantic-Schemas
  services/
    keepa.py        # Keepa-Client mit Live-Key-Support (+ Mock-Fallback)
    ebay.py         # eBay-Suche/Profil mit OAuth/AppID (+ Mock-Fallback)
    ai.py           # Einfache SEO-Text-Generator-Funktion
requirements.txt
```

## Lokale Nutzung

### Python-Versionen
- Empfohlen: **Python 3.10–3.12** (getestet und kompatibel mit den gelisteten Abhängigkeiten)
- Python 3.13: derzeit nicht verifiziert; falls du 3.13 nutzt, stelle sicher, dass für alle Abhängigkeiten Wheels verfügbar sind und wechsle bei Problemen bitte auf 3.12.
- Python 3.14 ("pythoncore-3.14" Verzeichnis): wird wegen fehlender Pydantic-/uvicorn-Wheels aktuell nicht unterstützt.

1. Abhängigkeiten installieren
   - **Windows (Python in PATH / `py` verfügbar)**
     ```powershell
     py -m pip install -r requirements.txt
     ```
   - **Windows (falls `py` nicht gefunden wird)**
     - Stelle sicher, dass bei der Python-Installation "Add to PATH" aktiviert wurde oder den Python-Installationspfad (z. B. `C:\Users\<Name>\AppData\Local\Programs\Python\Python3x`) manuell zur `PATH`-Umgebungsvariable hinzufügen.
     - Alternativ direkt mit der `python.exe` des Installationspfads arbeiten:
       ```powershell
       "C:\\Users\\<Name>\\AppData\\Local\\Programs\\Python\\Python3x\\python.exe" -m pip install -r requirements.txt
       ```
   - **Linux/macOS**
     ```bash
     pip install -r requirements.txt
     ```

2. Server starten
   - **Windows (Python in PATH / `py` verfügbar)**
     ```powershell
     py -m uvicorn app.main:app --reload
     ```
   - **Windows (falls `py`/`uvicorn` nicht gefunden wird)**
     - Verwende die gleiche `python.exe` wie bei der Installation, damit das richtige Environment benutzt wird:
       ```powershell
       "C:\\Users\\<Name>\\AppData\\Local\\Programs\\Python\\Python3x\\python.exe" -m uvicorn app.main:app --reload
       ```
     - Wenn ein virtuelles Environment genutzt wird, aktiviere es zuerst (`.\.venv\Scripts\Activate.ps1`) und starte danach wie oben.
   - **Linux/macOS**
     ```bash
     uvicorn app.main:app --reload
     ```

3. Swagger-UI öffnen: http://localhost:8000/docs

### Start schlägt mit `app/main.py` (NameError) fehl
- Prüfe zuerst, ob keine versehentlichen Änderungen in `app/main.py` stehen (`git status` sollte clean sein). Eine isolierte Zeile
  `app/main.py` im Code führt genau zu diesem Fehler – dann die Datei aus dem Repository-Stand wiederherstellen.
- Lösche alte Bytecode-Ordner und starte neu: `rmdir /s /q app\__pycache__` (Windows) bzw. `rm -rf app/__pycache__` (Linux/macOS).
- Starte Uvicorn immer mit dem Modulnamen: `py -m uvicorn app.main:app --reload` (Windows) bzw. `python -m uvicorn app.main:app --reload`.

## Browser-UI (kein Coding nötig)

1. Server starten (siehe oben, z. B. `py -m uvicorn app.main:app --reload`).
2. Im Browser `http://localhost:8000` öffnen. Du siehst zuerst Karten für **Registrierung** und **Login**. Nach erfolgreichem Login öffnet sich die App mit folgenden Bereichen:
  - **Account & eBay**: Login-Status, eBay-OAuth-Button und der angezeigte Kontoname. Trage deine echten eBay-Credentials in `app/main.py` ein und klicke **Jetzt anbinden** – das Popup reicht aus.
  - **CSV → Datenbank einsehen**: Unbearbeitete Artikel werden untereinander gelistet, inkl. Zähler „Aktuell X Artikel in der Datenbank“ und Suchfeld zum Scannen/Nachschlagen einer LPN. Einzelne LPNs lassen sich hier per **Löschen**-Button entfernen.
  - **CSV → Retourenliste erweitern**: CSV hochladen und zuerst **CSV prüfen** klicken. Die Vorschau zeigt, wie die Spalten erkannt wurden (LPN/ASIN/Artikelname) und listet Beispielzeilen. Falls die Zuordnung nicht stimmt, wähle mit den Dropdowns die korrekten Spalten und klicke anschließend **CSV hinzufügen**. Gemischte CSVs werden damit sicher auf Spalte A/B/C normalisiert; bestehende LPNs werden übersprungen. Erfolgs-Statusmeldungen verschwinden nach wenigen Sekunden automatisch.
  - **CSV → Bereits bearbeitet**: Anzeige aller finalisierten LPNs aus der Historie.
  - **CSV → Datenbank löschen**: Setzt unbearbeitete Jobs, Vorschauen und Historie zurück (Bestätigung mit „LÖSCHEN“ nötig).
  - **Vorschau/Finalisieren**: LPN eintragen, Vorschau erzeugen und danach **Listing finalisieren** klicken.
  - **Keepa/eBay Test**: Über die Swagger-UI den Endpoint `/suggest` mit ASIN oder EAN/Artikelnamen aufrufen, um zu sehen, welche Daten von Keepa oder eBay gezogen werden.
  - **Dashboard**: Kennzahlen inkl. aktivem Konto, **Artikel unbearbeitet**, Gesamtimporte und bereits gelistete Artikel.
3. eBay-OAuth testen: Im Tab **Account & eBay** auf **Jetzt anbinden** klicken. Ein eBay-Popup öffnet sich, du bestätigst und das Fenster schließt sich automatisch; der verknüpfte Kontoname erscheint in der Kopfzeile sowie im Account-Tab. Falls das Popup blockiert ist, klicke den eingeblendeten Link. Ein manuelles Code-Feld gibt es nicht mehr – der 1-Klick-Flow genügt.

### eBay-Konto verknüpfen – genauer Ablauf
1) **Platzhalter ersetzen (RuName + Secret)**: In `app/main.py` `EBAY_CLIENT_ID_PLACEHOLDER`, `EBAY_CLIENT_SECRET_PLACEHOLDER` und `EBAY_REDIRECT_URI_PLACEHOLDER` mit deinen eBay-Daten befüllen. Für Sandbox/Live akzeptiert eBay entweder den reinen **RuName** (z. B. `Andre_Zimmerman-...`) _oder_ eine registrierte HTTPS-Redirect-URL (`https://.../ebay/callback`). Nur wenn ein Schema gesetzt ist, muss es `https` sein.

2) **Login in ListGiant**: Registriere dich oder logge dich über die Startseite ein. Das stellt sicher, dass der eBay-OAuth-State deinem Account zugeordnet wird.

3) **Jetzt anbinden**: Im Menüpunkt **Account & eBay** auf „Jetzt anbinden“ klicken. Der Server baut die eBay-URL mit deiner Client-ID und dem registrierten RuName zusammen und hängt deinen Sitzungstoken als `state` an, damit der Callback weiß, welchem Nutzer das Konto gehört. Bei Blockade des Popups wird der Link eingeblendet.

4) **eBay bestätigt**: eBay leitet auf `/oauth/ebay/callback?code=...&state=listgiant-<token>` zurück (State darf nicht fehlen). Die App speichert `access_token`/`refresh_token` Platzhalter in der Tabelle `ebay_auth` für deinen Benutzer und zeigt den Kontonamen sofort im UI an. Das Popup sendet ein `postMessage` an die App, die daraufhin den Status aktualisiert.

### eBay-Link einrichten (Entwicklerportal)
1. Bei <https://developer.ebay.com/> anmelden und eine Anwendung anlegen.
2. Unter **OAuth Redirect URLs / RuNames** denselben Wert hinterlegen, den du in `EBAY_REDIRECT_URI_PLACEHOLDER` nutzt. Für klassische RuNames reicht der reine Name (ohne http/https). Wenn du stattdessen eine Redirect-URL verwendest, muss sie per `https://` erreichbar und identisch registriert sein; sonst meldet eBay `invalid_request`.
3. Die **Client ID** der App in `EBAY_CLIENT_ID_PLACEHOLDER` eintragen.
4. Server neu starten und im UI auf **Jetzt anbinden** klicken. Nach der Bestätigung bei eBay sollte der Kontoname oben im Account-Tab angezeigt werden.
5. Wenn eBay weiterhin `invalid_request` meldet, stimmt meist das RuName/Redirect nicht exakt mit dem eingetragenen Wert überein oder der Client-ID fehlt.

5) **Status prüfen**: Der Header-Chip und der Bereich **Account & eBay** zeigen „eBay verknüpft (Konto)“. Über `/oauth/ebay/status` kannst du auch via Swagger-UI prüfen, welches Konto gespeichert ist.
3. **Swagger-UI** weiterhin unter `http://localhost:8000/docs` erreichbar.

## Häufige Fehlerbehebung

### Pydantic/FastAPI Fehlermeldung beim Start

Die Meldung `pydantic.errors.ConfigError: unable to infer type for attribute "name"`
tritt häufig auf, wenn die installierte Pydantic-Version nicht zum gewählten
Python-Build passt.

- **Windows / Python 3.14 ("pythoncore-3.14" Ordner):** Hier gibt es aktuell
  keine vorkompilierten Wheels für `pydantic-core`, was zu der Rust-Toolchain-
  Fehlermeldung führt. Verwende deshalb die stabile V1-Schiene und installiere
  die mitgelieferte Anforderung `pydantic==1.10.19`:

  ```powershell
  py -m pip install --upgrade -r requirements.txt
  ```

- **Andere Plattformen (z. B. Linux/macOS/Python ≤ 3.12):** Falls bereits eine
  2.x-Version installiert war, entferne sie zuerst oder führe ebenfalls das
  obige Update aus, damit alle Abhängigkeiten konsistent sind.

```powershell
py -m pip install --upgrade -r requirements.txt
```

Zusätzlich wird für E-Mail-Felder weiterhin `email-validator` benötigt, was durch
das obige Upgrade mit installiert wird.

### "ModuleNotFoundError: No module named 'requests'"
Dieser Fehler erscheint, wenn nach einem Update nicht erneut `pip install -r requirements.txt`
ausgeführt wurde. Installiere die Abhängigkeiten noch einmal (im gleichen Python-
Environment, das du für `uvicorn` nutzt), dann startet der Server ohne Abbruch:

```powershell
py -m pip install --upgrade -r requirements.txt
```


### Beispiel-CSV
```
Retourennummer,ASIN,Name
LPNRRTRN1,B000TESTASIN,PARKSIDE PKSA 20-Li A2
LPNRRTRN2,,Philips Hue Bridge
```

Nach dem Upload unter `/ingest` kann für `LPNRRTRN1` eine Vorschau erstellt werden. `LPNRRTRN2` nutzt eBay-Fallback-Daten, der Titel greift auf den angegebenen Artikelnamen zurück.
