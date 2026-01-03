import sys, os
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db import init_db

if __name__ == "__main__":
    init_db()
    print("DB initialized")