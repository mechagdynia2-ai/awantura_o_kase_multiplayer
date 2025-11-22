import flet as ft
import asyncio
import js
from js import fetch
import json
import re
import random
import time
from thefuzz import fuzz
import unicodedata
from typing import Optional, List, Dict
from dataclasses import dataclass, field

# ----------------- KONFIGURACJA -----------------

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"
GITHUB_RAW_BASE_URL = "https://raw.githubusercontent.com/mechagdynia2-ai/game/main/assets/"

ENTRY_FEE = 500
BASE_ANSWER_TIME = 60
HINT_EXTRA_TIME = 30

# ----------------- ZARZĄDZANIE STANEM (State Management) -----------------

@dataclass
class GameState:
    player_id: Optional[str] = None
    player_name: str = ""
    is_admin: bool = False
    joined: bool = False
    set_name: str = ""
    questions: List[Dict] = field(default_factory=list)
    current_q_index: int = -1
    
    # Pieniądze i pula
    carryover_pot: int = 0
    current_round_pot: int = 0
    backend_pot: int = 0
    hints_extra: int = 0
    local_money: int = 10000
    
    # Fazy i czas
    local_phase: str = "idle"
    countdown_until: float = 0.0
    answer_deadline: float = 0.0
    discussion_until: float = 0.0
    
    # Rozgrywka
    answering_player_id: Optional[str] = None
    answering_player_name: str = ""
    current_answer_text: str = ""
    verdict_sent: bool = False
    current_round_id: Optional[str] = None
    bidding_fee_paid_for_round: Optional[str] = None
    abcd_bought_this_round: bool = False

# ----------------- WARSTWA SIECIOWA (Web/JS) -----------------

async def fetch_text(url: str) -> str:
    try:
        resp = await fetch(url)
        return await resp.text()
    except Exception as ex:
        print(f"[FETCH_TEXT ERROR] {ex}")
        return ""

def _build_fetch_kwargs(method: str = "GET", body: Optional[dict] = None):
    m = method.upper()
    if m == "GET":
        return js.Object.fromEntries([["method", "GET"]])
    
    payload = json.dumps(body or {})
    headers = js.Object.fromEntries([["Content-Type", "application/json"]])
    return js.Object.fromEntries([["method", m], ["headers", headers], ["body", payload]])

async def fetch_json(url: str, method: str = "GET", body: Optional[dict] = None):
    try:
        kwargs = _build_fetch_kwargs(method, body)
        resp = await fetch(url, kwargs)
        raw = await resp.json()
        try:
            return raw.to_py()
        except Exception:
            return raw
    except Exception as ex:
        print(f"[FETCH_JSON ERROR] {ex} url: {url}")
        return None

async def send_bot_message(msg: str):
    """Pomocnicza funkcja do wysyłania wiadomości bota."""
    await fetch_json(f"{BACKEND_URL}/chat", "POST", {"player": "BOT", "message": msg})

# ----------------- LOGIKA POMOCNICZA -----------------

def normalize_answer(text: Optional[str]) -> str:
    if not text:
        return ""
    s = str(text).lower().strip()
    # Szybsze mapowanie znaków
    trans = str.maketrans("ółżź.ćńśąęü", "olzzcnsaeu")
    s = s.translate(trans)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s)

_parsed_questions_cache: Dict[str, List[Dict]] = {}

