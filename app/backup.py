"""Backup automático em um branch do seu repositório no GitHub.

No Render grátis o disco apaga quando o servidor reinicia. Com GITHUB_TOKEN e GITHUB_REPO
configurados, cada alteração é salva (com alguns segundos de atraso) em
`financas-backup.json` no branch `dados`, e o servidor restaura de lá ao iniciar.
Se a restauração falhar por erro de rede, o envio fica bloqueado para nunca sobrescrever
o backup bom com dados vazios.
"""
import base64
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from . import config as C
from . import data, db

DEBOUNCE = 8


class GitHubBackup:
    def __init__(self):
        self.enabled = bool(C.GITHUB_TOKEN and C.GITHUB_REPO)
        self.sha = None
        self.blocked = False
        self.last_ok = None
        self.last_error = None
        self._timer = None
        self._lock = threading.Lock()

    # ---- HTTP
    def _req(self, method, path, body=None, raw=False):
        url = f"{C.GITHUB_API}/repos/{C.GITHUB_REPO}{path}"
        data_ = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + C.GITHUB_TOKEN, "X-GitHub-Api-Version": "2022-11-28",
                   "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
                   "User-Agent": "financas-app"}
        if data_ is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data_, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = r.read()
        return payload if raw else (json.loads(payload) if payload else {})

    def _file_path(self):
        return "/contents/" + quote(C.GITHUB_PATH) + "?ref=" + quote(C.GITHUB_BRANCH)

    def _fetch(self):
        """Devolve (conteúdo, sha) ou (None, None) se ainda não existe backup."""
        try:
            meta = self._req("GET", self._file_path())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, None
            raise
        raw = self._req("GET", self._file_path(), raw=True)
        return json.loads(raw.decode("utf-8")), meta.get("sha")

    # ---- restauração na partida
    def restore(self, conn):
        if not self.enabled:
            return False
        try:
            content, sha = self._fetch()
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            self.blocked = True
            self.last_error = f"não consegui ler o backup ({e.__class__.__name__}); envio pausado"
            C.log("backup: " + self.last_error)
            return False
        self.sha = sha
        if content is None:
            return False
        db.migrate(conn)
        data.restore(conn, content)
        C.log("backup: dados restaurados do GitHub")
        return True

    def retry_restore(self):
        """Se a partida não conseguiu ler o GitHub, tenta de novo e substitui os dados temporários."""
        try:
            content, sha = self._fetch()
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return False
        with db.connect() as conn:
            if content is not None:
                data.restore(conn, content)
        self.sha, self.blocked, self.last_error = sha, False, None
        return True

    # ---- envio
    def schedule(self):
        if not self.enabled:
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def _ensure_branch(self):
        repo = self._req("GET", "")
        base = repo.get("default_branch", "main")
        ref = self._req("GET", "/git/ref/heads/" + quote(base))
        try:
            self._req("POST", "/git/refs", {"ref": "refs/heads/" + C.GITHUB_BRANCH, "sha": ref["object"]["sha"]})
        except urllib.error.HTTPError as e:
            if e.code != 422:  # 422 = já existe
                raise

    def flush(self):
        if not self.enabled or self.blocked and not self.retry_restore():
            return False
        with self._lock:
            self._timer = None
        with db.connect() as conn:
            payload = data.export(conn)
        content = base64.b64encode(json.dumps(payload, ensure_ascii=False, indent=1).encode()).decode()
        for attempt in range(3):
            body = {"message": "backup automático " + C.now().strftime("%Y-%m-%d %H:%M"),
                    "content": content, "branch": C.GITHUB_BRANCH}
            if self.sha:
                body["sha"] = self.sha
            try:
                r = self._req("PUT", "/contents/" + quote(C.GITHUB_PATH), body)
                self.sha = r.get("content", {}).get("sha")
                self.last_ok, self.last_error = C.now().strftime("%Y-%m-%d %H:%M"), None
                self._push_files()
                return True
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "replace")[:200]
                if e.code == 404 and "branch" in msg.lower() or e.code == 422 and "branch" in msg.lower():
                    self._ensure_branch()
                    continue
                if e.code in (409, 422):  # sha desatualizado
                    try:
                        _, self.sha = self._fetch()
                    except (urllib.error.URLError, OSError):
                        pass
                    continue
                try:
                    gm = json.loads(msg).get("message", "")
                except ValueError:
                    gm = msg
                hint = {401: "token inválido ou expirado",
                        403: "o token não tem permissão de escrita: no GitHub, dê 'Contents: Read and write' "
                             "e acesso ao repositório Financas",
                        404: "repositório não encontrado para este token (confira o acesso ao repositório Financas)"}
                self.last_error = f"GitHub HTTP {e.code} — {hint.get(e.code, gm[:120] or 'erro')}"
                break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                self.last_error = f"sem conexão com o GitHub ({e.__class__.__name__})"
                time.sleep(2)
        C.log("backup: " + str(self.last_error))
        return False

    def _push_files(self):
        """Envia os arquivos novos (laudos, fotos) para `arquivos/<hash>` no mesmo branch."""
        from .vida import arquivo_nome_remoto
        with db.connect() as conn:
            pend = db.rows(conn, "SELECT id, hash, mime FROM arquivos WHERE enviado=0 AND dados IS NOT NULL")
        for a in pend:
            with db.connect() as conn:
                row = conn.execute("SELECT dados FROM arquivos WHERE id=?", (a["id"],)).fetchone()
            if not row or row["dados"] is None:
                continue
            body = {"message": "arquivo " + a["hash"][:12], "content": base64.b64encode(bytes(row["dados"])).decode(),
                    "branch": C.GITHUB_BRANCH}
            try:
                self._req("PUT", "/contents/" + quote(arquivo_nome_remoto(a)), body)
            except urllib.error.HTTPError as e:
                if e.code != 422:  # 422 = já existe (mesmo conteúdo, mesmo nome)
                    C.log(f"backup: arquivo {a['id']} não enviado (HTTP {e.code})")
                    continue
            except (urllib.error.URLError, TimeoutError, OSError):
                return
            with db.connect() as conn:  # sem write=True: não agenda outro backup
                conn.execute("UPDATE arquivos SET enviado=1 WHERE id=?", (a["id"],))

    def fetch_file(self, path):
        """Conteúdo de um arquivo do backup (depois de o servidor reiniciar), ou None."""
        try:
            return self._req("GET", "/contents/" + quote(path) + "?ref=" + quote(C.GITHUB_BRANCH), raw=True)
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

    def status(self):
        return {"ativo": self.enabled, "pausado": self.blocked, "ultimo": self.last_ok, "erro": self.last_error,
                "repo": C.GITHUB_REPO if self.enabled else None}


BACKUP = GitHubBackup()
db.on_write.append(BACKUP.schedule)
