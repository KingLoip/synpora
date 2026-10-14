"""Small, safe browser pages for one-time account verification and password reset."""

def install_auth_pages(app):
    @app.get("/verify-email", include_in_schema=False)
    def verify_email_page(token: str = ""):
        import re, json, secrets
        from fastapi.responses import HTMLResponse
        if not token or len(token)>512 or not re.fullmatch(r"[A-Za-z0-9_-]+",token):
            return HTMLResponse("<!doctype html><html lang='de'><meta charset='utf-8'><title>SYNPORA</title><h1>Ungültiger Bestätigungslink</h1><p>Fordere einen neuen Link an.</p></html>",status_code=400,headers={"Cache-Control":"no-store"})
        page="""<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>SYNPORA · E-Mail bestätigen</title><style>body{font:16px system-ui;max-width:620px;margin:10vh auto;padding:24px;background:#10151d;color:#e7edf5}main{border:1px solid #344255;border-radius:14px;padding:24px}</style><main><h1>E-Mail bestätigen</h1><p id="status" role="status">Bestätigungslink wird geprüft …</p><a href="/">Zur Anwendung</a></main><script>const token=TOKEN;fetch('/api/v1/auth/verify-email',{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',body:JSON.stringify({token})}).then(async r=>({ok:r.ok,data:await r.json()})).then(x=>document.getElementById('status').textContent=x.ok?'E-Mail bestätigt. Du kannst dich jetzt anmelden.':(x.data.detail||'Link ungültig oder abgelaufen.')).catch(()=>document.getElementById('status').textContent='Bestätigung derzeit nicht erreichbar. Bitte später erneut versuchen.');</script></html>"""
        nonce=secrets.token_urlsafe(18)
        page=page.replace("<script>",f'<script nonce="{nonce}">').replace("TOKEN",json.dumps(token))
        csp=f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        return HTMLResponse(page,headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer","Content-Security-Policy":csp})
    
    @app.get("/reset-password", include_in_schema=False)
    def reset_password_page(token: str = ""):
        import re, json, secrets
        from fastapi.responses import HTMLResponse
        if not token or len(token)>512 or not re.fullmatch(r"[A-Za-z0-9_-]+",token):
            return HTMLResponse("<!doctype html><html lang='de'><meta charset='utf-8'><title>SYNPORA</title><h1>Ungültiger Link</h1><p>Fordere einen neuen Link an.</p></html>",status_code=400,headers={"Cache-Control":"no-store"})
        page="""<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>SYNPORA · Passwort zurücksetzen</title><style>body{font:16px system-ui;max-width:620px;margin:10vh auto;padding:24px;background:#10151d;color:#e7edf5}main{border:1px solid #344255;border-radius:14px;padding:24px}label,input,button{display:block;width:100%;box-sizing:border-box;margin:12px 0;font:inherit;padding:12px}input{background:#192331;color:#e7edf5;border:1px solid #526174}button{background:#315c8b;color:white;border:0;border-radius:8px}</style><main><h1>Neues Passwort</h1><form id="form"><label for="pw">Neues Passwort (8–1024 Zeichen)</label><input id="pw" type="password" minlength="8" maxlength="1024" autocomplete="new-password" required><label for="confirm">Passwort wiederholen</label><input id="confirm" type="password" minlength="8" maxlength="1024" autocomplete="new-password" required><button>Passwort ändern</button></form><p id="status" role="status"></p><a href="/">Zur Anwendung</a></main><script>const token=TOKEN;document.getElementById('form').addEventListener('submit',async e=>{e.preventDefault();const pw=document.getElementById('pw').value;const status=document.getElementById('status');if(pw!==document.getElementById('confirm').value){status.textContent='Passwörter stimmen nicht überein.';return;}status.textContent='Bitte warten …';try{const r=await fetch('/api/v1/auth/password-reset/confirm',{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',body:JSON.stringify({token,new_password:pw})});const d=await r.json();status.textContent=r.ok?'Passwort geändert. Du kannst dich jetzt anmelden.':(d.detail||'Link ungültig oder abgelaufen.');if(r.ok)e.target.reset();}catch(_){status.textContent='Dienst derzeit nicht erreichbar. Bitte später erneut versuchen.';}});</script></html>"""
        nonce=secrets.token_urlsafe(18)
        page=page.replace("<script>",f'<script nonce="{nonce}">').replace("TOKEN",json.dumps(token))
        csp=f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        return HTMLResponse(page,headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer","Content-Security-Policy":csp})


    @app.get("/register", include_in_schema=False)
    def register_page():
        import secrets
        from fastapi.responses import HTMLResponse
        nonce=secrets.token_urlsafe(18)
        page="""<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>SYNPORA · Konto erstellen</title><style>body{font:16px system-ui;max-width:620px;margin:8vh auto;padding:24px;background:#10151d;color:#e7edf5}main{border:1px solid #344255;border-radius:14px;padding:24px}label,input,button{display:block;width:100%;box-sizing:border-box;margin:12px 0;font:inherit;padding:12px}input{background:#192331;color:#e7edf5;border:1px solid #526174;border-radius:8px}button{background:#55dcb0;color:#07140f;border:0;border-radius:8px;font-weight:700;cursor:pointer}a{color:#8ddfc2}#status{overflow-wrap:anywhere}</style><main><h1>SYNPORA-Konto erstellen</h1><p>Erstelle dein Konto mit deiner E-Mail-Adresse. Wenn die E-Mail-Verifizierung aktiviert ist, senden wir dir einen einmaligen Bestätigungslink.</p><form id="form"><label for="email">E-Mail</label><input id="email" type="email" maxlength="254" autocomplete="email" required><label for="pw">Passwort (mindestens 8 Zeichen)</label><input id="pw" type="password" minlength="8" maxlength="1024" autocomplete="new-password" required><button id="submit">Konto erstellen</button></form><p id="status" role="status" aria-live="polite"></p><p><a href="/">Zur Anwendung</a></p></main><script nonce="NONCE">const form=document.getElementById('form'),status=document.getElementById('status'),button=document.getElementById('submit');form.addEventListener('submit',async e=>{e.preventDefault();button.disabled=true;status.textContent='Konto wird erstellt …';try{const r=await fetch('/api/v1/auth/register',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},cache:'no-store',body:JSON.stringify({email:document.getElementById('email').value.trim(),password:document.getElementById('pw').value})});const d=await r.json();if(r.ok){form.reset();status.textContent=d.verification_required?'Konto angelegt. Bitte öffne dein E-Mail-Postfach und bestätige deine Adresse über den Link. Prüfe auch den Spam-Ordner.':'Konto angelegt. Du kannst dich jetzt über die Anwendung anmelden.';}else{status.textContent=d.detail||'Registrierung nicht möglich. Bitte später erneut versuchen.';}}catch(_){status.textContent='Dienst derzeit nicht erreichbar. Bitte später erneut versuchen.';}finally{button.disabled=false;}});</script></html>"""
        page=page.replace("NONCE",nonce)
        csp=f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        return HTMLResponse(page,headers={"Cache-Control":"no-store, max-age=0","Referrer-Policy":"no-referrer","Content-Security-Policy":csp,"X-Content-Type-Options":"nosniff"})

    # The packaged frontend is mounted at / and would otherwise shadow these
    # explicit routes when this helper is tested or mounted independently.
    try:
        from starlette.routing import Mount
        root_mounts=[r for r in app.router.routes if isinstance(r,Mount) and r.path in ("", "/")]
        if root_mounts:
            app.router.routes=[r for r in app.router.routes if r not in root_mounts]+root_mounts
    except Exception:
        pass
