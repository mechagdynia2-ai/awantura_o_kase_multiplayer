import flet as ft
import random
import re
import js
from js import fetch
from thefuzz import fuzz
import warnings
import asyncio
import time

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


# ------------------- POMOCNICY HTTP ------------------------


async def fetch_text(url: str) -> str:
    try:
        response = await fetch(url)
        return await response.text()
    except Exception as e:
        print("[FETCH ERROR]", e)
        return ""


async def fetch_json(method: str, path: str, json_data=None):
    """
    Uniwersalny helper do wywoływania backendu (JSON in/out).
    """
    import json as _json

    url = f"{BACKEND_URL}{path}"
    try:
        if json_data is None:
            options = js.Object.fromEntries([["method", method]])
        else:
            payload = _json.dumps(json_data)
            options = js.Object.fromEntries(
                [
                    ["method", method],
                    [
                        "headers",
                        js.Object.fromEntries(
                            [["Content-Type", "application/json"]]
                        ),
                    ],
                    ["body", payload],
                ]
            )

        resp = await fetch(url, options)
        raw = await resp.json()
        try:
            data = raw.to_py()
        except Exception:
            data = raw
        return data
    except Exception as ex:
        print(f"[HTTP ERROR] {method} {path} ->", ex)
        return None


# ------------------- PARSER PYTAŃ ------------------------


async def parse_question_file(page: ft.Page, filename: str) -> list:
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


# ------------------- GŁÓWNA FUNKCJA APLIKACJI ------------------------


