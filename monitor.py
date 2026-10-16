"""Avisa via ntfy quando o consumo da casa ultrapassa a produção solar (DeyeCloud OpenAPI)."""
import hashlib
import json
import os
import sys

import requests

BASE = os.environ.get("DEYE_BASE_URL", "https://eu1-developer.deyecloud.com/v1.0")
STATE_FILE = "state.json"
ALERT_W = int(os.environ.get("ALERT_W") or "200")        # avisa quando a importação da rede passa disto (W)
RESET_W = int(os.environ.get("RESET_W") or str(ALERT_W // 2))  # só volta a "normal" abaixo disto (W)
MIN_GEN_W = int(os.environ.get("MIN_GEN_W", "50"))       # abaixo disto = noite, sem alertas
NOTIFY_RETURN = os.environ.get("NOTIFY_RETURN", "false").lower() == "true"
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Falta a variável/secret {name}")
    return value


def api(path, token=None, params=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"bearer {token}"
    r = requests.post(f"{BASE}{path}", params=params, json=body or {}, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if DEBUG:
        print(path, json.dumps(data, indent=2)[:2000])
    return data


def get_token():
    password_hash = hashlib.sha256(env("DEYE_PASSWORD").encode()).hexdigest().lower()
    data = api(
        "/account/token",
        params={"appId": env("DEYE_APP_ID")},
        body={"appSecret": env("DEYE_APP_SECRET"), "email": env("DEYE_EMAIL"), "password": password_hash},
    )
    token = data.get("accessToken")
    if not token:
        sys.exit(f"Falha a obter token: {data.get('msg') or data}")
    return token


def get_station_id(token):
    sid = os.environ.get("DEYE_STATION_ID", "").strip()
    if sid:
        return int(sid)
    data = api("/station/list", token, body={"page": 1, "size": 20})
    stations = data.get("stationList") or []
    if not stations:
        sys.exit("Nenhuma estação encontrada na conta.")
    return stations[0]["id"]


def notify(title, message, priority="high", tags="zap"):
    requests.post(
        f"https://ntfy.sh/{env('NTFY_TOPIC')}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags},
        timeout=30,
    ).raise_for_status()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("over", False)
    except (FileNotFoundError, ValueError):
        return False


def save_state(over):
    with open(STATE_FILE, "w") as f:
        json.dump({"over": over}, f)


def main():
    if os.environ.get("ALERTAS", "on").strip().lower() == "off":
        print("Alertas desligados (ALERTAS=off).")
        return

    token = get_token()
    data = api("/station/latest", token, body={"stationId": get_station_id(token)})
    gen = data.get("generationPower")
    cons = data.get("consumptionPower")
    if gen is None or cons is None:
        sys.exit(f"Campos esperados em falta. Corre com DEBUG=true. Resposta: {data}")

    was_over = load_state()
    imported = cons - gen  # sem bateria: o que os painéis não cobrem vem da rede
    print(f"Produção: {gen:.0f} W | Consumo: {cons:.0f} W | Importação: {imported:.0f} W | estava acima: {was_over}")

    if gen < MIN_GEN_W:
        is_over = False  # noite: repõe estado sem avisar
    elif not was_over:
        is_over = imported > ALERT_W
    else:
        is_over = imported >= RESET_W

    if is_over and not was_over:
        notify("Consumo acima da producao",
               f"A importar {imported:.0f} W da rede (consumo {cons:.0f} W, produção {gen:.0f} W).")
    elif was_over and not is_over and gen >= MIN_GEN_W and NOTIFY_RETURN:
        notify("Consumo normalizado",
               f"Importação desceu para {imported:.0f} W (consumo {cons:.0f} W, produção {gen:.0f} W).",
               priority="default", tags="white_check_mark")

    save_state(is_over)


if __name__ == "__main__":
    main()
