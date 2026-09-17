# -*- coding: utf-8 -*-
"""
PC-Control - Relay-Server.

Kleiner Server in der Mitte. Der PC des Kindes (Agent) meldet sich hier
regelmaessig und holt Befehle ab. Das Panel (Handy oder PC) zeigt den Status
und schickt Befehle. So klappt die Steuerung von ueberall - ohne Portfreigabe,
weil sich der Agent von SICH AUS beim Server meldet (nach draussen).

Nur die Standard-Bibliothek von Python wird benutzt. Einfach starten mit:
    python relay.py

Das Passwort kommt aus der Umgebungsvariable PARENT_PASSWORD.
Es muss GENAU das gleiche sein wie parent_password in der config.json des Kindes
und das Passwort, das du im Panel eingibst.
"""

import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HIER = os.path.dirname(os.path.abspath(__file__))
PANEL_PFAD = os.path.join(HIER, "panel.html")
STATE_PFAD = os.path.join(HIER, "relay_state.json")

PASSWORT = os.environ.get("PARENT_PASSWORD", "AENDERE-MICH-BITTE")
PORT = int(os.environ.get("PORT", "8765"))

_lock = threading.Lock()
_devices = {}   # device_id -> {"status": {...}, "commands": [...]}


def _lade_state():
    global _devices
    try:
        with open(STATE_PFAD, "r", encoding="utf-8") as f:
            _devices = json.load(f)
    except Exception:
        _devices = {}


def _speichere_state():
    try:
        tmp = STATE_PFAD + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_devices, f, ensure_ascii=False)
        os.replace(tmp, STATE_PFAD)
    except Exception:
        pass


def _device(device_id):
    if device_id not in _devices:
        _devices[device_id] = {"status": {}, "commands": []}
    return _devices[device_id]


class Handler(BaseHTTPRequestHandler):
    server_version = "PcControlRelay/1.0"

    # ---- Hilfen ----------------------------------------------------------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # erlaubt auch den Zugriff von einer lokal geoeffneten panel.html-Datei
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code, text, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _autorisiert(self):
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        return hmac.compare_digest(token, PASSWORT)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, *args):
        pass  # keine laute Konsole

    # ---- GET -------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        pfad = self.path.split("?")[0]
        if pfad == "/health":
            return self._text(200, "ok")
        if pfad == "/" or pfad == "/panel.html":
            try:
                with open(PANEL_PFAD, "r", encoding="utf-8") as f:
                    return self._text(200, f.read(), "text/html; charset=utf-8")
            except Exception:
                return self._text(500, "panel.html nicht gefunden")

        if pfad == "/api/devices":
            if not self._autorisiert():
                return self._json(401, {"error": "unauthorized"})
            with _lock:
                liste = [{"device_id": d, **v.get("status", {})}
                         for d, v in _devices.items()]
            return self._json(200, {"devices": liste})

        teile = pfad.strip("/").split("/")
        if len(teile) == 3 and teile[0] == "api" and teile[2] == "status":
            if not self._autorisiert():
                return self._json(401, {"error": "unauthorized"})
            with _lock:
                st = _device(teile[1]).get("status", {})
            return self._json(200, st)

        return self._text(404, "nicht gefunden")

    # ---- POST ------------------------------------------------------------
    def do_POST(self):
        pfad = self.path.split("?")[0]
        teile = pfad.strip("/").split("/")

        # Agent meldet Status und holt Befehle ab
        if len(teile) == 3 and teile[0] == "api" and teile[2] == "report":
            if not self._autorisiert():
                return self._json(401, {"error": "unauthorized"})
            device_id = teile[1]
            status = self._body()
            with _lock:
                dev = _device(device_id)
                dev["status"] = status
                dev["status"]["server_seen"] = int(time.time())
                befehle = dev["commands"]
                dev["commands"] = []
                _speichere_state()
            return self._json(200, {"commands": befehle})

        # Panel schickt einen Befehl
        if len(teile) == 3 and teile[0] == "api" and teile[2] == "command":
            if not self._autorisiert():
                return self._json(401, {"error": "unauthorized"})
            device_id = teile[1]
            befehl = self._body()
            if "type" not in befehl:
                return self._json(400, {"error": "type fehlt"})
            with _lock:
                _device(device_id)["commands"].append(befehl)
                _speichere_state()
            return self._json(200, {"ok": True})

        # Panel: EIN Geraet aus der Liste entfernen
        if len(teile) == 3 and teile[0] == "api" and teile[2] == "forget":
            if not self._autorisiert():
                return self._json(401, {"error": "unauthorized"})
            with _lock:
                _devices.pop(teile[1], None)
                _speichere_state()
            return self._json(200, {"ok": True})

        # Panel: ALLE Geraete entfernen
        if len(teile) == 2 and teile[0] == "api" and teile[1] == "reset_all":
            if not self._autorisiert():
                return self._json(401, {"error": "unauthorized"})
            with _lock:
                _devices.clear()
                _speichere_state()
            return self._json(200, {"ok": True})

        return self._text(404, "nicht gefunden")


def main():
    _lade_state()
    if PASSWORT == "AENDERE-MICH-BITTE":
        print("!! WARNUNG: PARENT_PASSWORD ist noch das Standard-Passwort.")
        print("!! Bitte setze ein eigenes, geheimes Passwort.")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"PC-Control Relay laeuft auf Port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