async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Singleplayer + Multiplayer (beta)"
    page.scroll = ft.ScrollMode.AUTO
    page.theme_mode = ft.ThemeMode.LIGHT
    page.vertical_alignment = ft.MainAxisAlignment.START

    # ------------------- STAN SINGLEPLAYER ------------------------
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
    }

    # ------------------- STAN MULTIPLAYER / CZAT ------------------------
    mp_state = {
        "player_id": None,
        "player_name": "",
        "is_admin": False,
        "mode": None,  # "single" | "multi"
        "multi_started": False,
        "selected_set": None,
        "last_players": {},  # pid -> name
        "last_state_time": 0.0,
    }

    # mapa kolorów dla graczy (ciemne odcienie)
    nickname_colors = {}
    dark_colors = [
        "blueGrey900",
        "deepPurple900",
        "indigo900",
        "blue900",
        "teal900",
        "cyan900",
        "purple900",
        "brown900",
    ]
    color_index = 0

    def get_nickname_color(name: str) -> str:
        nonlocal color_index
        if name not in nickname_colors:
            nickname_colors[name] = dark_colors[color_index % len(dark_colors)]
            color_index += 1
        return nickname_colors[name]

    # ------------------- KONTROLKI UI ------------------------

    txt_money = ft.Text(
        f"Twoja kasa: {game['money']} zł",
        size=16,
        weight=ft.FontWeight.BOLD,
        color="green_600",
    )
    txt_spent = ft.Text(
        "Wydano: 0 zł",
        size=12,
        color="grey_700",
        text_align=ft.TextAlign.RIGHT,
    )

    # Nagłówek: Pytanie X / XX (Zestaw XX) PULA: XXX zł
    txt_counter = ft.Text(
        "",
        size=16,
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
        "Wciśnij 'Start', aby rozpocząć grę!",
        size=18,
        weight=ft.FontWeight.BOLD,
        text_align=ft.TextAlign.CENTER,
        color="blue900",
    )

    txt_feedback = ft.Text(
        "",
        size=14,
        text_align=ft.TextAlign.CENTER,
    )

    # Pole odpowiedzi + przycisk + pasek postępu 60 s
    txt_answer = ft.TextField(
        label="Wpisz swoją odpowiedź...",
        width=400,
        text_align=ft.TextAlign.CENTER,
    )
    btn_submit_answer = ft.FilledButton(
        "Zatwierdź odpowiedź",
        icon=ft.Icons.CHECK,
        width=400,
    )
    answer_progress = ft.ProgressBar(
        width=400,
        value=0.0,
        visible=False,
    )

    answers_column = ft.Column(
        [],
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
    )
    answer_box = ft.Column(
        [txt_answer, btn_submit_answer, answer_progress, answers_column],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
        spacing=6,
    )

    # PANEL LICYTACJI SINGLEPLAYER
    btn_bid = ft.FilledButton("...", width=400)
    btn_show_question = ft.FilledButton("Pokaż pytanie", width=400)
    bidding_panel = ft.Column(
        [btn_bid, btn_show_question],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
        spacing=6,
    )

    # Przyciski pomocy
    btn_5050 = ft.OutlinedButton(
        "Kup podpowiedź 50/50 (losowo 500-2500 zł)",
        width=400,
        disabled=True,
    )
    btn_buy_abcd = ft.OutlinedButton(
        "Kup opcje ABCD (losowo 1000-3000 zł)",
        width=400,
        disabled=True,
    )

    btn_next = ft.FilledButton("Następne pytanie", width=400, visible=False)
    btn_back = ft.OutlinedButton(
        "Wróć do menu",
        icon=ft.Icons.ARROW_BACK,
        width=400,
        visible=False,
        style=ft.ButtonStyle(color="red"),
    )

    # TRYBY GRY
    btn_mode_single = ft.FilledButton(
        "Singleplayer",
        width=160,
    )
    btn_mode_multi = ft.FilledButton(
        "Multiplayer",
        width=160,
    )

    main_feedback = ft.Text("", color="red", visible=False)

    # --- CZAT (wspólny dla trybów) ---
    col_chat = ft.Column(
        [],
        height=150,  # ~ 6 linii
        scroll=ft.ScrollMode.AUTO,
        spacing=0,
    )
    txt_chat_input = ft.TextField(
        label="Napisz na czacie",
        expand=True,
        multiline=False,
    )
    btn_chat_send = ft.FilledButton("Wyślij", width=100)

    chat_container = ft.Column(
        [
            ft.Container(
                content=col_chat,
                padding=ft.padding.all(6),
                border_radius=6,
                border=ft.border.all(1, "grey300"),
            ),
            ft.Row(
                [txt_chat_input, btn_chat_send],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        ],
        spacing=4,
    )

    # --- MULTIPLAYER INFO (tylko w trybie multi) ---
    txt_mp_status = ft.Text("", size=12, color="blue")
    txt_mp_timer = ft.Text("Czas: -- s", size=14, weight=ft.FontWeight.BOLD)
    txt_mp_pot = ft.Text(
        "Pula: 0 zł",
        size=14,
        weight=ft.FontWeight.BOLD,
        color="purple",
    )

    txt_mp_name = ft.TextField(label="Twoja ksywka", width=220)
    btn_mp_join = ft.FilledButton("Dołącz do pokoju", width=180)

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

    # To pole będziemy używać głównie do wewnętrznych statusów multi
    multiplayer_info_row = ft.Row(
        [txt_mp_timer, txt_mp_pot],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    # Główny widok gry
    game_view = ft.Column(
        [
            ft.Container(
                ft.Row(
                    [txt_money, txt_spent],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=ft.padding.only(10, 6, 10, 4),
            ),
            ft.Divider(height=1, color="grey_300"),
            ft.Container(txt_counter, alignment=ft.alignment.center),
            ft.Container(txt_bonus, alignment=ft.alignment.center, padding=4),
            multiplayer_info_row,
            ft.Container(chat_container, padding=ft.padding.only(10, 4, 10, 4)),
            ft.Container(
                txt_question,
                alignment=ft.alignment.center,
                padding=ft.padding.only(20, 8, 20, 4),
            ),
            bidding_panel,
            ft.Column(
                [btn_5050, btn_buy_abcd],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
            answer_box,
            ft.Column(
                [btn_next, txt_feedback, btn_back],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
        ],
        visible=False,
        spacing=4,
    )

    # --- KAFELKI ZESTAWÓW PYTAŃ ---

    async def start_game_session(e, filename: str):
        print(f"[LOAD] Pobieram zestaw: {filename}")
        questions = await parse_question_file(page, filename)
        game["questions"] = questions
        game["total"] = len(questions)
        game["set_name"] = filename.replace(".txt", "")
        reset_game()
        main_menu.visible = False
        game_view.visible = True
        main_feedback.visible = False
        mp_state["selected_set"] = game["set_name"]

        # BOT info o zestawie (tylko w multi + admin)
        if mp_state["mode"] == "multi" and mp_state["is_admin"]:
            await mp_post_bot_message(
                f"Zestaw pytań nr: {mp_state['selected_set']} został wybrany."
            )
            await mp_post_bot_message(
                "Gra rozpocznie się za 20 sekund. Rozpoczynamy licytację!"
            )

        page.update()
        start_bidding(None)

    def menu_tile(i, color):
        filename = f"{i:02d}.txt"

        async def click(e):
            await start_game_session(e, filename)

        return ft.Container(
            content=ft.Text(
                f"{i:02d}",
                size=14,
                weight=ft.FontWeight.BOLD,
                color="black",
            ),
            width=46,
            height=46,
            alignment=ft.alignment.center,
            bgcolor=color,
            border_radius=100,
            padding=0,
            on_click=make_async_click(click),
        )

    menu_standard = [menu_tile(i, "blue_grey_50") for i in range(1, 31)]
    menu_pop = [menu_tile(i, "deep_purple_50") for i in range(31, 41)]
    menu_music = [menu_tile(i, "amber_50") for i in range(41, 51)]

    # --- EKRAN GŁÓWNY (MENU) ---
    main_menu = ft.Column(
        [
            ft.Text("Awantura o Kasę", size=26, weight="bold"),
            ft.Row([btn_mode_single, btn_mode_multi], alignment="center"),
            main_feedback,
            ft.Divider(height=15),
            ft.Text("Wybierz zestaw pytań:", size=18, weight="bold"),
            ft.Text("Pliki pobierane są bezpośrednio z GitHuba", size=12),
            ft.Row(menu_standard[:10], alignment="center", wrap=True),
            ft.Row(menu_standard[10:20], alignment="center", wrap=True),
            ft.Row(menu_standard[20:30], alignment="center", wrap=True),
            ft.Divider(height=10),
            ft.Text("Pytania popkultura:", size=18, weight="bold"),
            ft.Row(menu_pop, alignment="center", wrap=True),
            ft.Divider(height=10),
            ft.Text("Pytania popkultura + muzyka:", size=18, weight="bold"),
            ft.Row(menu_music, alignment="center", wrap=True),
        ],
        spacing=8,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=True,
    )

    page.add(main_menu, game_view)

    # ------------------- LOGIKA SINGLEPLAYER ------------------------

    answer_timer_task = None
    answer_locked = False

    def refresh_money():
        txt_money.value = f"Twoja kasa: {game['money']} zł"
        if game["money"] <= 0:
            txt_money.color = "red_700"
        elif game["money"] < game["base_stake"]:
            txt_money.color = "orange_600"
        else:
            txt_money.color = "green_600"
        page.update(txt_money)

    def refresh_spent():
        txt_spent.value = f"Wydano: {game['spent']} zł"
        page.update(txt_spent)

    def refresh_header():
        idx = game["current_question_index"] + 1
        total = game["total"]
        name = game["set_name"] or "--"
        pot = game["main_pot"]
        if idx <= 0:
            idx = 0
        txt_counter.spans = [
            ft.TextSpan(f"Pytanie {idx} / {total} (Zestaw {name}) "),
            ft.TextSpan(f"PULA: {pot} zł", weight=ft.FontWeight.BOLD),
        ]
        page.update(txt_counter)

    def refresh_bonus():
        txt_bonus.value = f"Bonus od banku: {game['bonus']} zł"
        page.update(txt_bonus)

    def show_game_over(msg: str):
        nonlocal answer_locked
        answer_locked = True
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        btn_next.disabled = True
        txt_answer.disabled = True
        btn_submit_answer.disabled = True
        answer_progress.visible = False
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
        nonlocal answer_locked
        answer_locked = True
        answer_progress.visible = False
        answer_progress.value = 0
        txt_answer.disabled = True
        btn_submit_answer.disabled = True
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        for b in answers_column.controls:
            b.disabled = True

        if not game["questions"] or game["current_question_index"] < 0:
            txt_feedback.value = "Brak pytania."
            txt_feedback.color = "red"
            page.update(txt_feedback)
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
        refresh_header()
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
            page.update(txt_feedback)
            return
        cost = random.randint(500, 2500)
        if game["money"] < cost:
            txt_feedback.value = f"Nie stać Cię ({cost} zł)"
            txt_feedback.color = "orange"
            page.update(txt_feedback)
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
        page.update()

    def buy_abcd(e):
        cost = random.randint(1000, 3000)
        if game["money"] < cost:
            txt_feedback.value = f"Nie stać Cię ({cost} zł)"
            txt_feedback.color = "orange"
            page.update(txt_feedback)
            return
        game["abcd_unlocked"] = True
        game["money"] -= cost
        game["spent"] += cost
        refresh_money()
        refresh_spent()

        txt_answer.visible = False
        btn_submit_answer.visible = False
        answer_progress.visible = False
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

    async def answer_timer():
        nonlocal answer_locked
        answer_locked = False
        answer_progress.visible = True
        answer_progress.value = 0.0
        page.update(answer_progress)

        start_ts = time.time()
        duration = 60.0
        while not answer_locked:
            elapsed = time.time() - start_ts
            if elapsed >= duration:
                break
            answer_progress.value = elapsed / duration
            page.update(answer_progress)
            await asyncio.sleep(0.2)

        if answer_locked:
            return

        # Auto-zatwierdzenie po 60 s
        check_answer(txt_answer.value)

    def start_question(e):
        nonlocal answer_timer_task, answer_locked
        game["current_question_index"] += 1
        if (
            not game["questions"]
            or game["current_question_index"] >= game["total"]
        ):
            if not game["questions"]:
                show_game_over(
                    f"Błąd: Zestaw {game['set_name']} nie zawiera pytań "
                    "w poprawnym formacie. Spróbuj innego zestawu.",
                )
            else:
                show_game_over(
                    f"Ukończyłaś zestaw {game['set_name']}!\n"
                    f"Kasa: {game['money']} zł",
                )
            return

        refresh_header()
        q = game["questions"][game["current_question_index"]]
        txt_question.value = q["question"]
        txt_question.visible = True
        bidding_panel.visible = False
        txt_bonus.visible = False
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
        txt_feedback.value = "Odpowiedz na pytanie:"
        txt_feedback.color = "black"

        answer_locked = False
        answer_progress.value = 0.0
        answer_progress.visible = True

        page.update()

        # odpal timer 60 s
        answer_timer_task = page.run_task(answer_timer())

    def bid_100(e):
        if game["bid"] >= game["max_bid"]:
            txt_feedback.value = (
                f"Osiągnięto limit licytacji ({game['max_bid']} zł)"
            )
            txt_feedback.color = "orange"
            page.update()
            btn_bid.disabled = True
            return

        cost = 100
        if game["money"] < cost:
            txt_feedback.value = "Nie masz już pieniędzy!"
            txt_feedback.color = "orange"
            page.update(txt_feedback)
            return

        game["money"] -= cost
        game["spent"] += cost
        game["main_pot"] += cost
        game["bid"] += cost

        bonus_target = (game["bid"] // 1000) * 50
        if bonus_target > game["bonus"]:
            diff = bonus_target - game["bonus"]
            game["bonus"] = bonus_target
            game["main_pot"] += diff
            txt_feedback.value = f"BONUS! Bank dorzuca {diff} zł"
            txt_feedback.color = "blue"
        else:
            txt_feedback.value = f"Wrzuciłaś {cost} zł."
            txt_feedback.color = "black"

        refresh_money()
        refresh_spent()
        refresh_header()
        refresh_bonus()
        btn_bid.text = f"Licytuj +100 zł (Suma: {game['bid']} zł)"
        if game["bid"] >= game["max_bid"]:
            btn_bid.disabled = True
        page.update()

    def start_bidding(e):
        if not game["questions"]:
            show_game_over(
                f"Zestaw {game['set_name']} nie zawiera pytań. "
                f"Wybierz inny zestaw."
            )
            return

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
        refresh_header()
        refresh_bonus()

        txt_feedback.value = f"Start! Wrzuciłaś {stake} zł."
        txt_feedback.color = "black"
        txt_question.visible = False
        answer_box.visible = False
        btn_next.visible = False
        btn_back.visible = False
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        bidding_panel.visible = True
        txt_bonus.visible = True
        btn_bid.disabled = False
        btn_bid.text = f"Licytuj +100 zł (Suma: {game['bid']} zł)"
        btn_show_question.disabled = False
        page.update()

    def reset_game():
        nonlocal answer_locked
        game["money"] = 10000
        game["current_question_index"] = -1
        game["main_pot"] = 0
        game["spent"] = 0
        game["bid"] = 0
        game["bonus"] = 0
        txt_question.value = "Rozpoczęto grę — rozpocznij licytację!"
        txt_feedback.value = "Witaj w grze!"
        txt_feedback.color = "black"
        bidding_panel.visible = False
        answer_box.visible = False
        btn_next.visible = False
        btn_back.visible = False
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        refresh_money()
        refresh_spent()
        refresh_header()
        refresh_bonus()
        answer_locked = True
        answer_progress.visible = False
        page.update()

    def back_to_menu(e):
        main_menu.visible = True
        game_view.visible = False
        btn_mode_single.visible = True
        btn_mode_multi.visible = True
        mp_state["mode"] = None
        mp_state["multi_started"] = False
        if page.dialog:
            page.dialog.open = False
        page.update()

    # ------------------- CZAT I MULTIPLAYER ------------------------

    async def mp_post_chat(player_name: str, message: str):
        txt = message.strip()
        if not txt:
            return
        payload = {"player": player_name, "message": txt}
        await fetch_json("POST", "/chat", payload)

    async def mp_post_bot_message(message: str):
        await mp_post_chat("BOT", message)

    def render_chat_line(player: str, message: str):
        """
        BOT: czarny
        ADMIN: [ADMIN] czerwone, pogrubione
        inni gracze: kolorowe (ciemne odcienie)
        """
        spans = []
        color = "black"
        weight = ft.FontWeight.NORMAL

        if player == "BOT":
            color = "black"
        else:
            # sprawdź, czy to admin (po nazwie z ostatniego stanu)
            is_admin = False
            for p in mp_state["last_players"].values():
                if p["name"] == player and p.get("is_admin"):
                    is_admin = True
                    break
            if is_admin:
                spans.append(
                    ft.TextSpan(
                        "[ADMIN] ",
                        weight=ft.FontWeight.BOLD,
                        color="red",
                    )
                )
                color = "red"
                weight = ft.FontWeight.BOLD
            else:
                color = get_nickname_color(player)

        spans.append(
            ft.TextSpan(f"{player}: ", weight=weight, color=color)
        )
        spans.append(ft.TextSpan(message))

        return ft.Text(spans=spans, size=12)

    async def mp_register(e):
        name = (txt_mp_name.value or "").strip()
        if not name:
            txt_mp_status.value = "Podaj ksywkę, aby dołączyć."
            txt_mp_status.color = "red"
            page.update(txt_mp_status)
            return

        data = await fetch_json("POST", "/register", {"name": name})
        if not data or "id" not in data:
            txt_mp_status.value = "Błąd rejestracji na serwerze."
            txt_mp_status.color = "red"
            page.update(txt_mp_status)
            return

        mp_state["player_id"] = data["id"]
        mp_state["player_name"] = data.get("name", name)
        mp_state["is_admin"] = bool(data.get("is_admin", False))

        txt_mp_status.value = (
            f"Dołączono jako {mp_state['player_name']}."
            + (" (ADMIN)" if mp_state["is_admin"] else "")
        )
        txt_mp_status.color = "green"

        # po dołączeniu: ukryj formularz join
        txt_mp_name.visible = False
        btn_mp_join.visible = False

        btn_mp_bid.disabled = False
        btn_mp_allin.disabled = False
        btn_mp_finish.disabled = False
        btn_chat_send.disabled = False

        await mp_post_bot_message(
            f"{mp_state['player_name']} dołączył do pokoju."
        )

        page.update()

    async def mp_bid(kind: str):
        if not mp_state["player_id"]:
            txt_mp_status.value = "Najpierw dołącz do pokoju!"
            txt_mp_status.color = "red"
            page.update(txt_mp_status)
            return

        data = await fetch_json(
            "POST",
            "/bid",
            {"player_id": mp_state["player_id"], "kind": kind},
        )
        if not data:
            txt_mp_status.value = "Błąd połączenia przy licytacji."
            txt_mp_status.color = "red"
            page.update(txt_mp_status)
            return

        # pobierz aktualny stan, żeby znać własną stawkę
        state = await fetch_json("GET", "/state")
        if state:
            pot = state.get("pot", 0)
            txt_mp_pot.value = f"Pula: {pot} zł"
            page.update(txt_mp_pot)

            # znajdź swoją stawkę
            my_bid = 0
            for p in state.get("players", []):
                if p.get("id") == mp_state["player_id"]:
                    my_bid = p.get("bid", 0)
                    break

            # wpis na czat o nowej stawce
            if kind == "allin":
                msg = f"{my_bid} zł (VA BANQUE!)"
            else:
                msg = f"{my_bid} zł"

            await mp_post_chat(mp_state["player_name"], msg)

        txt_mp_status.value = "Licytacja wysłana."
        txt_mp_status.color = "blue"
        page.update(txt_mp_status)

    async def mp_bid_normal(e):
        await mp_bid("normal")

    async def mp_bid_allin(e):
        await mp_bid("allin")

    async def mp_send_chat(e):
        msg = (txt_chat_input.value or "").strip()
        if not msg:
            return

        name = mp_state["player_name"] or "Anonim"
        await mp_post_chat(name, msg)
        txt_chat_input.value = ""
        page.update(txt_chat_input)

    async def mp_finish_bidding(e):
        """
        Na razie tylko komunikat na czacie – faktyczne zakończenie
        licytacji następuje po 20 s w backendzie.
        """
        if not mp_state["player_name"]:
            return
        await mp_post_bot_message(
            f"{mp_state['player_name']} zakończył licytację. "
            "Czekamy na wynik rundy."
        )

    async def heartbeat_loop():
        """
        Utrzymuje gracza przy życiu na backendzie.
        Odpowiedź mówi nam, czy jesteśmy adminem.
        """
        while True:
            await asyncio.sleep(10)
            if not mp_state["player_id"]:
                continue
            data = await fetch_json(
                "POST",
                "/heartbeat",
                {"player_id": mp_state["player_id"]},
            )
            if not data:
                continue
            mp_state["is_admin"] = bool(data.get("is_admin", False))

    async def mp_poll_state():
        """
        Pętla odświeżająca stan:
        - timer
        - pula
        - lista graczy (tylko do logiki/admina)
        - czat (wczytywany z backendu)
        - wykrywanie zniknięcia gracza (BOT: "XYZ opuścił grę")
        - BOT: instrukcje gdy liczba graczy >= 2
        """
        last_player_ids = set()
        while True:
            await asyncio.sleep(1.5)
            state = await fetch_json("GET", "/state")
            if not state:
                continue

            mp_state["last_state_time"] = time.time()

            # TIMER + PULA
            t_left = int(state.get("time_left", 0))
            pot = state.get("pot", 0)
            txt_mp_timer.value = f"Czas: {t_left} s"
            txt_mp_pot.value = f"Pula: {pot} zł"

            # gracze (do wykrywania znikania i admina)
            players = state.get("players", [])
            current_ids = set()
            current_map = {}

            for p in players:
                pid = p.get("id")
                if not pid:
                    continue
                current_ids.add(pid)
                current_map[pid] = {
                    "name": p.get("name", "?"),
                    "is_admin": p.get("is_admin", False),
                }

            # wykrywanie zniknięcia
            disappeared = last_player_ids - current_ids
            if disappeared and mp_state["is_admin"]:
                for pid in disappeared:
                    info = mp_state["last_players"].get(pid)
                    if info:
                        name = info.get("name", "?")
                        await mp_post_bot_message(f"{name} opuścił grę")

            # minimalne komunikaty BOT gdy dołączy 2 graczy
            if mp_state["is_admin"]:
                if len(last_player_ids) < 2 and len(current_ids) >= 2:
                    await mp_post_bot_message(
                        "dołączyło 2 graczy, więc możemy zaczynać grę multiplayer!"
                    )
                    await mp_post_bot_message(
                        "Aby rozpocząć grę, wybierz zestaw pytań."
                    )

            last_player_ids = current_ids
            mp_state["last_players"] = current_map

            # odśwież czat (ostatnie 30)
            chat_entries = state.get("chat", [])
            col_chat.controls.clear()
            for m in chat_entries:
                player = m.get("player", "?")
                msg = m.get("message", "")
                col_chat.controls.append(
                    render_chat_line(player, msg)
                )

            # auto-scroll
            page.update(txt_mp_timer, txt_mp_pot, col_chat)

    # ------------------- TRYBY GRY (single / multi) ------------------------

    def select_mode_single(e):
        mp_state["mode"] = "single"
        btn_mode_single.visible = False
        btn_mode_multi.visible = False
        txt_mp_name.visible = False
        btn_mp_join.visible = False
        # przyciski multi wyłączone
        btn_mp_bid.disabled = True
        btn_mp_allin.disabled = True
        btn_mp_finish.disabled = True
        btn_chat_send.disabled = False  # czat możesz mieć też w single
        page.update()

    def select_mode_multi(e):
        mp_state["mode"] = "multi"
        btn_mode_single.visible = False
        btn_mode_multi.visible = False
        txt_mp_name.visible = True
        btn_mp_join.visible = True
        btn_chat_send.disabled = True  # dopiero po join
        page.update()

    # ------------------- HANDLERY PRZYCISKÓW ------------------------

    btn_submit_answer.on_click = submit_answer
    btn_5050.on_click = hint_5050
    btn_buy_abcd.on_click = buy_abcd
    btn_show_question.on_click = start_question
    btn_bid.on_click = bid_100
    btn_next.on_click = start_bidding
    btn_back.on_click = back_to_menu

    btn_mode_single.on_click = select_mode_single
    btn_mode_multi.on_click = select_mode_multi

    btn_mp_join.on_click = make_async_click(mp_register)
    btn_mp_bid.on_click = make_async_click(mp_bid_normal)
    btn_mp_allin.on_click = make_async_click(mp_bid_allin)
    btn_mp_finish.on_click = make_async_click(mp_finish_bidding)
    btn_chat_send.on_click = make_async_click(mp_send_chat)

    # ------------------- START APLIKACJI ------------------------

    # multiplayer info w game_view (podmieniamy górę menu, gdy tylko wchodzimy w grę)
    game_view.controls.insert(
        3,
        ft.Container(
            ft.Row(
                [
                    txt_mp_status,
                    ft.Container(
                        ft.Row(
                            [txt_mp_timer, txt_mp_pot],
                            alignment=ft.MainAxisAlignment.END,
                        ),
                        expand=True,
                        alignment=ft.alignment.center_right,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=ft.padding.only(10, 4, 10, 2),
        ),
    )

    page.update()

    # Odpal pętle asynchroniczne dla multi
    page.run_task(mp_poll_state())
    page.run_task(heartbeat_loop())


if __name__ == "__main__":
    try:
        ft.app(target=main)
    finally:
        try:
            loop = asyncio.get_event_loop()
            loop.close()
        except Exception:
            pass
