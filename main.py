import flet as ft
import asyncio
import js
from js import fetch
import json
import re
import random
import time
from thefuzz import fuzz

# ----------------- KONFIGURACJA -----------------

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"
GITHUB_RAW_BASE_URL = (
    "https://raw.githubusercontent.com/mechagdynia2-ai/game/main/assets/"
)

ENTRY_FEE = 500        # wpisowe na pytanie (lokalne, UI)
BASE_ANSWER_TIME = 60  # 60 s na odpowiedź
HINT_EXTRA_TIME = 30   # +30 s za każdą podpowiedź

# ----------------- POMOCNICZE -----------------


def make_async_click(async_callback):
    """Wrap dla on_click, żeby bezpiecznie używać async w Flet (Pyodide)."""

    def handler(e):
        async def task():
            await async_callback(e)

        e.page.run_task(task)

    return handler


async def fetch_text(url: str) -> str:
    try:
        resp = await fetch(url)
        return await resp.text()
    except Exception as ex:
        print("[FETCH_TEXT ERROR]", ex)
        return ""


async def fetch_json(url: str, method: str = "GET", body: dict | None = None):
    try:
        if method.upper() == "GET":
            kwargs = js.Object.fromEntries([["method", "GET"]])
        else:
            payload = json.dumps(body or {})
            kwargs = js.Object.fromEntries(
                [
                    ["method", method.upper()],
                    [
                        "headers",
                        js.Object.fromEntries(
                            [["Content-Type", "application/json"]]
                        ),
                    ],
                    ["body", payload],
                ]
            )
        resp = await fetch(url, kwargs)
        raw = await resp.json()
        try:
            return raw.to_py()
        except Exception:
            return raw
    except Exception as ex:
        print("[FETCH_JSON ERROR]", ex)
        return None


def normalize_answer(text: str) -> str:
    text = str(text).lower().strip()
    repl = {
        "ó": "o",
        "ł": "l",
        "ż": "z",
        "ź": "z",
        "ć": "c",
        "ń": "n",
        "ś": "s",
        "ą": "a",
        "ę": "e",
        "ü": "u",
    }
    for c, r in repl.items():
        text = text.replace(c, r)
    text = text.replace("u", "o")
    return "".join(text.split())


