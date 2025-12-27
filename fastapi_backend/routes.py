import os
import uuid
from datetime import datetime, timedelta
import requests
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from db import get_db
from models import CustomUser, Chat, ChatMessage, UserSearchHistory
from auth import (
    hash_password, verify_password, create_access_token, 
    create_refresh_token, verify_token
)

# Groq API settings
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_URL = os.getenv("GROQ_API_URL")

router = APIRouter()


# ======================= Pydantic Schemas =======================

class UserRegister(BaseModel):
    username: str
    email: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class PromptRequest(BaseModel):
    chat_id: Optional[str] = None
    content: str


class ChatMessageResponse(BaseModel):
    role: str
    content: str

    class Config:
        from_attributes = True


class ChatResponse(BaseModel):
    id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SearchQueryRequest(BaseModel):
    search_query: str


# ======================= Helper Functions =======================

def get_current_user(token: str, db: Session) -> CustomUser:
    """Get current user from JWT token"""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )
    
    if not token.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format"
        )
    
    token = token.replace("Bearer ", "")
    user_id = verify_token(token)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    user = db.query(CustomUser).filter(CustomUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    return user


def create_chat_title(user_message: str) -> str:
    """Create a short title for the chat using Groq"""
    try:
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant. Provide a short descriptive title "
                        "for the user's conversation in 3-5 words. Do not add quotes."
                    ),
                },
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 16,
            "temperature": 0.2,
        }
        response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        title = data["choices"][0]["message"]["content"].strip()
        if not title:
            title = user_message[:50]
    except Exception:
        title = user_message[:50]
    return title


# ======================= Authentication Endpoints =======================

@router.post("/api/register/")
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    """
    Register a new user
    """
    # Check if username already exists
    existing_user = db.query(CustomUser).filter(CustomUser.username == user_data.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists."
        )
    
    # Check if email already exists
    existing_email = db.query(CustomUser).filter(CustomUser.email == user_data.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists."
        )
    
    # Create new user
    new_user = CustomUser(
        username=user_data.username,
        email=user_data.email,
        password=hash_password(user_data.password)
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create tokens
    access_token = create_access_token({"sub": str(new_user.id)})
    refresh_token = create_refresh_token({"sub": str(new_user.id)})
    
    return {
        "access": access_token,
        "refresh": refresh_token,
        "user": {
            "username": new_user.username,
            "email": new_user.email
        }
    }


@router.post("/api/login/")
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    """
    Login user and return JWT tokens
    """
    # Find user by email
    user = db.query(CustomUser).filter(CustomUser.email == user_data.email).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials"
        )
    
    # Verify password
    if not verify_password(user_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials"
        )
    
    # Create tokens
    access_token = create_access_token({"sub": str(user.id)})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    
    return {
        "access": access_token,
        "refresh": refresh_token,
        "user": {
            "username": user.username,
            "email": user.email
        }
    }


# ======================= Chat Endpoints =======================

