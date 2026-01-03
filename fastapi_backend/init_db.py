"""Init DB helper script for fastapi_backend."""
from . import db

if __name__ == "__main__":
    db.init_db()
    print("DB initialized")
