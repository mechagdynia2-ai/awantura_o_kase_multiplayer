import flet as ft
import asyncio
import json
import re
import random
import warnings

import js
from js import fetch

warnings.filterwarnings("ignore")

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"


def make_async_click(async_callback):
    """
    Opakowanie handlerów asynchronicznych tak, żeby działały z Fletem w Pyodide.
    """
    def handler(e):
        async def task():
            await async_callback(e)
        e.page.run_task(task)

    return handler


async def fetch_json(url: str, method: str = "GET", body: dict | None = None):
    """
    Pomocnicza funkcja do HTTP JSON (Pyodide + fetch).
    """
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


async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Multiplayer"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    page.vertical_alignment = ft.MainAxisAlignment.START

    # -------- STAN MULTIPLAYER (tylko frontendowy) ----------

    mp_state: dict = {
        "player_id": None,
        "player_name": "",
        "is_admin": False,
        "is_observer": False,
        "joined": False,
        "phase": "bidding",
        "answering_player_id": None,
        "has_answered": False,
        "set_chosen": False,
        "last_phase": None,
        "last_round_id": None,
        "question_announce_round": None,
        "my_money": 10000,
        "players_count": 0,
        "enough_players_msg_sent": False,
    }

    # -------- KOLORY NICKÓW NA CZACIE --------

    name_color_cache: dict[str, str] = {}
    color_palette = [
        "#1e3a8a",  # dark blue
        "#4c1d95",  # dark purple
        "#064e3b",  # dark green
        "#7c2d12",  # dark brown
        "#111827",  # almost black
        "#075985",  # dark cyan
        "#7f1d1d",  # dark red
        "#374151",  # dark grey
    ]

    def name_to_color(name: str) -> str:
        if name in name_color_cache:
            return name_color_cache[name]
        idx = abs(hash(name)) % len(color_palette)
        name_color_cache[name] = color_palette[idx]
        return color_palette[idx]

    # -------------- KOMPONENTY UI ------------------

    txt_title = ft.Text(
        "AWANTURA O KASĘ – ONLINE (multiplayer)",
        size=22,
        weight=ft.FontWeight.BOLD,
    )

    txt_mp_name = ft.TextField(
        label="Twoja ksywka",
        width=220,
        dense=True,
    )
    btn_mp_join = ft.FilledButton("Dołącz do gry", width=160)
    txt_mp_status = ft.Text("", size=12, color="blue")

    txt_timer = ft.Text("Czas: -- s", size=16, weight=ft.FontWeight.BOLD)
    txt_pot = ft.Text(
        "Pula: 0 zł",
        size=16,
        weight=ft.FontWeight.BOLD,
        color="purple_700",
    )
    txt_my_money = ft.Text(
        "Twoja kasa: 10000 zł",
        size=16,
        weight=ft.FontWeight.BOLD,
        color="green_600",
    )

    txt_info = ft.Text(
        "Dołącz do gry, podając ksywkę.",
        size=13,
        color="grey_800",
    )

    # CZAT – minimalne odstępy, auto-scroll
    col_mp_chat = ft.Column(
        [],
        spacing=2,
        height=200,
        scroll=ft.ScrollMode.ALWAYS,
        auto_scroll=True,
    )

    txt_mp_chat = ft.TextField(
        hint_text="Napisz wiadomość lub (ADMIN) numer zestawu 1–50...",
        dense=True,
        text_size=13,
        border_radius=8,
        autofocus=False,
    )
    btn_mp_chat_send = ft.FilledButton(
        "Wyślij",
        width=100,
        disabled=True,
    )

    chat_box = ft.Container(
        content=ft.Column(
            [
                col_mp_chat,
                ft.Row(
                    [txt_mp_chat, btn_mp_chat_send],
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

    # Przyciski licytacji
    btn_mp_bid = ft.FilledButton(
        "Licytuj +100 zł",
        width=200,
        disabled=True,
    )
    btn_mp_finish = ft.FilledButton(
        "Kończę licytację",
        width=200,
        disabled=True,
    )
    btn_mp_allin = ft.FilledButton(
        "VA BANQUE!",
        width=200,
        disabled=True,
    )

    bid_row = ft.Column(
        [btn_mp_bid, btn_mp_finish, btn_mp_allin],
        spacing=4,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # Podpowiedzi – działają tylko dla zwycięzcy licytacji
    btn_hint_abcd = ft.OutlinedButton(
        "Kup opcje ABCD (losowo 1000–3000 zł)",
        width=260,
        disabled=True,
    )
    btn_hint_5050 = ft.OutlinedButton(
        "Kup podpowiedź 50/50 (losowo 500–2500 zł)",
        width=260,
        disabled=True,
    )

    hint_col = ft.Column(
        [btn_hint_abcd, btn_hint_5050],
        spacing=4,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # GŁÓWNY LAYOUT
    main_view = ft.Column(
        [
            txt_title,
            ft.Row([txt_mp_name, btn_mp_join], spacing=10),
            txt_mp_status,
            ft.Divider(height=8),
            ft.Row(
                [txt_timer, txt_pot, txt_my_money],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            txt_info,
            ft.Divider(height=8),
            chat_box,
            ft.Divider(height=8),
            bid_row,
            ft.Divider(height=8),
            hint_col,
        ],
        spacing=6,
    )

    page.add(main_view)
    page.update()

    # ---------------- RENDER CZATU -----------------

    def render_chat(chat_list: list[dict], players_list: list[dict]):
        admin_names = {p.get("name") for p in players_list if p.get("is_admin")}
        col_mp_chat.controls.clear()

        for m in chat_list:
            player_name = m.get("player", "?")
            msg_text = m.get("message", "")
            is_bot = player_name == "BOT"

            spans: list[ft.TextSpan] = []

            if is_bot:
                # specjalne wyróżnienie PYTANIA
                if msg_text.upper().startswith("PYTANIE:"):
                    spans.append(
                        ft.TextSpan(
                            "PYTANIE: ",
                            ft.TextStyle(
                                color="#0f172a",
                                weight=ft.FontWeight.BOLD,
                                size=13,
                            ),
                        )
                    )
                    rest = msg_text[len("PYTANIE:") :].strip()
                    spans.append(
                        ft.TextSpan(
                            f"{rest}",
                            ft.TextStyle(
                                color="#0f172a",
                                size=13,
                            ),
                        )
                    )
                else:
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

            col_mp_chat.controls.append(
                ft.Text(
                    spans=spans,
                    max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )

        col_mp_chat.update()

    # ------------- POMOCNICZE: UPDATE UI Z /state -----------

    def update_ui_from_state(data: dict):
        if not isinstance(data, dict):
            return

        round_id = data.get("round_id", 1)
        phase = data.get("phase", "bidding")
        pot = data.get("pot", 0)
        t_left = int(data.get("time_left", 0))
        answering_id = data.get("answering_player_id", None)
        players_list = data.get("players", [])
        chat_list = data.get("chat", [])

        mp_state["phase"] = phase
        mp_state["answering_player_id"] = answering_id
        mp_state["last_round_id"] = round_id
        mp_state["players_count"] = len(
            [p for p in players_list if not p.get("is_observer", False)]
        )

        # czas i pula
        txt_timer.value = f"Czas: {t_left} s"
        txt_pot.value = f"Pula: {pot} zł"

        # moja kasa – bierzemy z backendu
        my_money = mp_state.get("my_money", 10000)
        if mp_state["player_id"]:
            for p in players_list:
                if p.get("id") == mp_state["player_id"]:
                    my_money = p.get("money", my_money)
                    break
        mp_state["my_money"] = my_money
        txt_my_money.value = f"Twoja kasa: {my_money} zł"
        if my_money <= 0:
            txt_my_money.color = "red_700"
        elif my_money < 500:
            txt_my_money.color = "orange_600"
        else:
            txt_my_money.color = "green_600"

        # czat
        render_chat(chat_list, players_list)

        # komunikaty / info
        if not mp_state["joined"]:
            txt_info.value = "Dołącz do gry, podając ksywkę."
        else:
            if mp_state["is_admin"] and not mp_state["set_chosen"]:
                if mp_state["players_count"] >= 2:
                    txt_info.value = (
                        "ADMIN!!! Wpisz na czacie numer zestawu 1–50, aby rozpocząć grę."
                    )
                else:
                    txt_info.value = (
                        "Czekamy na co najmniej 2 graczy...\n"
                        "Gdy będą, ADMIN wybierze zestaw 1–50 wpisując go na czacie."
                    )
            else:
                if phase == "bidding":
                    txt_info.value = (
                        "Licytacja trwa! Użyj przycisków Licytuj / VA BANQUE / "
                        "Kończę licytację (ADMIN)."
                    )
                elif phase == "answering":
                    if (
                        mp_state["player_id"]
                        and mp_state["player_id"] == answering_id
                    ):
                        txt_info.value = (
                            "Twoja kolej na odpowiedź! Wpisz odpowiedź na czacie.\n"
                            "Możesz kupić podpowiedzi ABCD lub 50/50 (koszt idzie do puli)."
                        )
                    else:
                        txt_info.value = (
                            "Zwycięzca licytacji odpowiada na pytanie.\n"
                            "Ty poczekaj – za chwilę będzie dyskusja."
                        )
                elif phase == "discussion":
                    txt_info.value = (
                        "Dyskusja! Piszcie na czacie, co myślicie o odpowiedzi.\n"
                        "BOT za chwilę ogłosi, czy odpowiedź była poprawna."
                    )
                elif phase == "finished":
                    txt_info.value = "Gra zakończona."

        # aktywacja / blokada przycisków
        joined = mp_state["joined"]
        is_admin = mp_state["is_admin"]
        is_answering = (
            mp_state["player_id"] is not None
            and mp_state["player_id"] == answering_id
        )

        # licytacja – tylko w fazie bidding
        if joined and phase == "bidding" and not mp_state["is_observer"]:
            btn_mp_bid.disabled = False
            btn_mp_allin.disabled = False
            btn_mp_finish.disabled = not is_admin
        else:
            btn_mp_bid.disabled = True
            btn_mp_allin.disabled = True
            btn_mp_finish.disabled = True

        # podpowiedzi – tylko dla zwycięzcy licytacji, w fazie answering
        if joined and phase == "answering" and is_answering and not mp_state["has_answered"]:
            btn_hint_abcd.disabled = False
            btn_hint_5050.disabled = False
        else:
            btn_hint_abcd.disabled = True
            btn_hint_5050.disabled = True

        # czat – kto może pisać
        if not joined:
            txt_mp_chat.disabled = True
            btn_mp_chat_send.disabled = True
        else:
            if phase == "answering":
                if is_answering and not mp_state["has_answered"]:
                    # tylko zwycięzca może coś napisać (odpowiedź)
                    txt_mp_chat.disabled = False
                    btn_mp_chat_send.disabled = False
                else:
                    txt_mp_chat.disabled = True
                    btn_mp_chat_send.disabled = True
            elif phase in ("bidding", "discussion"):
                txt_mp_chat.disabled = False
                btn_mp_chat_send.disabled = False
            else:
                txt_mp_chat.disabled = True
                btn_mp_chat_send.disabled = True

        txt_timer.update()
        txt_pot.update()
        txt_my_money.update()
        txt_info.update()
        btn_mp_bid.update()
        btn_mp_allin.update()
        btn_mp_finish.update()
        btn_hint_abcd.update()
        btn_hint_5050.update()
        txt_mp_chat.update()
        btn_mp_chat_send.update()

    # ------------- HANDLERY ASYNC -------------------

    async def mp_register(e):
        name = (txt_mp_name.value or "").strip()
        if not name:
            txt_mp_status.value = "Podaj ksywkę, aby dołączyć."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        data = await fetch_json(f"{BACKEND_URL}/register", "POST", {"name": name})
        if not data or "id" not in data:
            txt_mp_status.value = "Błąd rejestracji na serwerze."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        mp_state["player_id"] = data["id"]
        mp_state["player_name"] = data.get("name", name)
        mp_state["is_admin"] = data.get("is_admin", False)
        mp_state["is_observer"] = data.get("is_observer", False)
        mp_state["joined"] = True
        mp_state["has_answered"] = False
        mp_state["set_chosen"] = False
        mp_state["enough_players_msg_sent"] = False

        role = "ADMIN" if mp_state["is_admin"] else "GRACZ"
        txt_mp_status.value = f"Dołączono jako {mp_state['player_name']} ({role})."
        txt_mp_status.color = "green"

        # po dołączeniu nie ma już potrzeby edycji ksywki
        txt_mp_name.disabled = True
        btn_mp_join.disabled = True

        txt_mp_status.update()
        txt_mp_name.update()
        btn_mp_join.update()

        # start heartbeat i polling stanu
        page.run_task(mp_heartbeat_loop)
        # mp_poll_state uruchamiamy na końcu main, nie tutaj

    async def mp_heartbeat_loop():
        while mp_state["player_id"]:
            await asyncio.sleep(10)
            pid = mp_state["player_id"]
            if not pid:
                break
            resp = await fetch_json(
                f"{BACKEND_URL}/heartbeat", "POST", {"player_id": pid}
            )
            if not resp or resp.get("status") != "ok":
                print("[HEARTBEAT] problem", resp)
                break
            mp_state["is_admin"] = resp.get("is_admin", mp_state["is_admin"])

    async def mp_send_chat(e):
        msg = (txt_mp_chat.value or "").strip()
        if not msg:
            return

        if not mp_state["joined"]:
            txt_mp_status.value = "Najpierw dołącz do gry."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        phase = mp_state.get("phase", "bidding")
        answering_id = mp_state.get("answering_player_id")
        is_answering = (
            mp_state["player_id"] is not None
            and mp_state["player_id"] == answering_id
        )

        # 1) ADMIN wybiera zestaw 1–50 (tylko jeśli jeszcze nie wybrał)
        if mp_state["is_admin"] and not mp_state["set_chosen"]:
            num_str = msg.strip()
            if re.fullmatch(r"0*\d{1,2}", num_str):
                set_no = int(num_str.lstrip("0") or "0")
                if 1 <= set_no <= 50:
                    resp = await fetch_json(
                        f"{BACKEND_URL}/select_set",
                        "POST",
                        {
                            "player_id": mp_state["player_id"],
                            "set_no": set_no,
                        },
                    )
                    if resp and resp.get("status") == "ok":
                        mp_state["set_chosen"] = True
                        mp_state["has_answered"] = False
                        mp_state["question_announce_round"] = None
                        txt_mp_status.value = f"Wybrano zestaw {set_no:02d}. Gra wystartuje!"
                        txt_mp_status.color = "green"
                    else:
                        txt_mp_status.value = "Błąd przy wyborze zestawu."
                        txt_mp_status.color = "red"
                    txt_mp_status.update()
                    txt_mp_chat.value = ""
                    txt_mp_chat.update()
                    return

        # 2) Faza ANSWERING – odpowiedź zwycięzcy licytacji
        if phase == "answering":
            if is_answering and not mp_state["has_answered"]:
                resp = await fetch_json(
                    f"{BACKEND_URL}/answer",
                    "POST",
                    {
                        "player_id": mp_state["player_id"],
                        "answer": msg,
                    },
                )
                if resp and resp.get("status") == "ok":
                    mp_state["has_answered"] = True
                    txt_mp_status.value = "Odpowiedź wysłana. BOT pyta mistrzów, co sądzą..."
                    txt_mp_status.color = "blue"
                else:
                    txt_mp_status.value = "Błąd wysyłania odpowiedzi."
                    txt_mp_status.color = "red"
                txt_mp_status.update()
                txt_mp_chat.value = ""
                txt_mp_chat.update()
                return
            else:
                # Inni nie mogą pisać w fazie odpowiedzi
                txt_mp_status.value = "Teraz odpowiada zwycięzca licytacji. Poczekaj."
                txt_mp_status.color = "red"
                txt_mp_status.update()
                txt_mp_chat.value = ""
                txt_mp_chat.update()
                return

        # 3) W pozostałych fazach – zwykła wiadomość na czacie
        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {
                "player": mp_state["player_name"] or "Anonim",
                "message": msg,
            },
        )
        txt_mp_chat.value = ""
        txt_mp_chat.update()

    async def mp_bid(kind: str):
        if not mp_state["joined"]:
            txt_mp_status.value = "Najpierw dołącz do gry."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        if mp_state["is_observer"]:
            txt_mp_status.value = "Jesteś obserwatorem – nie możesz licytować."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        if mp_state.get("phase") != "bidding":
            txt_mp_status.value = "Licytacja jest już zakończona."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        resp = await fetch_json(
            f"{BACKEND_URL}/bid",
            "POST",
            {"player_id": mp_state["player_id"], "kind": kind},
        )
        if not resp or resp.get("status") != "ok":
            detail = (
                resp.get("detail", "Błąd licytacji.")
                if isinstance(resp, dict)
                else "Błąd licytacji."
            )
            txt_mp_status.value = detail
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        # po udanej licytacji pobierz aktualny stan i wrzuć swoją kwotę na czat
        state = await fetch_json(f"{BACKEND_URL}/state", "GET")
        if state:
            update_ui_from_state(state)
            my_bid = 0
            for p in state.get("players", []):
                if p.get("id") == mp_state["player_id"]:
                    my_bid = p.get("bid", 0)
                    break
            if my_bid > 0:
                await fetch_json(
                    f"{BACKEND_URL}/chat",
                    "POST",
                    {
                        "player": mp_state["player_name"],
                        "message": f"{my_bid} zł (licytacja)",
                    },
                )

        txt_mp_status.value = "Licytacja przyjęta."
        txt_mp_status.color = "blue"
        txt_mp_status.update()

    async def mp_bid_normal(e):
        await mp_bid("normal")

    async def mp_bid_allin(e):
        await mp_bid("allin")

    async def mp_finish_bidding(e):
        if not mp_state["joined"]:
            return
        if not mp_state["is_admin"]:
            txt_mp_status.value = "Tylko ADMIN może zakończyć licytację."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        resp = await fetch_json(
            f"{BACKEND_URL}/finish_bidding",
            "POST",
            {"player_id": mp_state["player_id"]},
        )
        if not resp or resp.get("status") != "ok":
            txt_mp_status.value = "Błąd kończenia licytacji."
            txt_mp_status.color = "red"
        else:
            txt_mp_status.value = "Licytacja zakończona – za chwilę pytanie!"
            txt_mp_status.color = "blue"
        txt_mp_status.update()

    async def mp_buy_hint(kind: str):
        if not mp_state["joined"]:
            return
        if mp_state.get("phase") != "answering":
            txt_mp_status.value = "Podpowiedzi można kupować tylko podczas odpowiadania."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return
        if mp_state["player_id"] != mp_state.get("answering_player_id"):
            txt_mp_status.value = "Tylko gracz odpowiadający może kupić podpowiedź."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        resp = await fetch_json(
            f"{BACKEND_URL}/hint",
            "POST",
            {
                "player_id": mp_state["player_id"],
                "kind": kind,
            },
        )
        if not resp or resp.get("status") != "ok":
            txt_mp_status.value = "Błąd kupowania podpowiedzi."
            txt_mp_status.color = "red"
        else:
            txt_mp_status.value = "Podpowiedź kupiona – BOT opisał ją na czacie."
            txt_mp_status.color = "blue"
        txt_mp_status.update()

    async def mp_hint_abcd(e):
        await mp_buy_hint("abcd")

    async def mp_hint_5050(e):
        await mp_buy_hint("5050")

    async def mp_poll_state():
        """
        Główna pętla – co 1.5 sekundy pobieramy /state i odświeżamy UI.
        Tutaj też:
        - admin ogłasza zwycięzcę licytacji + pytanie (jeśli backend tego nie robi),
        - admin dostaje info, gdy jest >=2 graczy.
        """
        while True:
            data = await fetch_json(f"{BACKEND_URL}/state", "GET")
            if data:
                prev_phase = mp_state.get("last_phase")
                prev_round = mp_state.get("last_round_id")

                update_ui_from_state(data)

                round_id = data.get("round_id", 1)
                phase = data.get("phase", "bidding")
                answering_id = data.get("answering_player_id")
                current_question = data.get("current_question_text", None)

                # info: dołączyło 2+ graczy -> możemy zaczynać (tylko raz, tylko admin)
                if (
                    mp_state["is_admin"]
                    and mp_state["players_count"] >= 2
                    and not mp_state["set_chosen"]
                    and not mp_state["enough_players_msg_sent"]
                ):
                    await fetch_json(
                        f"{BACKEND_URL}/chat",
                        "POST",
                        {
                            "player": "BOT",
                            "message": "Dołączyło co najmniej dwóch graczy – możemy zaczynać grę multiplayer! ADMIN, wybierz zestaw wpisując numer 1–50.",
                        },
                    )
                    mp_state["enough_players_msg_sent"] = True

                # przejście z bidding -> answering: admin ogłasza zwycięzcę i pytanie
                if (
                    prev_phase == "bidding"
                    and phase == "answering"
                    and mp_state["is_admin"]
                    and round_id != mp_state.get("question_announce_round")
                    and answering_id is not None
                ):
                    winner_name = "ktoś"
                    for p in data.get("players", []):
                        if p.get("id") == answering_id:
                            winner_name = p.get("name", "ktoś")
                            break

                    await fetch_json(
                        f"{BACKEND_URL}/chat",
                        "POST",
                        {
                            "player": "BOT",
                            "message": f"Gracz {winner_name} zwyciężył licytację – oto pytanie:",
                        },
                    )

                    if current_question:
                        await fetch_json(
                            f"{BACKEND_URL}/chat",
                            "POST",
                            {
                                "player": "BOT",
                                "message": f"PYTANIE: {current_question}",
                            },
                        )

                    mp_state["question_announce_round"] = round_id
                    mp_state["has_answered"] = False  # nowa runda pytania

                mp_state["last_phase"] = phase
                mp_state["last_round_id"] = round_id

            await asyncio.sleep(1.5)

    # --------- PRZYPISANIE HANDLERÓW DO PRZYCISKÓW ------------

    btn_mp_join.on_click = make_async_click(mp_register)
    btn_mp_chat_send.on_click = make_async_click(mp_send_chat)
    btn_mp_bid.on_click = make_async_click(mp_bid_normal)
    btn_mp_allin.on_click = make_async_click(mp_bid_allin)
    btn_mp_finish.on_click = make_async_click(mp_finish_bidding)
    btn_hint_abcd.on_click = make_async_click(mp_hint_abcd)
    btn_hint_5050.on_click = make_async_click(mp_hint_5050)

    # start głównego pollingu
    page.run_task(mp_poll_state)


if __name__ == "__main__":
    try:
        ft.app(target=main)
    finally:
        try:
            loop = asyncio.get_event_loop()
            loop.close()
        except Exception:
            pass