@router.post("/prompt_gpt/")
def prompt_gpt(
    prompt_data: PromptRequest,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Send a prompt to Groq and get response
    """
    # Get current user
    user = get_current_user(authorization, db)
    
    if not prompt_data.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No prompt content provided."
        )
    
    # Get or create chat
    chat_id = prompt_data.chat_id or str(uuid.uuid4())
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    
    if chat:
        if chat.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized access to chat."
            )
    else:
        chat = Chat(
            id=chat_id,
            user_id=user.id,
            title=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(chat)
        db.commit()
        db.refresh(chat)
    
    # Create chat title if not exists
    if not chat.title:
        try:
            chat.title = create_chat_title(prompt_data.content)
            db.commit()
        except Exception:
            pass
    
    # Save user message
    user_message = ChatMessage(
        chat_id=chat.id,
        role="user",
        content=prompt_data.content,
        created_at=datetime.utcnow()
    )
    db.add(user_message)
    db.commit()
    
    # Get chat history (last 20 messages)
    chat_messages = db.query(ChatMessage).filter(
        ChatMessage.chat_id == chat.id
    ).order_by(ChatMessage.created_at).limit(20).all()
    
    groq_messages = [{"role": m.role, "content": m.content} for m in chat_messages]
    
    # Add system message if not exists
    if not any(msg["role"] == "system" for msg in groq_messages):
        groq_messages.insert(0, {"role": "system", "content": "You are a helpful assistant."})
    
    # Call Groq API
    try:
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": groq_messages,
            "max_tokens": 1024,
            "temperature": 0.6,
        }
        response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        groq_reply = data["choices"][0]["message"]["content"]
        
        if not groq_reply:
            raise RuntimeError("Groq returned no text.")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Groq error: {str(e)}"
        )
    
    # Save assistant response
    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content=groq_reply,
        created_at=datetime.utcnow()
    )
    db.add(assistant_message)
    db.commit()
    
    return {"reply": groq_reply}


@router.get("/get_chat_messages/{chat_id}/")
def get_chat_messages(
    chat_id: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Get all messages for a specific chat
    """
    # Get current user
    user = get_current_user(authorization, db)
    
    # Get chat
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    # Check authorization
    if chat.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized access to chat messages."
        )
    
    # Get messages
    messages = db.query(ChatMessage).filter(
        ChatMessage.chat_id == chat.id
    ).order_by(ChatMessage.created_at).all()
    
    return [
        {
            "role": msg.role,
            "content": msg.content
        }
        for msg in messages
    ]


@router.get("/todays_chat/")
def todays_chat(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Get today's chats
    """
    # Get current user
    user = get_current_user(authorization, db)
    
    today = datetime.utcnow().date()
    chats = db.query(Chat).filter(
        Chat.user_id == user.id,
        Chat.created_at >= datetime.combine(today, datetime.min.time())
    ).order_by(Chat.created_at.desc()).limit(10).all()
    
    return [
        {
            "id": chat.id,
            "title": chat.title,
            "created_at": chat.created_at,
            "updated_at": chat.updated_at
        }
        for chat in chats
    ]


@router.get("/yesterdays_chat/")
def yesterdays_chat(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Get yesterday's chats
    """
    # Get current user
    user = get_current_user(authorization, db)
    
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    
    chats = db.query(Chat).filter(
        Chat.user_id == user.id,
        Chat.created_at >= datetime.combine(yesterday, datetime.min.time()),
        Chat.created_at < datetime.combine(today, datetime.min.time())
    ).order_by(Chat.created_at.desc()).limit(10).all()
    
    return [
        {
            "id": chat.id,
            "title": chat.title,
            "created_at": chat.created_at,
            "updated_at": chat.updated_at
        }
        for chat in chats
    ]


@router.get("/seven_days_chat/")
def seven_days_chat(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Get chats from the last 7 days (excluding today and yesterday)
    """
    # Get current user
    user = get_current_user(authorization, db)
    
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    seven_days_ago = today - timedelta(days=7)
    
    chats = db.query(Chat).filter(
        Chat.user_id == user.id,
        Chat.created_at >= datetime.combine(seven_days_ago, datetime.min.time()),
        Chat.created_at < datetime.combine(yesterday, datetime.min.time())
    ).order_by(Chat.created_at.desc()).limit(10).all()
    
    return [
        {
            "id": chat.id,
            "title": chat.title,
            "created_at": chat.created_at,
            "updated_at": chat.updated_at
        }
        for chat in chats
    ]


@router.post("/api/store_search/")
def user_search(
    search_data: SearchQueryRequest,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Store user search query in the database
    """
    # Get current user
    user = get_current_user(authorization, db)
    
    if not search_data.search_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query is required."
        )
    
    # Store search query
    search_history = UserSearchHistory(
        user_id=user.id,
        search_query=search_data.search_query,
        created_at=datetime.utcnow()
    )
    db.add(search_history)
    db.commit()
    
    return {
        "message": "Search query stored successfully."
    }
