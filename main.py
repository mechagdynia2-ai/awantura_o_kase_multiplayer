import flet as ft
import asyncio
import json
import re
from dataclasses import dataclass

try:
    import js
    from js import fetch
except ImportError:
    js = None
    fetch = None

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"

@dataclass
class GameState:
    player_id: str = None
    player_name: str = ""
    is_admin: bool = False
    joined: bool = False
    
    local_money: int = 10000
    local_phase: str = "idle"
    
    answering_player_id: str = None
    
    # Do obsługi dźwięków i czatu
    last_chat_ts: float = 0.0
    last_audio_ts: float = 0.0  # Osobny timestamp dla dźwięków
    timer_alert_played: bool = False # Żeby dźwięk 5s zagrał tylko raz na fazę
    
    server_abcd_bought: bool = False

# Paleta kolorów dla graczy (wizualna)
PLAYER_COLORS = [
    "#2E7D32", "#C62828", "#AD1457", "#6A1B9A", 
    "#283593", "#0277BD", "#00695C", "#558B2F", 
    "#9E9D24", "#EF6C00", "#D84315", "#4E342E", 
    "#424242", "#37474F"
]

def get_player_color(name: str) -> str:
    return PLAYER_COLORS[abs(hash(name)) % len(PLAYER_COLORS)]

# Mapowanie gracza na plik dźwiękowy (player01 - player06)
def get_player_sound_file(name: str) -> str:
    # Zwraca "player01.wav" do "player06.wav"
    idx = (abs(hash(name)) % 6) + 1
    return f"player0{idx}.wav"

async def fetch_json(url: str, method: str = "GET", body: dict = None):
    if not js: return None
    try:
        opts = js.Object.fromEntries([["method", method]])
        if body:
            opts.headers = js.Object.fromEntries([["Content-Type", "application/json"]])
            opts.body = json.dumps(body)
        resp = await fetch(url, opts)
        data = await resp.json()
        return data.to_py()
    except Exception as e:
        print(f"Err {url}: {e}")
        return None

