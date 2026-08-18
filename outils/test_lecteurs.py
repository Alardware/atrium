# -*- coding: utf-8 -*-
"""Frigate, Netdata, Syncthing, Proxmox : quatre lecteurs, quatre serveurs.

Les reponses imitent celles des vraies API, y compris leurs travers : Netdata
detaille son processeur par mode d occupation et non en pourcentage, Proxmox
donne une charge entre zero et un, Syncthing distingue les appareils connectes
des appareils connus.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import os
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app'))
import services  # noqa: E402
import widgets   # noqa: E402

CLE = "cle-de-test"
ECHECS = []


def verifier(titre, obtenu, attendu):
    ok = obtenu == attendu
    print("    %-38s %-24s %s" % (titre, obtenu, "" if ok else "!!! attendu %s" % (attendu,)))
    if not ok:
        ECHECS.append(titre)


def servir(router):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            corps = router(self.path, self.headers)
            if corps is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            brut = corps if isinstance(corps, bytes) else json.dumps(corps).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(brut)))
            self.end_headers()
            self.wfile.write(brut)

    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1]


def frigate(chemin, entetes):
    if chemin == "/api/version":
        return b"0.14.1"
    if chemin == "/api/stats":
        return {"cameras": {"entree": {"camera_fps": 5}, "jardin": {"camera_fps": 5},
                            "garage": {"camera_fps": 5}},
                "service": {"uptime": 172800, "version": "0.14.1"}}
    if chemin.startswith("/api/events"):
        return [{"id": str(i)} for i in range(17)]
    return None


def netdata(chemin, entetes):
    if chemin == "/api/v1/info":
        return {"uid": "abc", "version": "v1.47.0",
                "alarms": {"normal": 120, "warning": 2, "critical": 1}}
    if chemin.startswith("/api/v1/data") and "system.cpu" in chemin:
        return {"labels": ["time", "guest", "system", "user", "idle"],
                "data": [[1755500000, 0.0, 4.5, 8.5, 87.0]]}
    if chemin.startswith("/api/v1/data") and "system.ram" in chemin:
        return {"labels": ["time", "free", "used", "cached", "buffers"],
                "data": [[1755500000, 2048.0, 6144.0, 1500.0, 308.0]]}
    return None


def syncthing(chemin, entetes):
    if entetes.get("X-API-Key") != CLE:
        return None
    if chemin == "/rest/noauth/health":
        return {"status": "OK"}
    if chemin == "/rest/config/folders":
        return [{"id": "photos"}, {"id": "docs"}, {"id": "musique"}, {"id": "sauvegardes"}]
    if chemin == "/rest/system/connections":
        return {"total": {}, "connections": {
            "AAAA": {"connected": True}, "BBBB": {"connected": True},
            "CCCC": {"connected": False}}}
    if chemin == "/rest/system/status":
        return {"myID": "AAAA", "uptime": 950400}
    return None


def proxmox(chemin, entetes):
    if entetes.get("Authorization") != "PVEAPIToken=" + CLE:
        return None
    if chemin == "/api2/json/version":
        return {"data": {"version": "8.2.4", "release": "8.2"}}
    if chemin == "/api2/json/nodes":
        return {"data": [{"node": "pve", "status": "online", "cpu": 0.1734,
                          "mem": 12884901888, "maxmem": 34359738368,
                          "uptime": 1728000}]}
    if chemin.startswith("/api2/json/cluster/resources"):
        return {"data": [{"vmid": 100, "status": "running"},
                         {"vmid": 101, "status": "running"},
                         {"vmid": 102, "status": "stopped"},
                         {"vmid": 103, "status": "running"}]}
    return None


def val(stats):
    return {m["id"]: m["val"] for m in (stats or [])}


print("1. Frigate : cameras suivies et detections du jour")
s, u = servir(frigate)
try:
    mes = val(widgets.mesurer("frigate", u, ""))
    verifier("cameras", mes.get("cameras"), "3")
    verifier("detections sur 24 h", mes.get("detections"), "17")
    verifier("uptime", mes.get("uptime"), "2 j")
    verifier("detection du service", services.identifier(u, "").get("type"), "frigate")
finally:
    s.shutdown()

print("2. Netdata : la charge, deduite de l occupation par mode")
s, u = servir(netdata)
try:
    mes = val(widgets.mesurer("netdata", u, ""))
    verifier("processeur (100 - repos)", mes.get("cpu"), "13 %")
    verifier("memoire (part utilisee)", mes.get("ram"), "61 %")
    verifier("alarmes en cours", mes.get("alarmes"), "3")
    verifier("detection du service", services.identifier(u, "").get("type"), "netdata")
finally:
    s.shutdown()

print("3. Syncthing : dossiers, appareils joints, uptime")
s, u = servir(syncthing)
try:
    mes = val(widgets.mesurer("syncthing", u, CLE))
    verifier("dossiers", mes.get("dossiers"), "4")
    verifier("appareils connectes", mes.get("appareils"), "2 / 3")
    verifier("uptime", mes.get("uptime"), "11 j")
    verifier("sans cle, aucune mesure", widgets.mesurer("syncthing", u, ""), None)
finally:
    s.shutdown()

print("4. Proxmox : charge du noeud et machines en marche")
s, u = servir(proxmox)
try:
    mes = val(widgets.mesurer("proxmox", u, CLE))
    verifier("processeur", mes.get("cpu"), "17 %")
    verifier("memoire", mes.get("ram"), "38 %")
    verifier("uptime", mes.get("uptime"), "20 j")
    verifier("machines en marche", mes.get("machines"), "3 / 4")
    verifier("jeton deja prefixe accepte",
             val(widgets.mesurer("proxmox", u, "PVEAPIToken=" + CLE)).get("cpu"), "17 %")
    verifier("sans jeton, aucune mesure", widgets.mesurer("proxmox", u, ""), None)
finally:
    s.shutdown()

print()
if ECHECS:
    print("RESUME : %d point(s) a corriger" % len(ECHECS))
    for e in ECHECS:
        print("  - " + e)
    sys.exit(1)
print("RESUME : les quatre lecteurs rendent ce qu ils lisent")
