import os
import telebot
import json
from flask import Flask, request, redirect
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

# --- 1. КОНФИГУРАЦИЯ И ПРОВЕРКА ПЕРЕМЕННЫХ ---

# Получение переменных окружения
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
WEBHOOK_BASE_URL = os.environ.get('WEBHOOK_BASE_URL') 

# Обязательная проверка наличия всех ключей
if not all([TELEGRAM_TOKEN, SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, WEBHOOK_BASE_URL]):
    print("FATAL ERROR: Один или несколько ключей окружения отсутствуют!")
    # Вызываем исключение, чтобы Render знал, что сервис не может быть запущен
    raise EnvironmentError("Необходимо установить все переменные окружения: TELEGRAM_TOKEN, SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, WEBHOOK_BASE_URL.")

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
    
    # ... (логика обновления токена остается прежней)
    if SpotifyOAuth.is_token_expired(token_info):
        sp_oauth = get_spotify_oauth(user_id)
        # ВАЖНО: убедитесь, что в spotipy 2.25.2 используется 'refresh_token'
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        USER_TOKENS[user_id] = token_info
        
    return Spotify(auth=token_info['access_token'])

# --- 3. ОБРАБОТЧИКИ TELEGRAM ---

@bot.message_handler(commands=['start', 'auth'])
def send_auth_link(message):
    user_id = str(message.chat.id)
    print(f"Обработка команды /start для пользователя {user_id}")
    
    # ПРОВЕРКА: Если ключи Spotify не установлены, здесь будет ошибка!
    try:
        sp_oauth = get_spotify_oauth(user_id)
        auth_url = sp_oauth.get_authorize_url()
    except Exception as e:
        print(f"Ошибка генерации ссылки Spotify: {e}")
        bot.send_message(user_id, "❌ Произошла ошибка конфигурации. Пожалуйста, проверьте ключи Spotify.")
        return

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("🔑 Авторизоваться в Spotify", url=auth_url))

    bot.send_message(user_id, 
                     "Для управления Spotify необходима авторизация.\nНажмите кнопку ниже:", 
                     reply_markup=markup,
                     parse_mode="Markdown")

# ... (Остальные обработчики, как /play, остаются прежними)
@bot.message_handler(commands=['play'])
def control_playback(message):
    user_id = str(message.chat.id)
    sp_client = get_spotify_client(user_id)
    
    if not sp_client:
        return bot.reply_to(message, "⚠️ Сначала авторизуйтесь, используя /auth")

    try:
        sp_client.start_playback()
        bot.reply_to(message, "▶️ Запрос на возобновление воспроизведения отправлен!")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка управления: Убедитесь, что Spotify запущен на одном из ваших устройств. Ошибка: {e}")

# --- 4. МАРШРУТЫ FLASK (ВЕБХУКИ) ---

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
    
    # ... (логика обработки callback остается прежней)
    if not code:
        bot.send_message(user_id, "❌ Авторизация Spotify отменена.")
        return "Авторизация отменена."

    sp_oauth = get_spotify_oauth(user_id)
    token_info = sp_oauth.get_access_token(code)
    
    USER_TOKENS[user_id] = token_info

    bot.send_message(user_id, "✅ **Авторизация Spotify прошла успешно!**\nТеперь вы можете использовать команды /play и другие.", parse_mode="Markdown")
    return "Авторизация завершена. Вернитесь в Telegram."

@app.route("/")
def index():
    """Простой маршрут для проверки статуса хостинга."""
    return "Spotify TG Bot is running."

# --- 5. ЗАПУСК И УСТАНОВКА ВЕБХУКА ---

# В этом коде мы полагаемся на gunicorn, который будет запускать app:app
# Функция set_telegram_webhook не запускается здесь, она должна быть вызвана отдельно.
if __name__ == '__main__':
    print("Этот код должен запускаться через 'gunicorn app:app' на Render.")
    pass