async def parse_question_file(filename: str) -> List[Dict]:
    if filename in _parsed_questions_cache:
        return _parsed_questions_cache[filename]

    content = await fetch_text(f"{GITHUB_RAW_BASE_URL}{filename}")
    if not content:
        return []

    parsed: List[Dict] = []
    # Dzielimy na bloki zaczynające się od numeru pytania (np. "1.", "15.")
    blocks = re.split(r"\n(?=\d{1,3}\.)", content)
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        
        lines = block.splitlines()
        if not lines:
            continue

        # Parsowanie pytania
        first_line = lines[0].strip()
        num_match = re.match(r"^\d{1,3}\.\s*(.+)", first_line)
        if not num_match:
            continue
        question = num_match.group(1).strip()

        # Szukanie odpowiedzi w reszcie bloku
        rest = "\n".join(lines[1:])
        
        # Regex dla poprawnej odpowiedzi
        correct_match = re.search(
            r"(praw\w*\s*odpow\w*|odpowiedzi?\.?\s*prawidłowe?|prawidłowa\s*odpowiedź|correct)\s*[:=]\s*(.+)",
            rest, re.IGNORECASE | re.MULTILINE
        )
        if not correct_match:
            continue
        correct = correct_match.group(2).strip().splitlines()[0].strip()

        # Regex dla wariantów A, B, C, D
        answers = []
        valid_abcd = True
        for letter in ["A", "B", "C", "D"]:
            m = re.search(rf"\b{letter}\s*=\s*(.+?)(?:,|\n|$)", rest, re.IGNORECASE)
            if not m:
                valid_abcd = False
                break
            answers.append(m.group(1).strip())

        if valid_abcd and len(answers) == 4:
            parsed.append({
                "question": question,
                "correct": correct,
                "answers": answers,
            })

    _parsed_questions_cache[filename] = parsed
    return parsed

# ----------------- GŁÓWNA APLIKACJA -----------------

