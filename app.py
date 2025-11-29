import os
import telebot
from flask import Flask, request, render_template, jsonify, redirect
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import sys

# --- 1. КОНФИГУРАЦИЯ И ПРОВЕРКА ПЕРЕМЕННЫХ ---

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
WEBHOOK_BASE_URL = os.environ.get('WEBHOOK_BASE_URL') 

if not all([TELEGRAM_TOKEN, SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, WEBHOOK_BASE_URL]):
    print("FATAL ERROR: Один или несколько ключей окружения отсутствуют!")
    sys.exit(1)

WEBHOOK_PATH = f'/{TELEGRAM_TOKEN}' 
SPOTIPY_REDIRECT_URI = f'{WEBHOOK_BASE_URL}/callback'
# Важный Scope: доступ к плейлистам, библиотеке и управлению
SCOPE = "user-read-playback-state user-modify-playback-state playlist-read-private user-library-read user-library-modify" 

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)
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
    
    token_info = USER_TOKENS.get(user_id)
    
    if SpotifyOAuth.is_token_expired(token_info):
        try:
            sp_oauth = get_spotify_oauth(user_id)
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            USER_TOKENS[user_id] = token_info
        except Exception as e:
            print(f"Token refresh failed for {user_id}: {e}")
            return None
        
    return Spotify(auth=token_info['access_token'])

# --- 3. ОБРАБОТЧИКИ TELEGRAM ---

@bot.message_handler(commands=['start', 'auth'])
def send_auth_link(message):
    user_id = str(message.chat.id)
    
    webapp_url = WebAppInfo(url=WEBHOOK_BASE_URL) 
    webapp_button = InlineKeyboardButton("✨ Запустить Mini App", web_app=webapp_url)

    markup = InlineKeyboardMarkup()
    markup.add(webapp_button) 

    bot.send_message(user_id, 
                     "Нажмите **Запустить Mini App**, чтобы войти в Spotify и начать управление. \n\n⚠️ **Требуется повторная авторизация после обновления кода.**", 
                     reply_markup=markup,
                     parse_mode="Markdown")

# --- 4. МАРШРУТЫ FLASK (ВЕБХУКИ И API ДЛЯ MINI APP) ---

@app.route(WEBHOOK_PATH, methods=['POST'])
def telegram_webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'ok'
    return '!'

# --- Новый корневой маршрут: Всегда ведет на экран авторизации/загрузки Mini App ---
@app.route("/")
def index():
    """Отдает экран авторизации (auth.html)."""
    return render_template('auth.html')

# --- Новый маршрут для плеера ---
@app.route("/player")
def player_index():
    """Отдает HTML-страницу Mini App с плеером (index.html)."""
    return render_template('index.html')

# --- Маршрут для обработки возврата Spotify ---
@app.route("/callback")
def spotify_callback():
    code = request.args.get('code')
    user_id = request.args.get('state') 
    
    if not code or not user_id:
        return render_template('auth.html')

    try:
        sp_oauth = get_spotify_oauth(user_id)
        token_info = sp_oauth.get_access_token(code)
        
        USER_TOKENS[user_id] = token_info

        # После успешной авторизации перенаправляем на главный экран плеера
        return redirect('/player') 
    except Exception as e:
        print(f"Ошибка получения токена Spotify: {e}")
        return "Ошибка авторизации. Пожалуйста, попробуйте снова."

# --- API: Получить URL для авторизации (вызывается из auth.html) ---
@app.route("/api/auth_url", methods=['POST'])
def api_auth_url():
    data = request.get_json()
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({"success": False, "message": "User ID is missing"}), 400

    try:
        sp_oauth = get_spotify_oauth(user_id)
        auth_url = sp_oauth.get_authorize_url()
        return jsonify({"success": True, "auth_url": auth_url}), 200
    except Exception as e:
        print(f"Auth URL Error: {e}")
        return jsonify({"success": False, "message": "Failed to generate auth URL."}), 500

# --- API: Проверить наличие токена (вызывается из auth.html и index.html) ---
@app.route("/api/check_token", methods=['POST'])
def api_check_token():
    data = request.get_json()
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({"is_authorized": False, "message": "User ID missing"}), 400

    # Проверяем, есть ли токен в USER_TOKENS
    is_authorized = str(user_id) in USER_TOKENS
    return jsonify({"is_authorized": is_authorized}), 200

