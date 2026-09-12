from flask import Flask
from google import genai
from dotenv import load_dotenv
import yfinance as yf


app = Flask(__name__)


@app.route("/")
def hello_world():
  return "<p>Hello, World!</p>"

