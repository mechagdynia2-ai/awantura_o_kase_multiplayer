import flet as ft
import random
import re
import json
import asyncio
import js
from js import fetch
from thefuzz import fuzz
import warnings

warnings.filterwarnings("ignore")

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"
GITHUB_RAW_BASE_URL = (
    "https://raw.githubusercontent.com/mechagdynia2-ai/game/main/assets/"
)


def make_async_click(async_callback):
    def handler(e):
        async def task():
            await async_callback(e)
        e.page.run_task(task)
    return handler


# ============================================================
#  FETCH HELPERS
# ============================================================

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
            kwargs = js.Object.fromEntries([
                ["method", method.upper()],
                ["headers", js.Object.fromEntries([["Content-Type", "application/json"]])],
                ["body", payload],
            ])
        resp = await fetch(url, kwargs)
        raw = await resp.json()
        try:
            return raw.to_py()
        except Exception:
            return raw
    except Exception as ex:
        print("[FETCH_JSON ERROR]", ex)
        return None


# ============================================================
#   PARSOWANIE PLIKÓW PYTAŃ
# ============================================================

async def parse_question_file(page: ft.Page, filename: str) -> list[dict]:
    url = f"{GITHUB_RAW_BASE_URL}{filename}"
    print(f"[FETCH] Pobieram: {url}")
    content = await fetch_text(url)
    if not content:
        print(f"[FETCH ERROR] {filename}: brak danych")
        return []

    parsed = []
    blocks = re.split(r"\n(?=\d{1,3}\.)", content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        q_match = re.match(r"^\d{1,3}\.\s*(.+)", block)
        if not q_match:
            continue
        question = q_match.group(1).strip()

        correct_match = re.search(
            r"prawidłowa\s+odpowied[zź]\s*=\s*(.+)",
            block,
            re.IGNORECASE,
        )
        if not correct_match:
            continue
        correct = correct_match.group(1).strip()

        answers_match = re.search(
            r"odpowied[zź]\s*abcd\s*=\s*A\s*=\s*(.+?),\s*B\s*=\s*(.+?),\s*C\s*=\s*(.+?),\s*D\s*=\s*(.+)",
            block,
            re.IGNORECASE,
        )
        if not answers_match:
            continue

        a = answers_match.group(1).strip()
        b = answers_match.group(2).strip()
        c = answers_match.group(3).strip()
        d = answers_match.group(4).strip()

        parsed.append({
            "question": question,
            "correct": correct,
            "answers": [a, b, c, d],
        })

    return parsed


def normalize_answer(text: str) -> str:
    text = str(text).lower().strip()
    repl = {
        "ó": "o", "ł": "l", "ż": "z", "ź": "z",
        "ć": "c", "ń": "n", "ś": "s", "ą": "a",
        "ę": "e", "ü": "u",
    }
    for c, r in repl.items():
        text = text.replace(c, r)
    text = text.replace("u", "o")
    return "".join(text.split())


# ============================================================
#   FRONTEND MAIN
# ============================================================

async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Multiplayer"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    page.vertical_alignment = ft.MainAxisAlignment.START

    # --------------------------------------------------------
    #   STAN SINGLEPLAYER
    # --------------------------------------------------------
    game = {
        "money": 10000,
        "current_question_index": -1,
        "base_stake": 500,
        "abcd_unlocked": False,
        "main_pot": 0,
        "spent": 0,
        "bid": 0,
        "bonus": 0,
        "max_bid": 5000,
        "questions": [],
        "total": 0,
        "set_name": "",
        "answer_submitted": False,
    }

    # --------------------------------------------------------
    #   STAN MULTIPLAYER
    # --------------------------------------------------------
    mp_state = {
        "player_id": None,
        "player_name": "",
        "is_admin": False,
        "is_observer": False,
        "joined": False,
    }

    # --------------------------------------------------------
    #   KOLORY GRACZY
    # --------------------------------------------------------
    name_color_cache = {}
    color_palette = [
        "#1e3a8a", "#4c1d95", "#064e3b", "#7c2d12",
        "#111827", "#075985", "#7f1d1d", "#374151",
    ]

    def name_to_color(name: str) -> str:
        if name in name_color_cache:
            return name_color_cache[name]
        idx = abs(hash(name)) % len(color_palette)
        name_color_cache[name] = color_palette[idx]
        return color_palette[idx]

    # --------------------------------------------------------
    #   CZAT (AUTO SCROLL)
    # --------------------------------------------------------
    col_mp_chat = ft.Column(
        [],
        spacing=2,
        height=160,
        scroll=ft.ScrollMode.ALWAYS,
        auto_scroll=True,     # AUTO SCROLL NA DOLE
    )

    txt_mp_chat = ft.TextField(
        label="Napisz na czacie",
        multiline=False,
        dense=True,
        border_radius=8,
        text_size=13,
    )
    btn_mp_chat_send = ft.FilledButton(
        "Wyślij",
        width=120,
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
        padding=6,
        border_radius=10,
        border=ft.border.all(1, "#cccccc"),
        bgcolor="white",
    )


    # ============================================================
    #   UI – ELEMENTY GRY
    # ============================================================

    txt_money = ft.Text(
        "Twoja kasa: 10000 zł",
        size=15,
        weight=ft.FontWeight.BOLD,
        color="green_600",
    )
    txt_spent = ft.Text(
        "Wydano: 0 zł",
        size=12,
        color="grey_700",
        text_align=ft.TextAlign.RIGHT,
    )

    txt_counter = ft.Text(
        "",
        size=15,
        weight=ft.FontWeight.BOLD,
        text_align=ft.TextAlign.CENTER,
    )
    txt_pot = ft.Text(
        "",
        size=18,
        weight=ft.FontWeight.BOLD,
        color="purple_700",
        text_align=ft.TextAlign.CENTER,
    )
    txt_bonus = ft.Text("", size=12, color="blue_600", visible=False)

    txt_question = ft.Text(
        "",
        size=17,
        weight=ft.FontWeight.BOLD,
        color="#0f172a",
        text_align=ft.TextAlign.CENTER,
    )

    txt_feedback = ft.Text("", size=14, text_align=ft.TextAlign.CENTER)

    # INPUT ODPOWIEDZI
    txt_answer = ft.TextField(
        label="Wpisz swoją odpowiedź...",
        width=350,
        text_align=ft.TextAlign.CENTER,
        dense=True,
    )
    btn_submit_answer = ft.FilledButton(
        "Zatwierdź odpowiedź",
        width=350,
    )
    pb_answer_timer = ft.ProgressBar(width=350, visible=False)

    answers_column = ft.Column([], spacing=6, visible=False)

    answer_box = ft.Column(
        [txt_answer, btn_submit_answer, pb_answer_timer, answers_column],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
    )

    # PRZYCISKI PODPOWIEDZI — TERAZ POD CZATEM (JEDEN POD DRUGIM)
    btn_5050 = ft.OutlinedButton(
        "Kup podpowiedź 50/50",
        width=180,
        disabled=True,
    )
    btn_buy_abcd = ft.OutlinedButton(
        "Kup opcje ABCD",
        width=180,
        disabled=True,
    )

    help_buttons = ft.Column(
        [
            btn_5050,
            btn_buy_abcd,
        ],
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    btn_next = ft.FilledButton("Następne pytanie", visible=False)
    btn_back = ft.OutlinedButton(
        "Wróć do menu",
        icon=ft.Icons.ARROW_BACK,
        visible=False,
        style=ft.ButtonStyle(color="red"),
    )

    # ============================================================
    #   PRZYCISKI MULTIPLAYER (LICYTACJA)
    # ============================================================

    txt_mp_name = ft.TextField(
        label="Twoja ksywka",
        width=220,
        dense=True,
    )
    btn_mp_join = ft.FilledButton("Dołącz do gry", width=180)
    txt_mp_status = ft.Text("", size=12, color="blue")

    txt_mp_timer = ft.Text("Czas: -- s", size=16, weight=ft.FontWeight.BOLD)
    txt_mp_pot = ft.Text("Pula: 0 zł", size=16, weight=ft.FontWeight.BOLD)

    btn_mp_bid = ft.FilledButton("Licytuj +100 zł", width=200, disabled=True)
    btn_mp_finish = ft.FilledButton("Kończę licytację", width=200, disabled=True)
    btn_mp_allin = ft.FilledButton("VA BANQUE!", width=200, disabled=True)

    mp_buttons = ft.Column(
        [btn_mp_bid, btn_mp_finish, btn_mp_allin],
        spacing=4,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    mp_join_row = ft.Row(
        [txt_mp_name, btn_mp_join],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
    )

    # ============================================================
    #   WIDOK GRY
    # ============================================================

    game_view = ft.Column(
        [
            ft.Row(
                [txt_money, txt_spent],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Row(
                [txt_counter, txt_pot],
                alignment=ft.MainAxisAlignment.SPACE_AROUND,
            ),
            chat_box,
            help_buttons,
            txt_question,
            answer_box,
            txt_feedback,
            ft.Row([btn_next, btn_back], alignment=ft.MainAxisAlignment.CENTER),
        ],
        visible=False,
        spacing=6,
    )

    # ============================================================
    #   MENU
    # ============================================================

    main_menu = ft.Column(
        [
            ft.Text(
                "AWANTURA O KASĘ",
                size=26,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Divider(height=10),
            ft.Row(
                [
                    ft.FilledButton("SINGLEPLAYER", width=180),
                    ft.FilledButton("MULTIPLAYER", width=180),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        ],
        visible=True,
        spacing=8,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ============================================================
    #   FUNKCJE SINGLEPLAYER
    # ============================================================

    def refresh_money():
        txt_money.value = f"Twoja kasa: {game['money']} zł"
        if game["money"] <= 0:
            txt_money.color = "red"
        else:
            txt_money.color = "green_600"
        txt_money.update()

    def refresh_spent():
        txt_spent.value = f"Wydano: {game['spent']} zł"
        txt_spent.update()

    def refresh_pot():
        txt_pot.value = f"PULA: {game['main_pot']} zł"
        txt_pot.update()

    def refresh_counter():
        idx = game["current_question_index"] + 1
        total = game["total"]
        name = game["set_name"]
        txt_counter.value = f"Pytanie {idx}/{total} (Zestaw {name})"
        txt_counter.update()

    def check_answer(user_answer: str):
        game["answer_submitted"] = True
        txt_answer.disabled = True
        btn_submit_answer.disabled = True
        pb_answer_timer.visible = False
        pb_answer_timer.update()

        btn_5050.disabled = True
        btn_buy_abcd.disabled = True

        if not game["questions"]:
            return

        q = game["questions"][game["current_question_index"]]
        correct = q["correct"]
        pot = game["main_pot"]

        sim = fuzz.ratio(normalize_answer(user_answer), normalize_answer(correct))

        if sim >= 80:
            game["money"] += pot
            game["main_pot"] = 0
            txt_feedback.value = f"DOBRZE! +{pot} zł\nPoprawna: {correct}"
            txt_feedback.color = "green"
        else:
            txt_feedback.value = f"ŹLE – pula przechodzi dalej\nPoprawna: {correct}"
            txt_feedback.color = "red"

        game["bid"] = 0
        game["bonus"] = 0

        refresh_money()
        refresh_pot()

        btn_next.visible = True
        btn_back.visible = True
        page.update()

    def submit_answer(e):
        check_answer(txt_answer.value)

    async def answer_timeout():
        pb_answer_timer.visible = True
        pb_answer_timer.value = 0
        pb_answer_timer.update()
        for i in range(60):
            if game["answer_submitted"]:
                return
            pb_answer_timer.value = (i + 1) / 60
            pb_answer_timer.update()
            await asyncio.sleep(1)
        if not game["answer_submitted"]:
            check_answer(txt_answer.value)

    def start_question(e):
        game["current_question_index"] += 1

        if not game["questions"] or game["current_question_index"] >= game["total"]:
            txt_question.value = "Koniec zestawu!"
            txt_feedback.value = f"Wynik końcowy: {game['money']} zł"
            btn_next.visible = False
            btn_back.visible = True
            page.update()
            return

        refresh_counter()
        q = game["questions"][game["current_question_index"]]
        txt_question.value = q["question"]

        txt_answer.value = ""
        txt_answer.disabled = False
        btn_submit_answer.disabled = False
        answer_box.visible = True

        answers_column.visible = False
        answers_column.controls.clear()
        game["abcd_unlocked"] = False
        btn_5050.disabled = True
        btn_buy_abcd.disabled = False

        game["answer_submitted"] = False
        txt_feedback.value = "Odpowiedz..."
        txt_feedback.color = "black"

        page.update()

        page.run_task(answer_timeout)

    def start_bidding_single():
        stake = game["base_stake"]
        if game["money"] < stake:
            txt_question.value = f"Potrzeba {stake} zł na start."
            return

        game["money"] -= stake
        game["spent"] += stake
        game["main_pot"] = stake

        refresh_money()
        refresh_spent()
        refresh_pot()

        start_question(None)

    def reset_game():
        game["money"] = 10000
        game["current_question_index"] = -1
        game["main_pot"] = 0
        game["spent"] = 0
        game["bonus"] = 0
        txt_question.value = ""
        txt_feedback.value = ""
        page.update()

    def back_to_menu(e):
        main_menu.visible = True
        game_view.visible = False
        page.update()

    # ============================================================
    #   FUNKCJE MULTIPLAYER
    # ============================================================

    def render_chat_from_state(chat_list, players_state):
        col_mp_chat.controls.clear()

        admin_names = {p["name"] for p in players_state if p.get("is_admin")}

        for m in chat_list:
            name = m.get("player", "?")
            msg = m.get("message", "")
            spans = []

            # BOT
            if name == "BOT":
                spans.append(ft.TextSpan("BOT: ", ft.TextStyle(color="black", weight=ft.FontWeight.BOLD)))
                spans.append(ft.TextSpan(msg, ft.TextStyle(color="black")))
            else:
                is_admin = name in admin_names
                if is_admin:
                    spans.append(ft.TextSpan("[ADMIN] ", ft.TextStyle(color="red", weight=ft.FontWeight.BOLD)))

                spans.append(ft.TextSpan(f"{name}: ", ft.TextStyle(color=name_to_color(name), weight=ft.FontWeight.BOLD)))
                spans.append(ft.TextSpan(msg, ft.TextStyle(color="black")))

            col_mp_chat.controls.append(ft.Text(spans=spans, size=12))

        col_mp_chat.update()

    # ---------------------------------------------------------

    async def mp_register(e):
        name = (txt_mp_name.value or "").strip()
        if not name:
            txt_mp_status.value = "Podaj ksywkę."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        data = await fetch_json(f"{BACKEND_URL}/register", "POST", {"name": name})

        if not data or "id" not in data:
            txt_mp_status.value = "Błąd rejestracji."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        mp_state["player_id"] = data["id"]
        mp_state["player_name"] = data["name"]
        mp_state["joined"] = True
        mp_state["is_admin"] = data.get("is_admin", False)
        mp_state["is_observer"] = data.get("is_observer", False)

        txt_mp_status.value = f"Dołączono jako {name}"
        txt_mp_status.color = "green"

        btn_mp_chat_send.disabled = False

        mp_join_row.visible = False
        game_view.visible = True

        if mp_state["is_admin"]:
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {"player": "BOT", "message": "Dołączono ADMINA. Wpisz numer zestawu 01–50 aby rozpocząć grę."},
            )

        page.update()

        page.run_task(mp_poll_state)
        page.run_task(mp_heartbeat_loop)

    # ---------------------------------------------------------

    async def mp_heartbeat_loop():
        while mp_state["player_id"]:
            await asyncio.sleep(10)
            resp = await fetch_json(
                f"{BACKEND_URL}/heartbeat",
                "POST",
                {"player_id": mp_state["player_id"]},
            )
            if not resp or resp.get("status") != "ok":
                return
            mp_state["is_admin"] = resp.get("is_admin", mp_state["is_admin"])

    # ---------------------------------------------------------

    async def mp_send_chat(e):
        msg = (txt_mp_chat.value or "").strip()
        if not msg:
            return

        # ADMIN WYBIERA ZESTAW — akceptujemy "7", "07", "007"
        if mp_state["is_admin"]:
            if re.fullmatch(r"\d{1,3}", msg):
                num = int(msg)  # konwersja 007 → 7
                if 1 <= num <= 50:
                    filename = f"{num:02d}.txt"

                    await fetch_json(
                        f"{BACKEND_URL}/chat",
                        "POST",
                        {"player": "BOT", "message": f"Wybrano zestaw {filename}"},
                    )

                    questions = await parse_question_file(page, filename)

                    game["questions"] = questions
                    game["total"] = len(questions)
                    game["set_name"] = filename

                    await fetch_json(f"{BACKEND_URL}/next_round", "POST", {})

                    txt_mp_chat.value = ""
                    txt_mp_chat.update()
                    return

        # normalna wiadomość
        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {"player": mp_state["player_name"], "message": msg},
        )
        txt_mp_chat.value = ""
        txt_mp_chat.update()

    # ---------------------------------------------------------

    async def mp_bid(kind: str):
        data = await fetch_json(
            f"{BACKEND_URL}/bid",
            "POST",
            {"player_id": mp_state["player_id"], "kind": kind},
        )
        if not data or data.get("status") != "ok":
            txt_mp_status.value = f"Błąd: {data}"
            txt_mp_status.color = "red"
        else:
            txt_mp_status.value = "Licytacja OK."
            txt_mp_status.color = "blue"
        txt_mp_status.update()

    async def mp_finish_bidding(e):
        _ = await fetch_json(
            f"{BACKEND_URL}/finish_bidding",
            "POST",
            {"player_id": mp_state["player_id"]},
        )
        txt_mp_status.value = "Zakończono."
        txt_mp_status.color = "blue"
        txt_mp_status.update()

    async def mp_bid_normal(e):
        await mp_bid("normal")

    async def mp_bid_allin(e):
        await mp_bid("allin")

    # ---------------------------------------------------------

    async def mp_poll_state():
        while True:
            data = await fetch_json(f"{BACKEND_URL}/state")
            if not data:
                await asyncio.sleep(1.5)
                continue

            players = data.get("players", [])
            chat = data.get("chat", [])

            # timer + pula
            txt_mp_timer.value = f"Czas: {int(data.get('time_left',0))} s"
            txt_mp_pot.value = f"Pula: {data.get('pot',0)} zł"

            # aktualizacja czatu
            render_chat_from_state(chat, players)

            page.update()
            await asyncio.sleep(1.5)

    # ============================================================
    #   HANDLERY PRZYCISKÓW
    # ============================================================

    btn_submit_answer.on_click = submit_answer
    btn_5050.on_click = lambda e: None
    btn_buy_abcd.on_click = lambda e: None
    btn_next.on_click = start_question
    btn_back.on_click = back_to_menu

    btn_mp_join.on_click = make_async_click(mp_register)
    btn_mp_chat_send.on_click = make_async_click(mp_send_chat)
    btn_mp_bid.on_click = make_async_click(mp_bid_normal)
    btn_mp_finish.on_click = make_async_click(mp_finish_bidding)
    btn_mp_allin.on_click = make_async_click(mp_bid_allin)

    # MODE SELECT
    def mode_single_click(e):
        main_menu.visible = False
        # singleplayer removed for this version
        pass

    def mode_multi_click(e):
        main_menu.visible = False
        game_view.visible = True
        page.update()

    main_menu.controls[2].controls[0].on_click = mode_single_click
    main_menu.controls[2].controls[1].on_click = mode_multi_click

    # ============================================================
    #   START
    # ============================================================

    page.add(main_menu, game_view)
    page.update()


if __name__ == "__main__":
    try:
        ft.app(target=main)
    finally:
        try:
            loop = asyncio.get_event_loop()
            loop.close()
        except Exception:
            pass
