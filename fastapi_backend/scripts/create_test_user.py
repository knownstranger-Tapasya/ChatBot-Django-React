import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from db import SessionLocal
from models import CustomUser
from auth import hash_password

if __name__ == '__main__':
    db = SessionLocal()
    email = 'test+reset@example.com'
    existing = db.query(CustomUser).filter(CustomUser.email==email).first()
    if existing:
        print('Test user already exists:', existing.id)
    else:
        user = CustomUser(username='testresetuser', email=email, password=hash_password('Password123'))
        db.add(user)
        db.commit()
        print('Created test user id=', user.id)
