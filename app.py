from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from io import BytesIO
import base64
import random
import string
from captcha.image import ImageCaptcha

app = Flask(__name__)
app.secret_key = 'super_secure_key_change_me_2026'

DB_FILE = 'db.sqlite'
CRYPTO_LIST = ['bitcoin', 'ethereum', 'tether', 'binancecoin', 'solana']  # BTC, ETH, USDT, BNB, SOL

# Генератор капчи
captcha_generator = ImageCaptcha(width=280, height=100)

def generate_captcha():
    text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    image = captcha_generator.generate_image(text)
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return {'text': text.upper(), 'image': f"data:image/png;base64,{img_str}"}

# Инициализация БД
def init_db():
    if not os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            balance_usd REAL DEFAULT 0.0
        )''')
        cur.execute('''CREATE TABLE portfolio (
            user_id INTEGER,
            crypto_id TEXT,
            amount REAL DEFAULT 0.0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        conn.commit()
        conn.close()

init_db()

# Получить соединение с БД
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# Главная
@app.route('/')
def index():
    return render_template('index.html')

# Регистрация
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        captcha_input = request.form['captcha_text'].upper()
        captcha_correct = session.get('captcha_text')

        if captcha_input != captcha_correct:
            captcha = generate_captcha()
            session['captcha_text'] = captcha['text']
            return render_template('register.html', error='Неверная капча!', captcha=captcha)

        conn = get_db()
        cur = conn.cursor()
        try:
            hash_pass = generate_password_hash(password)
            cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hash_pass))
            user_id = cur.lastrowid
            for crypto in CRYPTO_LIST:
                cur.execute("INSERT INTO portfolio (user_id, crypto_id, amount) VALUES (?, ?, 0.0)", (user_id, crypto))
            conn.commit()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            captcha = generate_captcha()
            session['captcha_text'] = captcha['text']
            return render_template('register.html', error='Пользователь уже существует!', captcha=captcha)
        finally:
            conn.close()

    captcha = generate_captcha()
    session['captcha_text'] = captcha['text']
    return render_template('register.html', captcha=captcha)

# Вход
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        captcha_input = request.form['captcha_text'].upper()
        captcha_correct = session.get('captcha_text')

        if captcha_input != captcha_correct:
            captcha = generate_captcha()
            session['captcha_text'] = captcha['text']
            return render_template('login.html', error='Неверная капча!', captcha=captcha)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, password FROM users WHERE username = ?", (username,))
        user = cur.fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        captcha = generate_captcha()
        session['captcha_text'] = captcha['text']
        return render_template('login.html', error='Неверный логин или пароль!', captcha=captcha)

    captcha = generate_captcha()
    session['captcha_text'] = captcha['text']
    return render_template('login.html', captcha=captcha)

# Выход
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# Личный кабинет
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT balance_usd FROM users WHERE id = ?", (session['user_id'],))
    balance_usd = cur.fetchone()['balance_usd']
    cur.execute("SELECT crypto_id, amount FROM portfolio WHERE user_id = ?", (session['user_id'],))
    portfolio = cur.fetchall()
    conn.close()
    return render_template('dashboard.html', balance_usd=balance_usd, portfolio=portfolio, crypto_list=CRYPTO_LIST)

# Биржа
@app.route('/exchange', methods=['GET', 'POST'])
def exchange():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    selected_crypto = request.form.get('crypto') if request.method == 'POST' else CRYPTO_LIST[0]
    return render_template('exchange.html', crypto_list=CRYPTO_LIST, selected_crypto=selected_crypto)

# API для цен
@app.route('/api/prices')
def get_prices():
    try:
        ids = ','.join(CRYPTO_LIST)
        response = requests.get(f'https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd')
        return jsonify(response.json())
    except:
        return jsonify({crypto: {'usd': 0} for crypto in CRYPTO_LIST})

