from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import psycopg2
import psycopg2.extras
import os
import re
from datetime import datetime, timedelta
import hashlib
import hmac

app = Flask(__name__)
CORS(app)

# Настройка ограничения запросов
limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Получаем переменные окружения
DATABASE_URL = os.environ.get('DATABASE_URL')
API_SECRET = os.environ.get('API_SECRET', 'default_secret_change_me')

# Подключение к базе данных
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# Создание таблиц при запуске (если не существуют)
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Таблица лидеров
    cur.execute('''
        CREATE TABLE IF NOT EXISTS leaderboard (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) NOT NULL,
            streak INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица побед (для эволюции)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS wins (
            id SERIAL PRIMARY KEY,
            player_name VARCHAR(50) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица состояния эволюции
    cur.execute('''
        CREATE TABLE IF NOT EXISTS evolution_state (
            id SERIAL PRIMARY KEY,
            total_wins INTEGER DEFAULT 0,
            target_wins INTEGER DEFAULT 10000,
            current_generation INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Вставляем начальное состояние, если таблица пуста
    cur.execute('SELECT COUNT(*) FROM evolution_state')
    if cur.fetchone()[0] == 0:
        cur.execute('''
            INSERT INTO evolution_state (total_wins, target_wins, current_generation)
            VALUES (0, 10000, 1)
        ''')
    
    conn.commit()
    cur.close()
    conn.close()

# Валидация имени игрока
def validate_name(name):
    if not name or len(name) > 50:
        return False
    # Только буквы, цифры, подчёркивания и русские буквы
    return bool(re.match(r'^[a-zA-Zа-яА-Я0-9_]{3,50}$', name))

# Проверка API ключа
def check_api_key():
    api_key = request.headers.get('X-API-Key')
    if not api_key or not hmac.compare_digest(api_key, API_SECRET):
        return False
    return True

# ============= API ЭНДПОЙНТЫ =============

@app.route('/api/leaderboard', methods=['GET'])
@limiter.limit("100 per minute")
def get_leaderboard():
    """Получить топ-50 рекордов"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute('''
            SELECT name, streak, created_at 
            FROM leaderboard 
            ORDER BY streak DESC 
            LIMIT 50
        ''')
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        leaderboard = [{'name': row['name'], 'streak': row['streak']} for row in rows]
        return jsonify(leaderboard)
    
    except Exception as e:
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/record-win', methods=['POST'])
@limiter.limit("20 per minute")
def record_win():
    """Записать победу игрока"""
    # Проверка API ключа
    if not check_api_key():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    player_name = data.get('player_name', '')
    
    # Валидация имени
    if not validate_name(player_name):
        return jsonify({'error': 'Invalid player name'}), 400
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Записываем победу
        cur.execute(
            'INSERT INTO wins (player_name) VALUES (%s)',
            (player_name,)
        )
        
        # Обновляем общий счётчик побед
        cur.execute('''
            UPDATE evolution_state 
            SET total_wins = total_wins + 1,
                updated_at = CURRENT_TIMESTAMP
        ''')
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({'status': 'ok', 'message': 'Win recorded'})
    
    except Exception as e:
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/evolution', methods=['GET'])
def get_evolution():
    """Получить текущий статус эволюции"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute('''
            SELECT total_wins, target_wins, current_generation 
            FROM evolution_state 
            LIMIT 1
        ''')
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if row:
            return jsonify({
                'total_wins': row['total_wins'],
                'target_wins': row['target_wins'],
                'current_generation': row['current_generation'],
                'remaining_wins': max(0, row['target_wins'] - row['total_wins'])
            })
        else:
            return jsonify({'error': 'No evolution state'}), 404
    
    except Exception as e:
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/record-record', methods=['POST'])
@limiter.limit("10 per minute")
def record_record():
    """Записать новый рекорд в таблицу лидеров"""
    if not check_api_key():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    name = data.get('name', '')
    streak = data.get('streak', 0)
    
    if not validate_name(name):
        return jsonify({'error': 'Invalid name'}), 400
    
    if not isinstance(streak, int) or streak <= 0:
        return jsonify({'error': 'Invalid streak'}), 400
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Проверяем, есть ли уже такой игрок с большим рекордом
        cur.execute(
            'SELECT streak FROM leaderboard WHERE name = %s',
            (name,)
        )
        existing = cur.fetchone()
        
        if existing and existing[0] >= streak:
            cur.close()
            conn.close()
            return jsonify({'status': 'ok', 'message': 'Record not higher than existing'})
        
        # Вставляем или обновляем рекорд
        cur.execute('''
            INSERT INTO leaderboard (name, streak, created_at) 
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (name) DO UPDATE 
            SET streak = EXCLUDED.streak,
                created_at = CURRENT_TIMESTAMP
        ''', (name, streak))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({'status': 'ok', 'message': 'Record saved'})
    
    except Exception as e:
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Проверка работоспособности сервера"""
    return jsonify({'status': 'alive', 'timestamp': datetime.now().isoformat()})

# Инициализация базы данных при запуске
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)