async def parse_question_file(filename: str) -> list[dict]:
    """
    Parsuje plik pytań w obu formatach:
    - 'prawidłowa odpowiedz' / 'prawidłowa odpowiedź'
    - 'odpowiedz ABCD' / 'odpowiedź ABCD'
    """
    url = f"{GITHUB_RAW_BASE_URL}{filename}"
    print(f"[FETCH PYTANIA] {url}")
    content = await fetch_text(url)
    if not content:
        print("[PYTANIA] Brak treści.")
        return []

    parsed: list[dict] = []

    # dzielimy po liniach typu "NN. "
    blocks = re.split(r"\n(?=\d{1,3}\.)", content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # pytanie
        q_match = re.match(r"^\d{1,3}\.\s*(.+)", block)
        if not q_match:
            print("[WARNING] Nie znaleziono pytania w bloku:", block[:60])
            continue
        question = q_match.group(1).strip()

        # prawidłowa odpowiedź (z/bez ogonka)
        correct_match = re.search(
            r"prawidłowa\s+odpowied[zź]\s*=\s*(.+)",
            block,
            re.IGNORECASE,
        )
        if not correct_match:
            print("[WARNING] Brak prawidłowej odpowiedzi:", block[:60])
            continue
        correct = correct_match.group(1).strip()

        # linia ABCD
        answers_match = re.search(
            r"odpowied[zź]\s*ABCD\s*=\s*A\s*=\s*(.+?),\s*B\s*=\s*(.+?),\s*C\s*=\s*(.+?),\s*D\s*=\s*(.+)",
            block,
            re.IGNORECASE,
        )
        if not answers_match:
            print("[WARNING] Brak ABCD:", block[:60])
            continue

        a = answers_match.group(1).strip()
        b = answers_match.group(2).strip()
        c = answers_match.group(3).strip()
        d = answers_match.group(4).strip()

        parsed.append(
            {
                "question": question,
                "correct": correct,
                "answers": [a, b, c, d],
            }
        )

    print(f"[PYTANIA] Sparsowano: {len(parsed)}")
    return parsed


# ----------------- GŁÓWNE UI / LOGIKA -----------------


async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Multiplayer"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    page.vertical_alignment = ft.MainAxisAlignment.START

    # -------- STAN MULTIPLAYER (lokalny) --------

    mp_state = {
        "player_id": None,
        "player_name": "",
        "is_admin": False,
        "joined": False,
        # pytania
        "set_name": "",
        "questions": [],
        "current_q_index": -1,
        # pot UI – w tym carryover między pytaniami
        "carryover_pot": 0,
        "current_round_pot": 0,
        # faza logiki lokalnej
        # idle / waiting_set / countdown / bidding / answering_wait / discussion / verdict
        "local_phase": "idle",
        "countdown_until": 0.0,
        "answer_deadline": 0.0,
        "discussion_until": 0.0,
        "answering_player_id": None,
        "answering_player_name": "",
        "current_answer_text": "",
        "verdict_sent": False,
        "last_round_id": None,
        # lokalna kasa tego gracza (do UI, niezależnie od backendu)
        "local_money": 10000,
    }

    # mapowanie nazwa -> kolor (ciemne odcienie) do czatu
    name_color_cache: dict[str, str] = {}
    color_palette = [
        "#1e3a8a",
        "#4c1d95",
        "#064e3b",
        "#7c2d12",
        "#111827",
        "#075985",
        "#7f1d1d",
        "#374151",
    ]

    def name_to_color(name: str) -> str:
        if name in name_color_cache:
            return name_color_cache[name]
        idx = abs(hash(name)) % len(color_palette)
        name_color_cache[name] = color_palette[idx]
        return color_palette[idx]

    # --------- KOMPONENTY UI ---------

    txt_title = ft.Text(
        "AWANTURA O KASĘ – MULTIPLAYER",
        size=22,
        weight=ft.FontWeight.BOLD,
        text_align=ft.TextAlign.CENTER,
    )

    txt_status = ft.Text(
        "Podaj ksywkę i dołącz do gry.",
        size=13,
        color="blue",
    )

    txt_local_money = ft.Text(
        "Twoja kasa (lokalnie): 10000 zł",
        size=14,
        weight=ft.FontWeight.BOLD,
        color="green_700",
    )

    txt_round_info = ft.Text(
        "Brak wybranego zestawu pytań.",
        size=13,
        color="grey_700",
    )

    txt_phase_info = ft.Text(
        "",
        size=13,
        color="grey_800",
    )

    # --- CZAT ---

    col_chat = ft.Column(
        [],
        spacing=2,
        height=160,
        scroll=ft.ScrollMode.ALWAYS,
        auto_scroll=True,
    )

    txt_chat_input = ft.TextField(
        label="Napisz na czacie",
        multiline=False,
        dense=True,
        border_radius=8,
        text_size=13,
        disabled=True,
    )
    btn_chat_send = ft.FilledButton(
        "Wyślij",
        width=110,
        disabled=True,
    )

    chat_box = ft.Container(
        content=ft.Column(
            [
                ft.Text("Czat:", size=12, color="grey_700"),
                col_chat,
                ft.Row(
                    [txt_chat_input, btn_chat_send],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=4,
        ),
        padding=8,
        border_radius=10,
        border=ft.border.all(1, "#e0e0e0"),
        bgcolor="white",
    )

    # --- PYTANIE / PODPOWIEDZI ---

    txt_question = ft.Text(
        "Czekamy na wybór zestawu pytań przez ADMINA (wpisz numer 1–50 na czacie).",
        size=16,
        weight=ft.FontWeight.BOLD,
        text_align=ft.TextAlign.CENTER,
        color="#0f172a",
    )

    btn_hint_abcd = ft.OutlinedButton(
        "Kup ABCD (1000–3000 zł)",
        width=220,
        disabled=True,
    )
    btn_hint_5050 = ft.OutlinedButton(
        "Kup 50/50 (500–2500 zł)",
        width=220,
        disabled=True,
    )

    hint_col = ft.Column(
        [btn_hint_abcd, btn_hint_5050],
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # --- LICYTACJA ---

    txt_pot = ft.Text(
        "Pula (backend): 0 zł",
        size=14,
        weight=ft.FontWeight.BOLD,
        color="purple_700",
    )
    txt_local_pot = ft.Text(
        "PULA (lokalnie – z carryover i podpowiedzi): 0 zł",
        size=13,
        color="purple_900",
    )

    txt_timer = ft.Text(
        "Czas: -- s",
        size=14,
        weight=ft.FontWeight.BOLD,
    )

    btn_bid = ft.FilledButton(
        "Licytuj +100 zł",
        width=180,
        disabled=True,
    )
    btn_finish_bidding = ft.FilledButton(
        "Kończę licytację",
        width=180,
        disabled=True,
    )
    btn_all_in = ft.FilledButton(
        "VA BANQUE!",
        width=180,
        disabled=True,
    )

    bidding_col = ft.Column(
        [btn_bid, btn_finish_bidding, btn_all_in],
        spacing=4,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # --- DOŁĄCZANIE ---

    txt_name = ft.TextField(
        label="Twoja ksywka",
        width=220,
        dense=True,
    )
    btn_join = ft.FilledButton("Dołącz do pokoju", width=180)

    join_row = ft.Row(
        [txt_name, btn_join],
        alignment=ft.MainAxisAlignment.START,
        spacing=10,
    )

    # --- UKŁAD GŁÓWNY ---

    layout = ft.Column(
        [
            txt_title,
            txt_status,
            ft.Divider(height=8),
            join_row,
            ft.Row(
                [txt_local_money, txt_pot],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Row(
                [txt_local_pot, txt_timer],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            txt_round_info,
            txt_phase_info,
            ft.Divider(height=8),
            chat_box,
            ft.Divider(height=8),
            txt_question,
            hint_col,
            ft.Divider(height=8),
            bidding_col,
        ],
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    page.add(layout)
    page.update()

    # ---------------- FUNKCJE UI ----------------

    def refresh_local_money():
        val = mp_state["local_money"]
        txt_local_money.value = f"Twoja kasa (lokalnie): {val} zł"
        if val <= 0:
            txt_local_money.color = "red_700"
        elif val < ENTRY_FEE:
            txt_local_money.color = "orange_700"
        else:
            txt_local_money.color = "green_700"
        txt_local_money.update()

    def refresh_local_pot():
        pot = mp_state["current_round_pot"]
        txt_local_pot.value = (
            f"PULA (lokalnie – z carryover i podpowiedzi): {pot} zł"
        )
        txt_local_pot.update()

    def set_phase_info(msg: str, color: str = "grey_800"):
        txt_phase_info.value = msg
        txt_phase_info.color = color
        txt_phase_info.update()

    # --- render czatu ---

    last_chat_len = 0

    def render_chat(chat_list: list[dict], players_state: list[dict]):
        nonlocal last_chat_len
        if not chat_list:
            return

        if len(chat_list) == last_chat_len:
            return
        last_chat_len = len(chat_list)

        admin_names = {p["name"] for p in players_state if p.get("is_admin")}

        col_chat.controls.clear()

        for m in chat_list:
            player_name = m.get("player", "?")
            msg_text = m.get("message", "")
            is_bot = player_name == "BOT"

            spans: list[ft.TextSpan] = []

            if is_bot:
                spans.append(
                    ft.TextSpan(
                        "BOT: ",
                        ft.TextStyle(
                            color="black",
                            weight=ft.FontWeight.BOLD,
                            size=12,
                        ),
                    )
                )
                spans.append(
                    ft.TextSpan(
                        msg_text,
                        ft.TextStyle(color="black", size=12),
                    )
                )
            else:
                is_admin = player_name in admin_names
                if is_admin:
                    spans.append(
                        ft.TextSpan(
                            "[ADMIN] ",
                            ft.TextStyle(
                                color="red",
                                weight=ft.FontWeight.BOLD,
                                size=12,
                            ),
                        )
                    )

                name_color = name_to_color(player_name)
                spans.append(
                    ft.TextSpan(
                        f"{player_name}: ",
                        ft.TextStyle(
                            color=name_color,
                            weight=ft.FontWeight.BOLD,
                            size=12,
                        ),
                    )
                )
                spans.append(
                    ft.TextSpan(
                        msg_text,
                        ft.TextStyle(color="black", size=12),
                    )
                )

            col_chat.controls.append(
                ft.Text(
                    spans=spans,
                    max_lines=4,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )

        col_chat.update()

    # --- analiza czatu pod komendy ADMINA (wybór zestawu) ---

    processed_chat_ids: set[float] = set()

    async def process_chat_commands(chat_list: list[dict], players_state: list[dict]):
        """
        Szukamy wiadomości admina będącej numerem 1–50 (01 też ok).
        Pierwsza taka wiadomość wybiera zestaw pytań.
        """
        if mp_state["set_name"]:
            return  # zestaw już wybrany

        admin_names = {p["name"] for p in players_state if p.get("is_admin")}
        if not admin_names:
            return

        for m in chat_list:
            ts = m.get("timestamp", 0.0)
            if ts in processed_chat_ids:
                continue
            processed_chat_ids.add(ts)

            player_name = m.get("player", "")
            msg_text = (m.get("message") or "").strip()

            if player_name not in admin_names:
                continue

            # czy to numer 1–50 (np. "7", "07", "  10  ")
            if not re.fullmatch(r"0?\d{1,2}", msg_text):
                continue

            num = int(msg_text)
            if num < 1 or num > 50:
                continue

            filename = f"{num:02d}.txt"
            # ładujemy pytania
            questions = await parse_question_file(filename)
            if not questions:
                await fetch_json(
                    f"{BACKEND_URL}/chat",
                    "POST",
                    {
                        "player": "BOT",
                        "message": f"Nie udało się załadować zestawu {filename}.",
                    },
                )
                return

            mp_state["set_name"] = filename.replace(".txt", "")
            mp_state["questions"] = questions
            mp_state["current_q_index"] = 0
            mp_state["carryover_pot"] = 0
            mp_state["current_round_pot"] = 0
            mp_state["local_phase"] = "countdown"
            mp_state["countdown_until"] = time.time() + 20.0
            mp_state["verdict_sent"] = False
            mp_state["answering_player_id"] = None
            mp_state["answering_player_name"] = ""
            mp_state["current_answer_text"] = ""

            txt_round_info.value = (
                f"Zestaw {mp_state['set_name']} – pytanie 1 / {len(questions)}"
            )
            txt_round_info.update()

            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": f"Zestaw pytań nr {mp_state['set_name']} został wybrany przez ADMINA.",
                },
            )
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": "Za 20 sekund zaczniemy pierwszą licytację!",
                },
            )
            set_phase_info(
                "Odliczanie 20 s do startu licytacji...",
                color="blue",
            )
            page.update()
            break

    # ----------------- BACKEND / MULTI -----------------

    async def mp_register(e):
        name = (txt_name.value or "").strip()
        if not name:
            txt_status.value = "Podaj ksywkę, aby dołączyć."
            txt_status.color = "red"
            txt_status.update()
            return

        data = await fetch_json(
            f"{BACKEND_URL}/register",
            "POST",
            {"name": name},
        )
        if not data or "id" not in data:
            txt_status.value = "Błąd rejestracji (backend)."
            txt_status.color = "red"
            txt_status.update()
            return

        mp_state["player_id"] = data["id"]
        mp_state["player_name"] = data.get("name", name)
        mp_state["is_admin"] = data.get("is_admin", False)
        mp_state["joined"] = True
        mp_state["local_money"] = 10000  # start

        txt_status.value = f"Dołączono jako {mp_state['player_name']}."
        if mp_state["is_admin"]:
            txt_status.value += " Jesteś ADMINEM – wpisz na czacie numer 1–50, aby wybrać zestaw pytań."
        else:
            txt_status.value += " Czekamy, aż ADMIN wybierze zestaw pytań (numer 1–50 na czacie)."
        txt_status.color = "green"
        txt_status.update()

        join_row.visible = False
        txt_chat_input.disabled = False
        btn_chat_send.disabled = False

        refresh_local_money()
        page.update()

        # odpalamy heartbeat i poll
        page.run_task(mp_heartbeat_loop)
        page.run_task(mp_poll_state)

    async def mp_heartbeat_loop():
        while mp_state["player_id"]:
            await asyncio.sleep(10)
            pid = mp_state["player_id"]
            if not pid:
                break
            resp = await fetch_json(
                f"{BACKEND_URL}/heartbeat",
                "POST",
                {"player_id": pid},
            )
            if not resp or resp.get("status") != "ok":
                print("[HEARTBEAT] problem", resp)
                break
            mp_state["is_admin"] = resp.get("is_admin", mp_state["is_admin"])

    async def mp_send_chat(e):
        msg = (txt_chat_input.value or "").strip()
        if not msg:
            return

        # ograniczenia czatu wg fazy
        lp = mp_state["local_phase"]
        answering_id = mp_state["answering_player_id"]
        my_id = mp_state["player_id"]

        # w fazie answering_wait – tylko zwycięzca może coś napisać (jego odpowiedź)
        if lp == "answering_wait" and answering_id and my_id != answering_id:
            set_phase_info(
                f"Teraz odpowiada {mp_state['answering_player_name']}. Poczekaj na swoją kolej.",
                color="red",
            )
            return

        # wysyłamy normalnie
        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {"player": mp_state["player_name"] or "Anonim", "message": msg},
        )
        txt_chat_input.value = ""
        txt_chat_input.update()

        # jeśli to jest odpowiedź zwycięzcy, zapisujemy ją lokalnie
        if (
            lp == "answering_wait"
            and answering_id
            and my_id == answering_id
            and not mp_state["current_answer_text"]
        ):
            mp_state["current_answer_text"] = msg
            mp_state["local_phase"] = "discussion"
            mp_state["discussion_until"] = time.time() + 20.0
            mp_state["verdict_sent"] = False

            # pytanie do reszty
            if mp_state["is_admin"]:
                await fetch_json(
                    f"{BACKEND_URL}/chat",
                    "POST",
                    {
                        "player": "BOT",
                        "message": (
                            "A wy jak myślicie mistrzowie, czy to jest poprawna "
                            "odpowiedź? Macie 20 sekund, żeby się wypowiedzieć!"
                        ),
                    },
                )

            set_phase_info(
                "Dyskusja: wszyscy mogą pisać na czacie przez 20 s.",
                color="blue",
            )
            page.update()

    async def mp_bid_normal(e):
        if not mp_state["player_id"]:
            return
        data = await fetch_json(
            f"{BACKEND_URL}/bid",
            "POST",
            {"player_id": mp_state["player_id"], "kind": "normal"},
        )
        if not data or data.get("status") != "ok":
            txt_status.value = data.get("detail", "Błąd licytacji +100.")
            txt_status.color = "red"
            txt_status.update()
            return
        pot = data.get("pot", 0)
        txt_pot.value = f"Pula (backend): {pot} zł"
        txt_pot.update()
        # botowa informacja o stawce tego gracza (po /state)
        state = await fetch_json(f"{BACKEND_URL}/state", "GET")
        if state and "players" in state:
            my_id = mp_state["player_id"]
            for p in state["players"]:
                if p["id"] == my_id:
                    bid_amount = p.get("bid", 0)
                    await fetch_json(
                        f"{BACKEND_URL}/chat",
                        "POST",
                        {
                            "player": "BOT",
                            "message": f"{mp_state['player_name']} licytuje: {bid_amount} zł.",
                        },
                    )
                    break

    async def mp_bid_allin(e):
        if not mp_state["player_id"]:
            return
        data = await fetch_json(
            f"{BACKEND_URL}/bid",
            "POST",
            {"player_id": mp_state["player_id"], "kind": "allin"},
        )
        if not data or data.get("status") != "ok":
            txt_status.value = data.get("detail", "Błąd licytacji VA BANQUE.")
            txt_status.color = "red"
            txt_status.update()
            return
        pot = data.get("pot", 0)
        txt_pot.value = f"Pula (backend): {pot} zł"
        txt_pot.update()
        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {
                "player": "BOT",
                "message": f"{mp_state['player_name']} idzie VA BANQUE!",
            },
        )

    async def mp_finish_bidding(e):
        if not mp_state["player_id"]:
            return
        data = await fetch_json(
            f"{BACKEND_URL}/finish_bidding",
            "POST",
            {"player_id": mp_state["player_id"]},
        )
        if not data or data.get("status") != "ok":
            txt_status.value = data.get("detail", "Błąd zakończenia licytacji.")
            txt_status.color = "red"
            txt_status.update()
            return
        txt_status.value = "Licytacja zakończona (backend). Czekamy na wynik."
        txt_status.color = "blue"
        txt_status.update()

    # --- podpowiedzi (lokalne) ---

    async def buy_hint_abcd(e):
        if (
            mp_state["local_phase"] != "answering_wait"
            and mp_state["local_phase"] != "discussion"
        ):
            set_phase_info(
                "Podpowiedzi są dostępne tylko podczas odpowiadania na pytanie.",
                color="red",
            )
            return
        if mp_state["player_id"] != mp_state["answering_player_id"]:
            set_phase_info(
                "Tylko gracz, który wygrał licytację, może kupować podpowiedzi.",
                color="red",
            )
            return

        cost = random.randint(1000, 3000)
        if mp_state["local_money"] < cost:
            set_phase_info(
                f"Nie stać Cię na podpowiedź ABCD ({cost} zł).",
                color="red",
            )
            return

        mp_state["local_money"] -= cost
        # powiększamy lokalną pulę
        mp_state["current_round_pot"] += cost
        # wydłużamy czas na odpowiedź
        mp_state["answer_deadline"] += HINT_EXTRA_TIME

        refresh_local_money()
        refresh_local_pot()

        # generujemy ABCD z aktualnego pytania
        q_idx = mp_state["current_q_index"]
        if 0 <= q_idx < len(mp_state["questions"]):
            q = mp_state["questions"][q_idx]
            answers = q["answers"]
            text_abcd = (
                f"A) {answers[0]}, B) {answers[1]}, "
                f"C) {answers[2]}, D) {answers[3]}"
            )
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": f"Podpowiedź ABCD (koszt {cost} zł): {text_abcd}",
                },
            )
            set_phase_info(
                f"Kupiono podpowiedź ABCD za {cost} zł. Czas na odpowiedź wydłużony.",
                color="blue",
            )
            page.update()

    async def buy_hint_5050(e):
        if (
            mp_state["local_phase"] != "answering_wait"
            and mp_state["local_phase"] != "discussion"
        ):
            set_phase_info(
                "Podpowiedzi są dostępne tylko podczas odpowiadania na pytanie.",
                color="red",
            )
            return
        if mp_state["player_id"] != mp_state["answering_player_id"]:
            set_phase_info(
                "Tylko gracz, który wygrał licytację, może kupować podpowiedzi.",
                color="red",
            )
            return

        cost = random.randint(500, 2500)
        if mp_state["local_money"] < cost:
            set_phase_info(
                f"Nie stać Cię na 50/50 ({cost} zł).",
                color="red",
            )
            return

        mp_state["local_money"] -= cost
        mp_state["current_round_pot"] += cost
        mp_state["answer_deadline"] += HINT_EXTRA_TIME

        refresh_local_money()
        refresh_local_pot()

        q_idx = mp_state["current_q_index"]
        if 0 <= q_idx < len(mp_state["questions"]):
            q = mp_state["questions"][q_idx]
            correct = q["correct"]
            wrong = [a for a in q["answers"] if a != correct]
            random.shuffle(wrong)
            removed = wrong[:2]
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        f"Podpowiedź 50/50 (koszt {cost} zł): "
                        f"to na pewno NIE są: {removed[0]} i {removed[1]}."
                    ),
                },
            )
            set_phase_info(
                f"Kupiono 50/50 za {cost} zł. Czas na odpowiedź wydłużony.",
                color="blue",
            )
            page.update()

    # --- poll stanu z backendu + logika faz lokalnych ---

    async def mp_poll_state():
        while mp_state["player_id"]:
            data = await fetch_json(f"{BACKEND_URL}/state", "GET")
            if not data:
                await asyncio.sleep(1.5)
                continue

            # backend: round, phase, pot, time_left, players, chat
            round_id = data.get("round_id")
            phase = data.get("phase")
            pot_backend = data.get("pot", 0)
            time_left_backend = int(data.get("time_left", 0))
            players_list = data.get("players", [])
            chat_list = data.get("chat", [])
            answering_player_id_backend = data.get("answering_player_id")

            txt_pot.value = f"Pula (backend): {pot_backend} zł"
            txt_timer.value = f"Czas (bidding z backendu): {time_left_backend} s"
            txt_pot.update()
            txt_timer.update()

            # lista graczy może nam się przydać
            my_id = mp_state["player_id"]
            for p in players_list:
                if p["id"] == my_id:
                    # można by tu zsynchronizować kasę z backendem, ale trzymamy UI osobno
                    pass

            # odśwież czat
            render_chat(chat_list, players_list)

            # spróbuj rozpoznać wybór zestawu (numer 1–50 admina)
            await process_chat_commands(chat_list, players_list)

            # logika faz lokalnych
            now = time.time()

            # 1) odliczanie do licytacji
            if mp_state["local_phase"] == "countdown":
                remain = int(mp_state["countdown_until"] - now)
                if remain < 0:
                    remain = 0
                set_phase_info(
                    f"Odliczanie do licytacji: {remain} s...",
                    color="blue",
                )
                # po zakończeniu odliczania prosimy backend o nową rundę (ADMIN)
                if remain <= 0 and mp_state["is_admin"]:
                    # nowa runda backendowa
                    nr = await fetch_json(
                        f"{BACKEND_URL}/next_round",
                        "POST",
                        {},
                    )
                    print("[NEXT ROUND]", nr)
                    mp_state["local_phase"] = "bidding"
                    mp_state["last_round_id"] = None  # wymusimy reakcję
                    set_phase_info(
                        "Licytacja rozpoczęta! Użyj przycisków licytacji.",
                        color="green",
                    )
                elif remain <= 0:
                    mp_state["local_phase"] = "bidding"
                # przyciski licytacji aktywne gdy backend-phase = bidding
                btn_bid.disabled = phase != "bidding"
                btn_all_in.disabled = phase != "bidding"
                btn_finish_bidding.disabled = not mp_state["is_admin"] or (
                    phase != "bidding"
                )
                btn_bid.update()
                btn_all_in.update()
                btn_finish_bidding.update()

            # 2) faza bidding – backend-phase powinien być "bidding"
            elif mp_state["local_phase"] == "bidding":
                btn_bid.disabled = phase != "bidding"
                btn_all_in.disabled = phase != "bidding"
                btn_finish_bidding.disabled = not mp_state["is_admin"] or (
                    phase != "bidding"
                )
                btn_bid.update()
                btn_all_in.update()
                btn_finish_bidding.update()

                if phase == "answering":
                    # backend zakończył licytację, znamy answering_player_id
                    mp_state["local_phase"] = "answering_wait"
                    mp_state["answer_deadline"] = now + BASE_ANSWER_TIME
                    mp_state["current_answer_text"] = ""
                    mp_state["verdict_sent"] = False
                    mp_state["current_round_pot"] = (
                        pot_backend + mp_state["carryover_pot"]
                    )
                    refresh_local_pot()

                    # ustalamy zwycięzcę
                    mp_state["answering_player_id"] = answering_player_id_backend
                    winner_name = "?"
                    for p in players_list:
                        if p["id"] == answering_player_id_backend:
                            winner_name = p["name"]
                            break
                    mp_state["answering_player_name"] = winner_name

                    # pytanie na czat + informacja – tylko ADMIN wysyła BOT-a
                    q_idx = mp_state["current_q_index"]
                    if (
                        mp_state["is_admin"]
                        and 0 <= q_idx < len(mp_state["questions"])
                    ):
                        q = mp_state["questions"][q_idx]
                        question_text = q["question"]
                        await fetch_json(
                            f"{BACKEND_URL}/chat",
                            "POST",
                            {
                                "player": "BOT",
                                "message": (
                                    f"Gracz {winner_name} wygrał licytację! "
                                    f"PYTANIE: {question_text}"
                                ),
                            },
                        )

                    txt_question.value = (
                        f"Pytanie: {mp_state['questions'][q_idx]['question']}"
                        if 0 <= q_idx < len(mp_state["questions"])
                        else "Brak pytania."
                    )
                    txt_question.update()

                    # podpowiedzi włączamy tylko dla zwycięzcy
                    is_me_winner = my_id == answering_player_id_backend
                    btn_hint_abcd.disabled = not is_me_winner
                    btn_hint_5050.disabled = not is_me_winner
                    btn_hint_abcd.update()
                    btn_hint_5050.update()

                    set_phase_info(
                        f"Na pytanie odpowiada: {mp_state['answering_player_name']} (ma 60 s).",
                        color="green",
                    )

            # 3) faza answering_wait – czekamy na odpowiedź zwycięzcy
            elif mp_state["local_phase"] == "answering_wait":
                remain = int(mp_state["answer_deadline"] - now)
                if remain < 0:
                    remain = 0
                set_phase_info(
                    f"Na pytanie odpowiada {mp_state['answering_player_name']} – pozostało {remain} s.",
                    color="green",
                )
                # jeśli czas minął, a odpowiedzi brak – przechodzimy do dyskusji z pustą odpowiedzią
                if remain <= 0 and not mp_state["current_answer_text"]:
                    mp_state["current_answer_text"] = ""
                    mp_state["local_phase"] = "discussion"
                    mp_state["discussion_until"] = now + 20.0
                    mp_state["verdict_sent"] = False
                    if mp_state["is_admin"]:
                        await fetch_json(
                            f"{BACKEND_URL}/chat",
                            "POST",
                            {
                                "player": "BOT",
                                "message": (
                                    "Czas na odpowiedź minął. "
                                    "A wy jak myślicie mistrzowie, czy brak odpowiedzi "
                                    "to poprawna odpowiedź? Macie 20 sekund na komentarze!"
                                ),
                            },
                        )

            # 4) dyskusja – po odpowiedzi
            elif mp_state["local_phase"] == "discussion":
                remain = int(mp_state["discussion_until"] - now)
                if remain < 0:
                    remain = 0
                set_phase_info(
                    f"Dyskusja – pozostało {remain} s na komentarze.",
                    color="blue",
                )
                if remain <= 0 and not mp_state["verdict_sent"]:
                    # czas na werdykt – tylko ADMIN ogłasza
                    if mp_state["is_admin"]:
                        await send_verdict_and_prepare_next_round()
                    mp_state["verdict_sent"] = True
                    mp_state["local_phase"] = "verdict"

            # 5) po werdykcie
            elif mp_state["local_phase"] == "verdict":
                # czekamy aż backend uruchomi nową rundę (phase wróci do bidding po /next_round)
                pass

            await asyncio.sleep(1.0)

    async def send_verdict_and_prepare_next_round():
        q_idx = mp_state["current_q_index"]
        if not (0 <= q_idx < len(mp_state["questions"])):
            return
        q = mp_state["questions"][q_idx]
        correct = q["correct"]
        user_answer = mp_state["current_answer_text"]

        norm_user = normalize_answer(user_answer)
        norm_correct = normalize_answer(correct)
        similarity = fuzz.ratio(norm_user, norm_correct)

        winner_name = mp_state["answering_player_name"]
        pot_ui = mp_state["current_round_pot"]

        if similarity >= 80:
            # DOBRA odpowiedź
            if winner_name == mp_state["player_name"]:
                mp_state["local_money"] += pot_ui
                refresh_local_money()
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        f"DOBRA odpowiedź! ({similarity}%) "
                        f"Gracz {winner_name} wygrywa {pot_ui} zł z puli!"
                    ),
                },
            )
            mp_state["carryover_pot"] = 0
        else:
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        f"ZŁA odpowiedź ({similarity}%). "
                        f"Prawidłowa odpowiedź: {correct}. "
                        f"Pula {pot_ui} zł przechodzi do następnego pytania!"
                    ),
                },
            )
            mp_state["carryover_pot"] = pot_ui

        mp_state["current_round_pot"] = 0
        refresh_local_pot()

        # kolejne pytanie
        next_idx = q_idx + 1
        if next_idx >= len(mp_state["questions"]):
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        "To było ostatnie pytanie z tego zestawu. "
                        "Koniec gry! Dzięki za udział."
                    ),
                },
            )
            set_phase_info("Koniec zestawu pytań.", color="red")
            return

        mp_state["current_q_index"] = next_idx
        mp_state["local_phase"] = "countdown"
        mp_state["countdown_until"] = time.time() + 20.0
        mp_state["answering_player_id"] = None
        mp_state["answering_player_name"] = ""
        mp_state["current_answer_text"] = ""
        mp_state["verdict_sent"] = False

        txt_round_info.value = (
            f"Zestaw {mp_state['set_name']} – pytanie {next_idx+1} / "
            f"{len(mp_state['questions'])}"
        )
        txt_round_info.update()

        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {
                "player": "BOT",
                "message": (
                    f"Za 20 sekund rozpocznie się nowa licytacja "
                    f"o pytanie {next_idx+1}!"
                ),
            },
        )
        set_phase_info(
            "Odliczanie do kolejnej licytacji...",
            color="blue",
        )

        # wyłączamy podpowiedzi do czasu, aż będzie znów answering
        btn_hint_abcd.disabled = True
        btn_hint_5050.disabled = True
        btn_hint_abcd.update()
        btn_hint_5050.update()

    # ----------------- HANDLERY PRZYCISKÓW -----------------

    btn_join.on_click = make_async_click(mp_register)
    btn_chat_send.on_click = make_async_click(mp_send_chat)
    btn_bid.on_click = make_async_click(mp_bid_normal)
    btn_all_in.on_click = make_async_click(mp_bid_allin)
    btn_finish_bidding.on_click = make_async_click(mp_finish_bidding)

    btn_hint_abcd.on_click = make_async_click(buy_hint_abcd)
    btn_hint_5050.on_click = make_async_click(buy_hint_5050)

    page.update()


if __name__ == "__main__":
    try:
        ft.app(target=main)
    finally:
        # bezpieczne domknięcie event loop w Pyodide
        try:
            loop = asyncio.get_event_loop()
            loop.close()
        except Exception:
            pass