async def main(page: ft.Page):
    page.title = "Awantura o Kasę"
    page.theme_mode = ft.ThemeMode.LIGHT
    state = GameState()

    # --- AUDIO SYSTEM ---
    # Tworzymy obiekty Audio dla każdego pliku
    sounds = {}
    
    def play_sound(key: str):
        if key in sounds:
            # Resetujemy i gramy
            try:
                sounds[key].seek(0)
                sounds[key].play()
            except Exception:
                pass # Ignoruj błędy audio w przeglądarce

    # Dodajemy audio do overlay
    # 1. Gracze
    for i in range(1, 7):
        fname = f"player0{i}.wav"
        snd = ft.Audio(src=f"/{fname}", autoplay=False)
        page.overlay.append(snd)
        sounds[fname] = snd
    
    # 2. Systemowe
    sys_files = {
        "question": "question.wav",
        "abcd": "questionABCD.wav",
        "5050": "question50.wav",
        "bot": "bot.wav",
        "out_of_time": "out_of_time.wav"
    }
    for k, v in sys_files.items():
        snd = ft.Audio(src=f"/{v}", autoplay=False)
        page.overlay.append(snd)
        sounds[k] = snd

    # --- UI Elements ---
    txt_money = ft.Text("Kasa: ---", color="green", weight="bold")
    txt_pot = ft.Text("Pula: 0 zł", color="purple", weight="bold")
    txt_timer = ft.Text("-- s", size=16, weight="bold")
    
    chat_col = ft.Column(scroll="auto", auto_scroll=True, height=300)
    input_chat = ft.TextField(hint_text="Wiadomość...", expand=True, disabled=True, dense=True)
    btn_send = ft.FilledButton("Wyślij", disabled=True)
    
    btn_bid = ft.FilledButton("+100", disabled=True, expand=1)
    btn_pass = ft.ElevatedButton("Pas", disabled=True, expand=1)
    btn_allin = ft.FilledButton("VA BANQUE", style=ft.ButtonStyle(bgcolor="red"), disabled=True, expand=1)
    
    btn_abcd = ft.OutlinedButton("ABCD", disabled=True, expand=1)
    btn_5050 = ft.OutlinedButton("50/50", disabled=True, expand=1)
    
    input_name = ft.TextField(label="Nick", expand=True)
    btn_join = ft.FilledButton("Dołącz")
    row_login = ft.Row([input_name, btn_join], alignment="center")

    layout = ft.Column([
        ft.Text("AWANTURA O KASĘ", size=20, weight="bold", text_align="center"),
        row_login,
        ft.Divider(height=5),
        ft.Row([txt_money, txt_pot, txt_timer], alignment="spaceBetween"),
        ft.Container(
            content=ft.Column([chat_col, ft.Row([input_chat, btn_send])]),
            border=ft.border.all(1, "grey"), border_radius=10, padding=5,
            expand=True
        ),
        ft.Row([btn_abcd, btn_5050], spacing=5),
        ft.Row([btn_bid, btn_pass, btn_allin], spacing=5)
    ], spacing=5, expand=True)
    
    page.add(layout)

    # --- LOGIKA DŹWIĘKÓW CZATU ---

    def process_chat_sounds(chat_list):
        # Analizujemy tylko nowe wiadomości
        new_msgs = [m for m in chat_list if m.get("timestamp", 0) > state.last_audio_ts]
        
        if not new_msgs:
            return

        # Aktualizujemy timestamp na ostatnią
        state.last_audio_ts = new_msgs[-1].get("timestamp", 0)

        for msg in new_msgs:
            p_name = msg.get("player", "")
            text = msg.get("message", "")

            if p_name == "BOT":
                # Analiza treści BOTA
                if "PYTANIE:" in text:
                    play_sound("question")
                elif "ABCD" in text and "Podpowiedź" in text:
                    play_sound("abcd")
                elif "50/50" in text and "Podpowiedź" in text:
                    play_sound("5050")
                else:
                    # Sprawdź czy to licytacja gracza (Bot pisze: "Gracz X podbija...")
                    # Backend wysyła: "{p.name} podbija o..." lub "{p.name} VA BANQUE!"
                    # Musimy wykryć, czy wiadomość zaczyna się od nazwy gracza i dotyczy licytacji
                    # Prosty heurystyk:
                    if "podbija o" in text or "VA BANQUE" in text or "Licytację wygrywa" in text:
                        # Próbujemy wyciągnąć imię gracza. Zazwyczaj jest na początku.
                        # Np. "Marek podbija o 100." -> Marek
                        possible_name = text.split(" ")[0]
                        # Gramy dźwięk tego gracza
                        play_sound(get_player_sound_file(possible_name))
                    else:
                        # Inny komunikat bota
                        play_sound("bot")
            else:
                # Zwykła wiadomość gracza (nie Bot)
                play_sound(get_player_sound_file(p_name))

    async def render_chat(chat_list, players_list):
        if not chat_list: return
        
        # Renderowanie tekstu (tylko jeśli coś nowego)
        last_ts = chat_list[-1].get("timestamp", 0)
        if last_ts != state.last_chat_ts:
            state.last_chat_ts = last_ts
            
            admin_names = {p["name"] for p in players_list if p.get("is_admin")}
            chat_col.controls.clear()
            
            for msg in chat_list:
                p_name = msg.get("player", "")
                m_text = msg.get("message", "")
                spans = []
                
                if p_name == "BOT":
                    is_question = "PYTANIE:" in m_text
                    bot_style = ft.TextStyle(color="blue", weight="bold" if is_question else "normal")
                    msg_color = "#0D47A1" if is_question else "blue"
                    msg_weight = "bold" if is_question else "normal"
                    msg_size = 13 if is_question else 12
                    spans.append(ft.TextSpan("BOT: ", bot_style))
                    spans.append(ft.TextSpan(m_text, ft.TextStyle(color=msg_color, weight=msg_weight, size=msg_size)))
                else:
                    if p_name in admin_names:
                        spans.append(ft.TextSpan("[ADMIN] ", ft.TextStyle(color="red", weight="bold")))
                    p_color = get_player_color(p_name)
                    spans.append(ft.TextSpan(f"{p_name}: ", ft.TextStyle(color=p_color, weight="bold")))
                    spans.append(ft.TextSpan(m_text, ft.TextStyle(color=p_color)))
                
                chat_col.controls.append(ft.Text(spans=spans, selectable=True, size=12))
            chat_col.update()
        
        # Dźwięki (zawsze wywołujemy, funkcja sama sprawdza czy są nowe)
        process_chat_sounds(chat_list)

    async def game_loop():
        current_local_phase = "idle"
        
        while state.joined:
            try:
                data = await fetch_json(f"{BACKEND_URL}/state")
                if data:
                    time_left = float(data.get('time_left', 0))
                    txt_timer.value = f"{int(time_left)} s"
                    pot_val = data.get("pot", 0)
                    phase = data.get("phase")
                    
                    # --- LOGIKA TIMERA (DŹWIĘK 5s) ---
                    # Jeśli faza się zmieniła, resetujemy flagę dźwięku
                    if phase != current_local_phase:
                        state.timer_alert_played = False
                        current_local_phase = phase
                    
                    # Jeśli czas < 5.5s (żeby złapać moment) i > 0 i jeszcze nie zagraliśmy
                    # Dotyczy licytacji, odpowiadania i dyskusji
                    if 0 < time_left <= 5.5 and not state.timer_alert_played:
                        if phase in ["bidding", "answering", "discussion"]:
                            play_sound("out_of_time")
                            state.timer_alert_played = True
                    # ----------------------------------

                    state.local_phase = phase
                    state.answering_player_id = data.get("answering_player_id")
                    state.server_abcd_bought = data.get("abcd_bought", False)

                    me = next((p for p in data.get("players",[]) if p["id"] == state.player_id), None)
                    if me:
                        state.is_admin = me.get("is_admin", False)
                        state.local_money = me.get("money", 0)
                        txt_money.value = f"{state.local_money} zł"
                        txt_pot.value = f"Pula: {pot_val} zł"
                        await fetch_json(f"{BACKEND_URL}/heartbeat", "POST", {"player_id": state.player_id})

                    is_my_turn_ans = (phase == "answering" and state.answering_player_id == state.player_id)
                    is_bidding = (phase == "bidding")
                    
                    btn_bid.disabled = not is_bidding
                    btn_pass.disabled = not is_bidding
                    btn_allin.disabled = not is_bidding
                    
                    btn_abcd.disabled = not is_my_turn_ans
                    btn_5050.disabled = not (is_my_turn_ans and state.server_abcd_bought)

                    page.update()
                    await render_chat(data.get("chat", []), data.get("players", []))
                    
            except Exception as e:
                print(e)
            await asyncio.sleep(1)

    async def do_join(e):
        name = input_name.value
        if not name: return
        res = await fetch_json(f"{BACKEND_URL}/register", "POST", {"name": name})
        if res:
            state.player_id = res["id"]
            state.player_name = res["name"]
            state.joined = True
            row_login.visible = False
            input_chat.disabled = False
            btn_send.disabled = False
            
            # WAŻNE: Przeglądarki blokują auto-play audio dopóki użytkownik nie wejdzie w interakcję.
            # Kliknięcie "Dołącz" jest tą interakcją, więc odblokowuje dźwięki.
            
            page.run_task(game_loop)
            page.update()

    async def do_send(e):
        msg = input_chat.value
        if not msg: return
        
        if state.local_phase == "answering" and state.answering_player_id == state.player_id:
            await fetch_json(f"{BACKEND_URL}/answer", "POST", {"player_id": state.player_id, "answer": msg})
        elif state.is_admin and state.local_phase == "idle" and re.fullmatch(r"\d+", msg):
            await fetch_json(f"{BACKEND_URL}/select_set", "POST", {"player_id": state.player_id, "set_no": int(msg)})
        else:
            await fetch_json(f"{BACKEND_URL}/chat", "POST", {"player": state.player_name, "message": msg})
        
        input_chat.value = ""
        input_chat.focus()
        page.update()

    async def do_bid(e):
        await fetch_json(f"{BACKEND_URL}/bid", "POST", {"player_id": state.player_id, "kind": "normal"})
    async def do_pass(e):
        await fetch_json(f"{BACKEND_URL}/finish_bidding", "POST", {"player_id": state.player_id})
    async def do_allin(e):
        await fetch_json(f"{BACKEND_URL}/bid", "POST", {"player_id": state.player_id, "kind": "allin"})
    async def do_hint_abcd(e):
        await fetch_json(f"{BACKEND_URL}/hint", "POST", {"player_id": state.player_id, "kind": "abcd"})
    async def do_hint_5050(e):
        await fetch_json(f"{BACKEND_URL}/hint", "POST", {"player_id": state.player_id, "kind": "5050"})

    btn_join.on_click = do_join
    btn_send.on_click = do_send
    input_chat.on_submit = do_send
    
    btn_bid.on_click = do_bid
    btn_pass.on_click = do_pass
    btn_allin.on_click = do_allin
    btn_abcd.on_click = do_hint_abcd
    btn_5050.on_click = do_hint_5050

    page.update()

if __name__ == "__main__":
    ft.app(target=main)