# API для исторических данных (для графика)
@app.route('/api/historical')
def get_historical():
    crypto = request.args.get('crypto', CRYPTO_LIST[0])
    days = request.args.get('days', 30)
    try:
        response = requests.get(f'https://api.coingecko.com/api/v3/coins/{crypto}/market_chart?vs_currency=usd&days={days}')
        data = response.json()['prices']
        labels = [str(i) for i in range(len(data))]  # Простые лейблы (дни)
        values = [price[1] for price in data]
        return jsonify({'labels': labels, 'values': values})
    except:
        return jsonify({'labels': [], 'values': []})

# Магазин (покупка/продажа)
@app.route('/store', methods=['GET', 'POST'])
def store():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT balance_usd FROM users WHERE id = ?", (session['user_id'],))
    balance_usd = cur.fetchone()['balance_usd']
    cur.execute("SELECT crypto_id, amount FROM portfolio WHERE user_id = ?", (session['user_id'],))
    portfolio = {row['crypto_id']: row['amount'] for row in cur.fetchall()}
    conn.close()

    if request.method == 'POST':
        crypto = request.form['crypto']
        amount = float(request.form['amount'])
        action = request.form['action']  # buy или sell

        try:
            response = requests.get(f'https://api.coingecko.com/api/v3/simple/price?ids={crypto}&vs_currencies=usd')
            price = response.json()[crypto]['usd']
            cost = amount * price

            conn = get_db()
            cur = conn.cursor()
            if action == 'buy':
                if cost > balance_usd:
                    return render_template('store.html', error='Недостаточно USD!', balance_usd=balance_usd, portfolio=portfolio, crypto_list=CRYPTO_LIST)
                cur.execute("UPDATE users SET balance_usd = balance_usd - ? WHERE id = ?", (cost, session['user_id']))
                cur.execute("UPDATE portfolio SET amount = amount + ? WHERE user_id = ? AND crypto_id = ?", (amount, session['user_id'], crypto))
            elif action == 'sell':
                if amount > portfolio.get(crypto, 0):
                    return render_template('store.html', error='Недостаточно крипты!', balance_usd=balance_usd, portfolio=portfolio, crypto_list=CRYPTO_LIST)
                cur.execute("UPDATE users SET balance_usd = balance_usd + ? WHERE id = ?", (cost, session['user_id']))
                cur.execute("UPDATE portfolio SET amount = amount - ? WHERE user_id = ? AND crypto_id = ?", (amount, session['user_id'], crypto))
            conn.commit()
            conn.close()
            return render_template('store.html', success=f'Успех: {action} {amount} {crypto.upper()} за ${cost:.2f}!', balance_usd=balance_usd - cost if action == 'buy' else balance_usd + cost, portfolio=portfolio, crypto_list=CRYPTO_LIST)
        except:
            return render_template('store.html', error='Ошибка API цен!', balance_usd=balance_usd, portfolio=portfolio, crypto_list=CRYPTO_LIST)

    return render_template('store.html', balance_usd=balance_usd, portfolio=portfolio, crypto_list=CRYPTO_LIST)

# Пополнение (фейк)
@app.route('/deposit', methods=['GET', 'POST'])
def deposit():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance_usd = balance_usd + 100 WHERE id = ?", (session['user_id'],))
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard'))
    return render_template('deposit.html')

# Вывод (псевдо)
@app.route('/withdraw', methods=['GET', 'POST'])
def withdraw():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT balance_usd FROM users WHERE id = ?", (session['user_id'],))
    balance_usd = cur.fetchone()['balance_usd']
    conn.close()

    if request.method == 'POST':
        amount = float(request.form['amount'])
        method = request.form['method']
        if amount > balance_usd:
            return render_template('withdraw.html', error='Недостаточно средств!', balance_usd=balance_usd)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE users SET balance_usd = balance_usd - ? WHERE id = ?", (amount, session['user_id']))
        conn.commit()
        conn.close()
        return render_template('withdraw.html', success=f'Заявка на вывод ${amount} на {method} принята (фейк)!')

    return render_template('withdraw.html', balance_usd=balance_usd)

if __name__ == '__main__':
    app.run(debug=True)