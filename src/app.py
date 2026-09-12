<<<<<<< HEAD
from flask import Flask
from google import genai
from dotenv import load_dotenv
import yfinance as yf

=======
import os
from flask import Flask, render_template
>>>>>>> df13ed8e8b007daef3dd886d2aaa1a4ffdde1f3d

base_dir = os.path.abspath(os.path.dirname(__file__))
template_dir = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_dir)

@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