async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Multiplayer"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    
    # Inicjalizacja stanu
    state = GameState()

    # --- Kolorowanie nicków ---
    name_color_cache: Dict[str, str] = {}
    color_palette = [
        "#1e3a8a", "#4c1d95", "#064e3b", "#7c2d12", "#111827",
        "#075985", "#7f1d1d", "#374151", "#b91c1c", "#047857"
    ]

    def name_to_color(name: str) -> str:
        if name not in name_color_cache:
            idx = abs(hash(name)) % len(color_palette)
            name_color_cache[name] = color_palette[idx]
        return name_color_cache[name]

    # ----------------- UI CONTROLS -----------------
    
    txt_status = ft.Text("Podaj ksywkę i dołącz do gry.", size=13, color="blue")
    txt_local_money = ft.Text("Twoja kasa: 10000 zł", size=14, weight=ft.FontWeight.BOLD, color="green_700")
    txt_pot = ft.Text("Pula: 0 zł", size=14, weight=ft.FontWeight.BOLD, color="purple_700")
    txt_timer = ft.Text("Czas: -- s", size=14, weight=ft.FontWeight.BOLD)
    txt_round_info = ft.Text("Oczekiwanie na zestaw pytań...", size=13, color="grey_700")
    txt_phase_info = ft.Text("", size=13, color="grey_800")

    # Czat
    col_chat = ft.Column([], spacing=1, height=220, scroll=ft.ScrollMode.ALWAYS, auto_scroll=True)
    txt_chat_input = ft.TextField(label="Napisz na czacie", dense=True, disabled=True, expand=1)
    btn_chat_send = ft.FilledButton("Wyślij", disabled=True, expand=0) # Expand 0 to fix layout

    # Przyciski gry
    btn_hint_abcd = ft.OutlinedButton("Kup ABCD (1000–3000 zł)", width=220, disabled=True)
    btn_hint_5050 = ft.OutlinedButton("Kup 50/50 (500–2500 zł)", width=220, disabled=True)
    
    btn_bid = ft.FilledButton("Licytuj +100 zł", width=180, disabled=True)
    btn_finish_bidding = ft.FilledButton("Pasuję", width=180, disabled=True)
    btn_all_in = ft.FilledButton("VA BANQUE!", width=180, disabled=True)

    # Rejestracja
    txt_name = ft.TextField(label="Twoja ksywka", width=220, dense=True)
    btn_join = ft.FilledButton("Dołącz", width=150)
    join_row = ft.Row([txt_name, btn_join], alignment=ft.MainAxisAlignment.CENTER)

    # Layout główny
    layout = ft.Column([
        ft.Text("AWANTURA O KASĘ – MULTIPLAYER", size=22, weight="bold", text_align="center"),
        txt_status,
        ft.Divider(height=8),
        join_row,
        ft.Row([txt_local_money, txt_pot], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Row([txt_timer, txt_phase_info], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        txt_round_info,
        ft.Divider(height=8),
        ft.Container(
            content=ft.Column([col_chat, ft.Row([txt_chat_input, btn_chat_send])]),
            padding=5, border_radius=10, border=ft.border.all(1, "#e0e0e0"), bgcolor="white", height=280
        ),
        ft.Divider(height=8),
        ft.Column([btn_hint_abcd, btn_hint_5050], horizontal_alignment="center"),
        ft.Divider(height=8),
        ft.Row([btn_bid, btn_finish_bidding, btn_all_in], alignment=ft.MainAxisAlignment.CENTER, wrap=True),
    ], spacing=6)

    page.add(layout)

    # ----------------- AKTUALIZACJA UI -----------------

    def refresh_local_money():
        val = state.local_money
        txt_local_money.value = f"Twoja kasa: {val} zł"
        txt_local_money.color = "red_700" if val <= 0 else ("orange_700" if val < ENTRY_FEE else "green_700")
        txt_local_money.update()

    def refresh_pot_label():
        total = state.carryover_pot + state.backend_pot + state.hints_extra
        state.current_round_pot = total
        txt_pot.value = f"Pula: {total} zł"
        txt_pot.update()

    def set_phase_info(msg: str, color: str = "grey_800"):
        txt_phase_info.value = msg
        txt_phase_info.color = color
        txt_phase_info.update()

    # ----------------- LOGIKA CZATU -----------------

    async def render_chat(chat_list: list[dict], players_state: list[dict]):
        # Optymalizacja: nie renderuj jeśli nic się nie zmieniło
        if not chat_list: return
        current_len = len(col_chat.controls)
        if len(chat_list) <= current_len: 
             # Opcjonalnie: sprawdź czy ostatnia wiadomość jest taka sama, 
             # ale proste porównanie długości w tym przypadku często wystarcza
             if len(chat_list) == current_len: return

        admin_names = {p["name"] for p in players_state if p.get("is_admin")}
        col_chat.controls.clear()

        for m in chat_list:
            player_name = m.get("player", "?")
            msg_text = m.get("message", "")
            is_bot = player_name == "BOT"
            spans = []

            if is_bot:
                color = "#123499" if msg_text.startswith("PYTANIE:") else "black"
                spans.append(ft.TextSpan(f"BOT: {msg_text}", ft.TextStyle(color=color, weight="bold", size=12)))
            else:
                if player_name in admin_names:
                    spans.append(ft.TextSpan("[ADMIN] ", ft.TextStyle(color="red", weight="bold", size=12)))
                
                spans.append(ft.TextSpan(f"{player_name}: ", ft.TextStyle(color=name_to_color(player_name), weight="bold", size=12)))
                spans.append(ft.TextSpan(msg_text, ft.TextStyle(color="black", size=12)))

            col_chat.controls.append(ft.Text(spans=spans, selectable=True, size=12))
        
        col_chat.update()

    # ----------------- OBSŁUGA LOGIKI GRY -----------------

    processed_chat_ids = set()

    async def process_chat_commands(chat_list: list[dict], players_state: list[dict]):
        """Służy głównie do wykrywania wyboru zestawu przez Admina."""
        if state.set_name: return # Zestaw już wybrany

        admin_names = {p["name"] for p in players_state if p.get("is_admin")}
        if not admin_names: return

        for m in chat_list:
            ts = m.get("timestamp", 0.0)
            if ts in processed_chat_ids: continue
            processed_chat_ids.add(ts)

            player = m.get("player", "")
            msg = str(m.get("message", "")).strip()
            
            if player in admin_names and re.fullmatch(r"0?\d{1,2}", msg):
                num = int(msg)
                if 1 <= num <= 50:
                    await load_question_set(num)
                    break

    async def load_question_set(num: int):
        filename = f"{num:02d}.txt"
        questions = await parse_question_file(filename)
        
        if not questions:
            await send_bot_message(f"Błąd: nie udało się załadować zestawu {filename}.")
            return

        if state.is_admin:
            await fetch_json(f"{BACKEND_URL}/select_set", "POST", {"player_id": state.player_id, "set_no": num})

        # Reset stanu gry pod nowy zestaw
        state.set_name = str(num)
        state.questions = questions
        state.current_q_index = 0
        state.carryover_pot = 0
        state.local_phase = "countdown"
        state.countdown_until = time.time() + 20.0
        state.verdict_sent = False
        
        refresh_pot_label()
        
        txt_round_info.value = f"Pytanie 1 / {len(questions)} (Zestaw {num})"
        txt_round_info.update()

        await send_bot_message(f"Wybrano zestaw {num}. Za 20 sekund start licytacji!")
        set_phase_info("Odliczanie do licytacji...", "blue")

    # ----------------- AKCJE UŻYTKOWNIKA -----------------

    async def on_join(e):
        name = txt_name.value.strip()
        if not name:
            txt_status.value = "Podaj ksywkę!"
            txt_status.update()
            return

        res = await fetch_json(f"{BACKEND_URL}/register", "POST", {"name": name})
        if not res or "id" not in res:
            txt_status.value = "Błąd serwera przy rejestracji."
            txt_status.update()
            return

        state.player_id = res["id"]
        state.player_name = res.get("name", name)
        state.is_admin = res.get("is_admin", False)
        state.joined = True
        state.local_money = 10000

        join_row.visible = False
        txt_chat_input.disabled = False
        btn_chat_send.disabled = False
        
        txt_status.value = f"Witaj {state.player_name}!" + (" (ADMIN)" if state.is_admin else "")
        txt_status.color = "green"
        txt_status.update()
        
        refresh_local_money()
        page.run_task(game_loop)

    async def on_send_chat(e):
        msg = txt_chat_input.value.strip()
        if not msg: return

        # Logika odpowiadania na pytanie
        if state.local_phase == "answering_wait":
             if state.player_id != state.answering_player_id:
                 set_phase_info("Cicho! Teraz odpowiada zwycięzca licytacji.", "red")
                 return
             elif not state.current_answer_text:
                 # To jest odpowiedź gracza na pytanie
                 state.current_answer_text = msg
                 state.local_phase = "discussion"
                 state.discussion_until = time.time() + 20.0
                 await send_bot_message(f"Gracz {state.player_name} odpowiada: {msg}. A wy jak myślicie? (20s na dyskusję)")
                 set_phase_info("Czas na dyskusję...", "blue")

        # Wysyłka standardowa
        await fetch_json(f"{BACKEND_URL}/chat", "POST", {"player": state.player_name, "message": msg})
        txt_chat_input.value = ""
        txt_chat_input.focus()
        txt_chat_input.update()

    async def on_bid(e, kind="normal"):
        if not state.player_id: return
        cost = 100 if kind == "normal" else state.local_money
        
        if state.local_money < cost and kind == "normal":
            return # Brak kasy

        res = await fetch_json(f"{BACKEND_URL}/bid", "POST", {"player_id": state.player_id, "kind": kind})
        if res and res.get("status") == "ok":
            state.local_money = 0 if kind == "allin" else (state.local_money - cost)
            refresh_local_money()

    async def on_finish_bidding(e):
        if not state.player_id: return
        await fetch_json(f"{BACKEND_URL}/finish_bidding", "POST", {"player_id": state.player_id})
        txt_status.value = "Zakończyłeś licytację."
        txt_status.update()

    async def on_buy_hint(e, hint_type="abcd"):
        if state.local_phase not in ("answering_wait", "discussion") or state.player_id != state.answering_player_id:
            return # Nie twoja kolej

        cost = random.randint(1000, 3000) if hint_type == "abcd" else random.randint(500, 2500)
        
        if hint_type == "5050" and not state.abcd_bought_this_round:
            set_phase_info("Najpierw kup ABCD!", "red")
            return

        if state.local_money < cost:
            set_phase_info(f"Za mało kasy ({cost} zł)", "red")
            return

        state.local_money -= cost
        state.hints_extra += cost
        state.answer_deadline += HINT_EXTRA_TIME
        if hint_type == "abcd": state.abcd_bought_this_round = True
        
        refresh_local_money()
        refresh_pot_label()

        # Generowanie treści podpowiedzi
        q = state.questions[state.current_q_index]
        if hint_type == "abcd":
            msg = f"Opcje: A) {q['answers'][0]}, B) {q['answers'][1]}, C) {q['answers'][2]}, D) {q['answers'][3]}"
        else:
            # 50/50
            wrong = [a for a in q['answers'] if a != q['correct']]
            random.shuffle(wrong)
            msg = f"To na pewno NIE jest: {wrong[0]} ani {wrong[1]}"

        await send_bot_message(f"Podpowiedź {hint_type.upper()} (koszt {cost} zł): {msg}")

    # ----------------- PĘTLA GRY (Heartbeat + State Polling) -----------------

    async def detect_verdict_logic():
        """Logika sprawdzania odpowiedzi - wykonuje ją ADMIN lokalnie."""
        if not state.is_admin or not (0 <= state.current_q_index < len(state.questions)):
            return

        q = state.questions[state.current_q_index]
        user_ans = normalize_answer(state.current_answer_text)
        correct_ans = normalize_answer(q["correct"])
        ratio = fuzz.ratio(user_ans, correct_ans)
        
        win_amt = state.current_round_pot
        winner = state.answering_player_name

        if ratio >= 80:
            # Sukces - aktualizujemy kasę u zwycięzcy (lokalnie u admina, ale trzeba by to wysłać do backendu w idealnym świecie)
            if winner == state.player_name:
                state.local_money += win_amt
                refresh_local_money()
            
            await send_bot_message(f"TAK! Odpowiedź prawidłowa ({ratio}%). {winner} zgarnia {win_amt} zł!")
            state.carryover_pot = 0
        else:
            # Porażka
            await send_bot_message(f"NIE! Prawidłowa to: {q['correct']}. Pula przechodzi dalej.")
            state.carryover_pot = win_amt

        # Przejście do następnego
        next_idx = state.current_q_index + 1
        if next_idx >= len(state.questions):
            await send_bot_message("Koniec zestawu! Dzięki za grę.")
            state.local_phase = "idle"
            return

        state.current_q_index = next_idx
        state.local_phase = "countdown"
        state.countdown_until = time.time() + 20.0
        state.current_answer_text = ""
        state.verdict_sent = False
        state.abcd_bought_this_round = False
        state.hints_extra = 0
        
        txt_round_info.value = f"Pytanie {next_idx+1} / {len(state.questions)}"
        txt_round_info.update()
        await send_bot_message("Za 20 sekund start kolejnej licytacji!")


    async def game_loop():
        """Jedna główna pętla odświeżająca stan."""
        while state.joined:
            # 1. Pobranie stanu z serwera
            data = await fetch_json(f"{BACKEND_URL}/state")
            if data:
                state.current_round_id = data.get("round_id")
                backend_phase = data.get("phase")
                state.backend_pot = data.get("pot", 0)
                
                # Synchronizacja czasu i graczy
                txt_timer.value = f"Czas serwera: {int(data.get('time_left', 0))} s"
                txt_timer.update()
                
                await render_chat(data.get("chat", []), data.get("players", []))
                await process_chat_commands(data.get("chat", []), data.get("players", []))
                refresh_pot_label()

                # Heartbeat admina
                my_player_data = next((p for p in data.get("players", []) if p["id"] == state.player_id), None)
                if my_player_data:
                    state.is_admin = my_player_data.get("is_admin", False)
                    # Ping (heartbeat)
                    await fetch_json(f"{BACKEND_URL}/heartbeat", "POST", {"player_id": state.player_id})

                # ---------------- LOGIKA FAZ ----------------
                now = time.time()

                # A. Odliczanie przed licytacją
                if state.local_phase == "countdown":
                    remain = int(state.countdown_until - now)
                    set_phase_info(f"Licytacja za: {max(0, remain)} s", "blue")
                    
                    if remain <= 0:
                        state.local_phase = "bidding"
                        if state.is_admin:
                            await fetch_json(f"{BACKEND_URL}/next_round", "POST", {})
                            await send_bot_message("START LICYTACJI!")

                # B. Licytacja
                elif state.local_phase == "bidding":
                    # Włącz przyciski
                    is_bidding = (backend_phase == "bidding")
                    btn_bid.disabled = not is_bidding
                    btn_all_in.disabled = not is_bidding
                    btn_finish_bidding.disabled = not is_bidding
                    btn_bid.update()
                    btn_all_in.update()
                    btn_finish_bidding.update()

                    # Pobranie wpisowego
                    if state.current_round_id and state.bidding_fee_paid_for_round != state.current_round_id:
                        state.local_money -= ENTRY_FEE
                        state.bidding_fee_paid_for_round = state.current_round_id
                        refresh_local_money()

                    # Przejście serwera w fazę answering -> u nas answering_wait
                    if backend_phase == "answering":
                        state.local_phase = "answering_wait"
                        state.answering_player_id = data.get("answering_player_id")
                        state.answer_deadline = now + BASE_ANSWER_TIME
                        
                        # Znajdź nazwę zwycięzcy
                        winner_name = next((p["name"] for p in data.get("players", []) if p["id"] == state.answering_player_id), "?")
                        state.answering_player_name = winner_name
                        
                        # Wyświetl pytanie (robi to admin, żeby nie spamować)
                        if state.is_admin and 0 <= state.current_q_index < len(state.questions):
                            q_text = state.questions[state.current_q_index]["question"]
                            await send_bot_message(f"Gracz {winner_name} wygrał! PYTANIE: {q_text}")

                        # Odblokuj podpowiedzi tylko dla wygranego
                        im_winner = (state.player_id == state.answering_player_id)
                        btn_hint_abcd.disabled = not im_winner
                        btn_hint_5050.disabled = not im_winner
                        btn_hint_abcd.update()
                        btn_hint_5050.update()

                # C. Czekanie na odpowiedź (Answering Wait)
                elif state.local_phase == "answering_wait":
                    remain = int(state.answer_deadline - now)
                    set_phase_info(f"Odpowiada {state.answering_player_name}: {max(0, remain)} s", "green")
                    
                    # Koniec czasu na odpowiedź
                    if remain <= 0 and not state.current_answer_text:
                        state.current_answer_text = "" # Brak odpowiedzi
                        state.local_phase = "discussion"
                        state.discussion_until = now + 20.0
                        if state.is_admin:
                            await send_bot_message("Czas minął! Brak odpowiedzi. Dyskusja 20s.")

                # D. Dyskusja
                elif state.local_phase == "discussion":
                    remain = int(state.discussion_until - now)
                    set_phase_info(f"Dyskusja: {max(0, remain)} s", "orange")
                    
                    if remain <= 0 and not state.verdict_sent:
                        state.verdict_sent = True
                        state.local_phase = "verdict"
                        if state.is_admin:
                            await detect_verdict_logic()

            await asyncio.sleep(1.0)

    # ----------------- PODPIĘCIE ZDARZEŃ -----------------

    # W Flet można podpinać funkcje async bezpośrednio
    btn_join.on_click = on_join
    txt_name.on_submit = on_join
    
    txt_chat_input.on_submit = on_send_chat
    btn_chat_send.on_click = on_send_chat
    
    btn_bid.on_click = lambda e: page.run_task(on_bid, "normal")
    btn_all_in.on_click = lambda e: page.run_task(on_bid, "allin")
    btn_finish_bidding.on_click = lambda e: page.run_task(on_finish_bidding)
    
    btn_hint_abcd.on_click = lambda e: page.run_task(on_buy_hint, "abcd")
    btn_hint_5050.on_click = lambda e: page.run_task(on_buy_hint, "5050")

    page.update()

if __name__ == "__main__":
    ft.app(target=main)
