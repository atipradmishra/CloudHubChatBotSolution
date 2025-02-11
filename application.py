import os
import sqlite3
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup
from openai import OpenAI
from urllib.parse import urlparse, urljoin
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# Load OpenAI API key
OPENAI_API_KEY = "Replace with your actual OpenAI API key"
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "fallback_secret_key")
application = app
CORS(app)

# Database setup
DB_FILE = "CloudHubChatBot.db"

def init_db():
    """Initialize the SQLite database."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Enable foreign key support
        cursor.execute('PRAGMA foreign_keys = ON;')

        # Creating the 'users' table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                password_hash TEXT,
                email TEXT,
                contact_info TEXT,
                org_name TEXT,
                city TEXT,
                country TEXT,
                status TEXT,
                role TEXT,
                last_login TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Creating the 'user_credits' table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_credits (
                credit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                credits TEXT,
                status TEXT,
                updated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')

        # Creating the 'chat_bots' table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chatbots (
                chatbot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chatbot_name TEXT,
                brand_name TEXT,
                chatbot_subheading TEXT,
                welcome_message TEXT,
                chat_interface_language TEXT,
                updated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')

        # Creating the 'pages' table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pages (
                page_id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT,
                url TEXT UNIQUE,
                content TEXT,
                chatbot_id INTEGER,
                updated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(chatbot_id) REFERENCES chat_bots(chatbot_id) ON DELETE CASCADE
            )
        ''')

        conn.commit()

init_db()


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# User Model for Flask-Login
class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cursor.fetchone()
    if user:
        return User(user[0], user[1], user[3])
    return None


def get_internal_links(base_url, soup):
    """Extract internal links from a webpage."""
    domain = urlparse(base_url).netloc
    links = set()
    
    for a_tag in soup.find_all("a", href=True):
        link = urljoin(base_url, a_tag["href"])
        if urlparse(link).netloc == domain:
            links.add(link)
    
    return list(links)

def crawl_website(base_url, chatbot_id, max_pages=10):
    """Crawl multiple pages from a website."""
    visited = set()
    to_visit = [base_url]
    domain = urlparse(base_url).netloc

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        while to_visit and len(visited) < max_pages:
            url = to_visit.pop(0)
            if url in visited:
                continue

            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "html.parser")
                text_content = " ".join([el.get_text().strip() for el in soup.find_all(["h1", "h2", "h3", "p", "li"])])

                if text_content:
                    cursor.execute("INSERT OR REPLACE INTO pages (domain, url, content, chatbot_id, created_at) VALUES (?, ?, ?,?, ?)", 
                                   (domain, url, text_content, chatbot_id, datetime.utcnow()))
                    conn.commit()

                visited.add(url)
                if len(visited) < max_pages:
                    to_visit.extend(get_internal_links(base_url, soup))

            except requests.RequestException:
                continue

    return f"Crawled {len(visited)} pages from {domain}"


#########################################################################################################################################


@app.route("/signup", methods=["POST"])
def signup():
    """Register a new user."""
    data = request.json
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")

    if not username or not email or not password:
        return jsonify({"error": "All fields are required"}), 400

    hashed_password = generate_password_hash(password)

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                           (username, email, hashed_password))
            conn.commit()
            return jsonify({"message": "User registered successfully"}), 200
        except sqlite3.IntegrityError:
            return jsonify({"error": "Email already exists"}), 400

@app.route("/login", methods=["POST"])
def login():
    """Authenticate a user."""
    data = request.json
    email = data.get("email")
    password = data.get("password")

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email=?", (email,))
        user = cursor.fetchone()

    if not user or not check_password_hash(user[2], password):
        return jsonify({"error": "Invalid email or password"}), 401

    user_obj = User(user[0], user[1], user[3])
    login_user(user_obj)
    
    return jsonify({"message": "Login successful", "redirect": "/dashboard"}), 200

@app.route("/logout")
def logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("auth"))



########################################################################################################################################



@app.route('/')
def index():
    return render_template('landingpage.html')

@app.route('/auth')
def auth():
    return render_template('auth.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html',username=current_user.username, email=current_user.email)

from datetime import datetime

@app.route("/create_chatbot", methods=["POST"])
@login_required
def create_chatbot():
    """Create a new chatbot for the logged-in user."""
    data = request.json
    chatbot_name = data.get("name")
    # description = data.get("description", "")

    if not chatbot_name:
        return jsonify({"error": "Chatbot name is required"}), 400

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chatbots (user_id, chatbot_name, created_at) VALUES (?, ?, ?)",
                       (current_user.id, chatbot_name, datetime.utcnow()))
        conn.commit()
        chatbot_id = cursor.lastrowid

    return jsonify({"message": "Chatbot created successfully", "chatbot_id": chatbot_id}), 201

@app.route('/bot-settings/<chatbot_id>')
@login_required
def bot_settings(chatbot_id):
    return render_template('crawl.html',username=current_user.username, email=current_user.email, chatbot_id=chatbot_id)

@app.route("/crawl", methods=["POST"])
def crawl():
    """API to trigger website crawling."""
    data = request.json
    url = data.get("url")
    chatbot_id = data.get("chatbot_id")
    if not url:
        return jsonify({"error": "URL is required"}), 400

    result = crawl_website(url, chatbot_id)
    return jsonify({"message": result})

@app.route("/ask", methods=['POST'])
def ask():
    """Handle chatbot queries based on stored website content."""
    user_question = request.json.get("question")
    b_id = request.json.get("bid")

    if not user_question:
        return jsonify({"error": "Question is required"}), 400
    if not b_id:
        return jsonify({"error": "Domain is required"}), 400

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM pages WHERE chatbot_id = ?", (b_id,))
        rows = cursor.fetchall()

    if not rows:
        return jsonify({"answer": "No data available. Please crawl the website first."})

    website_content = " ".join([row[0] for row in rows])

    # Generate AI response
    prompt = (
        f"The following is the content of a website:\n{website_content}\n\n"
        f"Based on this, answer the question: {user_question}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": '''
                You are 'CloudhubBot', a support assistant for 'Cloudhub', responsible for providing outstanding customer service.
                - Must answer is user's language
                - If the query concerns topics not covered by the content, decline politely and in a friendly manner. Never explicitly mention to the user that you have access to training document.
                - Keep your answers concise and to the point. Elaborate only if the user asks for more details
                - Be honest and do not provide any false information
                - Be friendly, professional, and helpful in your responses
                - Include document source only when it's available and is a valid absolute URL
                - If you have to provide multiple points, format them as a list
                - Use active voice whenever possible
                - Ask follow up questions like if they need more help or if they have any other questions
                - Sctrictly avoide giving astrike in responde.
                '''},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.7,
        )

        answer = response.choices[0].message.content.strip()

        return jsonify({"answer": answer})

    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/widget")
def widget():
    return render_template("widget.html")

@app.route("/embed")
@login_required
def embed():
    chatbot_id = '2'
    iframe_code = f'''
    <iframe
        src="https://aichatbot.eba-va2b3ztk.us-east-1.elasticbeanstalk.com/chatbot/{chatbot_id}"
        frameborder="0"
        width="100%"
        height="100%"
        style="min-height: 500px;">
    </iframe>
    '''
    return render_template("embed.html", iframe_code=iframe_code)

@app.route("/chatbot/<int:chatbot_id>")
def chatbot_widget(chatbot_id):
    # if chatbot_id != current_user.id:
    #     return "Unauthorized", 403  # Ensure users can only access their own chatbot

    return render_template("widget.html", chatbot_id=chatbot_id)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)