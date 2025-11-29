import os
import telebot
from flask import Flask, request, render_template, jsonify
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# --- 1. КОНФИГУРАЦИЯ И ПРОВЕРКА ПЕРЕМЕННЫХ ---

# Получение переменных окружения (должны быть заданы на Render!)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
WEBHOOK_BASE_URL = os.environ.get('WEBHOOK_BASE_URL') 

# Обязательная проверка наличия всех ключей
if not all([TELEGRAM_TOKEN, SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, WEBHOOK_BASE_URL]):
    print("FATAL ERROR: Один или несколько ключей окружения отсутствуют!")
    raise EnvironmentError("Необходимо установить все переменные окружения на Render.")

WEBHOOK_PATH = f'/{TELEGRAM_TOKEN}' 
SPOTIPY_REDIRECT_URI = f'{WEBHOOK_BASE_URL}/callback'
SCOPE = "user-read-playback-state user-modify-playback-state playlist-read-private" 

# Инициализация бота и Flask
bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# Хранилище токенов (ВНИМАНИЕ: Сбрасывается при перезапуске сервера!)
USER_TOKENS = {}

# --- 2. ФУНКЦИИ SPOTIFY ---

def get_spotify_oauth(user_id):
    """Создает менеджер авторизации для данного пользователя."""
    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE,
        state=user_id 
    )

def get_spotify_client(user_id):
    """Возвращает клиента Spotify, обновляя токен при необходимости."""
    if user_id not in USER_TOKENS:
        return None
    
    token_info = USER_TOKENS[user_id]
    
    # Проверка и обновление токена
    if SpotifyOAuth.is_token_expired(token_info):
        sp_oauth = get_spotify_oauth(user_id)
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        USER_TOKENS[user_id] = token_info
        
    return Spotify(auth=token_info['access_token'])

# --- 3. ОБРАБОТЧИКИ TELEGRAM ---

@bot.message_handler(commands=['start', 'auth'])
def send_auth_link(message):
    user_id = str(message.chat.id)
    
    try:
        sp_oauth = get_spotify_oauth(user_id)
        auth_url = sp_oauth.get_authorize_url()
    except Exception as e:
        print(f"Ошибка генерации ссылки Spotify: {e}")
        bot.send_message(user_id, "❌ Произошла ошибка конфигурации. Проверьте ключи Spotify.", parse_mode="Markdown")
        return

    markup = InlineKeyboardMarkup()
    
    # 1. Кнопка для OAuth (Авторизация Spotify)
    oauth_button = InlineKeyboardButton("🔑 Авторизоваться в Spotify (ШАГ 1)", url=auth_url)
    
    # 2. Кнопка для Mini App (Web App Button)
    webapp_url = WebAppInfo(url=WEBHOOK_BASE_URL) 
    webapp_button = InlineKeyboardButton("✨ Запустить Mini App (ШАГ 2)", web_app=webapp_url)

    markup.add(oauth_button) 
    markup.add(webapp_button) 

    bot.send_message(user_id, 
                     "Для работы с Spotify сначала авторизуйтесь (Шаг 1), затем запустите Mini App (Шаг 2).", 
                     reply_markup=markup,
                     parse_mode="Markdown")

@bot.message_handler(commands=['play'])
def control_playback(message):
    user_id = str(message.chat.id)
    # Эта функция только проверяет авторизацию
    sp_client = get_spotify_client(user_id)
    
    if not sp_client:
        return bot.reply_to(message, "⚠️ Сначала авторизуйтесь, используя /auth")
    
    bot.reply_to(message, "Используйте Mini App (кнопка '✨') для управления.")

# --- 4. МАРШРУТЫ FLASK (ВЕБХУКИ И API ДЛЯ MINI APP) ---

@app.route(WEBHOOK_PATH, methods=['POST'])
def telegram_webhook():
    """Маршрут для приема обновлений от Telegram."""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'ok'
    return '!'

@app.route("/callback")
def spotify_callback():
    """Маршрут для приема кода авторизации от Spotify."""
    code = request.args.get('code')
    user_id = request.args.get('state') 
    
    if not code:
        bot.send_message(user_id, "❌ Авторизация Spotify отменена.", parse_mode="Markdown")
        return "Авторизация отменена."

    try:
        sp_oauth = get_spotify_oauth(user_id)
        token_info = sp_oauth.get_access_token(code)
        
        USER_TOKENS[user_id] = token_info

        bot.send_message(user_id, "✅ **Авторизация Spotify прошла успешно!**\nТеперь вы можете использовать Mini App.", parse_mode="Markdown")
        return "Авторизация завершена. Вернитесь в Telegram."
    except Exception as e:
        print(f"Ошибка получения токена Spotify: {e}")
        return "Ошибка авторизации. Пожалуйста, попробуйте снова."

@app.route("/")
def index():
    """Корневой маршрут, который отдает HTML-страницу Mini App."""
    return render_template('index.html')

# --- API для управления из Mini App ---
@app.route("/api/control/<action>", methods=['POST'])
def api_control(action):
    """Маршрут для приема команд от JavaScript из Mini App."""
    # 1. Извлечение user_id из данных WebApp
    # В реальном приложении это требует более сложной аутентификации,
    # но для простоты берем его из тела запроса.
    data = request.get_json()
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({"success": False, "message": "User ID is missing"}), 400

    sp_client = get_spotify_client(user_id)
    if not sp_client:
        return jsonify({"success": False, "message": "User not authorized"}), 401

    try:
        if action == 'playpause':
            playback = sp_client.current_playback()
            if playback and playback.get('is_playing'):
                sp_client.pause_playback()
                msg = "Пауза"
            else:
                sp_client.start_playback()
                msg = "Воспроизведение"
        elif action == 'next':
            sp_client.next_track()
            msg = "Следующий трек"
        elif action == 'prev':
            sp_client.previous_track()
            msg = "Предыдущий трек"
        else:
            return jsonify({"success": False, "message": "Invalid action"}), 400

        return jsonify({"success": True, "message": msg}), 200

    except Exception as e:
        print(f"Spotify Control Error: {e}")
        return jsonify({"success": False, "message": "Spotify API error. Check device."}), 500

# --- 5. ЗАПУСК (Через Gunicorn) ---

if __name__ == '__main__':
    print("Приложение готово к запуску через Gunicorn на Render.")
    pass