# --- API для получения статуса плеера ---
@app.route("/api/status", methods=['POST'])
def api_status():
    data = request.get_json()
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({"success": False, "message": "User ID is missing"}), 400

    sp_client = get_spotify_client(user_id)
    if not sp_client:
        return jsonify({"success": False, "message": "User not authorized"}), 401

    try:
        playback = sp_client.current_playback()
        
        if not playback or not playback.get('item'):
            return jsonify({"success": True, "is_playing": False, "track_name": None, "message": "Нет активного устройства или трека."}), 200

        track = playback['item']
        
        is_liked = False
        if track.get('id'):
            is_liked_result = sp_client.current_user_saved_tracks_contains([track['id']])
            if is_liked_result:
                is_liked = is_liked_result[0]
        
        status_data = {
            "success": True,
            "is_playing": playback.get('is_playing', False),
            "track_name": track.get('name', 'Неизвестный трек'),
            "artist_name": ', '.join([artist['name'] for artist in track.get('artists', [])]),
            "progress_ms": playback.get('progress_ms', 0),
            "duration_ms": track.get('duration_ms', 1),
            "image_url": track.get('album', {}).get('images', [{}])[0].get('url') if track.get('album') else None,
            "track_id": track.get('id'),
            "is_liked": is_liked
        }
        return jsonify(status_data), 200

    except Exception as e:
        print(f"Spotify Status Error: {e}")
        return jsonify({"success": False, "message": "Spotify API error."}), 500

# --- API для управления (Play/Next/Prev) ---
@app.route("/api/control/<action>", methods=['POST'])
def api_control(action):
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

# --- API для лайка/дизлайка трека ---
@app.route("/api/like_toggle", methods=['POST'])
def api_like_toggle():
    data = request.get_json()
    user_id = data.get('user_id')
    track_id = data.get('track_id')
    is_liked = data.get('is_liked')

    sp_client = get_spotify_client(user_id)
    if not sp_client:
        return jsonify({"success": False, "message": "User not authorized"}), 401
    if not track_id:
        return jsonify({"success": False, "message": "Track ID missing"}), 400

    try:
        if is_liked:
            sp_client.current_user_saved_tracks_delete([track_id])
            msg = "Лайк убран."
        else:
            sp_client.current_user_saved_tracks_add([track_id])
            msg = "Трек добавлен в 'Мне нравится'."

        return jsonify({"success": True, "message": msg}), 200

    except Exception as e:
        print(f"Spotify Like Toggle Error: {e}")
        return jsonify({"success": False, "message": "Spotify API error during like operation."}), 500

# --- API для загрузки плейлистов и треков "Мне нравится" ---
@app.route("/api/playlists", methods=['POST'])
def api_playlists():
    data = request.get_json()
    user_id = data.get('user_id')

    sp_client = get_spotify_client(user_id)
    if not sp_client:
        return jsonify({"success": False, "message": "User not authorized"}), 401

    try:
        # 1. Загрузка плейлистов
        playlists_result = sp_client.current_user_playlists(limit=10)
        playlists = [{'id': p['id'], 'name': p['name'], 'uri': p['uri']} for p in playlists_result['items']]

        # 2. Загрузка первых 50 треков из "Мне нравится" (Saved Tracks)
        liked_tracks_result = sp_client.current_user_saved_tracks(limit=50)
        liked_tracks = [
            {'id': t['track']['id'], 'name': t['track']['name'], 'artist': t['track']['artists'][0]['name'], 'uri': t['track']['uri']} 
            for t in liked_tracks_result['items']
        ]

        return jsonify({"success": True, "playlists": playlists, "liked_tracks": liked_tracks}), 200

    except Exception as e:
        print(f"Spotify Playlists Error: {e}")
        return jsonify({"success": False, "message": "Spotify API error loading playlists."}), 500


# --- API для поиска и запуска треков/плейлистов ---
@app.route("/api/search_play", methods=['POST'])
def api_search_play():
    """Ищет трек/плейлист или запускает по URI."""
    data = request.get_json()
    user_id = data.get('user_id')
    query = data.get('query')
    is_uri = data.get('is_uri', False) 

    if not user_id or not query:
        return jsonify({"success": False, "message": "Missing user ID or query"}), 400

    sp_client = get_spotify_client(user_id)
    if not sp_client:
        return jsonify({"success": False, "message": "User not authorized"}), 401

    try:
        track_or_context_uri = query
        
        if not is_uri:
            # Если это не URI, ищем трек
            results = sp_client.search(q=query, limit=1, type='track')
            tracks = results['tracks']['items']

            if not tracks:
                return jsonify({"success": False, "message": f"Трек '{query}' не найден."}), 200

            track_or_context_uri = tracks[0]['uri']

        # Запуск воспроизведения
        if track_or_context_uri.startswith('spotify:track'):
            sp_client.start_playback(uris=[track_or_context_uri])
            msg = f"Запущен трек."
        else:
            # Запуск плейлиста или альбома (контекст)
            sp_client.start_playback(context_uri=track_or_context_uri)
            msg = "Запущен плейлист/альбом."


        return jsonify({"success": True, "message": msg}), 200

    except Exception as e:
        print(f"Spotify Search/Play Error: {e}")
        return jsonify({"success": False, "message": "Spotify API error during search/play."}), 500

# --- 5. ЗАПУСК (Через Gunicorn) ---

if __name__ == '__main__':
    print("Приложение готово к запуску через Gunicorn на Render.")
    pass
