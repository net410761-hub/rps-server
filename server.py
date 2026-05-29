from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import os
import re
from datetime import datetime

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get('DATABASE_URL')
API_SECRET = os.environ.get('API_SECRET')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def validate_name(name):
    if not name or len(name) > 50:
        return False
    return bool(re.match(r'^[a-zA-Zа-яА-Я0-9_]{3,50}$', name))

def check_auth():
    api_key = request.headers.get('X-API-Key')
    return api_key == API_SECRET

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT name, streak FROM leaderboard ORDER BY streak DESC LIMIT 50')
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([{'name': r[0], 'streak': r[1]} for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/record-win', methods=['POST'])
def record_win():
    if not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    player_name = data.get('player_name', '')
    if not validate_name(player_name):
        return jsonify({'error': 'Invalid name'}), 400
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('INSERT INTO wins (player_name) VALUES (%s)', (player_name,))
        cur.execute('UPDATE evolution_state SET total_wins = total_wins + 1')
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/evolution', methods=['GET'])
def get_evolution():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT total_wins, target_wins, current_generation FROM evolution_state LIMIT 1')
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return jsonify({
                'total_wins': row[0],
                'target_wins': row[1],
                'current_generation': row[2]
            })
        return jsonify({'error': 'No evolution state'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/record-record', methods=['POST'])
def record_record():
    if not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    name = data.get('name', '')
    streak = data.get('streak', 0)
    if not validate_name(name) or not isinstance(streak, int) or streak <= 0:
        return jsonify({'error': 'Invalid data'}), 400
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO leaderboard (name, streak, created_at) 
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (name) DO UPDATE 
            SET streak = EXCLUDED.streak, created_at = CURRENT_TIMESTAMP
        ''', (name, streak))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'alive'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
