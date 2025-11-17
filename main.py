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


# -------------------- POMOCNICZE FETCH'E --------------------


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


# -------------------- PYTANIA Z GITHUBA --------------------


async def parse_question_file(page: ft.Page, filename: str) -> list[dict]:
    url = f"{GITHUB_RAW_BASE_URL}{filename}"
    print(f"[FETCH] Pobieram: {url}")
    content = await fetch_text(url)
    if not content:
        print(f"[FETCH ERROR] {filename}: brak danych")
        return []

    parsed: list[dict] = []
    blocks = re.split(r"\n(?=\d{1,3}\.)", content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        q_match = re.match(r"^\d{1,3}\.\s*(.+)", block)
        if not q_match:
            print("[WARNING] Nie znaleziono pytania:", block[:50])
            continue
        question = q_match.group(1).strip()

        correct_match = re.search(
            r"prawidłowa\s+odpowied[zź]\s*=\s*(.+)",
            block,
            re.IGNORECASE,
        )
        if not correct_match:
            print("[WARNING] Brak prawidłowej odpowiedzi:", block[:50])
            continue
        correct = correct_match.group(1).strip()

        answers_match = re.search(
            r"odpowied[zź]\s*abcd\s*=\s*A\s*=\s*(.+?),\s*B\s*=\s*(.+?),\s*C\s*=\s*(.+?),\s*D\s*=\s*(.+)",
            block,
            re.IGNORECASE,
        )
        if not answers_match:
            print("[WARNING] Brak ABCD:", block[:50])
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

    return parsed


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


# -------------------- FRONTEND MAIN --------------------


async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Singleplayer + Multiplayer"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    page.vertical_alignment = ft.MainAxisAlignment.START

    # -------------------- STAN SINGLEPLAYER --------------------
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

    # -------------------- STAN MULTIPLAYER --------------------
    mp_state = {
        "player_id": None,
        "player_name": "",
        "is_admin": False,
        "is_observer": False,
        "joined": False,
    }

    # mapowanie nazwa -> kolor (ciemne odcienie)
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

    # -------------------- KOMPONENTY SINGLEPLAYER --------------------

    txt_money = ft.Text(
        "Twoja kasa: 10000 zł",
        size=16,
        weight=ft.FontWeight.BOLD,
        color="green_600",
    )
    txt_spent = ft.Text(
        "Wydano: 0 zł",
        size=14,
        color="grey_700",
        text_align=ft.TextAlign.RIGHT,
    )
    txt_counter = ft.Text(
        "Pytanie 0 / 0 (Zestaw --)",
        size=15,
        color="grey_800",
        text_align=ft.TextAlign.CENTER,
    )
    txt_pot = ft.Text(
        "PULA: 0 zł",
        size=18,
        weight=ft.FontWeight.BOLD,
        color="purple_700",
        text_align=ft.TextAlign.CENTER,
    )
    txt_bonus = ft.Text(
        "Bonus od banku: 0 zł",
        size=14,
        color="blue_600",
        text_align=ft.TextAlign.CENTER,
        visible=False,
    )

    txt_question = ft.Text(
        "Wybierz tryb gry: Singleplayer lub Multiplayer",
        size=18,
        weight=ft.FontWeight.BOLD,
        text_align=ft.TextAlign.CENTER,
        color="#0f172a",  # dark navy
    )

    txt_feedback = ft.Text(
        "",
        size=14,
        text_align=ft.TextAlign.CENTER,
    )

    # okienko czatu (multiplayer)
    col_mp_chat = ft.Column(
        [],
        spacing=2,
        height=140,
        scroll=ft.ScrollMode.ALWAYS,
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
                ft.Text("Czat (multiplayer):", size=12, color="grey_700"),
                col_mp_chat,
                ft.Row(
                    [
                        txt_mp_chat,
                        btn_mp_chat_send,
                    ],
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

    txt_answer = ft.TextField(
        label="Wpisz swoją odpowiedź...",
        width=400,
        text_align=ft.TextAlign.CENTER,
        dense=True,
    )
    btn_submit_answer = ft.FilledButton(
        "Zatwierdź odpowiedź",
        icon=ft.Icons.CHECK,
        width=400,
    )
    pb_answer_timer = ft.ProgressBar(width=400, visible=False)

    answers_column = ft.Column(
        [],
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
    )
    answer_box = ft.Column(
        [txt_answer, btn_submit_answer, pb_answer_timer, answers_column],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=6,
        visible=False,
    )

    btn_5050 = ft.OutlinedButton(
        "Kup podpowiedź 50/50 (losowo 500–2500 zł)",
        width=260,
        disabled=True,
    )
    btn_buy_abcd = ft.OutlinedButton(
        "Kup opcje ABCD (losowo 1000–3000 zł)",
        width=260,
        disabled=True,
    )
    btn_next = ft.FilledButton(
        "Następne pytanie",
        width=260,
        visible=False,
    )
    btn_back = ft.OutlinedButton(
        "Wróć do menu",
        icon=ft.Icons.ARROW_BACK,
        width=260,
        visible=False,
        style=ft.ButtonStyle(color="red"),
    )

    single_controls = ft.Row(
        [btn_5050, btn_buy_abcd],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
    )

    single_bottom_controls = ft.Row(
        [btn_next, btn_back],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
    )

    # -------------------- KOMPONENTY MULTIPLAYER --------------------

    txt_mp_title = ft.Text(
        "Tryb multiplayer (beta)",
        size=20,
        weight=ft.FontWeight.BOLD,
    )
    txt_mp_info = ft.Text(
        "Po dołączeniu ADMIN wybiera zestaw pytań i startuje wspólną licytację.\n"
        "Licytacja: +100 zł, „Kończę licytację”, VA BANQUE!",
        size=12,
        color="grey_700",
    )

    txt_mp_name = ft.TextField(
        label="Twoja ksywka",
        width=220,
        dense=True,
    )
    btn_mp_join = ft.FilledButton("Dołącz do pokoju", width=180)
    txt_mp_status = ft.Text("", size=12, color="blue")

    txt_mp_timer = ft.Text(
        "Czas: -- s",
        size=16,
        weight=ft.FontWeight.BOLD,
    )
    txt_mp_pot = ft.Text(
        "Pula (multiplayer): 0 zł",
        size=16,
        weight=ft.FontWeight.BOLD,
        color="purple",
    )

    btn_mp_bid = ft.FilledButton(
        "Licytuj +100 zł (multiplayer)",
        width=220,
        disabled=True,
    )
    btn_mp_finish = ft.FilledButton(
        "Kończę licytację",
        width=220,
        disabled=True,
    )
    btn_mp_allin = ft.FilledButton(
        "VA BANQUE!",
        width=220,
        disabled=True,
    )

    mp_buttons_row = ft.Column(
        [btn_mp_bid, btn_mp_finish, btn_mp_allin],
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # -------------------- MENU GŁÓWNE --------------------

    main_feedback = ft.Text("", color="red", visible=False)

    btn_mode_single = ft.FilledButton(
        "Tryb SINGLEPLAYER",
        width=220,
    )
    btn_mode_multi = ft.FilledButton(
        "Tryb MULTIPLAYER",
        width=220,
    )

    mode_row = ft.Row(
        [btn_mode_single, btn_mode_multi],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=20,
    )

    # kafelki zestawów – single / admin multi
    async def start_game_session_single(filename: str):
        print(f"[LOAD SINGLE] Pobieram zestaw: {filename}")
        questions = await parse_question_file(page, filename)
        game["questions"] = questions
        game["total"] = len(questions)
        game["set_name"] = filename.replace(".txt", "")
        reset_game()
        main_menu.visible = False
        game_view.visible = True
        multiplayer_view.visible = False
        main_feedback.visible = False
        single_set_selector.visible = False
        page.update()
        start_bidding_single()

    async def mp_select_question_set(filename: str):
        if not mp_state["is_admin"]:
            txt_mp_status.value = "Tylko ADMIN może wybierać zestaw."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {
                "player": "BOT",
                "message": f"Zestaw pytań nr: {filename.replace('.txt', '')} został wybrany.",
            },
        )
        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {
                "player": "BOT",
                "message": "Gra rozpocznie się – odliczanie na czacie (20 s).",
            },
        )

        questions = await parse_question_file(page, filename)
        game["questions"] = questions
        game["total"] = len(questions)
        game["set_name"] = filename.replace(".txt", "")

        main_menu.visible = False
        multiplayer_view.visible = True
        game_view.visible = True
        page.update()

        await fetch_json(f"{BACKEND_URL}/next_round", "POST", {})

    def menu_tile(i: int, color: str, for_multiplayer: bool = False):
        filename = f"{i:02d}.txt"

        async def click(e):
            if for_multiplayer:
                await mp_select_question_set(filename)
            else:
                await start_game_session_single(filename)

        return ft.Container(
            content=ft.Text(
                f"{i:02d}",
                size=14,
                weight=ft.FontWeight.BOLD,
                color="black",
            ),
            width=40,
            height=40,
            alignment=ft.alignment.center,
            bgcolor=color,
            border_radius=50,
            padding=0,
            on_click=make_async_click(click),
        )

    menu_standard = [menu_tile(i, "blue_grey_50") for i in range(1, 31)]
    menu_pop = [menu_tile(i, "deep_purple_50") for i in range(31, 41)]
    menu_music = [menu_tile(i, "amber_50") for i in range(41, 51)]

    mp_menu_standard = [
        menu_tile(i, "blue_grey_100", for_multiplayer=True)
        for i in range(1, 31)
    ]
    mp_menu_pop = [
        menu_tile(i, "deep_purple_100", for_multiplayer=True)
        for i in range(31, 41)
    ]
    mp_menu_music = [
        menu_tile(i, "amber_100", for_multiplayer=True)
        for i in range(41, 51)
    ]

    single_set_selector = ft.Column(
        [
            ft.Text(
                "Wybierz zestaw pytań (singleplayer):",
                size=16,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Row(menu_standard[:10], alignment="center", wrap=True),
            ft.Row(menu_standard[10:20], alignment="center", wrap=True),
            ft.Row(menu_standard[20:30], alignment="center", wrap=True),
            ft.Divider(height=10),
            ft.Text("Popkultura:", size=15, weight=ft.FontWeight.BOLD),
            ft.Row(menu_pop, alignment="center", wrap=True),
            ft.Divider(height=10),
            ft.Text(
                "Popkultura + muzyka:",
                size=15,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Row(menu_music, alignment="center", wrap=True),
        ],
        spacing=8,
        visible=False,
    )

    mp_set_selector = ft.Column(
        [
            ft.Text(
                "ADMIN: wybierz zestaw pytań (multiplayer):",
                size=14,
                weight=ft.FontWeight.BOLD,
                color="red",
            ),
            ft.Row(mp_menu_standard[:10], alignment="center", wrap=True),
            ft.Row(mp_menu_standard[10:20], alignment="center", wrap=True),
            ft.Row(mp_menu_standard[20:30], alignment="center", wrap=True),
            ft.Divider(height=8),
            ft.Text("Popkultura:", size=13, weight=ft.FontWeight.BOLD),
            ft.Row(mp_menu_pop, alignment="center", wrap=True),
            ft.Divider(height=8),
            ft.Text(
                "Popkultura + muzyka:",
                size=13,
                weight=ft.FontWeight.BOLD,
            ),
            ft.Row(mp_menu_music, alignment="center", wrap=True),
        ],
        spacing=6,
        visible=False,
    )

    # widok gry
    game_view = ft.Column(
        [
            ft.Container(
                ft.Row(
                    [txt_money, txt_spent],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=ft.padding.only(left=16, right=16, top=8, bottom=4),
            ),
            ft.Divider(height=1, color="grey_300"),
            ft.Container(
                content=ft.Row(
                    [txt_counter, txt_pot],
                    alignment=ft.MainAxisAlignment.SPACE_AROUND,
                ),
                padding=ft.padding.only(left=8, right=8, top=6, bottom=4),
            ),
            ft.Container(txt_bonus, alignment=ft.alignment.center, padding=4),
            chat_box,
            ft.Container(
                txt_question,
                alignment=ft.alignment.center,
                padding=ft.padding.only(
                    left=16, right=16, top=10, bottom=4
                ),
            ),
            answer_box,
            ft.Container(
                single_controls,
                alignment=ft.alignment.center,
                padding=ft.padding.only(top=6, bottom=6),
            ),
            ft.Container(
                txt_feedback,
                alignment=ft.alignment.center,
                padding=4,
            ),
            ft.Container(
                single_bottom_controls,
                alignment=ft.alignment.center,
                padding=ft.padding.only(top=6, bottom=10),
            ),
        ],
        visible=False,
        spacing=6,
    )

    # widok multiplayer – nad częścią gry
    mp_join_row = ft.Row(
        [txt_mp_name, btn_mp_join],
        alignment=ft.MainAxisAlignment.START,
        spacing=10,
    )

    mp_header = ft.Column(
        [
            txt_mp_title,
            txt_mp_info,
            mp_join_row,
            txt_mp_status,
            ft.Row([txt_mp_timer, txt_mp_pot], alignment="spaceBetween"),
            mp_buttons_row,
            ft.Divider(),
            mp_set_selector,
        ],
        spacing=6,
    )

    multiplayer_view = ft.Column(
        [
            mp_header,
            game_view,
        ],
        visible=False,
        spacing=8,
    )

    main_menu = ft.Column(
        [
            ft.Text("AWANTURA O KASĘ", size=26, weight=ft.FontWeight.BOLD),
            ft.Text(
                "Wersja: Singleplayer + Multiplayer (beta)",
                size=14,
                color="grey_700",
            ),
            ft.Divider(height=10),
            mode_row,
            ft.Divider(height=10),
            main_feedback,
            single_set_selector,
        ],
        spacing=8,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=True,
    )

    # -------------------- FUNKCJE SINGLEPLAYER --------------------

    def refresh_money():
        txt_money.value = f"Twoja kasa: {game['money']} zł"
        if game["money"] <= 0:
            txt_money.color = "red_700"
        elif game["money"] < game["base_stake"]:
            txt_money.color = "orange_600"
        else:
            txt_money.color = "green_600"
        txt_money.update()

    def refresh_spent():
        txt_spent.value = f"Wydano: {game['spent']} zł"
        txt_spent.update()

    def refresh_pot():
        txt_pot.value = f"PULA: {game['main_pot']} zł"
        txt_pot.update()

    def refresh_bonus():
        txt_bonus.value = f"Bonus od banku: {game['bonus']} zł"
        txt_bonus.update()

    def refresh_counter():
        idx = game["current_question_index"] + 1
        total = game["total"]
        name = game["set_name"]
        txt_counter.value = f"Pytanie {idx} / {total} (Zestaw {name})"
        txt_counter.update()

    def show_game_over(msg: str):
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        btn_next.disabled = True
        txt_answer.disabled = True
        btn_submit_answer.disabled = True
        for b in answers_column.controls:
            b.disabled = True
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Koniec gry!"),
            content=ft.Text(msg),
            actions=[
                ft.TextButton(
                    "Wróć do menu",
                    on_click=lambda e: back_to_menu(e),
                )
            ],
        )
        page.dialog = dlg
        dlg.open = True
        page.update()

    def check_answer(user_answer: str):
        game["answer_submitted"] = True
        txt_answer.disabled = True
        btn_submit_answer.disabled = True
        pb_answer_timer.visible = False
        pb_answer_timer.update()

        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        for b in answers_column.controls:
            b.disabled = True

        if not game["questions"]:
            return
        q = game["questions"][game["current_question_index"]]
        correct = q["correct"]
        pot = game["main_pot"]

        norm_user = normalize_answer(user_answer)
        norm_correct = normalize_answer(correct)
        similarity = fuzz.ratio(norm_user, norm_correct)

        if similarity >= 80:
            game["money"] += pot
            game["main_pot"] = 0
            txt_feedback.value = (
                f"DOBRZE! ({similarity}%) +{pot} zł\nPoprawna: {correct}"
            )
            txt_feedback.color = "green"
        else:
            txt_feedback.value = (
                f"ŹLE ({similarity}%) – pula przechodzi dalej.\n"
                f"Poprawna: {correct}"
            )
            txt_feedback.color = "red"

        game["bid"] = 0
        game["bonus"] = 0

        refresh_money()
        refresh_pot()
        refresh_bonus()

        btn_next.visible = True
        btn_back.visible = True
        page.update()

    def submit_answer(e):
        check_answer(txt_answer.value)

    def abcd_click(e):
        check_answer(e.control.data)

    def hint_5050(e):
        if not game["abcd_unlocked"]:
            txt_feedback.value = "50/50 działa tylko po kupnie ABCD!"
            txt_feedback.color = "orange"
            txt_feedback.update()
            return
        cost = random.randint(500, 2500)
        if game["money"] < cost:
            txt_feedback.value = f"Nie stać Cię ({cost} zł)"
            txt_feedback.color = "orange"
            txt_feedback.update()
            return

        game["money"] -= cost
        game["spent"] += cost
        refresh_money()
        refresh_spent()

        q = game["questions"][game["current_question_index"]]
        correct = q["correct"]
        wrong = [a for a in q["answers"] if a != correct]
        random.shuffle(wrong)
        to_disable = wrong[:2]
        for b in answers_column.controls:
            if b.data in to_disable:
                b.disabled = True
                b.opacity = 0.3
                b.on_click = None
                b.update()

        txt_feedback.value = (
            f"Usunięto 2 błędne odpowiedzi! (koszt {cost} zł)"
        )
        txt_feedback.color = "blue"
        txt_feedback.update()

    def buy_abcd(e):
        cost = random.randint(1000, 3000)
        if game["money"] < cost:
            txt_feedback.value = f"Nie stać Cię ({cost} zł)"
            txt_feedback.color = "orange"
            txt_feedback.update()
            return

        game["abcd_unlocked"] = True
        game["money"] -= cost
        game["spent"] += cost
        refresh_money()
        refresh_spent()

        txt_answer.visible = False
        btn_submit_answer.visible = False
        answers_column.visible = True
        btn_buy_abcd.disabled = True
        btn_5050.disabled = False

        q = game["questions"][game["current_question_index"]]
        answers_column.controls.clear()
        shuffled = q["answers"][:]
        random.shuffle(shuffled)
        for ans in shuffled:
            answers_column.controls.append(
                ft.FilledButton(
                    ans,
                    width=400,
                    data=ans,
                    on_click=abcd_click,
                )
            )

        txt_feedback.value = f"Kupiono ABCD (koszt {cost} zł)"
        txt_feedback.color = "blue"
        page.update()

    async def answer_timeout():
        pb_answer_timer.visible = True
        pb_answer_timer.value = 0
        pb_answer_timer.update()
        steps = 60
        for i in range(steps):
            if game["answer_submitted"]:
                return
            pb_answer_timer.value = (i + 1) / steps
            pb_answer_timer.update()
            await asyncio.sleep(1)
        if not game["answer_submitted"]:
            check_answer(txt_answer.value)

    def start_question(e):
        game["current_question_index"] += 1
        if not game["questions"] or game["current_question_index"] >= game[
            "total"
        ]:
            if not game["questions"]:
                show_game_over(
                    f"Błąd: Zestaw {game['set_name']} nie zawiera pytań "
                    "w poprawnym formacie. Spróbuj innego zestawu."
                )
            else:
                show_game_over(
                    f"Ukończyłaś zestaw {game['set_name']}!\n"
                    f"Kasa: {game['money']} zł"
                )
            return

        refresh_counter()
        q = game["questions"][game["current_question_index"]]
        txt_question.value = q["question"]
        txt_question.visible = True

        answer_box.visible = True
        txt_answer.visible = True
        txt_answer.disabled = False
        txt_answer.value = ""
        btn_submit_answer.visible = True
        btn_submit_answer.disabled = False

        btn_buy_abcd.disabled = False
        btn_5050.disabled = True
        game["abcd_unlocked"] = False
        answers_column.visible = False
        answers_column.controls.clear()

        game["answer_submitted"] = False

        txt_feedback.value = "Odpowiedz na pytanie:"
        txt_feedback.color = "black"
        page.update()

        page.run_task(answer_timeout)

    def start_bidding_single():
        stake = game["base_stake"]
        if game["money"] < stake:
            show_game_over(
                f"Nie masz {stake} zł na rozpoczęcie gry!"
            )
            return

        game["money"] -= stake
        game["spent"] += stake
        game["main_pot"] = stake
        game["bid"] = stake
        game["bonus"] = 0

        refresh_money()
        refresh_spent()
        refresh_pot()
        refresh_bonus()

        txt_feedback.value = f"Start! Wrzuciłaś {stake} zł."
        txt_feedback.color = "black"
        txt_question.value = "Rozpoczęto grę — rozpocznij odpowiadanie."
        txt_question.update()

        btn_next.visible = False
        btn_back.visible = False
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True

        start_question(None)

    def reset_game():
        game["money"] = 10000
        game["current_question_index"] = -1
        game["main_pot"] = 0
        game["spent"] = 0
        game["bid"] = 0
        game["bonus"] = 0
        game["answer_submitted"] = False
        txt_question.value = "Rozpoczęto grę — rozpocznij licytację!"
        txt_feedback.value = "Witaj w grze!"
        txt_feedback.color = "black"
        answer_box.visible = False
        btn_next.visible = False
        btn_back.visible = False
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        refresh_money()
        refresh_spent()
        refresh_pot()
        refresh_bonus()
        page.update()

    def back_to_menu(e):
        main_menu.visible = True
        single_set_selector.visible = False
        game_view.visible = False
        multiplayer_view.visible = False
        if page.dialog:
            page.dialog.open = False
        page.update()

    # -------------------- FUNKCJE MULTIPLAYER --------------------

    def render_chat_from_state(chat_list: list[dict], players_state: list[dict]):
       admin_names = {p["name"] for p in players_state if p.get("is_admin")}
       col_mp_chat.controls.clear()

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
                        size=12
                    )
                )
            )
            spans.append(
                ft.TextSpan(
                    msg_text,
                    ft.TextStyle(color="black", size=12)
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
                            size=12
                        )
                    )
                )

                name_color = name_to_color(player_name)
                spans.append(
                ft.TextSpan(
                    f"{player_name}: ",
                    ft.TextStyle(
                        color=name_color,
                        weight=ft.FontWeight.BOLD,
                        size=12
                    )
                )
            )

                spans.append(
                    ft.TextSpan(
                        msg_text,
                        ft.TextStyle(color="black", size=12)
                )
            )

                col_mp_chat.controls.append(
                ft.Text(
                   spans=spans,
                    max_lines=3,
                   overflow=ft.TextOverflow.ELLIPSIS
            )
        )

       col_mp_chat.update()


    async def mp_register(e):
        name = (txt_mp_name.value or "").strip()
        if not name:
            txt_mp_status.value = "Podaj ksywkę, aby dołączyć."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        data = await fetch_json(
            f"{BACKEND_URL}/register", "POST", {"name": name}
        )
        if not data or "id" not in data:
            txt_mp_status.value = "Błąd rejestracji."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        mp_state["player_id"] = data["id"]
        mp_state["player_name"] = data.get("name", name)
        mp_state["is_admin"] = data.get("is_admin", False)
        mp_state["is_observer"] = data.get("is_observer", False)
        mp_state["joined"] = True

        txt_mp_status.value = f"Dołączono jako {mp_state['player_name']}."
        if mp_state["is_admin"]:
            txt_mp_status.value += " (ADMIN)"
        txt_mp_status.color = "green"

        mp_join_row.visible = False
        btn_mp_chat_send.disabled = False
        btn_mp_bid.disabled = False
        btn_mp_finish.disabled = False
        btn_mp_allin.disabled = False

        if mp_state["is_admin"]:
            mp_set_selector.visible = True

        page.update()

        page.run_task(mp_heartbeat_loop)
        page.run_task(mp_poll_state)

    async def mp_heartbeat_loop():
        while mp_state["player_id"]:
            await asyncio.sleep(10)
            pid = mp_state["player_id"]
            if not pid:
                return
            resp = await fetch_json(
                f"{BACKEND_URL}/heartbeat", "POST", {"player_id": pid}
            )
            if not resp or resp.get("status") != "ok":
                print("[HEARTBEAT] problem", resp)
                return
            mp_state["is_admin"] = resp.get("is_admin", mp_state["is_admin"])

    async def mp_send_chat(e):
        msg = (txt_mp_chat.value or "").strip()
        if not msg:
            return
        name = mp_state["player_name"] or "Anonim"
        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {"player": name, "message": msg},
        )
        txt_mp_chat.value = ""
        txt_mp_chat.update()

    async def mp_bid(kind: str):
        if not mp_state["player_id"]:
            txt_mp_status.value = "Najpierw dołącz do pokoju."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return
        if mp_state["is_observer"]:
            txt_mp_status.value = "Jesteś obserwatorem, nie możesz licytować."
            txt_mp_status.color = "red"
            txt_mp_status.update()
            return

        resp = await fetch_json(
            f"{BACKEND_URL}/bid",
            "POST",
            {"player_id": mp_state["player_id"], "kind": kind},
        )
        if not resp or resp.get("status") != "ok":
            txt_mp_status.value = (
                resp.get("detail", "Błąd licytacji.")
                if isinstance(resp, dict)
                else "Błąd licytacji."
            )
            txt_mp_status.color = "red"
        else:
            txt_mp_status.value = "Licytacja OK."
            txt_mp_status.color = "blue"
            pot = resp.get("pot", 0)
            txt_mp_pot.value = f"Pula (multiplayer): {pot} zł"
        txt_mp_status.update()
        txt_mp_pot.update()

    async def mp_bid_normal(e):
        await mp_bid("normal")

    async def mp_bid_allin(e):
        await mp_bid("allin")

    async def mp_finish_bidding(e):
        if not mp_state["player_id"]:
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
            txt_mp_status.value = "Licytacja zakończona."
            txt_mp_status.color = "blue"
        txt_mp_status.update()

    async def mp_poll_state():
        while True:
            data = await fetch_json(f"{BACKEND_URL}/state", "GET")
            if not data:
                await asyncio.sleep(1.5)
                continue

            t_left = int(data.get("time_left", 0))
            txt_mp_timer.value = f"Czas: {t_left} s"
            txt_mp_pot.value = f"Pula (multiplayer): {data.get('pot', 0)} zł"

            players_list = data.get("players", [])
            chat_list = data.get("chat", [])

            render_chat_from_state(chat_list, players_list)

            if mp_state["joined"]:
                multiplayer_view.visible = True
                game_view.visible = True

            page.update()
            await asyncio.sleep(1.5)

    # -------------------- HANDLERY PRZYCISKÓW --------------------

    btn_submit_answer.on_click = submit_answer
    btn_5050.on_click = hint_5050
    btn_buy_abcd.on_click = buy_abcd
    btn_next.on_click = start_question
    btn_back.on_click = back_to_menu

    def mode_single_click(e):
        main_menu.visible = True
        single_set_selector.visible = True
        multiplayer_view.visible = False
        game_view.visible = False
        page.update()

    def mode_multi_click(e):
        main_menu.visible = False
        single_set_selector.visible = False
        multiplayer_view.visible = True
        game_view.visible = True
        page.update()
        page.run_task(mp_poll_state)

    btn_mode_single.on_click = mode_single_click
    btn_mode_multi.on_click = mode_multi_click

    btn_mp_join.on_click = make_async_click(mp_register)
    btn_mp_chat_send.on_click = make_async_click(mp_send_chat)
    btn_mp_bid.on_click = make_async_click(mp_bid_normal)
    btn_mp_finish.on_click = make_async_click(mp_finish_bidding)
    btn_mp_allin.on_click = make_async_click(mp_bid_allin)

    # -------------------- START STRONY --------------------

    page.add(main_menu, multiplayer_view)
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
